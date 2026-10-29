# Khata (खाता)

> **Find the money leaking out of a kirana shop's WhatsApp.**  
> Built for the **Meta AI Hackathon 2026** using open Meta Llama models.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Score: 100/100](https://img.shields.io/badge/practice%20score-100%2F100-success.svg)](grader/grader.py)
[![Capping Violations: 0](https://img.shields.io/badge/capping%20mistakes-0%20(clean)-brightgreen.svg)](grader/grader.py)
[![Evals: 36 passed](https://img.shields.io/badge/evals-36%2F36%20passed-green.svg)](evals/)

---

## 🏪 The Problem

A kirana shop in India (like **Shanti General Store**, Patna or **Laxmi Stores**, Varanasi) pays ~40 distributor bills a month from 6+ suppliers. Every transaction lives on WhatsApp:
- Salesmen send photo bills (often skewed, blurred, or tilted).
- Schemes are agreed verbally in Hinglish voice notes (*"10 carton pe 1 free"*).
- Rate lists arrive as PDFs on Monday and updated ones on Thursday.
- Payments go via UPI screenshots in the same chat.

Because nobody cross-references past chats and rate lists before paying, thousands of rupees leak out every month. **Khata** is an autonomous and supervised AI harness that stops this leakage.

---

## 🔍 The Six Discrepancy Classes

| # | Discrepancy Class | What It Means | Amount Calculation |
|---|---|---|---|
| 1 | `rate_mismatch` | An item billed higher than the rate list in force on the bill date | $(\text{billed rate} - \text{agreed rate}) \times \text{qty}$ |
| 2 | `duplicate_bill` | The same delivery billed twice, possibly weeks apart with a new bill ID | Full value of the repeat bill |
| 3 | `scheme_not_given` | Free goods or discount agreed in chat/voice missing on the bill | $\text{free goods qty} \times \text{active rate}$ or discount |
| 4 | `paid_but_shown_due` | A UPI payment already made still shown as outstanding on account statement | Exact payment amount |
| 5 | `gst_error` | Wrong GST slab for the item HSN, or tax worked out on the wrong base | Excess tax charged |
| 6 | `credit_period_risk` | Bill reaching credit terms deadline within 7 days (attracting interest) | Projected interest that would be incurred |

### Cross-Cutting Behaviors
- **`conflict`**: Raised when two trusted authorities disagree (e.g. salesman voice note says ₹410, signed rate list says ₹395). Escalated to owner.
- **`cannot_determine`**: When verification requires an absent document (e.g. rate list never sent). The harness names who has it (e.g. *"distributor — request rate list"*).

---

## 🛡️ Safety & Escalation Policies

The hackathon defines **5 fatal mistakes** that cap your score at 40 max. Khata enforces the safety gates below; unresolved trusted-source conflicts remain with the owner.

| Mistake | Risk | How Khata Prevents It |
|---|---|---|
| **CM1** | Following prompt injection in chat/bill | Screens chat before identification, quarantines unsafe forwarded content, and screens extracted bill, PDF, and voice text. |
| **CM2** | Treating one-time schemes as permanent | Verbal schemes default to `one_time`; applicability is derived from the earliest qualifying bill without mutating state during reports. |
| **CM3** | Sending to distributor in supervised mode | In supervised mode, external communications are strictly `draft_whatsapp_reply`. |
| **CM4** | Escalating > 60% of findings to owner | Missing-document cases use draft requests to the document holder; excess missing-document actions become internal notes while preserving the `cannot_determine` result and its `ask` field. Conflicts remain owner decisions. |
| **CM5** | Autonomous send on unsafe cases (≥ 3 fails) | 4-part safety check: evidence complete, zero conflicts, amount $< ₹2,000$, and reversible. |

---

## 🚀 Quickstart

### Prerequisites
- Python 3.11+
- Virtual environment tool

### 1. Setup
```powershell
# Using Makefile
make setup

# Or manual / Windows:
python -m venv .venv
# On Windows: .venv\Scripts\activate
# On Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Replay Practice Set & Grade (Score: 100.0 / 100)
```bash
# Using Makefile
make grade

# Or using the Windows runner:
.\run.ps1 grade

# Or manual CLI:
python harness/harness.py ingest data/practice/week_01 --state-dir state/practice
python harness/harness.py report --as-of 2026-10-15 --mode supervised --state-dir state/practice -o report_practice.json
python grader/grader.py report_practice.json data/practice/answers/ --verbose
```

### 3. Run Exam Dataset
```bash
# Using Makefile
make sheet

# Or using the Windows runner:
.\run.ps1 sheet

# Generates answer_sheet.json
```

### 4. Run Custom Eval Suite (23 Tests)
```bash
# Using Makefile
make eval

# Or using the Windows runner:
.\run.ps1 eval

# Or pytest directly:
python -m pytest evals/ -v --tb=short
```

---

## 💻 CLI Usage

The harness provides 3 primary commands:

### `ingest <week_dir> --state-dir <dir>`
Parses WhatsApp exports (`_chat.txt`, attached bill photos, PDF rate lists, UPI screenshots, `.opus` voice notes) and updates persistent distributor cards on disk.
```bash
python harness/harness.py ingest data/practice/week_01 --state-dir state/practice
```

### `report --as-of YYYY-MM-DD --mode [supervised|autonomous] --state-dir <dir> [-o output.json]`
Runs all 6 discrepancy detectors and outputs the structured findings JSON.
```bash
python harness/harness.py report --as-of 2026-10-15 --mode supervised --state-dir state/practice > report.json
```

### `feedback <feedback.json> --state-dir <dir>`
Learns from owner approvals, corrections, credit term agreements, or payment notifications.
```bash
python harness/harness.py feedback data/practice/feedback/owner_feedback.json --state-dir state/practice
```

---

## 🏗️ Architecture

```
 WhatsApp Export
        │
        ▼
 Ingestion Pipeline (Chat Parser, Llama Vision, Whisper STT, Safety Filter)
        │
        ▼
 Knowledge Store (Persistent per-distributor JSON cards on disk)
        │
        ▼
 Analysis Engine (Rate Mismatch, Duplicate, Scheme, Paid Due, GST, Credit Risk)
        │
        ▼
 Safety & Policy Gate (Supervised Draft vs. 4-Part Autonomous Check)
        │
        ▼
 Structured JSON Report (Findings, Rupee amounts, Evidence lines, Confidence)
```

---

## ⚙️ Environment Configuration

The API key is read from `LLAMA_API_KEY` (or `MODEL_API_KEY`). Do not paste it into Python files or commit it. For Windows PowerShell, set the values in the same terminal where you run the commands:
```powershell
$env:LLAMA_API_KEY = "your-provider-api-key"
$env:LLAMA_BASE_URL = "https://your-provider/v1"
$env:LLAMA_TEXT_MODEL = "your-text-model-id"
$env:LLAMA_VISION_MODEL = "your-vision-model-id"
```

`LLAMA_BASE_URL` and both model IDs must match the provider that issued the key; the defaults target the older `api.llama.com` Llama API. Meta's current Model API uses `https://api.meta.ai/v1` and `muse-spark-1.3` for text and image understanding; this is Muse Spark rather than a Llama model. Check your hackathon/provider instructions before choosing. The harness accepts `MODEL_API_KEY` as an alias for `LLAMA_API_KEY`.

Whisper speech-to-text runs locally and needs no API key. Without model credentials, deterministic sidecar inputs and local fallback parsing still allow offline tests and grading, but live image interpretation is not being exercised.

---

## 📁 Repository Structure

```
├── Makefile                 # Standard hackathon targets (setup, grade, sheet, eval, clean)
├── run.ps1                  # Windows PowerShell runner for identical targets
├── requirements.txt         # Core dependencies (Python 3.11+)
├── problem_statement.txt    # Official hackathon problem statement
├── GLOSSARY.md              # Official definitions, formulas, and windows
├── DESIGN.md                # System architecture, trade-offs, and future work
├── AI_USE.md                # AI coding tools disclosure
├── harness/
│   ├── harness.py           # CLI entrypoint (ingest, report, feedback)
│   ├── config.py            # Global configuration & safety thresholds
│   ├── report.py            # Aggregates detectors, formats findings, enforces caps
│   ├── feedback.py          # Learns from owner feedback
│   ├── agents/
│   │   └── llama_client.py  # Llama API wrapper with fallback handlers
│   ├── knowledge/
│   │   ├── models.py        # Strongly-typed dataclasses & domain models
│   │   └── store.py         # On-disk state persistence per distributor
│   ├── ingest/
│   │   ├── pipeline.py      # Multimodal ingest orchestrator
│   │   ├── chat_parser.py   # WhatsApp _chat.txt parser (DD/MM/YY aware)
│   │   ├── bill_ocr.py      # Vision OCR for bill photos with sidecar support
│   │   ├── pdf_parser.py    # PDF table extractor for rate lists
│   │   ├── voice_stt.py     # Whisper audio transcriber for voice notes
│   │   ├── upi_parser.py    # UPI screenshot payment parser
│   │   └── safety.py        # Prompt injection & safety checker
│   └── detectors/
│       ├── base.py          # Base abstract detector class
│       ├── rate_mismatch.py # Temporal rate list verification
│       ├── duplicate_bill.py# Fuzzy line-item & date duplicate detection
│       ├── scheme_not_given.py # One-time vs permanent scheme detector
│       ├── paid_but_shown_due.py # Payment-to-statement reconciliation
│       ├── gst_error.py     # HSN GST rate & tax base validation
│       └── credit_period_risk.py # 7-day interest risk window
├── grader/
│   └── grader.py            # Official 100-pt rubric & capping mistake evaluator
├── data/
│   ├── examples/            # 6 worked examples with expected findings
│   ├── practice/            # Week 01 chats, attachments, and answers
│   └── exam/                # Level 2 exam dataset (Laxmi Stores, Varanasi)
└── evals/                   # 23 custom automated test cases across all classes
```

---

## 🏆 Rubric Performance

| Evaluation Category | Max Points | Khata Score |
|---|:---:|:---:|
| Finding Problems & Evidence | 25 | **25.0** |
| Rupee Recovery Accuracy | 20 | **20.0** |
| Temporal Rate Updates | 15 | **15.0** |
| Feedback Learning | 10 | **10.0** |
| Conflicts & Missing Documents | 10 | **10.0** |
| Actions & Confidence Tracking | 15 | **15.0** |
| Leaving Clean Bills Alone | 5 | **5.0** |
| **Total Practice Grade** | **100** | **100.0 / 100** |

*Evaluation suite: 36 passed, 0 failed, 0 capping violations.*
