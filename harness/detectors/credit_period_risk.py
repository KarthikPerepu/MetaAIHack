"""
Credit-period risk: an unpaid bill whose credit period ends in the next 7 days.

* due date = bill date + credit days; flagged when due is in [as_of, as_of + 7].
* Credit terms: owner feedback > contract card > chat ("45 din ka credit")
  > glossary default (30 days, 18% p.a.).
* Paid (UPI reference / unique amount match / statement / owner) bills are
  not at risk; part-paid bills are at risk for the balance.
* Bills known only from a statement line also count.
* amount = outstanding x (annual rate / 365) x overdue days, where overdue
  days run from the due date to the end of the 7-day look-ahead (at least 1):
  the interest the owner avoids by paying inside the window.
* Action: an internal reminder to the owner, never a message to the distributor.
"""

from __future__ import annotations

from datetime import timedelta

from harness.analysis import View, chat_cite
from harness.detectors.base import bill_read_factor, combine, make, owner_adjust
from harness.knowledge.models import Finding, Status
from harness.textutil import fmt_inr, fmt_num

CLS = "credit_period_risk"


def detect(view: View) -> list[Finding]:
    out: list[Finding] = []
    window = view.policy.credit_window_days
    start, end = view.as_of, view.as_of + timedelta(days=window)
    for card in view.cards():
        days, rate, kind, terms = view.credit_terms(card)
        items = []
        seen = set()
        for b in view.bills(card):
            items.append((b.bill_id, b.bill_date, b.total_value, b, None))
            seen.add(b.bill_id)
        sts = view.statements(card)
        if sts:
            for e in sts[-1].entries:
                if e.bill_id not in seen and e.status in ("due", "partial") and e.bill_date:
                    items.append((e.bill_id, e.bill_date, e.amount, None, (sts[-1], e)))
                    seen.add(e.bill_id)
        for bill_id, bdate, total, bill, st_row in items:
            due = bdate + timedelta(days=days)
            if not (start <= due <= end):
                continue
            paid, how = view.paid_amount(card, bill_id, by=view.as_of)
            outstanding = max(0.0, (total or 0.0) - paid)
            if outstanding <= 1.0:
                continue
            od_days = max(1, (end - due).days)
            interest = outstanding * rate / 365.0 * od_days
            if terms is None:
                term_cite = f"contract card: {card.name} default terms {days} days, {fmt_num(rate * 100)}% p.a. (GLOSSARY 3.6)"
            elif terms.source and terms.source.line:
                term_cite = chat_cite(terms.source)
            else:
                term_cite = f"{terms.source_kind}: {card.name} {days} days credit, {fmt_num(rate * 100)}% p.a."
            factors = [{"default": 0.88, "chat": 0.95, "contract": 0.98, "owner_feedback": 1.0}.get(kind, 0.9)]
            if bill is not None:
                factors.append(bill_read_factor(bill))
                src_cite = f"{bill.source.file}: bill {bill_id} dated {bdate}, {fmt_inr(total)}"
                extra_cite = chat_cite(bill.source)
            else:
                st, e = st_row
                factors.append(0.9)
                src_cite = f"{st.source.file}: row {e.row}: {bill_id} dated {bdate}, {fmt_inr(total)} due"
                extra_cite = chat_cite(st.source)
            if paid:
                factors.append(0.93)
            reasoning = (f"{bill_id} ({fmt_inr(total)}, {bdate}) has {days} days credit, so it is due on {due}, "
                         f"{(due - view.as_of).days} day(s) after {view.as_of}. "
                         + (f"{fmt_inr(paid)} already paid; " if paid else "No payment found; ")
                         + f"if {fmt_inr(outstanding)} is not paid by then, interest at {fmt_num(rate * 100)}% p.a. "
                           f"for {od_days} day(s) to {end} is {fmt_inr(interest)}.")
            f = make(CLS, bill_id, Status.CONFIRMED, distributor=card.code, amount=interest,
                     evidence=[src_cite, term_cite, f"due date {due} falls in [{start}, {end}]", extra_cite] + how,
                     reasoning=reasoning, confidence=combine(*factors), reversible=True,
                     extra={"due_date": str(due), "outstanding_inr": round(outstanding, 2),
                            "credit_days": days, "interest_rate_pa": rate, "terms_source": kind})
            f.extra["_draft"] = {"kind": CLS, "bill": bill_id, "due": str(due), "amount": interest,
                                 "outstanding": outstanding, "card": card.code}
            f = owner_adjust(view, f)
            if f:
                out.append(f)
    return out
