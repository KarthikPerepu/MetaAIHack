"""
Duplicate bill: the same delivery billed twice under a new bill number,
possibly weeks later. amount = full value of the repeat bill.

Same delivery means: same distributor, the same items with the same
quantities (names matched fuzzily, rates may differ), and the same delivery
(same challan number, or delivery dates within 2 days). A later bill with the
same items but its own, later delivery is a re-order, not a duplicate. When
the later bill shows no delivery date we use its bill date, and a delivery
confirmed in chat near the later bill proves a separate delivery.
"""

from __future__ import annotations

from datetime import timedelta

from harness.analysis import View, chat_cite, first_name, recv
from harness.detectors.base import bill_read_factor, combine, make, owner_adjust
from harness.knowledge.models import Bill, Finding, Status
from harness.textutil import fmt_inr, fmt_num, product_similarity

CLS = "duplicate_bill"


def _items_match(a: Bill, b: Bill) -> tuple[bool, float]:
    la = [l for l in a.lines if not l.is_free and l.quantity > 0]
    lb = [l for l in b.lines if not l.is_free and l.quantity > 0]
    if not la or len(la) != len(lb):
        return False, 0.0
    used, scores = set(), []
    for x in la:
        best, best_i = 0.0, None
        for i, y in enumerate(lb):
            if i in used or abs(x.quantity - y.quantity) > 0.01:
                continue
            s = product_similarity(x.product, y.product)
            if s > best:
                best, best_i = s, i
        if best_i is None or best < 85:
            return False, 0.0
        used.add(best_i)
        scores.append(best)
    return True, min(scores)


def _delivery(b: Bill):
    return b.delivery_date or b.bill_date


def detect(view: View) -> list[Finding]:
    out: list[Finding] = []
    win = view.policy.duplicate_window_days
    for card in view.cards():
        bills = view.bills(card)
        flagged: set[str] = set()
        for i, later in enumerate(bills):
            for earlier in bills[:i]:
                if earlier.bill_id == later.bill_id or earlier.bill_id in flagged:
                    continue
                if view.not_duplicate(earlier.bill_id, later.bill_id):
                    continue
                ok, name_score = _items_match(earlier, later)
                if not ok:
                    continue
                same_challan = bool(earlier.challan_no and later.challan_no and earlier.challan_no == later.challan_no)
                diff_challan = bool(earlier.challan_no and later.challan_no and earlier.challan_no != later.challan_no)
                gap = abs((_delivery(later) - _delivery(earlier)).days)
                if diff_challan:
                    continue
                # weeks-apart repeats are caught through the printed delivery date or challan,
                # which is what identifies the delivery; bill dates alone only count within the window
                if not same_challan and gap > win:
                    continue
                amount = later.total_value
                basis = (f"same challan {later.challan_no}" if same_challan else
                         f"delivery dates {_delivery(earlier)} and {_delivery(later)} ({gap} day(s) apart)")
                items = ", ".join(f"{l.product} × {fmt_num(l.quantity)}" for l in later.lines if not l.is_free)
                factors = [bill_read_factor(later), bill_read_factor(earlier),
                           1.0 if name_score >= 99 else 0.95,
                           1.0 if (same_challan or later.delivery_date) else 0.93]
                if abs(earlier.total_value - later.total_value) > 1:
                    factors.append(0.95)
                f = make(CLS, later.bill_id, Status.CONFIRMED, distributor=card.code, amount=amount,
                         evidence=[f"{earlier.source.file}: bill {earlier.bill_id} dated {earlier.bill_date}, "
                                   f"delivery {_delivery(earlier)}: {items}",
                                   f"{later.source.file}: bill {later.bill_id} dated {later.bill_date}, "
                                   f"delivery {_delivery(later)}: same items and quantities, total {fmt_inr(amount)}",
                                   chat_cite(earlier.source), chat_cite(later.source)],
                         reasoning=(f"{later.bill_id} repeats {earlier.bill_id}: identical items and quantities "
                                    f"({items}); {basis}. Full value of the repeat bill: {fmt_inr(amount)}."),
                         confidence=combine(*factors), related_bill_ids=[earlier.bill_id],
                         extra={"duplicate_of": earlier.bill_id})
                f.extra["_draft"] = {"kind": CLS, "card": card.code, "bill": later.bill_id, "orig": earlier.bill_id,
                                     "items": items, "person": first_name(card), "amount": amount}
                f = owner_adjust(view, f)
                if f:
                    out.append(f)
                    flagged.add(later.bill_id)
                break
    return out
