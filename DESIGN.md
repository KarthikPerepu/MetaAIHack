# Khata — design

Khata reads a kirana shop's exported WhatsApp chats (text, bill photos, PDF rate lists,
UPI screenshots, statements, voice notes), remembers what each distributor agreed from one
week to the next, and reports the money leaking out: six classes of problem, each with the
rupee amount, the chat or bill lines that prove it, a confidence, and the action that is
safe in the current mode.

```
 week_dir/ (_chat.txt + media)                                   state_dir/
        │                                                         ├─ distributors/<CODE>.json  contract card + records
        ▼                                                         ├─ shop.json      owner, sender → distributor
 ┌──────────── ingest (harness.py ingest) ─────────────┐          ├─ feedback.json  owner decisions
 │ chat parser ─► safety screen ─► identity (owner,     │  writes  ├─ alerts.json    quarantined content
 │ distributors, salesmen) ─► document reader ─►        │ ───────► └─ cache/         model + extraction cache
 │ message agent ─► dated records with provenance       │
 └──────────────────────────────────────────────────────┘
        ┌──────────── report --as-of D --mode M ──────────────────────────────┐
 reads  │ as-of view (only what had arrived by D) ─► 6 detectors ─► owner      │
 ─────► │ feedback ─► escalation cap ─► actions + drafts ─► report.json        │
        └─────────────────────────────────────────────────────────────────────┘
        feedback <file>  ─► normalised owner decisions applied by every later report
```

Three commands, each in a fresh process (`ingest`, `report`, `feedback`, plus `sheet`
for the per-class answer sheet). Nothing is held in memory between them.

## 1. What we found in the starting code, and what changed

The repository arrived with a Gemini-generated harness, data and grader. The audit found:

| Finding | Consequence | What we did |
|---|---|---|
| Every photo, PDF and voice note in `data/` was a 16-byte placeholder (`dummy_image_data`); all results came from hand-written `.json` "sidecars". | The harness had never read a real bill. Without sidecars it found **0** findings. | Built real readers (Muse Spark vision with rotation retry, pdfplumber tables, tesseract OCR fallback, Muse Voice Transcribe with local Whisper fallback). Rendered real media for the data folders. Sidecars are now an optional extraction cache. |
| Distributors were hard-coded (`"GANGA" or "RAMESH" → GT`, `"KASHI" → KP` …). | A third shop (Level 3) would not be recognised at all. | Owner and distributors are identified from display names, bill numbers on the documents each sender sends, payee names and chat-file names. |
| The bundled grader awarded most points unconditionally (feedback 10/10 for any report, "temporal" 15/15 at 80% recall). | The "100/100" badge measured almost nothing. | Replaced by a strict local grader (§9) and our own two-week eval shop with an answer key. |
| GST raised `cannot_determine` on bills that charged no tax; bills with injected text were dropped unchecked; the injection filter blocked phrases such as "you are a" and "account number". | Noise towards the 60 % escalation cap; an attacker could hide a bill; false quarantines. | Untaxed bills are silent; injected text is reported and ignored while the bill is still audited; the screen was rebuilt and tested on normal trade talk. |
| A rate list without a printed "w.e.f." date was discarded. | Every bill from that distributor became unverifiable. | The list applies from the day it arrived, with lower confidence and a note saying so. |

Kept from the original: the package layout, per-distributor JSON cards under `--state-dir`,
the "rate in force on the bill date" idea, and the six-detector structure.

## 1b. Meta models used

All model calls go through the Meta Model API (`https://api.meta.ai/v1`, OpenAI-compatible):

| Model | Job | Guard rails |
|---|---|---|
| Muse Spark 1.3 (vision) | bill photos, UPI screenshots, statements, scanned PDFs | output checked against the document's arithmetic; rotation retry; instructions on the document reported, never followed |
| Muse Spark 1.3 (text) | Hinglish / Devanagari messages and transcripts, PDF fallback, item-name mapping, forward screening, free-text feedback | every number must appear in the source; "permanent" scheme only with the words in the message |
| Muse Voice Transcribe 1.0 | voice notes, `.opus` decoded to 16 kHz WAV and sent to `/v1/asr/transcribe` with trade keywords | local Whisper fallback |

