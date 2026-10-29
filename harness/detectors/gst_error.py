"""
GST Error Detector.

Detects wrong GST rates for item HSN codes, or tax calculated on the wrong base.

KEY RULES:
- GST should be calculated on (rate × qty − discount), NOT (rate × qty) if discount applies.
- If rate list doesn't include HSN codes, say cannot_determine.
- Standard rates: 5%, 12%, 18%, 28%.
"""

from __future__ import annotations
import logging
from datetime import date
from typing import Optional

from harness.detectors.base import BaseDetector
from harness.knowledge.models import (
    Finding, FindingStatus, DistributorCard, Bill,
)

logger = logging.getLogger(__name__)

# Standard GST rates by HSN chapter
HSN_GST_RATES = {
    "04": 0.05,    # Dairy
    "0401": 0.05,  # Milk
    "0402": 0.05,  # Concentrated milk
    "0403": 0.05,  # Buttermilk, yogurt
    "0405": 0.05,  # Butter
    "0406": 0.05,  # Cheese
    "1704": 0.18,  # Sugar confectionery
    "1905": 0.18,  # Biscuits (>₹100/kg default)
    "2106": 0.18,  # Food preparations
    "2201": 0.05,  # Water (packaged)
    "2202": 0.28,  # Aerated drinks
    "3401": 0.18,  # Soap
    "3402": 0.18,  # Detergent
}


