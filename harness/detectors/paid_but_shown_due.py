"""
Paid but shown due: a UPI payment already made is still listed as
outstanding on the distributor's next statement. amount = the payment amount.

* Only the latest statement (as of the report date) counts: if a newer
  statement no longer lists the bill, the distributor fixed it.
* A payment matches a statement line by bill reference (in the chat message
  sent with the screenshot, or the UPI note) or by a unique amount match with
  a payment date on/after the bill date (GLOSSARY 3.4).
* The payment must be dated before the statement. A statement that already
  shows the reduced balance is fine.
* The owner says "paid" but there is no screenshot -> cannot_determine,
  ask the owner for the UPI screenshot / reference.
"""

from __future__ import annotations

from harness.analysis import View, chat_cite, first_name
from harness.detectors.base import combine, make, owner_adjust
from harness.knowledge.models import Finding, Status
from harness.textutil import fmt_inr, fmt_num, normalize_bill_id

CLS = "paid_but_shown_due"


def detect(view: View) -> list[Finding]:
    out: list[Finding] = []
    for card in view.cards():
        statements = view.statements(card)
        if not statements:
            continue
        st = statements[-1]
        bills = {b.bill_id: b for b in view.bills(card)}
        for e in st.entries:
            if e.status not in ("due", "partial"):
                continue
            pays = [(p, how) for p, how in view.payment_for.get((card.code, e.bill_id), [])
                    if p.payment_date <= st.statement_date]
            if pays:
                paid = sum(p.amount for p, _ in pays)
                original = bills[e.bill_id].total_value if e.bill_id in bills else None
                shown = e.amount if e.amount else (e.balance or 0.0)
                if original and shown <= original - paid + 1.0:
                    continue  # statement already reflects the payment
                amount = min(paid, shown) if shown else paid
                factors = []
                for p, how in pays:
                    factors.append(1.0 if how == "reference" else 0.9)
                    factors.append(0.85 + 0.15 * min(1.0, p.confidence or 0.85))
                    if p.payment_date == st.statement_date:
                        factors.append(0.85)
                    if not p.upi_ref:
                        factors.append(0.95)
                factors.append(0.85 + 0.15 * min(1.0, st.confidence or 0.85))
                evidence = []
                for p, how in pays:
                    evidence.append(f"{p.source.file}: UPI payment {fmt_inr(p.amount)} on {p.payment_date}"
                                    + (f", ref {p.upi_ref}" if p.upi_ref else "")
                                    + (f", to {p.payee}" if p.payee else ""))
                    evidence.append(chat_cite(p.source))
                evidence.append(f"{st.source.file}: row {e.row}: {e.bill_id} {fmt_inr(shown)} still shown as due "
                                f"on statement dated {st.statement_date}")
                evidence.append(chat_cite(st.source))
                p0 = pays[0][0]
                f = make(CLS, e.bill_id, Status.CONFIRMED, distributor=card.code, amount=amount, evidence=evidence,
                         reasoning=(f"{fmt_inr(paid)} was paid by UPI on {p0.payment_date}"
                                    + (f" (ref {p0.upi_ref})" if p0.upi_ref else "")
                                    + f" for {e.bill_id}, matched by {'bill reference' if pays[0][1] == 'reference' else 'amount and date'}. "
                                    f"The statement dated {st.statement_date} still lists {e.bill_id} as due {fmt_inr(shown)}. "
                                    f"Claim: the payment amount, {fmt_inr(amount)}."),
                         confidence=combine(*factors))
                f.extra["_draft"] = {"kind": CLS, "card": card.code, "bill": e.bill_id, "amount": amount,
                                     "date": str(p0.payment_date), "ref": p0.upi_ref, "stmt": str(st.statement_date),
                                     "person": first_name(card)}
                f = owner_adjust(view, f)
                if f:
                    out.append(f)
                continue
            paid_fb, how = view.paid_amount(card, e.bill_id)
            if paid_fb and any(h.startswith("owner feedback") for h in how):
                continue
            claims = [c for c in card.payment_claims if view.known(c)
                      and c.source.received_at and c.source.received_at.date() <= st.statement_date
                      and (e.bill_id in [normalize_bill_id(b) for b in c.bill_refs]
                           or (c.amount and abs(c.amount - e.amount) <= 1.0 and not c.bill_refs))]
            if claims:
                c = claims[-1]
                f = make(CLS, e.bill_id, Status.CANNOT_DETERMINE, distributor=card.code,
                         evidence=[chat_cite(c.source), f"{st.source.file}: row {e.row}: {e.bill_id} "
                                                        f"{fmt_inr(e.amount)} shown as due on {st.statement_date}"],
                         reasoning=(f"The chat says {e.bill_id} was paid ({c.source.cite()}), but no UPI screenshot or "
                                    f"reference for it has been shared, and the statement dated {st.statement_date} lists "
                                    f"it as due {fmt_inr(e.amount)}."),
                         missing=f"UPI screenshot or reference for the payment of {e.bill_id}",
                         ask=f"owner ({view.owner_name}): share the UPI screenshot / reference for {e.bill_id}",
                         ask_party="owner", confidence=0.75, evidence_complete=False,
                         extra={"amount_at_stake_inr": round(c.amount or e.amount, 2)})
                out.append(f)
    return out
