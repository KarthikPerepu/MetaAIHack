"""
Base Detector.

Abstract base class for all six detection classes.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
from typing import Optional
import logging

from harness.config import HarnessConfig
from harness.agents.llama_client import LlamaClient
from harness.knowledge.store import KnowledgeStore
from harness.knowledge.models import Finding, FindingStatus, DistributorCard, Bill

logger = logging.getLogger(__name__)


class BaseDetector(ABC):
    """Base class for all bill discrepancy detectors."""

    # Override in subclass
    CLASS_NAME: str = "unknown"

    def __init__(
        self,
        config: HarnessConfig,
        store: KnowledgeStore,
        llama: LlamaClient,
    ):
        self.config = config
        self.store = store
        self.llama = llama

    @abstractmethod
    def detect(
        self,
        card: DistributorCard,
        bill: Bill,
        as_of: date,
    ) -> list[Finding]:
        """Run detection on a single bill.

        Args:
            card: The distributor's knowledge card.
            bill: The bill to check.
            as_of: The "as of" date for the report.

        Returns:
            List of findings (may be empty if bill is clean for this class).
        """
        raise NotImplementedError

    def detect_all(self, as_of: date) -> list[Finding]:
        """Run detection across all distributors and bills.

        Returns:
            All findings across all bills.
        """
        all_findings: list[Finding] = []

        for card in self.store.list_distributors():
            for bill in sorted(card.bills, key=lambda b: (b.bill_date, b.bill_id)):
                if bill.bill_date > as_of:
                    continue
                try:
                    findings = self.detect(card, bill, as_of)
                    all_findings.extend(findings)
                except Exception as e:
                    logger.error(f"Detection failed for {bill.bill_id}: {e}")

        return all_findings

    def _make_finding(
        self,
        bill: Bill,
        status: FindingStatus,
        amount: float = 0.0,
        evidence: list[str] = None,
        reasoning: str = "",
        confidence: float = 0.0,
        sources: list[dict] = None,
        missing: str = "",
        ask: str = "",
    ) -> Finding:
        """Helper to create a Finding object."""
        # Determine action based on mode
        action = {}
        if status == FindingStatus.CONFIRMED:
            action = {
                "supervised": "draft_whatsapp_reply",
                "autonomous": "send_whatsapp_reply",
            }
        elif status == FindingStatus.CONFLICT:
            action = {"supervised": "escalate_to_owner"}
        elif status == FindingStatus.CANNOT_DETERMINE:
            action = {"supervised": "request_document"}

        return Finding(
            bill_id=bill.bill_id,
            finding_class=self.CLASS_NAME,
            status=status,
            amount_inr=amount,
            evidence=evidence or [],
            reasoning=reasoning,
            confidence=confidence,
            action=action,
            sources=sources or [],
            missing=missing,
            ask=ask,
        )

    def _compute_base_confidence(
        self,
        bill: Bill,
        has_rate_list: bool = True,
        sources_agree: bool = True,
    ) -> float:
        """Compute a base confidence score."""
        confidence = 0.5

        # OCR quality
        if bill.ocr_confidence >= 0.9:
            confidence += 0.15
        elif bill.ocr_confidence >= 0.7:
            confidence += 0.05
        else:
            confidence -= 0.15

        # Rate list available
        if has_rate_list:
            confidence += 0.15
        else:
            confidence -= 0.1

        # Sources agree
        if sources_agree:
            confidence += 0.1
        else:
            confidence -= 0.1

        return max(0.0, min(1.0, confidence))
