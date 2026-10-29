"""
Scheme Not Given Detector.

Detects free-goods or discount schemes agreed in chat/voice but missing on the bill.

CRITICAL RULES:
- A scheme is ONE-TIME by default unless explicitly stated as permanent.
- "20 carton lo, 2 free" applies to the NEXT qualifying order only.
- "Hamesha 10+1" is permanent.
- Treating a one-time scheme as permanent is a CAPPING MISTAKE.
- Value = free_qty × rate (from rate list in force on bill date).
"""

from __future__ import annotations
import logging
from datetime import date

from harness.detectors.base import BaseDetector
from harness.knowledge.models import (
    Finding, FindingStatus, DistributorCard, Bill, Scheme, SchemeType,
    SourceType, SOURCE_AUTHORITY,
)

logger = logging.getLogger(__name__)


class SchemeNotGivenDetector(BaseDetector):
    """Detect schemes agreed but not reflected on bills."""

    CLASS_NAME = "scheme_not_given"

    def detect(
        self,
        card: DistributorCard,
        bill: Bill,
        as_of: date,
    ) -> list[Finding]:
        findings: list[Finding] = []
        handled_schemes: set[str] = set()

        for item in bill.items:
            if item.is_free:
                continue  # This item IS a free good

            # Find applicable schemes for this product
            schemes = card.get_applicable_schemes(item.product, bill.bill_date)

            for scheme in schemes:
                if scheme.scheme_id in handled_schemes:
                    continue
                if scheme.product.casefold() in ("", "general", "all", "any"):
                    eligible_items = [candidate for candidate in bill.items
                        if not candidate.is_free
                        and (not scheme.buy_qty or candidate.quantity >= scheme.buy_qty)]
                    eligible_products = {candidate.product.casefold() for candidate in eligible_items}
                    if len(eligible_products) > 1:
                        handled_schemes.add(scheme.scheme_id)
                        findings.append(self._make_finding(
                            bill,
                            FindingStatus.CANNOT_DETERMINE,
                            missing=f"Product covered by the general scheme from {scheme.source_ref}",
                            ask=f"{scheme.agreed_by or 'owner'} — confirm which product the scheme covers",
                            confidence=0.0,
                        ))
                        continue
                    if eligible_items and item not in eligible_items:
                        continue
                # Check if this scheme should apply to this bill
                if not self._scheme_applies(card, scheme, bill, item):
                    continue
                handled_schemes.add(scheme.scheme_id)

                # Check if the scheme is already reflected on the bill
                if self._scheme_is_on_bill(scheme, bill, item):
                    continue

                # Scheme is NOT on the bill — this is a finding!
                finding = self._compute_scheme_finding(card, bill, item, scheme)
                if finding:
                    findings.append(finding)

        return findings

    def _scheme_applies(
        self, card: DistributorCard, scheme: Scheme, bill: Bill, item,
    ) -> bool:
        """Check if a scheme applies to a specific bill item."""
        # Check quantity threshold
        if scheme.buy_qty and item.quantity < scheme.buy_qty:
            return False  # Not enough quantity to trigger scheme

        # Check if scheme was agreed BEFORE the bill date
        if scheme.agreed_date and scheme.agreed_date > bill.bill_date:
            return False  # Scheme agreed after this bill

        # One-time scheme already used?
        if scheme.scheme_type == SchemeType.ONE_TIME and scheme.is_used:
            return False

        if scheme.scheme_type == SchemeType.ONE_TIME:
            # Consume the offer on the earliest qualifying order, derived from
            # the ledger instead of mutating state during report generation.
            for previous in card.bills:
                if (previous.bill_id == bill.bill_id or previous.bill_date > bill.bill_date
                        or (previous.bill_date == bill.bill_date and previous.bill_id >= bill.bill_id)):
                    continue
                if scheme.agreed_date and previous.bill_date < scheme.agreed_date:
                    continue
                for prior_item in previous.items:
                    product_matches = (
                        prior_item.product.casefold() == item.product.casefold()
                        or prior_item.product.casefold() in scheme.product.casefold()
                        or scheme.product.casefold() in prior_item.product.casefold()
                        or scheme.product.casefold() in ("all", "general", "any", "")
                    )
                    if (product_matches and not prior_item.is_free
                            and (not scheme.buy_qty or prior_item.quantity >= scheme.buy_qty)):
                        return False

        return True

    def _scheme_is_on_bill(
        self, scheme: Scheme, bill: Bill, trigger_item,
    ) -> bool:
        """Check if a scheme's benefits are already on the bill."""
        # Look for free goods lines
        if scheme.free_qty:
            return self._free_quantity_on_bill(scheme, bill, trigger_item) + 0.01 >= scheme.free_qty

        # Look for discount lines
        if scheme.discount_pct:
            expected_discount = trigger_item.amount * (scheme.discount_pct / 100.0)
            if trigger_item.discount_amount + 0.01 >= expected_discount:
                return True

        return False

    def _free_quantity_on_bill(self, scheme: Scheme, bill: Bill, trigger_item) -> float:
        """Sum matching free lines so split/partial free quantities reconcile correctly."""
        total = 0.0
        for free_item in bill.items:
            if not free_item.is_free:
                continue
            name = free_item.product.casefold()
            product_match = (
                name in scheme.product.casefold()
                or scheme.product.casefold() in name
                or name in trigger_item.product.casefold()
            )
            if product_match:
                total += free_item.quantity
        return total

    def _compute_scheme_finding(
        self,
        card: DistributorCard,
        bill: Bill,
        item,
        scheme: Scheme,
    ) -> Finding | None:
        """Compute the finding for a missing scheme."""
        # Get the rate from rate list in force on bill date
        active_rl = card.get_active_rate_list(bill.bill_date)

        if scheme.free_qty:
            if not active_rl:
                return self._make_finding(
                    bill,
                    FindingStatus.CANNOT_DETERMINE,
                    missing=f"Rate list for {card.name} covering {bill.bill_date} to value the free goods",
                    ask=f"distributor ({card.name}) — request the rate list in force on {bill.bill_date}",
                    confidence=0.0,
                )
            rate_entry = active_rl.get_rate(item.product)
            if rate_entry is None:
                return self._make_finding(
                    bill,
                    FindingStatus.CANNOT_DETERMINE,
                    missing=f"Rate for {item.product} in the rate list covering {bill.bill_date}",
                    ask=f"distributor ({card.name}) — request the product rate",
                    confidence=0.0,
                )
            rate = rate_entry.rate

            given_qty = self._free_quantity_on_bill(scheme, bill, item)
            missing_qty = max(0.0, scheme.free_qty - given_qty)
            amount = missing_qty * rate
            if amount <= 0.01:
                return None

            evidence = [
                f"{scheme.source_ref}: {scheme.raw_text or scheme.agreed_by + ' agreed scheme'}",
                f"{bill.source_file}: {given_qty:g} of {scheme.free_qty:g} free units shown for {item.product}",
            ]

            if active_rl:
                evidence.append(
                    f"{active_rl.source_file}: {item.product} rate ₹{rate}"
                )

            # Check for conflicts (e.g., salesman says one thing, rate list another)
            conflicting_schemes = self._conflicting_schemes(card, scheme, bill.bill_date)
            if conflicting_schemes:
                return self._make_finding(
                    bill,
                    FindingStatus.CONFLICT,
                    sources=[
                        {"source": scheme.source_ref, "value": f"Buy {scheme.buy_qty}, get {scheme.free_qty} free"},
                    ] + [
                        {"source": other.source_ref,
                         "value": f"Buy {other.buy_qty}, get {other.free_qty} free"}
                        for other in conflicting_schemes
                    ],
                    reasoning=f"Conflicting information about scheme for {item.product}",
                    confidence=0.5,
                )

            # Compute confidence
            confidence = self._compute_base_confidence(bill, has_rate_list=active_rl is not None)
            # Adjust by source authority
            source_trust = SOURCE_AUTHORITY.get(scheme.source_type, 0.5)
            confidence = confidence * 0.7 + source_trust * 0.3

            # Lower confidence for voice-note-only schemes without rate list backing
            if scheme.source_type == SourceType.VOICE_NOTE and not active_rl:
                confidence *= 0.7

            return self._make_finding(
                bill,
                FindingStatus.CONFIRMED,
                amount=amount,
                evidence=evidence,
                reasoning=(
                    f"Scheme agreed by {scheme.agreed_by} on {scheme.agreed_date}: "
                    f"Buy {scheme.buy_qty}, get {scheme.free_qty} free for {item.product}. "
                    f"Bill {bill.bill_id} has {int(item.quantity)} {item.product}; "
                    f"{missing_qty:g} of {scheme.free_qty:g} free units are missing. "
                    f"Missing value: {missing_qty:g} × ₹{rate} = ₹{amount}"
                ),
                confidence=confidence,
            )

        elif scheme.discount_pct:
            amount = item.amount * (scheme.discount_pct / 100.0)
            amount = max(0.0, amount - item.discount_amount)
            if amount <= 0.01:
                return None

            evidence = [
                f"{scheme.source_ref}: {scheme.discount_pct}% discount agreed",
                f"{bill.source_file}: No discount applied on {item.product}",
            ]

            confidence = self._compute_base_confidence(bill)
            source_trust = SOURCE_AUTHORITY.get(scheme.source_type, 0.5)
            confidence = confidence * 0.7 + source_trust * 0.3

            return self._make_finding(
                bill,
                FindingStatus.CONFIRMED,
                amount=amount,
                evidence=evidence,
                reasoning=(
                    f"Discount scheme of {scheme.discount_pct}% agreed by "
                    f"{scheme.agreed_by}. Not reflected on bill {bill.bill_id}. "
                    f"Missing discount: ₹{amount}"
                ),
                confidence=confidence,
            )

        return None

    def _conflicting_schemes(
        self, card: DistributorCard, scheme: Scheme, on_date: date,
    ) -> list[Scheme]:
        """Check if there are conflicting scheme offers."""
        related = [
            s for s in card.schemes
            if (s.scheme_id != scheme.scheme_id and
                s.is_active and
                (s.agreed_date is None or s.agreed_date <= on_date) and
                s.product.lower() == scheme.product.lower() and
                not s.is_used)
        ]

        return [other for other in related
                if other.free_qty != scheme.free_qty
                or other.discount_pct != scheme.discount_pct]
