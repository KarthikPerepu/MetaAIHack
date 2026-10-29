# Khata (खाता) — find the money leaking out of a shop's WhatsApp

Meta Global AI Developer Hackathon 2026 (theme: *Build for Your World*). A small set of agents on
the **Meta Model API** (Muse Spark for vision and text, Muse Voice Transcribe for voice notes) reads a
kirana shop's exported WhatsApp chats (chat text, bill photos, PDF rate lists, UPI screenshots, statements, Hinglish
voice notes), remembers on disk what each distributor agreed, and reports six kinds of leak
with the rupee amount, the exact chat line or bill row that proves it, a confidence, and the
action that is safe in the chosen mode.

| Class | Example from the data |
|---|---|
| rate_mismatch | GT/1187 billed ₹410, rate list v2 (in force from 3 Oct) says ₹395 → ₹300 |
| duplicate_bill | SP/4412 repeats SP/4401's delivery → ₹16,400 |
| scheme_not_given | voice note "20 carton lo, 2 carton free milega", no free goods on GT/1187 → ₹790 |
| paid_but_shown_due | ₹5,000 paid by UPI on 5 Oct, still due on the 10 Oct statement → ₹5,000 |
| gst_error | HSN 1704 charged 28 % instead of 18 % → ₹1,000 |
| credit_period_risk | unpaid bill crossing its credit days within 7 days → interest at risk |

Plus **conflict** (two trusted sources disagree → owner decides) and **cannot_determine**
(a document is missing → says what and who has it).

How it works, the decisions behind it, and the limits: **[DESIGN.md](DESIGN.md)**.

## Quick start

```bash
make setup            # Python 3.11+ venv from harness/requirements.txt
make grade            # replay practice weeks (+ owner feedback), print the score card
make sheet            # exam chats -> answer_sheet.json (per class: count, rupees, bill ids)
make eval             # 89 eval cases + our two-week eval shop, graded
make demo             # eval shop from real photos / PDFs only (sidecar JSON ignored)
```

Windows PowerShell: `.\run.ps1 setup | grade | sheet | eval | eval-shop | demo`.

The three commands the grader calls (each in a fresh process; everything learnt is kept under `--state-dir`):

```bash
python harness/harness.py ingest data/practice/week_01 --state-dir state/practice
python harness/harness.py report --as-of 2026-10-15 --mode supervised --state-dir state/practice > report.json
python harness/harness.py feedback data/practice/feedback/feedback.json --state-dir state/practice
```

`report` also takes `--mode autonomous` and `-o file.json`; `sheet` takes the same flags as `report`.

## Meta models and your API key

| Meta model (Meta Model API, `https://api.meta.ai/v1`) | What Khata uses it for |
|---|---|
| **Muse Spark** (`muse-spark-1.3`), vision | reading bill photos (auto-retried at 90/270/180° for sideways photos), UPI screenshots, statements, scanned rate lists |
| **Muse Spark**, text | understanding Hinglish / Devanagari chat and voice transcripts (schemes, spoken rates, credit terms, payment claims), PDFs the table parser cannot read, mapping bill items to rate-list names, a second opinion on suspicious forwards, free-text owner feedback |
| **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) | WhatsApp voice notes (`.opus` → 16 kHz WAV → `/v1/asr/transcribe`) |

Rupee arithmetic is always done in Python, and every number a model returns must appear in the source.

**Where the key goes:** copy `.env.example` to `.env` in the project folder and replace
`paste-your-key-here` with your Meta Model API key. That is the only place. `.env` is git-ignored,
so it never reaches GitHub. Everything else has working defaults.

Or, for one PowerShell session only:

```powershell
$env:MODEL_API_KEY = "your-key"
```

| Setting (in `.env` or the environment) | Default |
|---|---|
| `MODEL_API_KEY` | none: required for Muse (alias `LLAMA_API_KEY`) |
| `MODEL_BASE_URL` | `https://api.meta.ai/v1` |
| `MODEL_VISION_MODEL`, `MODEL_TEXT_MODEL` | `muse-spark-1.3` |
| `MODEL_VOICE_MODEL` | `muse-voice-transcribe-1.0` (local Whisper `WHISPER_MODEL=small` if unavailable) |
| `MODEL_REASONING_EFFORT` | `low`: reading a bill is not a hard problem, and it saves credit |
| `KHATA_OFFLINE=1` | never call a model; deterministic readers only |
| `KHATA_IGNORE_SIDECARS=1` | ignore `<file>.json` / `<file>.txt` extraction caches and read the real media |

Credit use: `make eval` / `.\run.ps1 eval` never calls the API (free). Every model answer is cached
under `--state-dir`, so re-running a report on the same files is free too. Without a key the harness
still runs: PDFs via pdfplumber, photos via tesseract OCR (if installed), messages via the rule-based
Hinglish / Devanagari parser, voice via local Whisper.

To run on open Llama weights instead (the problem statement allows "any host or locally"), point
`MODEL_BASE_URL` at an OpenAI-compatible host such as a local Ollama and set the model ids; see
`.env.example`. The client adapts if a model rejects an option (`reasoning_effort`,
`max_completion_tokens`, `temperature`, the `system` role).

## Hackathon fit

