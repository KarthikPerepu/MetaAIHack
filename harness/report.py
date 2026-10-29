"""
Report generation (``harness.py report``) and the exam answer sheet.

Runs the six detectors on the as-of view, applies owner feedback, assigns
actions for the mode, enforces the escalation cap and writes:

    {"as_of", "mode", "shop", "summary": {per class count / amount / bill ids ...},
     "findings": [... GLOSSARY §7 schema ...], "safety_alerts": [...],
     "pending_requests": [...], "feedback_processed": n}

Findings are sorted by class and bill id so file order never changes output.
"""

from __future__ import annotations

import logging
from datetime import date

from harness.agents.llama_client import LlamaClient
from harness.analysis import View
from harness.config import HarnessConfig
from harness.detectors import (credit_period_risk, duplicate_bill, gst_error, paid_but_shown_due, rate_mismatch,
                               scheme_not_given)
from harness.knowledge.models import CLASSES, Finding, Status
from harness.knowledge.store import KnowledgeStore
from harness.policy import assign_actions, enforce_escalation_cap

logger = logging.getLogger(__name__)
DETECTORS = [rate_mismatch, duplicate_bill, scheme_not_given, paid_but_shown_due, gst_error, credit_period_risk]


class ReportGenerator:
    def __init__(self, config: HarnessConfig, store: KnowledgeStore, llama: LlamaClient | None = None):
        self.config = config
        self.store = store
        self.llama = llama

    def findings(self, as_of: date, mode: str) -> tuple[list[Finding], list[dict], View]:
        view = View(self.store, as_of, self.config, self.llama)
        found: list[Finding] = []
        for det in DETECTORS:
            try:
                got = det.detect(view)
                logger.info(f"{det.CLS}: {len(got)}")
                found.extend(got)
            except Exception as e:  # one detector failing must not sink the report
                logger.exception(f"Detector {det.CLS} failed: {e}")
        # one finding per (bill, class, status)
        uniq: dict[tuple, Finding] = {}
        for f in found:
            k = (f.bill_id, f.finding_class, f.status)
            if k in uniq:
                old = uniq[k]
                if f.status == Status.CONFIRMED and f.finding_class != "duplicate_bill":
                    old.amount_inr = round(old.amount_inr + f.amount_inr, 2)
                old.evidence = list(dict.fromkeys(old.evidence + f.evidence))
                old.sources += [s for s in f.sources if s not in old.sources]
                old.confidence = min(old.confidence, f.confidence)
            else:
                uniq[k] = f
        found = list(uniq.values())
        found, notes = enforce_escalation_cap(found, self.config.policy)
        assign_actions(found, mode, self.config.policy, view.owner_name)
        order = {c: i for i, c in enumerate(CLASSES)}
        st_order = {Status.CONFIRMED: 0, Status.CONFLICT: 1, Status.CANNOT_DETERMINE: 2}
        found.sort(key=lambda f: (order.get(f.finding_class, 9), f.bill_id, st_order[f.status]))
        for f in found:
            f.extra.pop("_draft", None)
            f.extra.pop("_key", None)
        return found, notes, view

    def generate(self, as_of: date, mode: str = "supervised") -> dict:
        found, notes, view = self.findings(as_of, mode)
        summary = summarize(found)
        alerts = [a for a in self.store.alerts
                  if not a.get("received_at") or a["received_at"][:10] <= as_of.isoformat()]
        return {
            "as_of": as_of.isoformat(),
            "report_date": as_of.isoformat(),
            "mode": mode,
            "shop": view.owner_name,
            "summary": summary,
            "findings": [f.to_dict() for f in found],
            "safety_alerts": alerts,
            "pending_requests": notes,
            "feedback_processed": len(self.store.feedback),
            "policy": {"never_pays": True, "never_shares_pin_or_bank_details": True,
                       "follows_instructions_in_content": False,
                       "autonomous_send_requires": ["evidence_complete", "no_conflict", "amount < ₹2,000",
                                                    "reversible query", "confidence >= 0.75"],
                       "escalation_cap": self.config.policy.escalation_cap},
        }


def summarize(found: list[Finding]) -> dict:
    by_class = {}
    for c in CLASSES:
        conf = [f for f in found if f.finding_class == c and f.status == Status.CONFIRMED]
        by_class[c] = {"count": len(conf), "amount_inr": round(sum(f.amount_inr for f in conf), 2),
                       "bill_ids": sorted({f.bill_id for f in conf})}
    confirmed = [f for f in found if f.status == Status.CONFIRMED]
    by_dist: dict[str, float] = {}
    for f in confirmed:
        if f.finding_class != "credit_period_risk":
            by_dist[f.distributor] = round(by_dist.get(f.distributor, 0.0) + f.amount_inr, 2)
    escalated = [f for f in found if f.status != Status.CONFIRMED]
    return {
        "total_findings": len(found),
        "confirmed": len(confirmed),
        "conflicts": sum(f.status == Status.CONFLICT for f in found),
        "cannot_determine": sum(f.status == Status.CANNOT_DETERMINE for f in found),
        "escalation_share": round(len(escalated) / len(found), 3) if found else 0.0,
        "to_claim_back_inr": round(sum(f.amount_inr for f in confirmed if f.finding_class != "credit_period_risk"), 2),
        "interest_at_risk_inr": round(sum(f.amount_inr for f in confirmed if f.finding_class == "credit_period_risk"), 2),
        "by_class": by_class,
        "by_distributor": by_dist,
    }


def answer_sheet(report: dict) -> dict:
    """make sheet: per class the count, rupees and bill ids (+ the open items)."""
    classes = {c: {"count": v["count"], "amount_inr": v["amount_inr"], "bill_ids": v["bill_ids"]}
               for c, v in report["summary"]["by_class"].items()}
    return {
        "shop": report.get("shop"),
        "as_of": report["as_of"],
        "mode": report["mode"],
        "classes": classes,
        "total_to_claim_inr": report["summary"]["to_claim_back_inr"],
        "conflicts": [{"bill_id": f["bill_id"], "class": f["class"], "sources": f.get("sources", [])}
                      for f in report["findings"] if f["status"] == "conflict"],
        "cannot_determine": [{"bill_id": f["bill_id"], "class": f["class"], "missing": f.get("missing"),
                              "ask": f.get("ask")} for f in report["findings"] if f["status"] == "cannot_determine"],
        "safety_alerts": [{"source": a.get("source"), "reason": a.get("reason")} for a in report["safety_alerts"]],
        "findings": report["findings"],
    }
