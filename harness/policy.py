"""
Actions, drafts and the escalation cap.

Supervised (default): anything for a distributor is only DRAFTED
("draft_whatsapp_reply"); internal actions (owner reminder, khata note)
happen on their own.

Autonomous: a WhatsApp reply is sent only when all four hold:
    1. evidence is complete (documents, not just a voice note; no gaps)
    2. nothing conflicts on that bill
    3. amount < ₹2,000
    4. the message can be taken back (a query, never a payment)
plus the reading is reliable (confidence >= 0.75). Otherwise it is drafted
and waits for the owner.

There is no action that pays, and drafts never contain a UPI PIN, bank
details or payment instructions.

Escalation cap: at most 60% of findings may go to the owner. Conflicts always
go to the owner; cannot_determine items first ask the person holding the
document, and are consolidated if they would push escalations past half.
"""

from __future__ import annotations

import re
from collections import defaultdict

from harness.config import Policy
from harness.knowledge.models import Finding, Status
from harness.textutil import fmt_inr

FORBIDDEN_IN_DRAFT = re.compile(r"(upi\s*pin|\bpin\b|otp|password|cvv|ifsc|account\s*(no|number)|@[a-z]{2,}\b)", re.I)
CD_PRIORITY = {"rate_mismatch": 0, "scheme_not_given": 1, "paid_but_shown_due": 2, "duplicate_bill": 3, "gst_error": 4,
               "credit_period_risk": 5}


def assign_actions(findings: list[Finding], mode: str, policy: Policy, shop: str) -> None:
    conflict_bills = {f.bill_id for f in findings if f.status == Status.CONFLICT}
    for f in findings:
        if f.finding_class == "credit_period_risk" and f.status == Status.CONFIRMED:
            f.action = {"supervised": "remind_owner", "autonomous": "remind_owner"}
            f.draft_message = _draft(f, shop)
            continue
        if f.status == Status.CONFLICT:
            f.action = {"supervised": "escalate_to_owner", "autonomous": "escalate_to_owner"}
            f.draft_message = (f"Owner decision needed on {f.bill_id} ({f.finding_class.replace('_', ' ')}): "
                               + "; ".join(f"{s['source']} says {s['value']}" for s in f.sources))
            continue
        if f.status == Status.CANNOT_DETERMINE:
            if f.ask_party == "owner":
                f.action = {"supervised": "ask_owner", "autonomous": "ask_owner"}
            else:
                f.action = {"supervised": "draft_whatsapp_reply", "autonomous": "draft_whatsapp_reply",
                            "message_type": "document_request"}
            f.draft_message = _draft(f, shop)
            continue
        checks = {
            "evidence_complete": bool(f.evidence_complete and len(f.evidence) >= 2),
            "no_conflict": f.bill_id not in conflict_bills,
            "under_2000": f.amount_inr < policy.autonomous_amount_limit,
            "reversible": bool(f.reversible),
            "reliable_reading": f.confidence >= 0.75,
        }
        passed = all(checks.values())
        f.action = {"supervised": "draft_whatsapp_reply",
                    "autonomous": "send_whatsapp_reply" if passed else "draft_whatsapp_reply"}
        if mode == "autonomous":
            f.extra["safety_check"] = {**checks, "passed": passed}
            if not passed:
                f.extra["waits_for_owner"] = [k for k, v in checks.items() if not v]
        f.draft_message = _draft(f, shop)
    for f in findings:
        if f.draft_message and FORBIDDEN_IN_DRAFT.search(f.draft_message):
            f.draft_message = FORBIDDEN_IN_DRAFT.sub("[removed]", f.draft_message)