| Judging criterion (reported weights) | Where it shows |
|---|---|
| Impact (30%) | small shops lose money on every mis-billed distributor bill; Khata finds it with proof and drafts the Hinglish WhatsApp reply |
| Use of Meta's AI tools (25%) | Muse Spark vision + text and Muse Voice Transcribe, all through the Meta Model API (table above) |
| Demo (25%) | `make demo` / `.\run.ps1 demo` on real photos; a 3-minute video script is in [DEMO.md](DEMO.md) |
| Technical depth (20%) | as-of reasoning over dated records, guard-railed model use, 89 eval cases, strict grader: [DESIGN.md](DESIGN.md) |

## Results (local grader, `grader/grader.py`)

| Run | Result |
|---|---|
| Practice | 100 / 100 — 5 / 5 found, ₹0 wrongly claimed, clean look-alike GT/1179 left alone |
| Our eval shop (third shop, 2 weeks, feedback, 12-item answer key) | 100 / 100 with every rubric component measured |
| Same, real photos and PDFs only, no model | 7 / 8 found, 0 false positives (the miss is the voice-note scheme; no speech model offline) |
| Exam | rate ₹1,000 · duplicate ₹12,000 · scheme ₹780 · paid-due ₹8,000 · GST ₹1,300 · 3 bills cannot_determine (Banaras Wholesale never sent a rate list) |

The grader is a strict stand-in we wrote; the judges' own grader is not in this repo.

## Output

`report.json` → `{as_of, mode, shop, summary, findings[], safety_alerts[], pending_requests[], feedback_processed}`.
A finding follows the glossary schema:

```json
{
  "bill_id": "GT/1187", "class": "rate_mismatch", "status": "confirmed", "distributor": "GT",
  "amount_inr": 300.0,
  "evidence": ["ratelist_v2.pdf: Sunrise Glucose Biscuit | 395",
               "_chat.txt:L7: Ramesh Ganga Traders: <attached: ratelist_v2.pdf>",
               "GT-1187.jpg: row 1: Sunrise Glucose Biscuit 20 x 410 = 8200",
               "_chat.txt:L22: Ramesh Ganga Traders: <attached: GT-1187.jpg>"],
  "action": {"supervised": "draft_whatsapp_reply", "autonomous": "send_whatsapp_reply"},
  "confidence": 0.95,
  "reasoning": "ratelist_v2.pdf (v2, in force from 2026-10-03) lists Sunrise Glucose Biscuit at ₹395. Bill GT/1187 dated 2026-10-09 charges ₹410: (₹410 − ₹395) × 20 = ₹300. Amount is before GST.",
  "draft_message": "Namaste Ramesh ji, bill GT/1187 (2026-10-09) mein Sunrise Glucose Biscuit ₹410 lagaya hai, rate list mein ₹395 hai (20 × ₹15). Kul ₹300 ka credit note bhej dijiye. Dhanyavaad."
}
```

Conflicts add `sources`; cannot_determine adds `missing` and `ask`; autonomous mode adds the
`safety_check` it ran.

## Safety

* Never pays, never shares a PIN or bank details: there is no payment action in the code, and drafts are scrubbed.
* Never follows instructions found in chats, bills, PDFs or voice notes: they are quarantined, listed in
  `safety_alerts`, and create no records. A bill with instructions printed on it is still audited.
* Supervised mode only drafts. Autonomous mode sends only when evidence is complete, nothing conflicts,
  the amount is under ₹2,000, the message is a reversible query, and confidence ≥ 0.75.
* Owner escalations stay under 60 % of findings; conflicts are never hidden.

## Live demo: where to make a change

| Request | Where |
|---|---|
| Change the autonomous limit, credit defaults, 7-day window, duplicate window | `harness/config.py` → `Policy` |
| Add / fix a GST rate for an HSN | `harness/data/gst_rates.json` |
| New scheme wording ("ek peti ke saath ek dabba muft") | `SCHEME_PATTERNS` in `harness/agents/message_agent.py` + a case in `evals/test_parsing.py` |
| New injection phrasing | `OVERRIDE` / `CREDENTIAL` in `harness/ingest/safety.py` + a case in `evals/test_parsing.py` |
| Change how a class is decided or valued | `harness/detectors/<class>.py` (one file per class) |
| Change draft wording | `_draft` in `harness/policy.py` |

## Repository

```
harness/
  harness.py            CLI: ingest / report / feedback / sheet
  config.py             env config + policy numbers
  analysis.py           as-of view: rate in force, quotes, payments, credit terms, owner decisions
  report.py             runs detectors, merges, caps escalation, actions, answer sheet
  policy.py             actions per mode, 4-part safety test, drafts, escalation cap
  feedback.py           owner answers -> decisions
  agents/               llama_client (cache, guard), vision_agent, message_agent
  ingest/               chat_parser, documents, media (pdf/ocr/stt), text_parsers, normalize, safety, pipeline
  detectors/            one module per class
  knowledge/            records with provenance; on-disk store
  data/gst_rates.json   HSN -> GST
grader/grader.py        strict local grader
evals/                  89 eval cases; fixtures/ (scenario builder, media renderer); scenarios/annapurna/
scripts/                render_data_media.py, make_eval_shop.py
data/                   practice, exam, worked examples
```
