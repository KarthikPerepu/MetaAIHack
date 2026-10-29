"""
GST error: wrong GST rate for the item's HSN, or tax worked out on the wrong
base. amount = tax charged - tax that should have been charged.

* Correct rate: statutory table (harness/data/gst_rates.json, from the
  glossary) by HSN prefix; for HSNs not in the table, the GST column of the
  distributor's signed rate list; an owner answer from feedback wins.
* HSN: rate list (signed) > owner feedback > bill.
* Base: line value minus line discount minus its share of any bill discount;
  free goods carry no tax.
* A bill that charges no tax cannot have excess tax and is left alone.
* Tax charged on an item whose correct rate cannot be established ->
  cannot_determine, ask the distributor for HSN codes.
"""

from __future__ import annotations

import re
from typing import Optional

from harness.analysis import View, chat_cite, first_name
from harness.detectors.base import bill_read_factor, combine, make, owner_adjust
from harness.knowledge.models import Bill, BillLine, Finding, Status
from harness.textutil import fmt_inr, fmt_num

CLS = "gst_error"


def statutory_rate(table: dict, hsn: str, line: BillLine) -> tuple[Optional[float], str]:
    hsn = re.sub(r"\D", "", hsn or "")
    if not hsn:
        return None, ""
    for n in range(len(hsn), 1, -1):
        pre = hsn[:n]
        if pre in table.get("per_kg_threshold", {}):
            rule = table["per_kg_threshold"][pre]
            per_kg = _price_per_kg(line)
            if per_kg is None:
                return None, f"price per kg for HSN {pre} (needed to pick the slab)"
            r = rule["at_or_below"] if per_kg <= rule["threshold_inr_per_kg"] else rule["above"]
            return r, f"HSN {pre} at ₹{per_kg:.0f}/kg"
        if pre in table.get("rates", {}):
            return float(table["rates"][pre]), f"HSN {pre}"
    return None, ""


def _price_per_kg(line: BillLine) -> Optional[float]:
    unit = (line.unit or "").lower()
    if unit in ("kg", "kilo", "kgs"):
        return line.rate
    m = re.search(r"(\d+(?:\.\d+)?)\s*(g|gm|gms|kg)\b", line.product.lower())
    if m and unit in ("", "piece", "pc", "pcs", "packet", "pkt", "pouch"):
        grams = float(m.group(1)) * (1000 if m.group(2) == "kg" else 1)
        if grams > 0:
            return line.rate / (grams / 1000.0)
    return None