class GSTErrorDetector(BaseDetector):
    """Detect GST errors on bills."""

    CLASS_NAME = "gst_error"

    def detect(
        self,
        card: DistributorCard,
        bill: Bill,
        as_of: date,
    ) -> list[Finding]:
        findings: list[Finding] = []
        errors: list[tuple[float, list[str], str, float]] = []
        missing_items: list[str] = []

        for item in bill.items:
            if item.is_free:
                continue

            # We need the HSN code to verify GST
            hsn = item.hsn
            active_rl = card.get_active_rate_list(bill.bill_date)
            rate_entry = active_rl.get_rate(item.product) if active_rl else None
            if not hsn:
                # Try to get HSN from rate list
                if rate_entry:
                    hsn = rate_entry.hsn

            if not hsn:
                missing_items.append(f"HSN code for {item.product}")
                continue

            # Look up correct GST rate
            unit = item.unit or (rate_entry.unit if rate_entry else "")
            correct_rate = self._get_gst_rate(hsn, item, unit)
            if correct_rate is None:
                qualifier = "unit price per kg for HSN 1905" if hsn.startswith("1905") else f"GST mapping for HSN {hsn}"
                missing_items.append(f"{qualifier} ({item.product})")
                continue  # Unknown HSN, skip

            if item.gst_rate is None and item.gst_amount is None:
                missing_items.append(f"GST rate and tax amount for {item.product}")
                continue

            if item.gst_rate is None and item.gst_amount is not None:
                taxable_base = max(0.0, item.amount - item.discount_amount)
                expected_tax = taxable_base * correct_rate
                excess = item.gst_amount - expected_tax
                if excess > 1.0:
                    errors.append((excess,
                        [f"{bill.source_file}: {item.product} (HSN {hsn}) GST charged ₹{item.gst_amount:.2f}",
                         f"Expected tax on ₹{taxable_base:.2f} at {correct_rate*100:.0f}%: ₹{expected_tax:.2f}"],
                        f"GST charged for {item.product} exceeds the HSN rate calculation by ₹{excess:.2f}.",
                        self._compute_base_confidence(bill, has_rate_list=True) * 0.75))
                continue

            # Check 1: Is the GST rate correct?
            if item.gst_rate is not None and abs(item.gst_rate - correct_rate) > 0.001:
                # Wrong GST rate!
                taxable_base = max(0.0, item.amount - item.discount_amount)
                correct_tax = taxable_base * correct_rate
                charged_tax = (item.gst_amount if item.gst_amount is not None
                               else item.amount * item.gst_rate)
                excess = charged_tax - correct_tax

                if excess > 0.01:  # Only flag overcharges
                    evidence = [
                        f"{bill.source_file}: {item.product} (HSN {hsn}) "
                        f"taxed at {item.gst_rate*100:.0f}%",
                        f"Correct GST rate for HSN {hsn}: {correct_rate*100:.0f}%",
                    ]

                    errors.append((excess, evidence,
                                   f"{item.product} (HSN {hsn}) charged GST at "
                                   f"{item.gst_rate*100:.0f}% instead of correct "
                                   f"{correct_rate*100:.0f}%.",
                                   self._compute_base_confidence(bill, has_rate_list=True)))

            # Check 2: Is the tax base correct?
            # A wrong slab is already captured above. Comparing its charged tax
            # against that same wrong rate would create a duplicate finding and
            # obscure the statutory overcharge.
            if (item.gst_amount is not None and item.gst_rate is not None
                    and abs(item.gst_rate - correct_rate) <= 0.001):
                taxable_base = max(0.0, item.amount - item.discount_amount)
                expected_tax = taxable_base * correct_rate
                if abs(item.gst_amount - expected_tax) > 1.0:
                    # Tax computed on wrong base
                    excess = item.gst_amount - expected_tax

                    if excess > 0.01:
                        evidence = [
                            f"{bill.source_file}: {item.product} — taxable amount ₹{item.amount}, "
                            f"GST charged ₹{item.gst_amount}",
                            f"Expected GST: ₹{item.amount} × {item.gst_rate*100:.0f}% = ₹{expected_tax:.2f}",
                        ]

                        errors.append((excess, evidence,
                                       f"GST for {item.product} uses the wrong taxable base. "
                                       f"Expected ₹{expected_tax:.2f}, charged ₹{item.gst_amount:.2f}.",
                                       self._compute_base_confidence(bill) * 0.9))

        if errors:
            evidence = [line for _, lines, _, _ in errors for line in lines]
            reasons = [reason for _, _, reason, _ in errors]
            findings.append(self._make_finding(
                bill,
                FindingStatus.CONFIRMED,
                amount=round(sum(amount for amount, _, _, _ in errors), 2),
                evidence=evidence,
                reasoning=" ".join(reasons) + f" Total excess tax: ₹{sum(x[0] for x in errors):.2f}.",
                confidence=min(conf for _, _, _, conf in errors),
            ))

        if missing_items:
            findings.append(self._make_finding(
                bill,
                FindingStatus.CANNOT_DETERMINE,
                missing="; ".join(dict.fromkeys(missing_items)),
                ask=f"distributor ({card.name}) — request a complete tax invoice and a rate list with HSN codes / GST mapping",
                confidence=0.0,
            ))

        return findings

    def _get_gst_rate(self, hsn: str, item, unit: str = "") -> Optional[float]:
        """Look up the correct GST rate for an HSN code."""
        hsn = hsn.strip()

        # Try exact match first, then prefix matches
        for prefix_len in [4, 3, 2]:
            prefix = hsn[:prefix_len]
            if prefix in HSN_GST_RATES:
                rate = HSN_GST_RATES[prefix]

                # Special case: biscuits (1905) — 5% if ≤₹100/kg, 18% if >₹100/kg
                if prefix == "1905":
                    unit = str(unit or getattr(item, "unit", "") or "").casefold()
                    if unit in ("kg", "kilogram", "kilograms", "per kg", "₹/kg", "rs/kg") and item.rate is not None:
                        if item.rate <= 100:
                            return 0.05
                        return 0.18
                    # A carton/packet rate cannot establish the per-kg threshold.
                    return None

                return rate

        # Also check config for additional rates
        for prefix, rate in self.config.gst_rates.items():
            if hsn.startswith(prefix):
                return rate

        logger.debug(f"Unknown HSN code: {hsn}")
        return None
