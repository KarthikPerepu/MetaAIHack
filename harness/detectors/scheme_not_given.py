"""
Scheme not given: a free-goods or discount scheme agreed in chat / a voice
note / the rate list is missing on the bill.

amount = missing free units x rate in force on the bill date (free goods),
         or the missing discount (percentage or per-unit).

* A scheme is ONE-TIME unless the words say it repeats. A one-time scheme is
  used up by the first qualifying bill dated on/after the agreement, whether or
  not the free goods were given, so a later bill is never claimed again
  (treating one-time as permanent is a capping mistake).
* "X pe Y free" scales with the quantity (20 bought on "10 pe 1" = 2 free);
  "X lo, Y free milega" gives Y once.
* Free goods shown on the bill (a free row, a free-qty column, or a price that
  already absorbs the free units) count as given.
* Two trusted sources that disagree about the scheme (rate list vs salesman,
  or two different people) are a conflict. A later message from the same
  person replaces the earlier offer.
* Only the distributor's named people (or the rate list, or the owner) can
  create a scheme. Forwarded or unknown senders cannot.
"""

from __future__ import annotations

import math
from typing import Optional

from harness.analysis import View, bill_line_cite, chat_cite, first_name, recv
from harness.detectors.base import authority_factor, bill_read_factor, combine, make, match_factor, owner_adjust
from harness.knowledge.models import Authority, Bill, BillLine, Finding, Scheme, Status
from harness.textutil import UNIT_WORDS, fmt_inr, fmt_num, product_similarity

CLS = "scheme_not_given"
TRUSTED = (Authority.SIGNED_RATE_LIST, Authority.OWNER, Authority.NAMED_SALESMAN)


def _unit(u: str) -> str:
    return UNIT_WORDS.get((u or "").lower().rstrip("s"), (u or "").lower().rstrip("s"))


def _describe(s: Scheme) -> str:
    if s.kind == "free_goods":
        txt = f"{fmt_num(s.buy_qty)} pe {fmt_num(s.free_qty)} free" if s.per_multiple else \
            f"{fmt_num(s.buy_qty)} lo, {fmt_num(s.free_qty)} free"
        return txt + (f" {s.unit}" if s.unit else "")
    if s.kind == "discount_pct":
        return f"{fmt_num(s.discount_pct)}% discount"
    return f"₹{fmt_num(s.discount_per_unit)} per {s.unit or 'unit'} off"


def _signature(s: Scheme) -> tuple:
    if s.kind == "free_goods" and s.buy_qty:
        return ("free", round((s.free_qty or 0) / s.buy_qty, 4))
    return (s.kind, s.discount_pct, s.discount_per_unit)


def _qualifying_lines(s: Scheme, bill: Bill) -> list[BillLine]:
    lines = [l for l in bill.lines if not l.is_free and l.quantity > 0]
    if s.product:
        lines = [l for l in lines if product_similarity(l.product, s.product) >= 85]
    if s.unit:
        lines = [l for l in lines if not l.unit or _unit(l.unit) == _unit(s.unit)]
    if s.kind == "free_goods" and s.buy_qty:
        lines = [l for l in lines if l.quantity + 1e-9 >= s.buy_qty]
    return lines


def _in_window(s: Scheme, bill: Bill) -> bool:
    if s.valid_from and bill.bill_date < s.valid_from:
        return False
    if s.valid_until and bill.bill_date > s.valid_until:
        return False
    return True