def detect(view: View) -> list[Finding]:
    out: list[Finding] = []
    table = view.config.gst
    tol = view.policy.tax_tolerance
    for card in view.cards():
        for bill in view.bills(card):
            paid = [l for l in bill.lines if not l.is_free and l.amount > 0]
            line_tax_known = any(l.gst_amount or l.gst_rate for l in paid)
            if not ((bill.tax_total or 0) > tol or line_tax_known):
                continue
            gross = sum(l.amount for l in paid) or 1.0
            rows, missing = [], []
            for l in paid:
                base = l.taxable_value if l.taxable_value else \
                    l.amount - l.discount - (bill.discount_total or 0) * l.amount / gross
                res = view.resolve_rate(card, l.product, bill.bill_date, bill.bill_id)
                fb = view.owner_hsn(card, l.product)
                hsn = (res.entry.hsn if res.entry and res.entry.hsn else None) or (fb or {}).get("hsn") or l.hsn
                correct, basis = statutory_rate(table, hsn, l)
                if correct is None and fb and fb.get("gst_rate") is not None:
                    correct, basis = float(fb["gst_rate"]), "owner feedback"
                if correct is None and res.entry and res.entry.gst_rate is not None:
                    correct, basis = res.entry.gst_rate, f"{res.rate_list.source.file} GST column"
                rows.append((l, base, hsn, correct, basis))
                if correct is None:
                    missing.append(f"HSN code / GST rate for {l.product}" if not hsn else
                                   (basis or f"GST rate for HSN {hsn} ({l.product})"))
            errors, evidence, reasons, factors = [], [], [], [bill_read_factor(bill)]
            if line_tax_known:
                for l, base, hsn, correct, basis in rows:
                    if correct is None or not (l.gst_amount or l.gst_rate):
                        continue
                    charged = l.gst_amount if l.gst_amount is not None else base * (l.gst_rate or 0)
                    expected = base * correct
                    excess = charged - expected
                    if excess <= tol:
                        continue
                    kind = "wrong_rate" if (l.gst_rate is not None and abs(l.gst_rate - correct) > 0.001) else "wrong_base"
                    errors.append((l, excess, kind))
                    printed = f"{fmt_num(round((l.gst_rate or 0) * 100, 2))}%" if l.gst_rate is not None else "tax"
                    evidence.append(f"{bill.source.file}: row {l.row}: {l.product} (HSN {hsn}) {printed} GST "
                                    f"₹{fmt_num(round(charged, 2))} on ₹{fmt_num(round(base, 2))}")
                    evidence.append(f"GST for {basis or 'HSN ' + str(hsn)}: {fmt_num(round(correct * 100, 2))}% "
                                    f"(harness/data/gst_rates.json, GLOSSARY §6)" if "column" not in basis else
                                    f"{basis}: {fmt_num(round(correct * 100, 2))}%")
                    if kind == "wrong_rate":
                        reasons.append(f"{l.product} (HSN {hsn}) is taxed at {printed} but the rate for {basis} is "
                                       f"{fmt_num(round(correct * 100, 2))}%: ₹{fmt_num(round(charged, 2))} charged vs "
                                       f"₹{fmt_num(round(expected, 2))} due on ₹{fmt_num(round(base, 2))}.")
                    else:
                        reasons.append(f"{l.product}: {printed} GST worked out on the wrong base: "
                                       f"₹{fmt_num(round(charged, 2))} charged, but {fmt_num(round(correct * 100, 2))}% of the "
                                       f"taxable ₹{fmt_num(round(base, 2))} (after discount) is ₹{fmt_num(round(expected, 2))}.")
            elif all(c is not None for _, _, _, c, _ in rows):
                expected = sum(base * c for _, base, _, c, _ in rows)
                charged = bill.tax_total or 0.0
                excess = charged - expected
                if excess > tol:
                    errors.append((None, excess, "wrong_total"))
                    evidence.append(f"{bill.source.file}: total GST charged ₹{fmt_num(round(charged, 2))}"
                                    + (f" (@ {fmt_num(round(bill.tax_rate * 100, 2))}%)" if bill.tax_rate else ""))
                    for l, base, hsn, c, basis in rows:
                        evidence.append(f"{bill.source.file}: row {l.row}: {l.product} (HSN {hsn}) taxable "
                                        f"₹{fmt_num(round(base, 2))} → {fmt_num(round(c * 100, 2))}% = ₹{fmt_num(round(base * c, 2))}")
                    reasons.append(f"Tax charged ₹{fmt_num(round(charged, 2))}; by HSN the tax due is ₹{fmt_num(round(expected, 2))}.")
                    factors.append(0.92)
            if errors:
                amount = sum(x for _, x, _ in errors)
                evidence.append(chat_cite(bill.source))
                f = make(CLS, bill.bill_id, Status.CONFIRMED, distributor=card.code, amount=amount, evidence=evidence,
                         reasoning=" ".join(reasons) + f" Excess tax: {fmt_inr(amount)}.",
                         confidence=combine(*factors), extra={"gst_issue": sorted({k for *_, k in errors})})
                f.extra["_draft"] = {"kind": CLS, "card": card.code, "bill": bill.bill_id, "amount": amount,
                                     "detail": reasons[0] if reasons else "", "person": first_name(card)}
                f = owner_adjust(view, f)
                if f:
                    out.append(f)
            elif missing:
                what = "; ".join(dict.fromkeys(missing))
                f = make(CLS, bill.bill_id, Status.CANNOT_DETERMINE, distributor=card.code,
                         evidence=[f"{bill.source.file}: tax charged ₹{fmt_num(bill.tax_total or 0)}",
                                   chat_cite(bill.source)],
                         reasoning=f"{bill.bill_id} charges GST but the correct rate cannot be established: {what}.",
                         missing=what, ask=f"distributor ({card.name}, {first_name(card)}): send HSN codes / a rate list "
                                           f"with HSN and GST for the items on {bill.bill_id}",
                         ask_party="distributor", confidence=0.8, evidence_complete=False)
                f.extra["_draft"] = {"kind": "request", "card": card.code, "bill": bill.bill_id,
                                     "what": "HSN codes and GST rates", "person": first_name(card)}
                out.append(f)
    return out
