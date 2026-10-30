# AI tools we used

The problem statement asks us to list the AI coding tools we used, so here they are.

**While building it**

- **Google Gemini (Antigravity)** gave us the first version: the folder layout, the first attempt at
  the six checks, a draft glossary, placeholder data and some tests.
- **Claude Code (Anthropic)** helped us go through that version, find what didn't actually work, and
  rebuild it: reading real photos, PDFs and voice notes, recognising distributors without
  hard-coding names, the date logic, the checks, the safety rules, the tests, the grader, the test
  shop and these docs.

We ran the tests after every change, and the git history shows each step starting from the Gemini
version.

**Inside Khata when it runs**

- **Muse Spark 1.3** (Meta Model API) reads bill photos, UPI screenshots, statements and scanned
  rate lists.
- **Muse Spark 1.3** also reads Hinglish and Devanagari messages, PDFs our parser can't handle, and
  the owner's free-text answers, and double-checks suspicious forwarded messages.
- **Muse Voice Transcribe 1.0** (Meta Model API) turns voice notes into text.
- **Whisper** (open model, runs locally) is the backup for voice notes if the Meta API isn't set up.
- **Tesseract OCR** (open source, optional) is the backup for photos when there's no API key.

The models only read. All the money calculations are plain Python, any number a model gives back
has to appear in the original document, and the API key only ever comes from the `.env` file or the
environment, never from the code.
