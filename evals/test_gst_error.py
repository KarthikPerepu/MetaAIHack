"""
Evaluation tests for Class 5: GST Error.
Verifies statutory HSN mapping, tax base calculation, and missing HSN handling.
"""

import pytest
from datetime import date

from harness.config import HarnessConfig
from harness.knowledge.store import KnowledgeStore
from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import (
    DistributorCard, Bill, BillLineItem, FindingStatus, RateList, RateEntry,
)
from harness.detectors.gst_error import GSTErrorDetector


@pytest.fixture
def test_setup(tmp_path):
    config = HarnessConfig(state_dir=str(tmp_path))
    store = KnowledgeStore(state_dir=str(tmp_path))
    llama = LlamaClient(config.llama)
    detector = GSTErrorDetector(config, store, llama)
    card = store.get_or_create_distributor("BC", "Bengal Confectionery")
    return card, detector


def test_wrong_gst_slab_flagged(test_setup):
    """HSN 1704 billed at 28% instead of statutory 18% is flagged for excess tax."""
    card, detector = test_setup

    bill = Bill(
        bill_id="BC/2091",
        distributor_code="BC",
        bill_date=date(2026, 10, 8),
        items=[
            BillLineItem(
                product="Cadbury Dairy Milk",
                quantity=100,
                rate=100.0,
                amount=10000.0,
                hsn="1704",
                gst_rate=0.28,
                gst_amount=2800.0,
            )
        ],
        subtotal=10000.0,
        tax_total=2800.0,
        grand_total=12800.0,
    )
    card.bills = [bill]

    findings = detector.detect(card, bill, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 1
    assert confirmed[0].bill_id == "BC/2091"
    assert confirmed[0].amount_inr == 1000.0  # 2800 - 1800


def test_correct_gst_rate_not_flagged(test_setup):
    """Correct GST rate applied should produce 0 confirmed findings."""
    card, detector = test_setup

    bill = Bill(
        bill_id="BC/2092",
        distributor_code="BC",
        bill_date=date(2026, 10, 8),
        items=[
            BillLineItem(
                product="Cadbury Dairy Milk",
                quantity=100,
                rate=100.0,
                amount=10000.0,
                hsn="1704",
                gst_rate=0.18,
                gst_amount=1800.0,
            )
        ],
        subtotal=10000.0,
        tax_total=1800.0,
        grand_total=11800.0,
    )
    card.bills = [bill]

    findings = detector.detect(card, bill, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 0, "Correctly taxed bill must not be flagged"


def test_missing_hsn_outputs_cannot_determine(test_setup):
    """Item without HSN code cannot be verified and outputs cannot_determine."""
    card, detector = test_setup

    bill = Bill(
        bill_id="BC/2093",
        distributor_code="BC",
        bill_date=date(2026, 10, 8),
        items=[
            BillLineItem(
                product="Uncoded Sweet",
                quantity=50,
                rate=20.0,
                amount=1000.0,
                hsn=None,  # Missing HSN
                gst_rate=0.18,
                gst_amount=180.0,
            )
        ],
        grand_total=1180.0,
    )
    card.bills = [bill]

    findings = detector.detect(card, bill, as_of=date(2026, 10, 15))
    cannot_det = [f for f in findings if f.status == FindingStatus.CANNOT_DETERMINE]
    assert len(cannot_det) == 1
    assert "HSN" in cannot_det[0].missing
