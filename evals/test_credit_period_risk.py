"""
Evaluation tests for Class 6: Credit Period Risk.
Verifies the 7-day risk window, interest computation, and exclusion of paid bills.
"""

import pytest
from datetime import date

from harness.config import HarnessConfig
from harness.knowledge.store import KnowledgeStore
from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import (
    DistributorCard, Bill, FindingStatus,
)
from harness.detectors.credit_period_risk import CreditPeriodRiskDetector


@pytest.fixture
def test_setup(tmp_path):
    config = HarnessConfig(state_dir=str(tmp_path))
    store = KnowledgeStore(state_dir=str(tmp_path))
    llama = LlamaClient(config.llama)
    detector = CreditPeriodRiskDetector(config, store, llama)

    card = store.get_or_create_distributor("GT", "Ganga Traders")
    card.credit_days = 30
    card.interest_rate = 0.18
    return card, detector


def test_bill_due_within_7_days_flagged(test_setup):
    """Unpaid bill due in 5 days is flagged with internal reminder to owner."""
    card, detector = test_setup

    bill = Bill(
        bill_id="GT/1040",
        distributor_code="GT",
        bill_date=date(2026, 9, 10),  # +30 days = 10 Oct
        grand_total=20000.0,
        is_paid=False,
    )
    card.bills = [bill]

    # As-of 5 Oct: due date 10 Oct is in 5 days (within [5 Oct, 12 Oct])
    findings = detector.detect(card, bill, as_of=date(2026, 10, 5))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 1
    assert confirmed[0].bill_id == "GT/1040"
    assert confirmed[0].action.get("supervised") == "remind_owner"
    assert confirmed[0].amount_inr > 0.0


def test_bill_due_outside_window_not_flagged(test_setup):
    """Bill due in 20 days is NOT at immediate risk and must not be flagged."""
    card, detector = test_setup

    bill = Bill(
        bill_id="GT/1055",
        distributor_code="GT",
        bill_date=date(2026, 9, 25),  # +30 days = 25 Oct (20 days away)
        grand_total=20000.0,
        is_paid=False,
    )
    card.bills = [bill]

    findings = detector.detect(card, bill, as_of=date(2026, 10, 5))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 0, "Bill due outside the 7-day window must not be flagged"


def test_already_paid_bill_not_flagged(test_setup):
    """A bill marked as paid (is_paid=True) is never at credit risk."""
    card, detector = test_setup

    bill = Bill(
        bill_id="GT/1041",
        distributor_code="GT",
        bill_date=date(2026, 9, 10),
        grand_total=20000.0,
        is_paid=True,  # Paid
    )
    card.bills = [bill]

    findings = detector.detect(card, bill, as_of=date(2026, 10, 5))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 0, "Paid bill must never be flagged as credit risk"