`reasoning_effort` is `low` (Meta's guidance for simple tasks), which also keeps the hackathon credit
for real work; answers are cached by content, so reruns cost nothing. The endpoint and model ids are
settings, so the same harness runs on open Llama weights through any OpenAI-compatible host (the
problem statement allows "any host, or locally"). Meta's hosted Llama API was reported retired in
July 2026 in favour of the Meta Model API.

## 2. Ingest

**Chat parser** (`ingest/chat_parser.py`). Handles iOS (`[dd/mm/yy, hh:mm:ss] Name: …`, `<attached: …>`)
and Android (`dd/mm/yyyy, h:mm am - Name: …`, `IMG-…jpg (file attached)`) exports, 12/24-hour clocks,
narrow no-break spaces and LRM marks, multi-line messages, captions on the next line, system lines,
forwarded markers. Day/month order is detected from the whole file. Any `*.txt` that looks like a chat
is used, in any sub-folder, so one export per distributor also works.

**Identity** (`ingest/pipeline.py::_identify_people`). The owner is scored: the shop's name is used to
address them ("Namaste Laxmi Stores ji"), they send UPI screenshots, they give orders; distributors send
bills. If nobody scores, the most active sender who is not a company and sends no bills is the owner.
A sender like "Ramesh Ganga Traders" splits into person "Ramesh" and company "Ganga Traders"; the
distributor code is the prefix of the bill numbers that sender sends (`GT/1187` → GT), else the company
initials. Senders are mapped once and remembered in `shop.json`, so week 2 keeps week 1's codes.

**Document reader** (`ingest/documents.py`). Every attachment is classified by content, not filename
(real exports are `IMG-20261009-WA0012.jpg`):

* images → Muse Spark vision on the Meta Model API (one prompt classifies and extracts a bill / UPI / statement / rate list).
  The read is checked against the document's own arithmetic (qty × rate = amount, rows add to the
  subtotal, subtotal − discount + tax = total). A poor read is retried rotated 90 / 270 / 180°, which
  fixes sideways photos; tesseract's orientation detector chooses the first rotation. Without a model
  key, tesseract OCR + layout-free text parsers read the photo (a bill row is any line with three
  numbers where qty × rate = amount).
* PDFs → pdfplumber tables mapped through the header row; text parser; Muse Spark text if that
  fails; scanned PDFs are rendered and sent to vision / OCR.
* voice notes → faster-whisper (open model, no key), prompted with Hinglish trade words so the
  transcript stays in Latin script; openai-whisper as second choice.
* Results are cached by file hash, so reruns are identical and free.

OCR misreads are repaired from context: a bill number read as `G1/1187` becomes `GT/1187` when the chat
line says "Bill GT/1187", and noisy item names are matched to the distributor's known products.

**Message agent** (`agents/message_agent.py`). Turns a message or transcript into facts: schemes,
spoken rates, credit terms, payment claims, deliveries. A deterministic parser covers the common
Hinglish and Devanagari wordings ("20 carton lo, 2 free milega", "10 pe 1", "बीस कार्टन पर दो फ्री",
"do carton free denge agar das carton loge", "hamesha 10+1", "is mahine 5% discount",
"45 din ka credit, 2% mahina byaj"); Hindi number words are only read as numbers before a unit, so
"bhej do" is never "2". When a model key is set, Muse Spark reads the message too, and its
answer is accepted only if **every number it reports appears in the message**.

**Records.** Each fact becomes a record on the distributor's card with its provenance: file, chat line,
bill row, sender, and `received_at` (when it arrived in the chat). Record ids are deterministic, so
re-ingesting a week replaces rather than duplicates.

