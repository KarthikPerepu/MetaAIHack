"""
Rate mismatch: an item billed above the rate in force on the *bill date*.

amount = (billed rate - agreed rate) x billed quantity, before GST.

* The governing rate comes from View.resolve_rate (effective dates, backdated
  and corrected lists, owner-agreed rates).
* Two lists for the same date that disagree, or a named salesman's quote that
  disagrees with the signed list, is a conflict for the owner.
* No rate list covering the bill date -> cannot_determine, ask the distributor.
* Billed below the list is in the shop's favour and is left alone.
"""

from __future__ import annotations

from harness.analysis import View, bill_line_cite, chat_cite, first_name
from harness.detectors.base import bill_read_factor, combine, make, match_factor, owner_adjust
from harness.knowledge.models import Finding, Status
from harness.textutil import UNIT_WORDS, fmt_inr, fmt_num

CLS = "rate_mismatch"


def _unit(u: str) -> str:
    return UNIT_WORDS.get((u or "").lower().rstrip("s"), (u or "").lower().rstrip("s"))


def detect(view: View) -> list[Finding]:
    out: list[Finding] = []
    tol = view.policy.rate_tolerance
    for card in view.cards():
        for bill in view.bills(card):
            claims, conflicts, missing = [], [], []
            for line in bill.lines:
                if line.is_free or line.quantity <= 0 or line.rate <= 0:
                    continue
                billed = line.rate
                if line.discount:
                    billed = max(0.0, (line.amount - line.discount) / line.quantity)
                res = view.resolve_rate(card, line.product, bill.bill_date, bill.bill_id)
                if res.rate is None:
                    quote = view.rate_without_list(card, line.product, bill.bill_date)
                    if quote and billed > quote.rate + tol:
                        claims.append((line, quote.rate, None, res, quote))
                    elif not quote:
                        missing.append((line, res))
                    continue
                if res.entry and res.entry.unit and line.unit and _unit(res.entry.unit) != _unit(line.unit):
                    ratio = max(billed, res.rate) / max(0.01, min(billed, res.rate))
                    if ratio > 2.5:
                        missing.append((line, res))
                        continue
                if res.conflict:
                    srcs = sorted({(f, r) for f, r, _ in res.conflict}, key=lambda t: t[0])
                    if billed > min(r for _, r in srcs) + tol:
                        conflicts.append((line, [{"source": f, "value": f"₹{fmt_num(r)}"} for f, r in srcs], res, None))
                    continue
                against = view.quotes_against(card, line.product, bill.bill_date, res)
                if against:
                    low = min([res.rate] + [q.rate for q in against])
                    if billed > low + tol:
                        srcs = [{"source": res.rate_list.source.file if res.rate_list else "owner",
                                 "value": f"₹{fmt_num(res.rate)}"}]
                        srcs += [{"source": q.source.file if q.source_kind == "voice" else q.source.cite(),
                                  "value": f"₹{fmt_num(q.rate)} (said by {q.source.sender} on {q.said_on})"} for q in against]
                        conflicts.append((line, srcs, res, against))
                    continue
                if billed > res.rate + tol:
                    claims.append((line, res.rate, res.rate_list, res, None))

            if claims:
                amount = 0.0
                evidence, reasons, factors = [], [], [bill_read_factor(bill)]
                complete = True
                for line, agreed, rl, res, quote in claims:
                    billed = line.rate if not line.discount else (line.amount - line.discount) / line.quantity
                    diff = billed - agreed
                    amount += diff * line.quantity
                    if res.owner_rate:
                        evidence.append(f"owner feedback: agreed rate for {line.product} ₹{fmt_num(agreed)}")
                        reasons.append(f"Owner-agreed rate for {line.product} is ₹{fmt_num(agreed)}.")
                    elif rl is not None:
                        evidence.append(f"{rl.source.file}: {res.entry.product} | {fmt_num(agreed)}")
                        evidence.append(chat_cite(rl.source))
                        eff = f"in force from {rl.effective_from}" + (" (no printed date; taken from when it arrived)"
                                                                      if rl.effective_inferred else "")
                        reasons.append(f"{rl.source.file} ({rl.version or 'rate list'}, {eff}) lists "
                                       f"{res.entry.product} at ₹{fmt_num(agreed)}." + (f" {res.note}." if res.note else ""))
                        factors.append(match_factor(res.match_score))
                        factors.append(0.93 if rl.effective_inferred else 1.0)
                    else:
                        evidence.append(chat_cite(quote.source) or f"{quote.source.file}: {quote.raw_text}")
                        reasons.append(f"No rate list covers {bill.bill_date}; {quote.source.sender} quoted "
                                       f"₹{fmt_num(agreed)} for {line.product} on {quote.said_on}.")
                        factors.append(0.8)
                        complete = False
                    evidence.append(bill_line_cite(bill, line))
                    reasons.append(f"Bill {bill.bill_id} dated {bill.bill_date} charges ₹{fmt_num(billed)}: "
                                   f"(₹{fmt_num(billed)} − ₹{fmt_num(agreed)}) × {fmt_num(line.quantity)} = "
                                   f"{fmt_inr(diff * line.quantity)}.")
                evidence.append(chat_cite(bill.source))
                f = make(CLS, bill.bill_id, Status.CONFIRMED, distributor=card.code, amount=amount, evidence=evidence,
                         reasoning=" ".join(reasons) + " Amount is before GST.", confidence=combine(*factors),
                         evidence_complete=complete,
                         extra={"lines": [l.row for l, *_ in claims]})
                f.extra["_draft"] = {"kind": CLS, "card": card.code, "bill": bill.bill_id, "date": str(bill.bill_date),
                                     "items": [(l.product, l.rate, a, l.quantity) for l, a, *_ in claims],
                                     "person": first_name(card), "amount": amount}
                f = owner_adjust(view, f)
                if f:
                    out.append(f)
            for line, sources, res, quotes in conflicts:
                resolved = view.resolution_for(bill.bill_id, CLS)
                if resolved:
                    continue
                f = make(CLS, bill.bill_id, Status.CONFLICT, distributor=card.code, sources=sources,
                         evidence=[bill_line_cite(bill, line)] + [chat_cite(q.source) for q in quotes or []]
                                  + [f"{s['source']}: {line.product} | {s['value']}" for s in sources if "said by" not in s["value"]],
                         reasoning=(f"Trusted sources disagree on the rate for {line.product} on {bill.bill_date}: "
                                    + "; ".join(f"{s['source']} says {s['value']}" for s in sources)
                                    + f". Bill charges ₹{fmt_num(line.rate)}. Left for the owner to decide."),
                         confidence=0.85, evidence_complete=False,
                         extra={"amount_at_stake_inr": round(max(0.0, (line.rate - min(float(s['value'].split('₹')[1].split()[0].replace(',', '')) for s in sources)) * line.quantity), 2),
                                "_key": line.product})
                out.append(f)
            if missing and not claims:
                first_line, res = missing[0]
                names = ", ".join(sorted({l.product for l, _ in missing}))
                if res.lists_in_force:
                    what = f"rate for {names} (not on the rate list in force on {bill.bill_date})"
                else:
                    what = f"rate list from {card.name} covering {bill.bill_date}"
                f = make(CLS, bill.bill_id, Status.CANNOT_DETERMINE, distributor=card.code,
                         evidence=[bill_line_cite(bill, l) for l, _ in missing] + [chat_cite(bill.source)],
                         reasoning=f"Cannot check the billed rates of {bill.bill_id}: no {what} has been received.",
                         missing=what, ask=f"distributor ({card.name}, {first_name(card)}): please send the {what}",
                         ask_party="distributor", confidence=0.8, evidence_complete=False)
                f.extra["_draft"] = {"kind": "request", "card": card.code, "bill": bill.bill_id, "what": what,
                                     "person": first_name(card)}
                out.append(f)
    return out
