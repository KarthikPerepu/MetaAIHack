"""
Evaluation tests for Class 3: Scheme Not Given.
Verifies one-time vs permanent schemes, free goods valuation, and authority.
"""

import pytest
from datetime import date

from harness.config import HarnessConfig
from harness.knowledge.store import KnowledgeStore
from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import (
    DistributorCard, Bill, BillLineItem, Scheme, SchemeType, SourceType,
    RateList, RateEntry, FindingStatus,
)
from harness.detectors.scheme_not_given import SchemeNotGivenDetector


@pytest.fixture
def test_setup(tmp_path):
    config = HarnessConfig(state_dir=str(tmp_path))
    store = KnowledgeStore(state_dir=str(tmp_path))
    llama = LlamaClient(config.llama)
    detector = SchemeNotGivenDetector(config, store, llama)

    card = store.get_or_create_distributor("GT", "Ganga Traders")
    rl = RateList(
        distributor_code="GT",
        version="v2",
        effective_date=date(2026, 10, 3),
        entries=[RateEntry(product="Sunrise Glucose Biscuit", rate=395.0, hsn="1905")],
    )
    card.rate_lists = [rl]
    return card, detector


def test_one_time_scheme_detected(test_setup):
    """Agreed scheme 'Buy 20 get 2 free' missing on bill flags ₹790 (2 x ₹395)."""
    card, detector = test_setup

    scheme = Scheme(
        scheme_id="SCH-1",
        distributor_code="GT",
        product="Sunrise Glucose Biscuit",
        scheme_type=SchemeType.ONE_TIME,
        buy_qty=20,
        free_qty=2,
        agreed_by="Ramesh",
        agreed_date=date(2026, 10, 5),
        source_type=SourceType.VOICE_NOTE,
        source_ref="voice_note_ramesh_5oct.opus",
    )
    card.schemes = [scheme]

    bill = Bill(
        bill_id="GT/1187",
        distributor_code="GT",
        bill_date=date(2026, 10, 9),
        items=[BillLineItem(product="Sunrise Glucose Biscuit", quantity=20, rate=410.0, amount=8200.0)],
        grand_total=8200.0,
    )
    card.bills = [bill]

    findings = detector.detect(card, bill, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 1
    assert confirmed[0].bill_id == "GT/1187"
    assert confirmed[0].amount_inr == 790.0  # 2 x 395


def test_scheme_already_given_on_bill_not_flagged(test_setup):
    """If bill includes the free goods line item, no discrepancy should be raised."""
    card, detector = test_setup

    scheme = Scheme(
        scheme_id="SCH-2",
        distributor_code="GT",
        product="Sunrise Glucose Biscuit",
        scheme_type=SchemeType.ONE_TIME,
        buy_qty=20,
        free_qty=2,
        agreed_by="Ramesh",
        agreed_date=date(2026, 10, 5),
        source_type=SourceType.VOICE_NOTE,
    )
    card.schemes = [scheme]

    bill = Bill(
        bill_id="GT/1190",
        distributor_code="GT",
        bill_date=date(2026, 10, 9),
        items=[
            BillLineItem(product="Sunrise Glucose Biscuit", quantity=20, rate=395.0, amount=7900.0),
            BillLineItem(product="Sunrise Glucose Biscuit", quantity=2, rate=0.0, amount=0.0, is_free=True),
        ],
        grand_total=7900.0,
    )
    card.bills = [bill]

    findings = detector.detect(card, bill, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 0, "Bill with free goods line must not be flagged"


def test_one_time_scheme_does_not_apply_to_subsequent_orders(test_setup):
    """A one-time scheme marked as used does NOT apply to a subsequent order."""
    card, detector = test_setup

    scheme = Scheme(
        scheme_id="SCH-3",
        distributor_code="GT",
        product="Sunrise Glucose Biscuit",
        scheme_type=SchemeType.ONE_TIME,
        buy_qty=20,
        free_qty=2,
        is_used=True,  # Already used on GT/1187
    )
    card.schemes = [scheme]

    next_bill = Bill(
        bill_id="GT/1205",
        distributor_code="GT",
        bill_date=date(2026, 10, 14),
        items=[BillLineItem(product="Sunrise Glucose Biscuit", quantity=20, rate=395.0, amount=7900.0)],
        grand_total=7900.0,
    )
    card.bills = [next_bill]

    findings = detector.detect(card, next_bill, as_of=date(2026, 10, 15))
    confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
    assert len(confirmed) == 0, "Used one-time scheme must not apply to future bills (Capping Mistake prevention)"
