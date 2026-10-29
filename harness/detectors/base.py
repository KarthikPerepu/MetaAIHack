"""
Shared helpers for the six detectors.

Confidence is "the probability that both the verdict and the amount are
right". It is built from the reliability of each fact the finding depends on
(bill read quality, source authority, product match, whether the rate in
force is certain, corroboration) so that shakier findings score lower.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional

from harness.analysis import View
from harness.knowledge.models import AUTHORITY_WEIGHT, Authority, Bill, Finding, Status


def bill_read_factor(bill: Bill) -> float:
    f = 0.80 + 0.20 * max(0.0, min(1.0, bill.ocr_confidence))
    if not bill.arithmetic_ok:
        f *= 0.90
    if bill.injected_text:
        f *= 0.95
    return f


def authority_factor(auth: Authority, stt: float = 1.0, kind: str = "chat") -> float:
    base = {Authority.SIGNED_RATE_LIST: 1.0, Authority.OWNER: 0.98, Authority.NAMED_SALESMAN: 0.95,
            Authority.IDENTIFIED_PERSON: 0.82, Authority.UNKNOWN: 0.6, Authority.FORWARDED: 0.3}[auth]
    if kind == "voice":
        base *= 0.86 + 0.14 * max(0.0, min(1.0, stt))
    return base


def match_factor(score: float) -> float:
    if score >= 99:
        return 1.0
    if score >= 92:
        return 0.97
    if score >= 85:
        return 0.93
    return 0.88


def combine(*factors: float, start: float = 0.97) -> float:
    p = start
    for f in factors:
        p *= f
    return round(max(0.05, min(0.98, p)), 3)


def finding_key(cls: str, bill_id: str, status: Status, extra: str = "") -> str:
    return hashlib.sha1(f"{cls}|{bill_id}|{status.value}|{extra}".encode()).hexdigest()[:12]


def make(cls: str, bill_id: str, status: Status, *, distributor: str, amount: float = 0.0,
         evidence: Iterable[Optional[str]] = (), reasoning: str = "", confidence: float = 0.5, **kw) -> Finding:
    ev = [e for e in evidence if e]
    ev = list(dict.fromkeys(ev))
    f = Finding(bill_id=bill_id, finding_class=cls, status=status, distributor=distributor,
                amount_inr=round(max(0.0, amount), 2), evidence=ev, reasoning=reasoning, confidence=confidence, **kw)
    f.key = finding_key(cls, bill_id, status, kw.get("extra", {}).get("_key", "") if kw.get("extra") else "")
    return f


def owner_adjust(view: View, f: Finding) -> Optional[Finding]:
    """Apply an owner verdict from feedback: rejected -> suppressed; confirmed -> high confidence."""
    v = view.verdict(f.bill_id, f.finding_class)
    if not v:
        return f
    if v.get("verdict") == "rejected":
        return None
    if v.get("verdict") == "confirmed":
        f.confidence = max(f.confidence, 0.97)
        f.extra["owner_confirmed"] = True
        if v.get("amount") is not None and f.status == Status.CONFIRMED:
            try:
                f.amount_inr = round(float(v["amount"]), 2)
                f.extra["amount_set_by_owner"] = True
            except (TypeError, ValueError):
                pass
    return f
