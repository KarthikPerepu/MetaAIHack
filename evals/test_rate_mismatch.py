"""
Evaluation tests for Class 1: Rate Mismatch.
Verifies temporal logic (bill date vs rate list effective date) and edge cases.
"""

import pytest
from datetime import date

from harness.config import HarnessConfig
from harness.knowledge.store import KnowledgeStore
from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import (
    DistributorCard, Bill, BillLineItem, RateList, RateEntry, FindingStatus,
)
from harness.detectors.rate_mismatch import RateMismatchDetector


@pytest.fixture
def test_setup(tmp_path):
    config = HarnessConfig(state_dir=str(tmp_path))
    store = KnowledgeStore(state_dir=str(tmp_path))
    llama = LlamaClient(config.llama)
    detector = RateMismatchDetector(config, store, llama)

    # Setup distributor with two temporal rate lists:
    # v1: effective 2026-10-01, rate = ₹410
    # v2: effective 2026-10-03, rate = ₹395
    card = store.get_or_create_distributor("GT", "Ganga Traders")
    rl_v1 = RateList(
        distributor_code="GT",
        version="v1",
        effective_date=date(2026, 10, 1),
        entries=[RateEntry(product="Sunrise Glucose Biscuit", rate=410.0, hsn="1905")],
    )
    rl_v2 = RateList(
        distributor_code="GT",
        version="v2",
        effective_date=date(2026, 10, 3),
        entries=[RateEntry(product="Sunrise Glucose Biscuit", rate=395.0, hsn="1905")],
    )
    card.rate_lists = [rl_v1, rl_v2]
    return card, detector


def test_clean_bill_under_old_rate_list(test_setup):
    """Bill GT/1179 dated 2 Oct charged at ₹410 is CLEAN because v1 was in force."""
    card, detector = test_setup
    bill = Bill(
        bill_id="GT/1179",
        distributor_code="GT",
        bill_date=date(2026, 10, 2),
        items=[BillLineItem(product="Sunrise Glucose Biscuit", quantity=10, rate=410.0, amount=4100.0)],
        grand_total=4100.0,
    )
    card.bills.append(bill)

    findings = detector.detect(card, bill, as_of=date(2026, 10, 15))
    # Must NOT flag GT/1179
    confirmed_findings = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed_findings) == 0, "GT/1179 should be clean because v1 was valid on 2 Oct"


def test_mismatch_bill_under_new_rate_list(test_setup):
    """Bill GT/1187 dated 9 Oct charged at ₹410 is a MISMATCH (agreed ₹395 under v2)."""
    card, detector = test_setup
    bill = Bill(
        bill_id="GT/1187",
        distributor_code="GT",
        bill_date=date(2026, 10, 9),
        items=[BillLineItem(product="Sunrise Glucose Biscuit", quantity=20, rate=410.0, amount=8200.0)],
        grand_total=8200.0,
    )
    card.bills.append(bill)

    findings = detector.detect(card, bill, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 1
    f = confirmed[0]
    assert f.bill_id == "GT/1187"
    assert f.amount_inr == 300.0  # (410 - 395) * 20
    assert f.confidence >= 0.85


def test_missing_rate_list_returns_cannot_determine(tmp_path):
    """If no rate list exists for a distributor, harness must return cannot_determine."""
    config = HarnessConfig(state_dir=str(tmp_path))
    store = KnowledgeStore(state_dir=str(tmp_path))
    llama = LlamaClient(config.llama)
    detector = RateMismatchDetector(config, store, llama)

    card = store.get_or_create_distributor("NEW_DIST", "New Distributor")
    bill = Bill(
        bill_id="ND/001",
        distributor_code="NEW_DIST",
        bill_date=date(2026, 10, 5),
        items=[BillLineItem(product="Item A", quantity=10, rate=100.0, amount=1000.0)],
        grand_total=1000.0,
    )
    card.bills.append(bill)

    findings = detector.detect(card, bill, as_of=date(2026, 10, 15))
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.CANNOT_DETERMINE
    assert "Rate list" in findings[0].missing
