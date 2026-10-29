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
| Meta Llama vision model (default Llama 4 Maverick; any OpenAI-compatible host) | Reading bill photos, UPI screenshots, statements and scanned rate lists |
| Meta Llama text model | Hinglish / Devanagari message understanding, PDF interpretation fallback, second opinion on suspicious forwards, product-name mapping, free-text owner feedback |
| Whisper (open model, local, via faster-whisper) | Speech-to-text for voice notes |
| Tesseract OCR (open source, local, optional) | Offline fallback for photos when no model key is set |

Guard rails around the models: untrusted text is wrapped as data, every number a model returns must
appear in the source, money is computed in Python, results are cached by file hash for reproducible
reruns. Keys are read only from the environment (`LLAMA_API_KEY`, `LLAMA_BASE_URL`).