def _draft(f: Finding, shop: str) -> str:
    d = f.extra.get("_draft") or {}
    person = d.get("person") or "ji"
    sign = f"\n— {shop}" if shop else ""
    kind = d.get("kind")
    if kind == "rate_mismatch":
        parts = [f"{p} ₹{billed:g} lagaya hai, rate list mein ₹{agreed:g} hai ({qty:g} × ₹{billed - agreed:g})"
                 for p, billed, agreed, qty in d.get("items", [])]
        return (f"Namaste {person} ji, bill {d['bill']} ({d['date']}) mein " + "; ".join(parts)
                + f". Kul {fmt_inr(d['amount'])} ka credit note bhej dijiye. Dhanyavaad.{sign}")
    if kind == "duplicate_bill":
        return (f"Namaste {person} ji, bill {d['bill']} aur bill {d['orig']} mein bilkul same maal hai ({d['items']}), "
                f"ek hi delivery ka. {d['bill']} ({fmt_inr(d['amount'])}) duplicate lag raha hai, kripya cancel karke "
                f"confirm kar dijiye.{sign}")
    if kind == "scheme_not_given":
        return (f"Namaste {person} ji, {d['date']} ko aapne '{d['scheme']}' scheme boli thi, lekin bill {d['bill']} "
                f"mein free maal / discount nahi laga. {fmt_inr(d['amount'])} ka credit note ya free maal bhej dijiye.{sign}")
    if kind == "paid_but_shown_due":
        ref = f" (UPI ref {d['ref']})" if d.get("ref") else ""
        return (f"Namaste {person} ji, bill {d['bill']} ka {fmt_inr(d['amount'])} humne {d['date']} ko UPI se de diya "
                f"tha{ref}, phir bhi {d['stmt']} ke statement mein due dikh raha hai. Kripya account update kar dijiye.{sign}")
    if kind == "gst_error":
        return (f"Namaste {person} ji, bill {d['bill']} mein GST galat laga hai: {d.get('detail', '')} "
                f"Extra tax {fmt_inr(d['amount'])} hai, kripya corrected invoice / credit note bhej dijiye.{sign}")
    if kind == "credit_period_risk":
        return (f"Reminder for the owner: bill {d['bill']} ({fmt_inr(d['outstanding'])} outstanding) crosses its credit "
                f"period on {d['due']}. Pay before then to avoid about {fmt_inr(d['amount'])} interest.")
    if kind == "request":
        return (f"Namaste {person} ji, bill {d['bill']} check karne ke liye {d['what']} chahiye. Kripya bhej dijiye. "
                f"Dhanyavaad.{sign}")
    if f.status == Status.CANNOT_DETERMINE:
        return f"Question for the owner: {f.ask}"
    return ""


def enforce_escalation_cap(findings: list[Finding], policy: Policy) -> tuple[list[Finding], list[dict]]:
    """Keep owner escalations (conflicts + cannot_determine) at or under the target share."""
    notes: list[dict] = []

    def share(fs):
        return (sum(f.status != Status.CONFIRMED for f in fs) / len(fs)) if fs else 0.0

    if len(findings) < 5 or share(findings) <= policy.escalation_target:
        return findings, notes
    # A: one open question per bill (keep the most important)
    by_bill = defaultdict(list)
    for f in findings:
        if f.status == Status.CANNOT_DETERMINE:
            by_bill[f.bill_id].append(f)
    drop = set()
    for bill_id, fs in by_bill.items():
        fs.sort(key=lambda f: CD_PRIORITY.get(f.finding_class, 9))
        for f in fs[1:]:
            drop.add(id(f))
            notes.append(_note(f, "kept as a note: another open question on the same bill"))
    findings = [f for f in findings if id(f) not in drop]
    if share(findings) <= policy.escalation_target:
        return findings, notes
    # B: one request per distributor and class (the same document unblocks every bill)
    groups = defaultdict(list)
    for f in findings:
        if f.status == Status.CANNOT_DETERMINE:
            groups[(f.distributor, f.finding_class, f.ask_party)].append(f)
    keep = []
    merged = set()
    for key, fs in groups.items():
        if len(fs) < 2:
            continue
        fs.sort(key=lambda f: f.bill_id)
        head = fs[0]
        head.related_bill_ids = sorted({x.bill_id for x in fs[1:]} | set(head.related_bill_ids))
        head.reasoning += f" The same document is needed for {', '.join(head.related_bill_ids)}."
        for x in fs[1:]:
            merged.add(id(x))
            notes.append(_note(x, f"covered by the request on {head.bill_id}"))
    findings = [f for f in findings if id(f) not in merged]
    # C: last resort, keep the hard cap; conflicts are never removed. Tiny reports
    # (a single open question) are not "escalating most findings", so they are left alone.
    while share(findings) > policy.escalation_cap:
        cds = sorted((f for f in findings if f.status == Status.CANNOT_DETERMINE),
                     key=lambda f: (-CD_PRIORITY.get(f.finding_class, 9), f.confidence))
        if not cds:
            break
        victim = cds[0]
        findings = [f for f in findings if f is not victim]
        notes.append(_note(victim, "moved to pending requests to keep owner escalations under the cap"))
    return findings, notes


def _note(f: Finding, why: str) -> dict:
    return {"bill_id": f.bill_id, "class": f.finding_class, "status": f.status.value, "missing": f.missing,
            "ask": f.ask, "why": why}
