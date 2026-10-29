"""
Rate Mismatch Detector.

Detects items billed above the rate list in force on the bill date.

CRITICAL TEMPORAL LOGIC:
- A rate list has an effective date.
- If a bill is dated BEFORE the new rate list's effective date,
  the OLD rate list governs.
- Always check: which rate list was in force on the bill date?
"""

from __future__ import annotations
import logging
from datetime import date

from harness.detectors.base import BaseDetector
from harness.knowledge.models import (
    Finding, FindingStatus, DistributorCard, Bill, RateEntry,
)

logger = logging.getLogger(__name__)


class RateMismatchDetector(BaseDetector):
    """Detect rate mismatches between bills and rate lists."""

    CLASS_NAME = "rate_mismatch"

    def detect(
        self,
        card: DistributorCard,
        bill: Bill,
        as_of: date,
    ) -> list[Finding]:
        findings: list[Finding] = []

        # Get the rate list in force ON THE BILL DATE (not today!)
        active_rl = card.get_active_rate_list(bill.bill_date)

        if active_rl is None:
            # No rate list → cannot determine
            return [self._make_finding(
                bill,
                FindingStatus.CANNOT_DETERMINE,
                missing=f"Rate list for {card.name} covering {bill.bill_date}",
                ask=f"distributor ({card.name}) — request rate list",
                confidence=0.0,
            )]

        # Check each line item
        for item in bill.items:
            if item.is_free:
                continue  # Free items don't have rate mismatches

            active_lists = card.get_rate_lists_on(bill.bill_date)
            entries = [(rl, rl.get_rate(item.product)) for rl in active_lists]
            entries = [(rl, entry) for rl, entry in entries if entry is not None]
            distinct_rates = {round(entry.rate, 2) for _, entry in entries}
            if len(distinct_rates) > 1:
                findings.append(self._make_finding(
                    bill,
                    FindingStatus.CONFLICT,
                    sources=[
                        {"source": rl.source_file or rl.version, "value": f"₹{entry.rate:.2f}"}
                        for rl, entry in entries
                    ],
                    reasoning=(f"Rate lists with the same effective date "
                               f"({active_rl.effective_date}) disagree on {item.product}."),
                    confidence=0.5,
                ))
                continue

            rate_entry = next((entry for _, entry in entries), None)
            rate_source = next((rl for rl, entry in entries if entry is not None), active_rl)
            if rate_entry is None:
                findings.append(self._make_finding(
                    bill,
                    FindingStatus.CANNOT_DETERMINE,
                    missing=f"Rate for {item.product} in the rate list covering {bill.bill_date}",
                    ask=f"distributor ({card.name}) — request the rate for {item.product}",
                    confidence=0.0,
                ))
                continue

            agreed_rate = rate_entry.rate
            billed_rate = item.rate

            applicable_quotes = [q for q in card.rate_quotes
                if q.agreed_date <= bill.bill_date
                and q.agreed_date >= active_rl.effective_date
                and (q.product.casefold() == item.product.casefold()
                     or q.product.casefold() in item.product.casefold()
                     or item.product.casefold() in q.product.casefold())]
            if applicable_quotes:
                newest_quote_date = max(q.agreed_date for q in applicable_quotes)
                newest_quotes = sorted(
                    (q for q in applicable_quotes if q.agreed_date == newest_quote_date),
                    key=lambda q: (q.source_ref, q.rate, q.agreed_by),
                )
                quote_rates = {round(q.rate, 2) for q in newest_quotes}
                if len(quote_rates) > 1 or any(abs(q.rate - agreed_rate) > 0.01 for q in newest_quotes):
                    sources = [{
                        "source": rate_source.source_file or rate_source.version,
                        "value": f"₹{agreed_rate:.2f}",
                    }]
                    sources.extend({
                        "source": q.source_ref,
                        "value": f"₹{q.rate:.2f} (quoted by {q.agreed_by} on {q.agreed_date})",
                    } for q in newest_quotes)
                    findings.append(self._make_finding(
                        bill,
                        FindingStatus.CONFLICT,
                        sources=sources,
                        reasoning=(f"The signed rate list and authorized salesman quote disagree "
                                   f"for {item.product} on {bill.bill_date}; owner decision required."),
                        confidence=0.5,
                    ))
                    continue

            # Check for mismatch
            if abs(billed_rate - agreed_rate) < 0.01:
                continue  # Rates match

            if billed_rate > agreed_rate:
                # Overcharged!
                diff = billed_rate - agreed_rate
                amount = diff * item.quantity

                confidence = self._compute_base_confidence(
                    bill, has_rate_list=True, sources_agree=True,
                )
                # Boost confidence if rate list is signed/official
                confidence = min(1.0, confidence + 0.1)

                evidence = [
                    f"{rate_source.source_file}: {item.product} | {agreed_rate}",
                    f"{bill.source_file}: {item.product} {int(item.quantity)} x {billed_rate}",
                ]

                findings.append(self._make_finding(
                    bill,
                    FindingStatus.CONFIRMED,
                    amount=amount,
                    evidence=evidence,
                    reasoning=(
                        f"Rate list {rate_source.version} effective {rate_source.effective_date} "
                        f"shows ₹{agreed_rate} for {item.product}. "
                        f"Bill dated {bill.bill_date} charges ₹{billed_rate}. "
                        f"Difference: ₹{diff} × {int(item.quantity)} = ₹{amount}"
                    ),
                    confidence=confidence,
                ))

            elif billed_rate < agreed_rate:
                # Undercharged — unusual, log but don't flag as issue
                logger.info(f"Bill {bill.bill_id} has lower rate for {item.product}: "
                           f"₹{billed_rate} vs ₹{agreed_rate} (in shop's favor)")

        return findings
