"""
Local readers that need no model API:

* PDF text + tables (pdfplumber, then pypdf), page rendering for scanned PDFs
* tesseract OCR with automatic orientation detection (offline fallback for photos)
* speech-to-text for voice notes with an open Whisper model
  (faster-whisper first, openai-whisper second)
"""

from __future__ import annotations

import io
import logging
import math
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────
def pdf_text_and_tables(path: str | Path) -> tuple[str, list]:
    text_parts, tables = [], []
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
                for tb in page.extract_tables() or []:
                    tables.append([[(c or "").strip() for c in row] for row in tb if row])
    except Exception as e:
        logger.info(f"pdfplumber could not read {Path(path).name}: {e}")
    text = "\n".join(t for t in text_parts if t).strip()
    if not text:
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore
            except ImportError:
                PdfReader = None
        if PdfReader:
            try:
                reader = PdfReader(str(path))
                text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
            except Exception as e:
                logger.info(f"pypdf could not read {Path(path).name}: {e}")
    return text, tables


def render_pdf_pages(path: str | Path, max_pages: int = 3) -> list[Path]:
    """Render PDF pages to PNG files (for scanned PDFs). Returns temp file paths."""
    out = []
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        for i in range(min(len(pdf), max_pages)):
            img = pdf[i].render(scale=2).to_pil()
            tmp = Path(tempfile.mkstemp(suffix=f"-p{i + 1}.png")[1])
            img.save(tmp)
            out.append(tmp)
    except Exception as e:
        logger.info(f"Could not render {Path(path).name}: {e}")
    return out


# ─────────────────────────────────────────────────────────────
# OCR
# ─────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def tesseract_langs() -> tuple[str, ...]:
    if not shutil.which("tesseract"):
        return ()
    try:
        import pytesseract
        return tuple(pytesseract.get_languages(config=""))
    except Exception:
        return ("eng",)


def detect_rotation(path: str | Path) -> int:
    """Clockwise degrees needed to make the text upright (0 if unknown)."""
    if not tesseract_langs():
        return 0
    try:
        import pytesseract
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("L")
            osd = pytesseract.image_to_osd(im, config="--psm 0 -c min_characters_to_try=20")
        m = re.search(r"Rotate:\s*(\d+)", osd)
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
        if m and (not conf or float(conf.group(1)) >= 1.0):
            return int(m.group(1)) % 360
    except Exception:
        pass
    return 0


def ocr_image(path: str | Path, rotate: Optional[int] = None) -> tuple[str, float, int]:
    """Return (text, mean word confidence 0-1, rotation used)."""
    langs = tesseract_langs()
    if not langs:
        return "", 0.0, 0
    try:
        import pytesseract
        from PIL import Image, ImageOps, ImageFilter
        rot = detect_rotation(path) if rotate is None else rotate
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if rot:
                im = im.rotate(-rot, expand=True)
            im = ImageOps.autocontrast(im.convert("L"))
            if max(im.size) < 1400:
                f = 1400 / max(im.size)
                im = im.resize((int(im.width * f), int(im.height * f)))
            im = im.filter(ImageFilter.SHARPEN)
            lang = "+".join(l for l in ("eng", "hin") if l in langs) or "eng"
            data = pytesseract.image_to_data(im, lang=lang, config="--psm 6", output_type=pytesseract.Output.DICT)
            text = pytesseract.image_to_string(im, lang=lang, config="--psm 6")
        confs = [float(c) for c, w in zip(data.get("conf", []), data.get("text", [])) if str(w).strip() and float(c) >= 0]
        mean = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return text, round(mean, 3), rot
    except Exception as e:
        logger.info(f"OCR failed on {Path(path).name}: {e}")
        return "", 0.0, 0


# ─────────────────────────────────────────────────────────────
# Speech to text
# ─────────────────────────────────────────────────────────────
HINGLISH_PROMPT = ("Bhaiya, carton, bag, free, scheme, rate, bill, delivery, payment, discount. "
                   "20 carton lo, 2 carton free milega. Rate 395 hai.")


@lru_cache(maxsize=2)
def _faster_whisper(size: str):
    from faster_whisper import WhisperModel
    return WhisperModel(size, device="cpu", compute_type="int8")


@lru_cache(maxsize=2)
def _openai_whisper(size: str):
    import whisper
    return whisper.load_model(size)


TRADE_KEYWORDS = ["carton", "peti", "bag", "bori", "box", "free", "scheme", "rate", "bill", "discount",
                  "credit", "byaj", "bhaiya", "didi"]


