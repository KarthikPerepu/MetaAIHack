"""
Voice Note Speech-to-Text.

Transcribes .opus voice notes using Whisper (open model).
Handles Hinglish (Hindi in Latin script mixed with English).
"""

from __future__ import annotations
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _convert_opus_to_wav(opus_path: str) -> Optional[str]:
    """Convert .opus to .wav for Whisper processing.

    Tries ffmpeg first, falls back to pydub.
    """
    import subprocess
    wav_path = str(Path(opus_path).with_suffix(".wav"))

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", opus_path, "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True,
            timeout=30,
        )
        if Path(wav_path).exists():
            return wav_path
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("ffmpeg not available for audio conversion")

    # Fallback: try pydub
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(opus_path, format="opus")
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(wav_path, format="wav")
        return wav_path
    except Exception as e:
        logger.warning(f"pydub conversion failed: {e}")

    return None


def transcribe_voice_note(
    audio_path: str,
    language: str = "hi",
    model_size: str = "base",
) -> dict:
    """Transcribe a voice note using Whisper.

    Args:
        audio_path: Path to the audio file (.opus, .mp3, .wav).
        language: Language code (default "hi" for Hindi/Hinglish).
        model_size: Whisper model size ("tiny", "base", "small", "medium", "large").

    Returns:
        Dict with "text", "language", "confidence", "segments".
    """
    audio_path = str(Path(audio_path).resolve())
    logger.info(f"Transcribing voice note: {audio_path}")

    # Check for sidecar transcript (.txt) if available
    p = Path(audio_path)
    for sidecar in [p.with_suffix(".txt"), p.with_name(p.name + ".txt")]:
        if sidecar.exists():
            try:
                with open(sidecar, "r", encoding="utf-8") as f:
                    text = f.read().strip()
                logger.info(f"Loaded sidecar transcript from {sidecar.name}")
                return {
                    "text": text,
                    "language": language,
                    "confidence": 0.95,
                    "segments": [{"text": text, "start": 0.0, "end": 1.0, "confidence": 0.95}],
                }
            except Exception as e:
                logger.warning(f"Failed to read sidecar transcript: {e}")

    # Convert if needed
    input_path = audio_path
    if audio_path.lower().endswith(".opus"):
        converted = _convert_opus_to_wav(audio_path)
        if converted:
            input_path = converted
        else:
            logger.error(f"Could not convert {audio_path} to WAV")
            return _empty_result()

    # Try faster-whisper first (much faster)
    result = _try_faster_whisper(input_path, language, model_size)
    if result:
        return result

    # Fall back to openai-whisper
    result = _try_openai_whisper(input_path, language, model_size)
    if result:
        return result

    logger.error(f"All STT methods failed for {audio_path}")
    return _empty_result()


def _try_faster_whisper(
    audio_path: str,
    language: str,
    model_size: str,
) -> Optional[dict]:
    """Try transcription with faster-whisper."""
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            vad_filter=True,
        )

        segments_list = []
        full_text_parts = []
        for segment in segments:
            segments_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
                "confidence": segment.avg_logprob,
            })
            full_text_parts.append(segment.text.strip())

        full_text = " ".join(full_text_parts)
        avg_confidence = (
            sum(s["confidence"] for s in segments_list) / len(segments_list)
            if segments_list else 0.0
        )
        # Convert log prob to 0-1 range
        confidence = min(1.0, max(0.0, 1.0 + avg_confidence / 3.0))

        logger.info(f"faster-whisper: '{full_text[:80]}...' (confidence: {confidence:.2f})")
        return {
            "text": full_text,
            "language": info.language if hasattr(info, 'language') else language,
            "confidence": confidence,
            "segments": segments_list,
        }
    except ImportError:
        logger.debug("faster-whisper not available")
        return None
    except Exception as e:
        logger.warning(f"faster-whisper failed: {e}")
        return None


def _try_openai_whisper(
    audio_path: str,
    language: str,
    model_size: str,
) -> Optional[dict]:
    """Try transcription with openai-whisper."""
    try:
        import whisper

        model = whisper.load_model(model_size)
        result = model.transcribe(
            audio_path,
            language=language,
            fp16=False,
        )

        segments_list = []
        for seg in result.get("segments", []):
            segments_list.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip(),
                "confidence": seg.get("avg_logprob", 0.0),
            })

        full_text = result.get("text", "").strip()
        logger.info(f"openai-whisper: '{full_text[:80]}...'")

        return {
            "text": full_text,
            "language": result.get("language", language),
            "confidence": 0.75,  # Default confidence for whisper
            "segments": segments_list,
        }
    except ImportError:
        logger.debug("openai-whisper not available")
        return None
    except Exception as e:
        logger.warning(f"openai-whisper failed: {e}")
        return None


def _empty_result() -> dict:
    """Return an empty transcription result."""
    return {
        "text": "",
        "language": "unknown",
        "confidence": 0.0,
        "segments": [],
    }
