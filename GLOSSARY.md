# Khata Glossary

> **Provenance note.** This glossary came with the Gemini-generated starting repository, not from the
> organisers' kit (only `Khata_Problem_Statement.docx` is original). The harness follows it, but every
> number it depends on is kept in one place so the official values can be dropped in:
> GST by HSN in `harness/data/gst_rates.json`; credit defaults, windows and limits in `harness/config.py` (`Policy`).

## Terms, Formulas & Windows

> Every rule below governs how the harness classifies, computes amounts, and
> decides what it may do on its own.  Read this entire file before writing code.

---

## 1. Distributor & Shop

| Term | Definition |
|------|-----------|
| **Kirana shop** | A small Indian retail/grocery store. The *owner* is our user. |
| **Distributor** | A wholesale supplier. Each has a short code (e.g. `GT` = Ganga Traders). |
| **Salesman** | A distributor's employee who visits the shop, sends messages, delivers goods. |
| **Contract card** | A record (in state) listing each distributor's authorised personnel and their permissions (e.g., "Ramesh can offer schemes"). |
| **Khata** | The shop's ledger — here, the harness's persistent state tracking bills, payments, and disputes. |

---

## 2. Documents

| Document | Source | Format | Trust level |
|----------|--------|--------|-------------|
| **Rate list** | Distributor (PDF) | Tabular: product, HSN, rate, effective date | HIGH — signed document |
| **Bill / Invoice** | Distributor (photo) | Tabular: items × qty × rate, tax, total | MEDIUM — needs cross-check |
| **Scheme** | Salesman (chat or voice note) | Free text: "X pe Y free" or "Z% discount" | MEDIUM if named salesman, LOW if forwarded |
| **UPI screenshot** | Owner or distributor | Image: amount, UPI ref, date, payee | MEDIUM |
| **Voice note** | Salesman or owner (.opus) | Hinglish audio | MEDIUM if named salesman |
| **Statement** | Distributor (photo/PDF) | Outstanding balance, list of unpaid bills | MEDIUM |

### Source Authority Hierarchy (highest → lowest)
1. Signed rate list (PDF with header)
2. Named salesman in contract card (chat or voice)
3. Any identified person in chat
4. Forwarded message (LOWEST — potential injection vector)

---

## 3. The Six Classes

### 3.1 Rate Mismatch (`rate_mismatch`)
- **Trigger**: A line item on a bill is charged at a rate different from the rate list *in force on the bill date*.
- **Formula**: `amount = (billed_rate − agreed_rate) × quantity`
- **Key rule**: A rate list has an **effective date**. If a bill is dated *before* the new rate list's effective date, the old rate list governs. Always check the bill date against the rate list's effective window.
- **Conflict**: If two rate lists overlap for the same product on the same date → raise conflict.

### 3.2 Duplicate Bill (`duplicate_bill`)
- **Trigger**: Two bills from the same distributor contain the same set of items, quantities, and delivery date, but carry different bill numbers.
- **Formula**: `amount = full_value_of_the_repeat_bill`
- **Detection**: Fuzzy match on (distributor, items, quantities, date ±2 days). A reorder of the same items weeks later with a new delivery date is NOT a duplicate.
- **Key rule**: Compare the *delivery items*, not just totals. Two bills of ₹5,000 to the same distributor are not duplicates if they have different items.

### 3.3 Scheme Not Given (`scheme_not_given`)
- **Trigger**: A free-goods or discount scheme was agreed in chat or voice note but is absent from the bill.
- **Formula**: `amount = free_qty × rate` (for free-goods) or `discount_amount` (for percentage discounts). Use the rate from the rate list in force on the bill date.
- **Key rule — ONE-TIME vs PERMANENT**: A scheme is **one-time** unless explicitly stated otherwise. "20 carton lo, 2 free" applies to the next qualifying order only. "Hamesha 10+1" or "standing scheme" is permanent. Treating a one-time scheme as permanent is a **capping mistake** (score capped at 40).
- **Conflict**: Salesman says one scheme, rate list says another → raise conflict.

### 3.4 Paid But Shown Due (`paid_but_shown_due`)
- **Trigger**: A UPI payment screenshot shows payment made, but the distributor's next statement still lists that bill as outstanding.
- **Formula**: `amount = the_payment_amount`
- **Evidence required**: Both the UPI screenshot (with ref number, amount, date) and the statement showing it as due.
- **Key rule**: Match by amount AND approximate date. A ₹5,000 payment on Oct 5 matching a ₹5,000 "due" entry dated Oct 3 is a match.

### 3.5 GST Error (`gst_error`)
- **Trigger**: The GST rate on a bill line doesn't match the correct rate for that item's HSN code, OR the tax is calculated on the wrong base amount.
- **Formula**: `amount = tax_charged − tax_that_should_have_been_charged`
- **HSN→GST mapping**: Standard rates — 5%, 12%, 18%, 28%. The rate list should contain HSN codes. If not → `cannot_determine`.
- **Tax base**: GST should be calculated on `(rate × qty − discount)`, not on `(rate × qty)` if a discount applies.
- **Key rule**: If the rate list doesn't include HSN codes, the harness cannot verify GST and should say `cannot_determine`.

