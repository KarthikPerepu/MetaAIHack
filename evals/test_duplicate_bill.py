"""
Evaluation tests for Class 2: Duplicate Bill.
Verifies item-level matching, date tolerance, and prevention of false alarms on reorders.
"""

import pytest
from datetime import date

from harness.config import HarnessConfig
from harness.knowledge.store import KnowledgeStore
from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import (
    DistributorCard, Bill, BillLineItem, FindingStatus,
)
from harness.detectors.duplicate_bill import DuplicateBillDetector


@pytest.fixture
def test_setup(tmp_path):
    config = HarnessConfig(state_dir=str(tmp_path))
    store = KnowledgeStore(state_dir=str(tmp_path))
    llama = LlamaClient(config.llama)
    detector = DuplicateBillDetector(config, store, llama)
    card = store.get_or_create_distributor("SP", "Sharma Products")
    return card, detector


def test_duplicate_delivery_detected(test_setup):
    """Two bills with identical items/quantities within 2 days flagged as duplicate."""
    card, detector = test_setup

    bill1 = Bill(
        bill_id="SP/4401",
        distributor_code="SP",
        bill_date=date(2026, 10, 5),
        delivery_date=date(2026, 10, 5),
        items=[
            BillLineItem(product="Fortune Mustard Oil", quantity=10, rate=1450.0, amount=14500.0),
            BillLineItem(product="Aashirvaad Atta", quantity=5, rate=380.0, amount=1900.0),
        ],
        grand_total=16400.0,
    )
    bill2 = Bill(
        bill_id="SP/4412",
        distributor_code="SP",
        bill_date=date(2026, 10, 7),
        delivery_date=date(2026, 10, 6),
        items=[
            BillLineItem(product="Fortune Mustard Oil", quantity=10, rate=1450.0, amount=14500.0),
            BillLineItem(product="Aashirvaad Atta", quantity=5, rate=380.0, amount=1900.0),
        ],
        grand_total=16400.0,
    )
    card.bills = [bill1, bill2]

    findings = detector.detect(card, bill2, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 1
    assert confirmed[0].bill_id == "SP/4412"
    assert confirmed[0].amount_inr == 16400.0


def test_different_items_same_total_not_duplicate(test_setup):
    """Bills with same total amount but completely different items are NOT duplicate."""
    card, detector = test_setup

    bill1 = Bill(
        bill_id="SP/5001",
        distributor_code="SP",
        bill_date=date(2026, 10, 5),
        items=[BillLineItem(product="Premium Ghee 1L", quantity=10, rate=500.0, amount=5000.0)],
        grand_total=5000.0,
    )
    bill2 = Bill(
        bill_id="SP/5002",
        distributor_code="SP",
        bill_date=date(2026, 10, 6),
        items=[BillLineItem(product="Basmati Rice 10kg", quantity=5, rate=1000.0, amount=5000.0)],
        grand_total=5000.0,
    )
    card.bills = [bill1, bill2]

    findings = detector.detect(card, bill2, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 0, "Different items must not be flagged as duplicates"


def test_reorder_weeks_later_not_duplicate(test_setup):
    """Reorder with same items 3 weeks later is a valid reorder, not duplicate."""
    card, detector = test_setup

    bill1 = Bill(
        bill_id="SP/3001",
        distributor_code="SP",
        bill_date=date(2026, 9, 10),
        items=[BillLineItem(product="Fortune Mustard Oil", quantity=10, rate=1450.0, amount=14500.0)],
        grand_total=14500.0,
    )
    bill2 = Bill(
        bill_id="SP/3020",
        distributor_code="SP",
        bill_date=date(2026, 10, 5),
        items=[BillLineItem(product="Fortune Mustard Oil", quantity=10, rate=1450.0, amount=14500.0)],
        grand_total=14500.0,
    )
    card.bills = [bill1, bill2]

    findings = detector.detect(card, bill2, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 0, "Reorder weeks later must not be flagged as duplicate"
