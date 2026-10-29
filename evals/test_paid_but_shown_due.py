"""
Evaluation tests for Class 4: Paid But Shown Due.
Verifies matching of verified UPI screenshots against distributor statements.
"""

import pytest
from datetime import date

from harness.config import HarnessConfig
from harness.knowledge.store import KnowledgeStore
from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import (
    DistributorCard, Payment, Statement, StatementEntry, FindingStatus,
)
from harness.detectors.paid_but_shown_due import PaidButShownDueDetector


@pytest.fixture
def test_setup(tmp_path):
    config = HarnessConfig(state_dir=str(tmp_path))
    store = KnowledgeStore(state_dir=str(tmp_path))
    llama = LlamaClient(config.llama)
    detector = PaidButShownDueDetector(config, store, llama)
    card = store.get_or_create_distributor("SP", "Sharma Products")
    return card, detector


def test_paid_bill_still_on_statement_flagged(test_setup):
    """Payment on 5 Oct for ₹5,000 still listed as due on statement dated 10 Oct is flagged."""
    card, detector = test_setup

    payment = Payment(
        payment_id="UPI-4380",
        distributor_code="SP",
        amount=5000.0,
        payment_date=date(2026, 10, 5),
        upi_ref="427918239012",
        source_file="upi_sp_4380.jpg",
        is_verified=True,
    )
    card.payments = [payment]

    statement = Statement(
        distributor_code="SP",
        statement_date=date(2026, 10, 10),
        source_file="statement_sp_10oct.jpg",
        entries=[
            StatementEntry(bill_id="SP/4380", bill_date=date(2026, 10, 2), amount=5000.0, status="due")
        ],
    )
    card.statements = [statement]

    findings = detector.detect_all(as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 1
    assert confirmed[0].bill_id == "SP/4380"
    assert confirmed[0].amount_inr == 5000.0
    assert confirmed[0].confidence >= 0.90


def test_statement_dated_before_payment_not_flagged(test_setup):
    """A statement dated BEFORE the payment date is expected to show due and NOT flagged."""
    card, detector = test_setup

    payment = Payment(
        payment_id="UPI-4380",
        distributor_code="SP",
        amount=5000.0,
        payment_date=date(2026, 10, 8),  # Paid on 8 Oct
        upi_ref="427918239012",
        is_verified=True,
    )
    card.payments = [payment]

    statement = Statement(
        distributor_code="SP",
        statement_date=date(2026, 10, 5),  # Statement from 5 Oct
        entries=[
            StatementEntry(bill_id="SP/4380", bill_date=date(2026, 10, 2), amount=5000.0, status="due")
        ],
    )
    card.statements = [statement]

    findings = detector.detect_all(as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 0, "Statement issued prior to payment should not be flagged"