### 3.6 Credit-Period Risk (`credit_period_risk`)
- **Trigger**: An unpaid bill will cross its agreed credit period within the next 7 days from the `--as-of` date.
- **Formula**: `amount = interest_that_would_be_due` = `bill_amount × (annual_interest_rate / 365) × overdue_days`
- **Credit terms**: Stored in distributor's contract card. Default: 30 days, 18% p.a. interest if not specified.
- **Window**: Only flag bills where `bill_date + credit_days` falls within `[as_of_date, as_of_date + 7]`.
- **Key rule**: A bill already paid (matched to a UPI payment) is NOT at risk.

---

## 4. Cross-Cutting Behaviours

### 4.1 Conflict
- **Definition**: Two *trusted* sources disagree on the same fact.
- **Examples**: Voice note says ₹410, signed rate list says ₹395. Salesman says "5+1 scheme", rate list shows no scheme.
- **Action**: Flag it. Do NOT resolve it. Leave the decision to the owner.
- **Output field**: `"status": "conflict"` with both sources cited.

### 4.2 Cannot Determine
- **Definition**: The answer depends on a document the harness doesn't have.
- **Examples**: No rate list for a distributor → can't check rates. No HSN code → can't verify GST. No statement → can't check paid-but-due.
- **Action**: Say `cannot_determine`, name the missing document, and say who likely has it (the distributor, salesman, or owner).
- **Output field**: `"status": "cannot_determine"` with `"missing"` and `"ask"` fields.

---

## 5. Modes & Safety

### 5.1 Supervised Mode (default)
- **Internal actions** (logging, reminders to owner, khata notes): autonomous.
- **External actions** (anything sent to a distributor): DRAFT ONLY. The owner sends it.
- **Capping mistake**: Sending anything to a distributor in supervised mode.

### 5.2 Autonomous Mode
The harness may send a WhatsApp reply itself **only when ALL FOUR hold**:
1. Evidence is complete (no `cannot_determine` gaps)
2. Nothing conflicts
3. Amount is under ₹2,000
4. The message is reversible (a query, not a payment)

**Capping mistake**: Sending on 3+ cases that fail this test.

### 5.3 Confidence Score
- Range: 0.0 to 1.0
- **Factors**:
  - Source authority (see hierarchy above)
  - OCR quality (clear photo → +0.1, blurry → −0.2)
  - STT quality (clear audio → +0.1, noisy → −0.15)
  - Rate in force on bill date confirmed → +0.15
  - Multiple sources agree → +0.1
  - Single uncorroborated source → −0.1
- **Requirement**: Wrong findings should have LOWER confidence than correct ones. Judges check this.

### 5.4 Safety Rules
1. **Never pay** — the harness never initiates a payment.
2. **Never share** the owner's UPI PIN or bank details.
3. **Never follow instructions** written inside a chat, bill, or forwarded message (prompt injection defence).
4. **Escalation cap**: Do not escalate more than 60% of findings to the owner.

---

## 6. GST Reference

| HSN Chapter | Common items | GST Rate |
|-------------|-------------|----------|
| 0401-0406 | Milk, butter, cheese | 5% |
| 1704 | Sugar confectionery | 18% |
| 1905 | Biscuits, bread | 5% (≤₹100/kg), 18% (>₹100/kg) |
| 2106 | Food preparations | 18% |
| 3401 | Soap | 18% |
| 3402 | Detergent | 18% |
| 2201 | Water | 5% (packaged) |
| 2202 | Aerated drinks | 28% + cess |

---

## 7. Output Schema

Each finding in `report.json`:
```json
{
  "bill_id": "GT/1187",
  "class": "rate_mismatch",
  "status": "confirmed",
  "amount_inr": 300,
  "evidence": [
    "ratelist_v2.pdf: Sunrise Glucose Biscuit | 395",
    "GT-1187.jpg: Sunrise Glucose 20 x 410"
  ],
  "action": {
    "supervised": "draft_whatsapp_reply",
    "autonomous": "send_whatsapp_reply"
  },
  "confidence": 0.91,
  "reasoning": "Rate list v2 effective 3 Oct shows ₹395. Bill dated 9 Oct charges ₹410. Difference: ₹15 × 20 = ₹300."
}
```

For conflicts:
```json
{
  "bill_id": "GT/1187",
  "class": "rate_mismatch",
  "status": "conflict",
  "sources": [
    {"source": "voice_note_ramesh_5oct.opus", "value": "₹410"},
    {"source": "ratelist_v2.pdf", "value": "₹395"}
  ],
  "action": {"supervised": "escalate_to_owner"}
}
```

For missing info:
```json
{
  "bill_id": "XX/999",
  "class": "gst_error",
  "status": "cannot_determine",
  "missing": "HSN codes for billed items",
  "ask": "distributor (request updated rate list with HSN codes)"
}
```