def detect(view: View) -> list[Finding]:
    out: list[Finding] = []
    for card in view.cards():
        schemes = []
        for s in view.schemes(card):
            if s.authority not in TRUSTED:
                continue
            if s.authority == Authority.NAMED_SALESMAN and view.contact_allowed(card, s.agreed_by) is False:
                continue
            schemes.append(s)
        # a later offer from the same person for the same product replaces the earlier one
        live: list[Scheme] = []
        for s in schemes:
            live = [o for o in live if not (o.agreed_by == s.agreed_by and o.authority == s.authority
                                            and (o.product == s.product or not o.product or not s.product)
                                            and o.kind == s.kind)]
            live.append(s)
        bills = view.bills(card)
        consumed: dict[str, str] = {}
        for bill in bills:
            cands: list[tuple[Scheme, list[BillLine]]] = []
            for s in live:
                if s.record_id in consumed or not _in_window(s, bill):
                    continue
                lines = _qualifying_lines(s, bill)
                if lines:
                    cands.append((s, lines))
            if not cands:
                continue
            # conflicts: disagreeing schemes from different trusted sources for the same product
            groups: dict[str, list[tuple[Scheme, list[BillLine]]]] = {}
            for s, lines in cands:
                key = s.product or (lines[0].product if len({l.product for l in lines}) == 1 else "*")
                groups.setdefault(key.lower(), []).append((s, lines))
            for key, group in sorted(groups.items()):
                sigs = {_signature(s) for s, _ in group}
                if len(group) > 1 and len(sigs) > 1:
                    srcs = [{"source": s.source.file if s.source_kind in ("voice", "rate_list") else s.source.cite(),
                             "value": _describe(s) + f" ({s.agreed_by})"} for s, _ in group]
                    f = make(CLS, bill.bill_id, Status.CONFLICT, distributor=card.code, sources=srcs,
                             evidence=[chat_cite(s.source) or s.source.file for s, _ in group]
                                      + [bill_line_cite(bill, l) for l in group[0][1]],
                             reasoning=(f"Sources disagree about the scheme on {key} for {bill.bill_id}: "
                                        + "; ".join(f"{x['source']}: {x['value']}" for x in srcs) + ". Owner to decide."),
                             confidence=0.82, evidence_complete=False, scheme_type=group[-1][0].recurrence,
                             extra={"_key": key})
                    if not view.resolution_for(bill.bill_id, CLS):
                        out.append(f)
                    for s, _ in group:
                        if s.recurrence == "one_time":
                            consumed[s.record_id] = bill.bill_id
                    continue
                s, lines = group[-1]
                if s.recurrence == "one_time":
                    consumed[s.record_id] = bill.bill_id
                    for other, _ in group[:-1]:
                        if other.recurrence == "one_time":
                            consumed[other.record_id] = bill.bill_id
                f = _evaluate(view, card, bill, s, lines)
                if f:
                    f = owner_adjust(view, f)
                    if f:
                        out.append(f)
    return out


