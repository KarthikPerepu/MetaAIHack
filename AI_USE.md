# AI use

## Coding tools

| Tool | What it did |
|---|---|
| Google Gemini (Antigravity) | First version of the repository: package layout, initial detectors, a stand-in glossary, placeholder data with JSON "sidecars", initial tests, docs. |
| Claude Code (Anthropic Claude Opus) | Audit of that version and the rewrite described in DESIGN.md §1: real media readers, generic distributor identification, as-of reasoning, detector and policy rewrite, feedback learning, the eval suite, the strict local grader, rendered test media, the two-week eval shop, and these docs. |

Every change was run against the eval suite; the commit history shows the Gemini baseline and each
step after it.

## Models used by the harness at runtime

| Model | Use |
|---|---|
| Muse Spark 1.3 (vision), Meta Model API | Reading bill photos, UPI screenshots, statements and scanned rate lists |
| Muse Spark 1.3 (text), Meta Model API | Hinglish / Devanagari message understanding, PDF interpretation fallback, second opinion on suspicious forwards, product-name mapping, free-text owner feedback |
| Muse Voice Transcribe 1.0, Meta Model API | Speech-to-text for voice notes |
| Whisper (open model, local, via faster-whisper) | Fallback speech-to-text when the Meta API is not configured |
| Tesseract OCR (open source, local, optional) | Offline fallback for photos when no model key is set |

Guard rails around the models: untrusted text is wrapped as data, every number a model returns must
appear in the source, money is computed in Python, results are cached by file hash for reproducible
reruns. Keys are read only from the environment or a git-ignored `.env` (`MODEL_API_KEY` / `LLAMA_API_KEY`, `MODEL_BASE_URL`).
