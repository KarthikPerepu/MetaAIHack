"""
Duplicate Bill Detector.

Detects the same delivery billed twice with different bill numbers.

KEY RULES:
- Match by (distributor, items, quantities, delivery date ±2 days)
- Two bills with the same total but different items are NOT duplicates
- A reorder weeks later with a new delivery date is NOT a duplicate
"""

from __future__ import annotations
import logging
from difflib import SequenceMatcher
from datetime import date, timedelta

from harness.detectors.base import BaseDetector
from harness.knowledge.models import (
    Finding, FindingStatus, DistributorCard, Bill,
)

logger = logging.getLogger(__name__)


class DuplicateBillDetector(BaseDetector):
    """Detect bills that are duplicates of each other."""

    CLASS_NAME = "duplicate_bill"

    def detect(
        self,
        card: DistributorCard,
        bill: Bill,
        as_of: date,
    ) -> list[Finding]:
        findings: list[Finding] = []
        tolerance_days = self.config.duplicate_date_tolerance_days

        # Compare this bill against all other bills from the same distributor
        for other_bill in card.bills:
            if other_bill.bill_id == bill.bill_id:
                continue  # Don't compare with self

            # Already found this pair?
            # Only report the LATER bill as the duplicate
            if other_bill.bill_date > bill.bill_date:
                continue  # We'll report from the other bill's perspective
            if other_bill.bill_date == bill.bill_date and other_bill.bill_id > bill.bill_id:
                continue

            # Check if bills are within date tolerance
            date1 = bill.delivery_date or bill.bill_date
            date2 = other_bill.delivery_date or other_bill.bill_date
            date_diff = abs((date1 - date2).days)

            if date_diff > tolerance_days:
                continue  # Too far apart in date

            # Compare items (not just totals!)
            similarity = self._compute_item_similarity(bill, other_bill)

            if similarity >= self.config.duplicate_similarity_threshold:
                amount = bill.grand_total or bill.total_items_value

                confidence = self._compute_base_confidence(bill)
                confidence = min(1.0, confidence + (similarity - 0.85) * 2)

                evidence = [
                    f"{bill.source_file}: Bill {bill.bill_id} dated {bill.bill_date}, "
                    f"total ₹{bill.grand_total}",
                    f"{other_bill.source_file}: Bill {other_bill.bill_id} dated "
                    f"{other_bill.bill_date}, total ₹{other_bill.grand_total}",
                ]

                # Build item comparison
                bill_items_str = ", ".join(
                    f"{i.product}×{int(i.quantity)}" for i in bill.items
                )
                other_items_str = ", ".join(
                    f"{i.product}×{int(i.quantity)}" for i in other_bill.items
                )
                evidence.append(f"Items match: [{bill_items_str}] vs [{other_items_str}]")

                findings.append(self._make_finding(
                    bill,
                    FindingStatus.CONFIRMED,
                    amount=amount,
                    evidence=evidence,
                    reasoning=(
                        f"Bill {bill.bill_id} appears to be a duplicate of "
                        f"{other_bill.bill_id}. Items match with {similarity:.0%} "
                        f"similarity, dates {date_diff} days apart. "
                        f"Full value of repeat: ₹{amount}"
                    ),
                    confidence=confidence,
                ))

        return findings

    def _compute_item_similarity(self, bill1: Bill, bill2: Bill) -> float:
        """Compute item-level similarity between two bills.

        Uses fuzzy matching on product names and exact matching on quantities.
        Returns a similarity score from 0 to 1.
        """
        if not bill1.items or not bill2.items:
            return 0.0

        # Build item signatures. The rate is intentionally excluded: prices may
        # change between duplicate invoices for the same delivery.
        items1 = self._build_item_set(bill1)
        items2 = self._build_item_set(bill2)

        if not items1 or not items2:
            return 0.0

        # Jaccard-style similarity on item signatures
        matched = 0
        used_indices: set[int] = set()

        for sig1 in items1:
            best_match = -1
            best_score = 0.0

            for idx, sig2 in enumerate(items2):
                if idx in used_indices:
                    continue
                score = self._signature_match(sig1, sig2)
                if score > best_score:
                    best_score = score
                    best_match = idx

            if best_score >= 0.8:
                matched += 1
                used_indices.add(best_match)

        total = max(len(items1), len(items2))
        return matched / total if total > 0 else 0.0

    def _build_item_set(self, bill: Bill) -> list[dict]:
        """Build normalized item signatures from a bill."""
        items = []
        for item in bill.items:
            if item.is_free:
                continue
            items.append({
                "product": item.product.lower().strip(),
                "quantity": item.quantity,
                "rate": item.rate,
            })
        return items

    def _signature_match(self, sig1: dict, sig2: dict) -> float:
        """Match two item signatures. Returns 0 to 1."""
        score = 0.0

        # Product name match (fuzzy), but quantities must agree exactly.
        p1, p2 = sig1["product"], sig2["product"]
        if p1 == p2:
            name_score = 1.0
        elif p1 in p2 or p2 in p1:
            name_score = 0.9
        else:
            name_score = SequenceMatcher(None, p1, p2).ratio()

        if abs(sig1["quantity"] - sig2["quantity"]) > 0.01:
            return 0.0
        return name_score
