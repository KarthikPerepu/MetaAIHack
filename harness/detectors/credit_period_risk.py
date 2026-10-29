"""
Credit Period Risk Detector.

Flags unpaid bills that will cross their agreed credit period
within the next 7 days from the --as-of date.

KEY RULES:
- Only flag bills where (bill_date + credit_days) falls within
  [as_of_date, as_of_date + 7].
- Already paid bills are NOT at risk.
- Default credit period: 30 days, 18% p.a. interest.
- Amount = bill_amount × (annual_rate / 365) × overdue_days.
"""

from __future__ import annotations
import logging
from datetime import date, timedelta

from harness.detectors.base import BaseDetector
from harness.knowledge.models import (
    Finding, FindingStatus, DistributorCard, Bill,
)

logger = logging.getLogger(__name__)


class CreditPeriodRiskDetector(BaseDetector):
    """Detect bills at risk of crossing credit period."""

    CLASS_NAME = "credit_period_risk"

    def detect(
        self,
        card: DistributorCard,
        bill: Bill,
        as_of: date,
    ) -> list[Finding]:
        findings: list[Finding] = []

        # Skip paid bills
        if bill.is_paid:
            return []

        # Get credit terms
        credit_days = (card.credit_days if card.credit_days is not None
                       else self.config.default_credit_days)
        interest_rate = (card.interest_rate if card.interest_rate is not None
                         else self.config.default_interest_rate)
        risk_window = self.config.credit_risk_window_days

        # Calculate due date
        due_date = bill.bill_date + timedelta(days=credit_days)

        # Check if due date falls within [as_of, as_of + 7]
        window_start = as_of
        window_end = as_of + timedelta(days=risk_window)

        if not (window_start <= due_date <= window_end):
            return []

        # Bill is at risk! Calculate potential interest
        # Interest starts from due_date, so overdue days = as_of - due_date (if positive)
        # But since due_date is in the future (within the window), interest is what WOULD be due
        # We calculate for 1 day overdue as the minimum
        days_until_due = (due_date - as_of).days
        projected_overdue_days = max(1, risk_window - days_until_due)

        bill_amount = bill.grand_total or bill.total_items_value
        daily_rate = interest_rate / 365.0
        interest_amount = bill_amount * daily_rate * projected_overdue_days

        evidence = [
            f"{bill.source_file}: Bill {bill.bill_id} dated {bill.bill_date}, "
            f"₹{bill_amount:.2f}",
            f"Credit terms: {credit_days} days, {interest_rate*100:.1f}% p.a.",
            f"Due date: {due_date} ({days_until_due} days from {as_of})",
        ]

        confidence = self._compute_base_confidence(bill)
        # Higher confidence if closer to due date
        if days_until_due <= 2:
            confidence = min(1.0, confidence + 0.15)
        elif days_until_due <= 5:
            confidence = min(1.0, confidence + 0.05)

        findings.append(self._make_finding(
            bill,
            FindingStatus.CONFIRMED,
            amount=round(interest_amount, 2),
            evidence=evidence,
            reasoning=(
                f"Bill {bill.bill_id} (₹{bill_amount:.2f}) dated {bill.bill_date} "
                f"has a {credit_days}-day credit period. Due date is {due_date}, "
                f"which is {days_until_due} days from now ({as_of}). "
                f"If unpaid, interest of ₹{interest_amount:.2f} would accrue "
                f"at {interest_rate*100:.1f}% p.a."
            ),
            confidence=confidence,
        ))
        # Override action for credit period — it's a reminder, not a claim
        findings[-1].action = {
            "supervised": "remind_owner",
            "autonomous": "remind_owner",
        }

        return findings