## 3. The as-of view: why answers change correctly

`report --as-of D` builds a view of records that had arrived by the end of day D. Everything the detectors
need is answered on that view (`analysis.py`):

* **Rate in force for a product on a bill date**: among rate lists whose effective window covers the bill
  date, the most recently *received* wins. So a corrected list sent on Thursday replaces Monday's, and a
  backdated list ("3 Dec se lagu", sent 9 Dec) re-prices bills from 3 Dec onwards, but a bill dated 2 Dec
  stays on the old list. An as-of report from before the new list arrived still gives the old answer.
  Two lists with the same effective date that disagree, neither marked as a correction, are a conflict.
* **Spoken rates**: a named salesman's quote that arrived after the governing list and disagrees with it
  makes the bill a conflict rather than a claim.
* **Payments**: a UPI payment is tied to a bill by the bill number in the chat line sent with the
  screenshot (or the line just before it, or the UPI note), else by a unique amount match on or after the
  bill date. Each payment pays one bill. The same screenshot forwarded twice counts once (UPI ref).
* **Credit terms**: owner feedback > contract card > chat ("hamara credit 15 din ka hai, 18% byaj") >
  the glossary default (30 days, 18 % p.a.).

Because every answer is a function of dated records and the as-of date, file order inside a week
never changes the output (tested by rebuilding a week in the other export format, split across two chat
files, with renamed attachments).

## 4. The six detectors (`harness/detectors/`)

| Class | Rule | Amount |
|---|---|---|
| rate_mismatch | billed (net of line discount) > rate in force on the bill date + ₹0.50 | (billed − agreed) × qty, before GST |
| duplicate_bill | same distributor, same items and quantities (fuzzy names, rates may differ), same delivery: same challan, or delivery dates ≤ 2 days apart. A repeat billed weeks later is caught through its printed delivery date; a later bill with its own later delivery is a re-order. | full value of the repeat bill |
| scheme_not_given | a scheme from the rate list, a named salesman (chat or voice) or the owner; qualifying line on a bill dated on/after the offer; free units shown (free row, free-qty column, or a price that already absorbs them) are subtracted | missing free units × rate in force on the bill date; or the missing discount |
| paid_but_shown_due | the **latest** statement lists a bill as due although a successful payment dated before the statement covers it and the balance was not reduced | the payment amount |
| gst_error | charged tax > correct rate × taxable base (after discounts, free goods untaxed). Correct rate from the HSN table (glossary §6, `harness/data/gst_rates.json`), else the signed rate list's GST column. HSN from the rate list, else the bill. | tax charged − tax due |
| credit_period_risk | unpaid (or part-paid) bill whose due date (bill date + credit days) is in [as-of, as-of + 7] — including bills known only from a statement | outstanding × rate / 365 × days from the due date to the end of the 7-day window (min 1) |

**One-time vs permanent (capping mistake CM2).** A scheme is one-time unless the words say it repeats
("hamesha", "har order", "every order", "standing", "jab bhi"); "is mahine" makes it valid until month
end. A one-time scheme is used up by the *first* qualifying bill after it was offered, whether or not the
goods were given, so the next order is never claimed. "X pe Y free" scales with quantity; "X lo, Y free
milega" gives Y once. The model can never mark a scheme permanent unless the message contains the phrase.
A later offer from the same person replaces their earlier one; disagreeing offers from different trusted
sources (rate list vs salesman, two salesmen) are a conflict.

**Authority** follows the glossary: signed rate list > owner > named salesman > identified person >
unknown > forwarded. Forwarded and unknown senders cannot create schemes, rates or payments.

## 5. Conflicts and cannot_determine

