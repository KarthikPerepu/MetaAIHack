"""
Paid But Shown Due Detector.

Detects UPI payments already made but still shown as outstanding
on the distributor's next statement.
"""

from __future__ import annotations
import logging
from datetime import date

from harness.detectors.base import BaseDetector
from harness.knowledge.models import (
    Finding, FindingStatus, DistributorCard, Bill,
)

logger = logging.getLogger(__name__)


class PaidButShownDueDetector(BaseDetector):
    """Detect payments that are still shown as outstanding."""

    CLASS_NAME = "paid_but_shown_due"

    def detect(
        self,
        card: DistributorCard,
        bill: Bill,
        as_of: date,
    ) -> list[Finding]:
        findings: list[Finding] = []

        # Need statements to check against
        if not card.statements:
            return []  # No statement to check

        # Find payments for this bill
        bill_payments = [
            p for p in card.payments
            if p.matched_bill_id == bill.bill_id and p.is_verified
        ]

        if not bill_payments:
            return []  # No verified payment for this bill

        # Check if the bill appears as "due" on any statement
        # (that is dated AFTER the payment)
        for payment in bill_payments:
            for statement in card.statements:
                if statement.statement_date < payment.payment_date or statement.statement_date > as_of:
                    continue  # Statement is before payment, expected to show due

                for entry in statement.entries:
                    if entry.status != "due":
                        continue

                    # Match by bill ID
                    bill_id_match = (
                        entry.bill_id == bill.bill_id or
                        entry.bill_id.replace("/", "-") == bill.bill_id.replace("/", "-")
                    )

                    # Match by amount (approximate)
                    amount_match = abs(entry.amount - payment.amount) < max(1.0, payment.amount * 0.02)

                    if bill_id_match or (amount_match and self._date_close(entry.bill_date, bill.bill_date)):
                        confidence = self._compute_base_confidence(bill)
                        if bill_id_match:
                            confidence = min(1.0, confidence + 0.15)
                        if payment.upi_ref:
                            confidence = min(1.0, confidence + 0.1)

                        evidence = [
                            f"{payment.source_file}: UPI payment ₹{payment.amount} "
                            f"on {payment.payment_date} (ref: {payment.upi_ref})",
                            f"{statement.source_file}: Statement dated {statement.statement_date} "
                            f"shows bill {entry.bill_id} as due for ₹{entry.amount}",
                        ]

                        findings.append(self._make_finding(
                            bill,
                            FindingStatus.CONFIRMED,
                            amount=payment.amount,
                            evidence=evidence,
                            reasoning=(
                                f"Payment of ₹{payment.amount} made on {payment.payment_date} "
                                f"(UPI ref: {payment.upi_ref}) for bill {bill.bill_id}. "
                                f"However, statement from {statement.statement_date} still shows "
                                f"₹{entry.amount} as outstanding."
                            ),
                            confidence=confidence,
                        ))

        return findings

    def _date_close(self, date1: date, date2: date, tolerance_days: int = 3) -> bool:
        """Check if two dates are close enough to be the same bill."""
        return abs((date1 - date2).days) <= tolerance_days

    def detect_all(self, as_of: date) -> list[Finding]:
        """Override to also check unmatched payments against statements."""
        findings = super().detect_all(as_of)

        # Also check for payments not matched to any bill but shown as due
        for card in self.store.list_distributors():
            for payment in card.payments:
                if payment.matched_bill_id or not payment.is_verified:
                    continue  # Already matched or unverified

                for statement in card.statements:
                    if statement.statement_date < payment.payment_date or statement.statement_date > as_of:
                        continue

                    for entry in statement.entries:
                        if entry.status != "due":
                            continue

                        if abs(entry.amount - payment.amount) < max(1.0, payment.amount * 0.02):
                            # Unmatched payment, amount matches a due entry
                            dummy_bill = Bill(
                                bill_id=entry.bill_id,
                                distributor_code=card.code,
                                bill_date=entry.bill_date,
                                source_file=statement.source_file,
                            )

                            evidence = [
                                f"{payment.source_file}: UPI Ref {payment.upi_ref or 'Verified'}, ₹{payment.amount} paid on {payment.payment_date}",
                                f"{statement.source_file}: Statement shows bill {entry.bill_id} still due ₹{entry.amount}",
                            ]

                            findings.append(self._make_finding(
                                dummy_bill,
                                FindingStatus.CONFIRMED,
                                amount=payment.amount,
                                evidence=evidence,
                                reasoning=(
                                    f"Payment of ₹{payment.amount} on {payment.payment_date} (Ref: {payment.upi_ref}) "
                                    f"was made, but statement dated {statement.statement_date} lists bill {entry.bill_id} as due."
                                ),
                                confidence=0.95 if payment.upi_ref else 0.85,
                            ))

        return findings
