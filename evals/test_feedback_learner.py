"""
Evaluation tests for Feedback Learning.
Verifies that owner feedback updates distributor terms, rates, schemes, and payments.
"""

import pytest
from datetime import date

from harness.config import HarnessConfig
from harness.knowledge.store import KnowledgeStore
from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import DistributorCard, Bill, BillLineItem
from harness.feedback import FeedbackLearner


@pytest.fixture
def test_setup(tmp_path):
    config = HarnessConfig(state_dir=str(tmp_path))
    store = KnowledgeStore(state_dir=str(tmp_path))
    llama = LlamaClient(config.llama)
    learner = FeedbackLearner(config, store, llama)

    card = store.get_or_create_distributor("SP", "Sharma Products")
    card.credit_days = 30
    bill = Bill(
        bill_id="SP/4450",
        distributor_code="SP",
        bill_date=date(2026, 10, 1),
        grand_total=5000.0,
        is_paid=False,
    )
    card.bills.append(bill)
    store.save()
    return card, store, learner


def test_feedback_updates_credit_terms(test_setup):
    """Owner feedback updating credit days to 45 persists in knowledge store."""
    card, store, learner = test_setup

    feedback = {
        "distributor": "SP",
        "credit_days": 45,
        "interest_rate": 0.15,
        "comment": "Sharma Products agreed 45 days credit",
    }
    learner.process_feedback(feedback)

    reloaded_card = store.get_distributor("SP")
    assert reloaded_card.credit_days == 45
    assert reloaded_card.interest_rate == 0.15


def test_feedback_marks_bill_paid(test_setup):
    """Owner feedback confirming a cash payment marks the bill as paid."""
    card, store, learner = test_setup

    feedback = {
        "bill_id": "SP/4450",
        "feedback": "paid",
        "payment_ref": "cash_paid_at_store",
    }
    learner.process_feedback(feedback)

    reloaded_card = store.get_distributor("SP")
    bill = reloaded_card.get_bill("SP/4450")
    assert bill is not None
    assert bill.is_paid is True
    assert bill.payment_ref == "cash_paid_at_store"
