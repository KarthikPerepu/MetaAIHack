# Khata (खाता) — find the money leaking out of a shop's WhatsApp

Meta AI Hackathon 2026. A small set of Llama-powered agents reads a kirana shop's exported
WhatsApp chats (chat text, bill photos, PDF rate lists, UPI screenshots, statements, Hinglish
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
make eval             # 85 eval cases + our two-week eval shop, graded
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

## Models and keys

Keys come only from the environment (never from files in the repo):

| Variable | Meaning |
|---|---|
| `LLAMA_API_KEY` (alias `MODEL_API_KEY`) | key for an OpenAI-compatible endpoint serving Meta Llama |
| `LLAMA_BASE_URL` | default `https://api.llama.com/compat/v1`; also Together, Groq, a local Ollama… |
| `LLAMA_VISION_MODEL`, `LLAMA_TEXT_MODEL` | default `Llama-4-Maverick-17B-128E-Instruct-FP8` (set the id your host uses) |
| `WHISPER_MODEL` | open Whisper size for voice notes (default `small`, runs locally, no key) |
| `KHATA_OFFLINE=1` | never call a model; deterministic readers only |
| `KHATA_IGNORE_SIDECARS=1` | ignore `<file>.json` / `<file>.txt` extraction caches and read the real media |

Easiest: copy `.env.example` to `.env` in the project folder and paste your key there. `.env` is
git-ignored, so it never reaches GitHub. Or set the variables for one terminal session:

```powershell
$env:LLAMA_API_KEY = "..."; $env:LLAMA_BASE_URL = "https://api.llama.com/compat/v1"
```

The eval suite (`make eval`) never calls the API, so it costs nothing; model answers are cached under
`--state-dir`, so re-running a report on the same files is free too.

Where Llama is used: reading bill photos, UPI screenshots and statements (vision, with rotation
retry for sideways photos), interpreting PDFs that the table parser cannot, understanding Hinglish /
Devanagari messages and transcripts, a second opinion on suspicious forwarded messages, mapping
bill item names to rate-list names, and parsing free-text owner feedback. Rupee arithmetic is always
done in Python, and every number the model returns must appear in the source text.

Without a key the harness still runs: PDFs via pdfplumber, photos via tesseract OCR (if installed),
messages via the rule-based Hinglish / Devanagari parser.

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
evals/                  85 eval cases; fixtures/ (scenario builder, media renderer); scenarios/annapurna/
scripts/                render_data_media.py, make_eval_shop.py
data/                   practice, exam, worked examples
```
