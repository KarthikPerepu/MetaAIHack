"""
Report Generator.

Runs all six detectors, resolves conflicts, applies safety checks,
and produces the final report.json with findings and actions.
"""

from __future__ import annotations
import json
import logging
from datetime import date
from typing import Optional

from harness.config import HarnessConfig
from harness.agents.llama_client import LlamaClient
from harness.knowledge.store import KnowledgeStore
from harness.knowledge.models import Finding, FindingStatus
from harness.ingest.safety import SafetyChecker

# Import all detectors
from harness.detectors.rate_mismatch import RateMismatchDetector
from harness.detectors.duplicate_bill import DuplicateBillDetector
from harness.detectors.scheme_not_given import SchemeNotGivenDetector
from harness.detectors.paid_but_shown_due import PaidButShownDueDetector
from harness.detectors.gst_error import GSTErrorDetector
from harness.detectors.credit_period_risk import CreditPeriodRiskDetector

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates the final findings report."""

    def __init__(
        self,
        config: HarnessConfig,
        store: KnowledgeStore,
        llama: LlamaClient,
    ):
        self.config = config
        self.store = store
        self.llama = llama
        self.safety = SafetyChecker(llama)

        # Initialize all detectors
        self.detectors = [
            RateMismatchDetector(config, store, llama),
            DuplicateBillDetector(config, store, llama),
            SchemeNotGivenDetector(config, store, llama),
            PaidButShownDueDetector(config, store, llama),
            GSTErrorDetector(config, store, llama),
            CreditPeriodRiskDetector(config, store, llama),
        ]

    def generate(
        self,
        as_of: date,
        mode: str = "supervised",
    ) -> dict:
        """Generate the full findings report.

        Args:
            as_of: The report date.
            mode: "supervised" or "autonomous".

        Returns:
            The complete report dict.
        """
        logger.info(f"═══ Generating report as of {as_of}, mode={mode} ═══")

        # Step 1: Run all detectors
        all_findings: list[Finding] = []
        for detector in self.detectors:
            try:
                findings = detector.detect_all(as_of)
                logger.info(f"{detector.CLASS_NAME}: {len(findings)} findings")
                all_findings.extend(findings)
            except Exception as e:
                logger.error(f"Detector {detector.CLASS_NAME} failed: {e}")

        # Step 2: Deduplicate findings
        all_findings = self._deduplicate(all_findings)

        # Step 3: Apply mode-specific action filtering
        all_findings = self._apply_mode(all_findings, mode)

        # Step 4: Check escalation cap (max 60% escalated)
        all_findings = self._enforce_escalation_cap(all_findings)

        # Step 5: Sort by confidence (highest first)
        all_findings.sort(key=lambda f: f.confidence, reverse=True)

        # Step 6: Compute summary
        summary = self._compute_summary(all_findings)

        report = {
            "report_date": as_of.isoformat(),
            "mode": mode,
            "summary": summary,
            "findings": [f.to_dict() for f in all_findings],
        }

        logger.info(f"Report: {summary['total_findings']} findings, "
                     f"₹{summary['total_amount']:.2f} total, "
                     f"₹{summary['recoverable_amount']:.2f} recoverable")
        return report

    def _deduplicate(self, findings: list[Finding]) -> list[Finding]:
        """Merge multiple line-level results into one bill/class/status finding."""
        grouped: dict[tuple[str, str, FindingStatus], Finding] = {}
        for finding in findings:
            key = (finding.bill_id, finding.finding_class, finding.status)
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = finding
                continue

            if finding.status == FindingStatus.CONFIRMED:
                if finding.finding_class == "duplicate_bill":
                    existing.amount_inr = max(existing.amount_inr, finding.amount_inr)
                else:
                    existing.amount_inr += finding.amount_inr
            existing.evidence = list(dict.fromkeys(existing.evidence + finding.evidence))
            existing.sources = list(dict.fromkeys(
                tuple(sorted(source.items())) for source in existing.sources + finding.sources
            )) if existing.sources or finding.sources else []
            existing.sources = [dict(source) for source in existing.sources]
            if finding.missing:
                existing.missing = "; ".join(dict.fromkeys(
                    part.strip() for part in (existing.missing + "; " + finding.missing).split(";")
                    if part.strip()
                ))
            if finding.ask and not existing.ask:
                existing.ask = finding.ask
            if finding.reasoning and finding.reasoning not in existing.reasoning:
                existing.reasoning = (existing.reasoning + " " + finding.reasoning).strip()
            existing.confidence = min(existing.confidence, finding.confidence)

        unique = list(grouped.values())
        if len(findings) != len(unique):
            logger.info(f"Merged duplicate bill/class findings: {len(findings)} → {len(unique)}")
        return unique

    def _apply_mode(
        self, findings: list[Finding], mode: str,
    ) -> list[Finding]:
        """Apply mode-specific action filtering."""
        for finding in findings:
            if mode == "supervised":
                current = finding.action.get("supervised", "draft_whatsapp_reply")
                # Preserve internal reminders; every external action is a draft.
                if current in ("remind_owner", "log_note"):
                    finding.action = {"supervised": current}
                elif finding.status == FindingStatus.CONFLICT:
                    finding.action = {"supervised": "escalate_to_owner"}
                else:
                    finding.action = {"supervised": "draft_whatsapp_reply"}

            elif mode == "autonomous":
                # Internal reminders stay internal and are never converted into
                # an external message by the autonomous send gate.
                if finding.action.get("autonomous") == "remind_owner" or finding.action.get("supervised") == "remind_owner":
                    finding.action = {"autonomous": "remind_owner"}
                    continue
                if finding.status == FindingStatus.CONFLICT:
                    finding.action = {"autonomous": "escalate_to_owner"}
                    continue
                # Check the four-part safety test
                safety_check = self.safety.check_autonomous_action(
                    amount=finding.amount_inr,
                    evidence_complete=(finding.status == FindingStatus.CONFIRMED and bool(finding.evidence)),
                    has_conflict=(finding.status == FindingStatus.CONFLICT),
                    is_reversible=True,  # Queries are reversible
                    amount_limit=self.config.autonomous_amount_limit,
                )

                if safety_check["allowed"]:
                    finding.action = {
                        "autonomous": "send_whatsapp_reply",
                        "safety_check": "passed",
                    }
                else:
                    # Downgrade to draft
                    finding.action = {
                        "autonomous": "draft_whatsapp_reply",
                        "safety_check": "failed",
                        "safety_failures": safety_check["failures"],
                    }

        return findings

    def _enforce_escalation_cap(
        self, findings: list[Finding],
    ) -> list[Finding]:
        """Limit owner escalations without changing the classification result.

        Missing-document findings can be turned into a draft request to the
        document holder. Conflicts remain owner decisions and are never silently
        resolved by this policy gate.
        """
        if not findings:
            return findings

        owner_escalations = [
            f for f in findings
            if f.status in (FindingStatus.CONFLICT, FindingStatus.CANNOT_DETERMINE)
            and f.action.get("supervised") != "log_note"
        ]
        max_escalated = int(len(findings) * self.config.escalation_cap)
        if len(owner_escalations) > max_escalated:
            # Cannot-determine results can request the missing record directly
            # as a draft. Conflicts have no safe automated resolution.
            reducible = sorted(
                (f for f in owner_escalations if f.status == FindingStatus.CANNOT_DETERMINE),
                key=lambda f: f.confidence,
            )
            for finding in reducible[:len(owner_escalations) - max_escalated]:
                action_key = "autonomous" if "autonomous" in finding.action else "supervised"
                finding.action = {action_key: "log_note"}
            if sum(f.action.get("supervised") == "escalate_to_owner" for f in findings) > max_escalated:
                logger.warning("Owner escalation cap cannot be met without overriding unresolved conflicts")

        return findings

    def _compute_summary(self, findings: list[Finding]) -> dict:
        """Compute summary statistics."""
        confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
        conflicts = [f for f in findings if f.status == FindingStatus.CONFLICT]
        cannot_determine = [f for f in findings if f.status == FindingStatus.CANNOT_DETERMINE]

        # Group by class
        by_class: dict[str, list] = {}
        for f in findings:
            by_class.setdefault(f.finding_class, []).append(f)

        class_summary = {}
        for cls_name, cls_findings in by_class.items():
            cls_confirmed = [f for f in cls_findings if f.status == FindingStatus.CONFIRMED]
            class_summary[cls_name] = {
                "count": len(cls_confirmed),
                "amount": sum(f.amount_inr for f in cls_confirmed),
                "bill_ids": sorted({f.bill_id for f in cls_confirmed}),
            }

        # Group by distributor
        by_distributor: dict[str, float] = {}
        for f in confirmed:
            # Extract distributor from bill_id
            parts = f.bill_id.split("/")
            dist = parts[0] if len(parts) > 1 else "UNK"
            by_distributor[dist] = by_distributor.get(dist, 0) + f.amount_inr

        total_amount = sum(f.amount_inr for f in confirmed)
        recoverable_amount = sum(
            f.amount_inr for f in confirmed
            if f.finding_class != "credit_period_risk"
        )

        return {
            "total_findings": len(findings),
            "confirmed": len(confirmed),
            "conflicts": len(conflicts),
            "cannot_determine": len(cannot_determine),
            "total_amount": round(total_amount, 2),
            "recoverable_amount": round(recoverable_amount, 2),
            "by_class": class_summary,
            "by_distributor": {k: round(v, 2) for k, v in by_distributor.items()},
            "avg_confidence": (
                round(sum(f.confidence for f in confirmed) / len(confirmed), 3)
                if confirmed else 0.0
            ),
        }