def _evaluate(view: View, card, bill: Bill, s: Scheme, lines: list[BillLine]) -> Optional[Finding]:
    inferred_product = s.product is None
    if inferred_product and len({l.product.lower() for l in lines}) > 1:
        return make(CLS, bill.bill_id, Status.CANNOT_DETERMINE, distributor=card.code,
                    evidence=[chat_cite(s.source) or s.source.file] + [bill_line_cite(bill, l) for l in lines],
                    reasoning=(f"{s.agreed_by} offered '{_describe(s)}' without naming the product, and {bill.bill_id} "
                               f"has several items that qualify."),
                    missing="which product the scheme covers", ask_party="salesman",
                    ask=f"salesman ({s.agreed_by}): which product does the '{_describe(s)}' scheme cover?",
                    confidence=0.75, evidence_complete=False, scheme_type=s.recurrence)
    line = max(lines, key=lambda l: (l.quantity, l.row))
    factors = [bill_read_factor(bill), authority_factor(s.authority, s.stt_confidence, s.source_kind)]
    if inferred_product:
        factors.append(0.93)
    if not s.owner_acknowledged and s.authority == Authority.NAMED_SALESMAN:
        factors.append(0.98)
    complete = s.source_kind in ("chat", "rate_list", "owner_feedback", "contract") or s.authority == Authority.OWNER
    evidence = [_scheme_cite(s), chat_cite(s.source) if s.source_kind == "voice" else None,
                bill_line_cite(bill, line)]
    if s.kind == "free_goods":
        mult = math.floor(line.quantity / s.buy_qty + 1e-9) if s.per_multiple else 1
        expected = mult * (s.free_qty or 0)
        free_product = s.free_product or s.product or line.product
        given = line.free_qty + sum(l.quantity for l in bill.lines if l.is_free and
                                    (product_similarity(l.product, free_product) >= 80 or
                                     product_similarity(l.product, line.product) >= 80 or
                                     len([x for x in bill.lines if not x.is_free]) == 1))
        res = view.resolve_rate(card, free_product, bill.bill_date, bill.bill_id)
        rate, rate_src = res.rate, None
        if rate is not None:
            rate_src = res.rate_list
            factors.append(match_factor(res.match_score))
            if res.conflict:
                factors.append(0.85)
            # price already absorbs the free units?
            net = line.amount - line.discount
            if net <= (line.quantity - expected) * rate + 1.0 and expected > 0:
                given = max(given, expected)
        else:
            rate = (line.amount - line.discount) / line.quantity if line.quantity else line.rate
            factors.append(0.85)
            complete = False
        missing = max(0.0, expected - given)
        if missing <= 1e-9:
            return None
        amount = missing * rate
        if rate_src is not None:
            evidence.append(f"{rate_src.source.file}: {res.entry.product} | {fmt_num(rate)}")
        elif res.owner_rate:
            evidence.append(f"owner feedback: agreed rate ₹{fmt_num(rate)} for {free_product}")
        free_rows = [l for l in bill.lines if l.is_free]
        evidence.append(f"{bill.source.file}: " + (f"{fmt_num(given)} of {fmt_num(expected)} free units shown"
                                                   if (given or free_rows) else
                                                   f"{line.product} {fmt_num(line.quantity)}, no free goods line"))
        valuation = (f"rate in force on {bill.bill_date} from {rate_src.source.file}" if rate_src is not None else
                     "owner-agreed rate" if res.owner_rate else
                     "the billed rate (no rate list covers these goods, so this claim waits for the owner)")
        reasoning = (f"{s.agreed_by} agreed '{_describe(s)}' on {s.valid_from} ({s.source_kind}, "
                     f"{s.recurrence.replace('_', '-')}). {bill.bill_id} ({bill.bill_date}) bills "
                     f"{fmt_num(line.quantity)} {line.product}, so {fmt_num(expected)} free unit(s) were due and "
                     f"{fmt_num(given)} shown. Missing {fmt_num(missing)} × ₹{fmt_num(rate)} ({valuation}) = {fmt_inr(amount)}.")
    else:
        if s.kind == "discount_pct":
            expected_disc = line.amount * (s.discount_pct or 0) / 100.0
        else:
            expected_disc = line.quantity * (s.discount_per_unit or 0)
        paid_lines = [l for l in bill.lines if not l.is_free]
        gross = sum(l.amount for l in paid_lines) or 1.0
        given_disc = line.discount + (bill.discount_total or 0) * line.amount / gross
        missing_amt = expected_disc - given_disc
        if missing_amt <= view.policy.tax_tolerance:
            return None
        amount = missing_amt
        evidence.append(f"{bill.source.file}: discount shown ₹{fmt_num(round(given_disc, 2))} on {line.product}")
        reasoning = (f"{s.agreed_by} agreed '{_describe(s)}' on {s.valid_from} ({s.recurrence.replace('_', '-')}). "
                     f"{bill.bill_id} shows ₹{fmt_num(round(given_disc, 2))} discount on {line.product} "
                     f"(₹{fmt_num(line.amount)}); ₹{fmt_num(round(expected_disc, 2))} was due. Missing {fmt_inr(amount)}.")
    if s.owner_acknowledged:
        reasoning += " The owner accepted the offer in chat."
    evidence.append(chat_cite(bill.source))
    f = make(CLS, bill.bill_id, Status.CONFIRMED, distributor=card.code, amount=amount, evidence=evidence,
             reasoning=reasoning, confidence=combine(*factors), evidence_complete=complete,
             scheme_type=s.recurrence, extra={"scheme_source": s.source.file if s.source_kind == "voice" else s.source.cite()})
    f.extra["_draft"] = {"kind": CLS, "card": card.code, "bill": bill.bill_id, "scheme": _describe(s),
                         "date": str(s.valid_from), "person": first_name(card), "amount": amount}
    return f


def _scheme_cite(s: Scheme) -> str:
    if s.source_kind == "voice":
        return f"{s.source.file}: {s.raw_text}"[:240]
    if s.source_kind == "rate_list":
        return f"{s.source.file}: {s.raw_text}"
    return chat_cite(s.source) or f"{s.source.file}: {s.raw_text}"