* **conflict** — two trusted sources disagree on a fact the answer depends on (list vs list on the same
  date, list vs salesman's rate, scheme vs scheme). Both sources are cited, the amount at stake is shown,
  and the action is `escalate_to_owner`. The harness never picks a side.
* **cannot_determine** — the answer needs a document we do not have. Each says what is `missing` and
  who to `ask`: the distributor for a rate list or HSN codes (a drafted request), the salesman when a
  scheme did not name its product, the owner when the chat says "paid" but there is no screenshot.
  A bill with no tax charged, or billed below the list, is *not* a question.

## 6. Modes, actions and safety

| Finding | supervised | autonomous |
|---|---|---|
| confirmed claim | `draft_whatsapp_reply` | `send_whatsapp_reply` only if all hold: evidence complete (documents, not a voice note alone), no conflict on the bill, amount < ₹2,000, reversible query, confidence ≥ 0.75. Otherwise drafted and waiting, with the failed checks listed. |
| credit_period_risk | `remind_owner` (internal) | `remind_owner` |
| conflict | `escalate_to_owner` | `escalate_to_owner` |
| cannot_determine | drafted document request, or `ask_owner` | same |

Every external item carries a polite Hinglish `draft_message`. There is **no payment action anywhere in
the code**; drafts are scrubbed of PINs, OTPs, account numbers and UPI ids.

**Prompt injection.** Every message, transcript and document text is screened (rules in English, Hinglish
and Devanagari, plus a Muse Spark second opinion for forwarded / unknown-sender text). Quarantined content
creates no records and is listed in `safety_alerts`; it never appears in findings. Instructions printed on
a bill are reported and ignored, and the bill is still audited. Model prompts wrap all chat / OCR text in
`<<<DATA … DATA>>>` and say it is data, not instructions.

**Escalation cap.** At most 60 % of findings may go to the owner. Conflicts always go. If open questions
would exceed half the findings, we keep one question per bill, then merge requests for the same document
from the same distributor (one rate list unblocks every bill), and only as a last resort move the least
important requests to `pending_requests`. Tiny reports are left alone.

## 7. Confidence

Confidence is meant as the probability that the verdict *and* the amount are right, built from the
reliability of each fact the finding depends on: bill reading quality (arithmetic consistency, OCR vs
vision vs cache), source authority (voice discounted by speech-to-text confidence), product-match score,
printed vs inferred effective date, inferred scheme product, valuation at billed rate (no list), amount-only
payment matches, default vs stated credit terms, corroboration (owner accepted the offer in chat). Owner
confirmation raises it to ≥ 0.97. Effect in our runs: sidecar reads give 0.95–0.96 on rate claims, offline
OCR reads 0.81–0.92, voice-only schemes 0.83–0.87, default-terms credit risk lower still.

## 8. Learning from the owner (`feedback.py`)

Structured items or free text ("Mohan ke saath 150 pe hi deal hua tha", "Sharma Products ka credit 45 din")
become normalised decisions in `feedback.json` that every later report applies:

* verdicts: rejected findings stay suppressed; confirmed ones get ≥ 0.97 confidence (optionally a
  corrected amount);
* an agreed rate becomes an owner-authority rate from that bill date forward, so the *next* bill at that
  rate is not flagged either (learning, not just suppression);
* a conflict choice ("ratelist_v2.pdf", "voice note", or a number) settles that conflict;
* answers to open questions (a rate, an HSN / GST rate) unblock the check;
* paid-in-cash, credit terms, salesman permissions ("Suresh cannot give schemes"), "not a duplicate".

Free text is parsed by rules, then by Muse Spark when the rules find nothing.

## 9. Evaluation

`make eval` runs 89 cases in `evals/` plus a full replay of our own eval shop:

* **test_detectors.py** — each class with its look-alikes: rate before/after a new list, billed below
  list, no list, salesman-vs-list conflict; duplicates within 2 days, weeks later with the original
  delivery date, re-order, same total different items; one-time / permanent / given-on-bill / below
  threshold / before the offer / Devanagari voice / unknown sender / % discount schemes; paid-due,
  statement before payment, later statement clears it, "paid" without screenshot; GST wrong slab, wrong
  base, unknown HSN, untaxed bill; credit window with chat terms and a paid bill; feedback changing terms.
* **test_level3.py** — two weeks in fresh processes with a backdated list; a scheme meant only once
  across weeks; forwarded "pay ₹50,000" + credential request + instruction in a salesman message; text
  on a bill; conflict settled by feedback; rejected finding and agreed rate carried forward; supervised
  never sends; autonomous four-part test; escalation cap with eight unverifiable bills; file order / export
  format / renamed files invariance; idempotent re-ingest; third shop identified without hard-coding.
* **test_media.py** — rendered photos (one sideways, tilted, blurred), a real PDF, a UPI screenshot and a
  statement read end to end with sidecars disabled.
* **test_llm_paths.py** — a scripted fake model checks the guard rails: rotation retry, invented numbers
  rejected, no unearned "permanent", injected document text reported, model-flagged forward quarantined.
* **test_datasets.py** — practice and exam replays, and `evals/scenarios/annapurna/`: a third shop over
  two weeks with feedback in between and 12 answer-key items covering every trap the brief lists.

**Grader.** `grader/grader.py` is a strict stand-in for the judges' grader: true positives need bill +
class + amount within ₹1 / 1 %; evidence must name a real file or chat line; wrong claims cost 2×; clean
bills, conflicts (with both sources) and cannot_determine (with an `ask`) are checked; all five capping
mistakes are enforced; components not covered by an answer key are shown as n/a and the score rescaled.

| Run | Score |
|---|---|
| practice (`make grade`) | 100 / 100 (5 / 5 found, ₹0 wrongly claimed) |
| our eval shop, two weeks + feedback (`make eval-shop`) | 100 / 100 (8 / 8, all components measured) |
| our eval shop with sidecars ignored, offline OCR only (`make demo`) | 7 / 8 found, 0 false positives; the miss is the voice-note scheme (no speech model offline) |
| exam (`make sheet`) | rate ₹1,000 · duplicate ₹12,000 · scheme ₹780 · paid-due ₹8,000 · GST ₹1,300; 3 BW bills cannot_determine (no BW rate list) |

## 10. Limits and what we would do next

* **Live model runs.** The development sandbox could not reach the Meta Model API or download Whisper
  weights, so the vision / text / speech paths are exercised with a scripted fake model and the offline
  readers. First thing with a key: run `KHATA_IGNORE_SIDECARS=1 make eval-shop` and the exam against
  Muse Spark and tune the prompt on real tilted photos.
* **The reference files.** `GLOSSARY.md`, the data and the bundled grader in the starting repo were not
  the organisers' originals (see §1). The GST table lives in `harness/data/gst_rates.json` and the policy
  numbers in `harness/config.py::Policy`, so the official values can be dropped in without code changes.
  The interest formula for credit risk (days from due date to the end of the 7-day window) is our
  reading of "interest that would be due"; it is one line in `detectors/credit_period_risk.py`.
* **Units.** We compare rates when units agree or are unknown and ask when they clearly differ
  (carton vs piece). Next: a per-product unit-conversion table learnt from bills and rate lists.
* **Duplicates without delivery dates.** A repeat billed weeks later with no printed delivery date or
  challan is treated as a re-order (we prefer missing a claim to claiming twice). Next: match delivery
  confirmations in chat to bills.
* **Speech.** Whisper on Hinglish is the weakest link; next is a Hindi-tuned checkpoint and asking the
  Muse Spark text model to repair transcripts against the distributor's product list.
* **Calibration.** Confidence factors are hand-set from the glossary's guidance. With more labelled weeks
  we would fit them (isotonic regression on findings vs owner verdicts).
* **Product.** WhatsApp Business webhook instead of exports; credit-note tracking after a claim; a
  short Hinglish voice summary for the owner.