def to_wav_16k(path: str | Path) -> Optional[bytes]:
    """Decode any WhatsApp audio (.opus/.ogg/.m4a/.mp3) to mono 16-bit 16 kHz WAV bytes.

    Uses PyAV (installed with faster-whisper, works on Windows without ffmpeg), else the ffmpeg CLI.
    """
    import wave
    pcm = b""
    try:
        import av
        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            chunks = []
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().tobytes())
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().tobytes())
            pcm = b"".join(chunks)
    except Exception as e:
        logger.info(f"PyAV could not decode {Path(path).name}: {str(e)[:120]}")
    if not pcm and shutil.which("ffmpeg"):
        try:
            pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "16000",
                                  "-f", "s16le", "-"], capture_output=True, timeout=60, check=True).stdout
        except Exception as e:
            logger.info(f"ffmpeg could not decode {Path(path).name}: {str(e)[:120]}")
    if not pcm:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


def muse_transcribe(path: str | Path, cfg, keywords: Optional[list[str]] = None) -> Optional[dict]:
    """Meta Model API speech-to-text (Muse Voice Transcribe), one-shot file endpoint.

    POST {base}/asr/transcribe, multipart: a JSON "request" part + the "audio" part, Bearer auth
    (as in Meta's meta-model-cookbook, 06_muse_voice/01_voice_api_fundamentals).
    Returns None when not configured or on failure, so the caller can fall back to Whisper.
    """
    if cfg is None or cfg.offline or not cfg.api_key or not cfg.is_meta_model_api or not cfg.voice_model:
        return None
    import json
    import time
    import urllib.error
    import urllib.request
    import uuid
    wav = to_wav_16k(path)
    if not wav:
        return None
    req = {"mode": "PUSH_TO_TALK", "model": cfg.voice_model, "audioEncoding": "WAV",
           "keywords": (keywords or TRADE_KEYWORDS)[:50]}
    boundary = "khata-" + uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"request\"\r\n"
        f"Content-Type: application/json\r\n\r\n".encode(), json.dumps(req).encode(), b"\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"voice.wav\"\r\n"
        f"Content-Type: audio/wav\r\n\r\n".encode(), wav, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    url = cfg.base_url.rstrip("/") + "/asr/transcribe"
    for attempt in range(3):
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {cfg.api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=cfg.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = (data.get("transcript") or " ".join(t.get("transcript", "") for t in data.get("turns") or [])).strip()
            return {"text": text, "confidence": 0.9 if text else 0.0, "language": "",
                    "method": f"meta:{cfg.voice_model}"}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            logger.warning(f"Muse Voice Transcribe failed [{e.code}] for {Path(path).name}: {detail}")
            if e.code not in (429, 500, 502, 503, 504):
                return None
        except Exception as e:
            logger.warning(f"Muse Voice Transcribe failed for {Path(path).name}: {str(e)[:160]}")
        time.sleep(2 ** attempt)
    return None


def transcribe(path: str | Path, model_size: str = "small", cfg=None,
               keywords: Optional[list[str]] = None) -> dict:
    """Return {'text', 'confidence', 'language', 'method'}; empty text when no STT is available.

    Order: Muse Voice Transcribe (Meta Model API) -> faster-whisper -> openai-whisper (both local).
    """
    res = muse_transcribe(path, cfg, keywords)
    if res and res["text"]:
        return res
    path = str(path)
    try:
        model = _faster_whisper(model_size)
        segments, info = model.transcribe(path, beam_size=5, vad_filter=True, initial_prompt=HINGLISH_PROMPT,
                                          condition_on_previous_text=False)
        segs = list(segments)
        text = " ".join(s.text.strip() for s in segs).strip()
        if segs:
            probs = [math.exp(max(-5.0, s.avg_logprob)) * (1 - min(1.0, s.no_speech_prob)) for s in segs]
            conf = sum(probs) / len(probs)
        else:
            conf = 0.0
        return {"text": text, "confidence": round(conf, 3), "language": getattr(info, "language", ""),
                "method": f"faster-whisper-{model_size}"}
    except Exception as e:
        logger.info(f"faster-whisper unavailable for {Path(path).name}: {str(e)[:120]}")
    try:
        model = _openai_whisper(model_size)
        res = model.transcribe(path, fp16=False, initial_prompt=HINGLISH_PROMPT)
        segs = res.get("segments") or []
        probs = [math.exp(max(-5.0, s.get("avg_logprob", -1))) * (1 - s.get("no_speech_prob", 0)) for s in segs]
        return {"text": (res.get("text") or "").strip(), "confidence": round(sum(probs) / len(probs), 3) if probs else 0.5,
                "language": res.get("language", ""), "method": f"openai-whisper-{model_size}"}
    except Exception as e:
        logger.info(f"openai-whisper unavailable for {Path(path).name}: {str(e)[:120]}")
    return {"text": "", "confidence": 0.0, "language": "", "method": "none"}
