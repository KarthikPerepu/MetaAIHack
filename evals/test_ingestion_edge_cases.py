"""Regression coverage for ambiguous dates, OCR numbers, and API configuration."""

import json
from datetime import date

import pytest

from harness.agents.llama_client import LlamaClient
from harness.config import HarnessConfig, LlamaConfig
from harness.ingest.bill_ocr import extract_bill_from_image
from harness.ingest.parse_utils import parse_finite_number, parse_iso_date
from harness.ingest.pdf_parser import parse_rate_list
from harness.ingest.pipeline import IngestPipeline
from harness.ingest.upi_parser import extract_upi_payment
from harness.knowledge.store import KnowledgeStore


def _sidecar(image_path, data):
    image_path.write_bytes(b"test")
    image_path.with_name(image_path.name + ".json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    return image_path


def test_date_parser_accepts_iso_dates_and_rejects_ambiguous_values():
    assert parse_iso_date("2026-02-28") == date(2026, 2, 28)
    assert parse_iso_date("2026-02-29") is None
    assert parse_iso_date("03/04/2026") is None
    assert parse_iso_date("2026-02-28garbage") is None
    assert parse_iso_date(None) is None


def test_number_parser_handles_ocr_currency_and_rejects_nonfinite_values():
    assert parse_finite_number("₹1,25,000.50") == 125000.50
    assert parse_finite_number("Rs. 1,25,000.50") == 125000.50
    assert parse_finite_number("(1,250.00)") == -1250.00
    assert parse_finite_number("NaN") is None
    assert parse_finite_number("Infinity") is None
    assert parse_finite_number("not a number") is None


def test_bill_without_valid_date_or_items_is_quarantined(tmp_path):
    image_path = _sidecar(tmp_path / "bill.jpg", {
        "bill_id": "B-1", "items": [{"product": "Tea", "quantity": 1, "rate": 10}],
    })
    assert extract_bill_from_image(None, str(image_path)) is None

    _sidecar(tmp_path / "bill.jpg", {
        "bill_id": "B-1", "bill_date": "2026-02-30",
        "items": [{"product": "Tea", "quantity": 1, "rate": 10}],
    })
    assert extract_bill_from_image(None, str(image_path)) is None


def test_bill_ocr_parses_currency_and_rejects_nonfinite_line_amount(tmp_path):
    image_path = _sidecar(tmp_path / "bill.jpg", {
        "bill_id": "B-1", "bill_date": "2026-02-28",
        "items": [{"product": "Tea", "quantity": "2", "rate": "₹1,250", "amount": "₹2,500"}],
    })
    bill = extract_bill_from_image(None, str(image_path))
    assert bill is not None
    assert bill.items[0].rate == 1250
    assert bill.items[0].amount == 2500

    _sidecar(tmp_path / "bill.jpg", {
        "bill_id": "B-1", "bill_date": "2026-02-28",
        "items": [{"product": "Tea", "quantity": 2, "rate": 1250, "amount": "NaN"}],
    })
    assert extract_bill_from_image(None, str(image_path)) is None


def test_upi_missing_date_is_rejected_and_pending_or_missing_status_is_unverified(tmp_path):
    image_path = _sidecar(tmp_path / "upi.jpg", {
        "amount": "₹5,000", "upi_ref": "ABC123", "confidence": 0.99,
    })
    assert extract_upi_payment(None, str(image_path), "D1") is None

    _sidecar(tmp_path / "upi.jpg", {
        "amount": "₹5,000", "payment_date": "2026-02-28", "upi_ref": "ABC123",
        "confidence": 0.99, "status": "PENDING",
    })
    payment = extract_upi_payment(None, str(image_path), "D1")
    assert payment is not None
    assert payment.is_verified is False

    _sidecar(tmp_path / "upi.jpg", {
        "amount": "₹5,000", "payment_date": "2026-02-28", "upi_ref": "ABC123",
        "confidence": 0.99,
    })
    payment = extract_upi_payment(None, str(image_path), "D1")
    assert payment is not None
    assert payment.is_verified is False


@pytest.mark.parametrize("status", ["FAILED", "REVERSED", "REFUNDED", "PROCESSING", "CANCELLED"])
def test_upi_non_success_terminal_states_are_not_verified(tmp_path, status):
    image_path = _sidecar(tmp_path / "upi.jpg", {
        "amount": 5000, "payment_date": "2026-02-28", "upi_ref": "ABC123",
        "confidence": 0.99, "status": status,
    })
    payment = extract_upi_payment(None, str(image_path), "D1")
    assert payment is not None
    assert payment.is_verified is False


def test_statement_rejects_missing_date_and_quarantines_bad_entries(tmp_path):
    store = KnowledgeStore(state_dir=str(tmp_path))
    pipeline = IngestPipeline(HarnessConfig(state_dir=str(tmp_path)), store)

    pipeline._process_statement_data({"entries": []}, "D1", "missing-date.jpg")
    assert store.get_distributor("D1") is None

    pipeline._process_statement_data({
        "statement_date": "2026-03-01",
        "entries": [
            {"bill_id": "good", "bill_date": "2026-02-01", "amount": 100, "status": "due"},
            {"bill_id": "bad-date", "bill_date": "", "amount": 100, "status": "due"},
            {"bill_id": "bad-amount", "bill_date": "2026-02-01", "amount": "Infinity", "status": "due"},
            {"bill_id": "unknown-status", "bill_date": "2026-02-01", "amount": 50},
        ],
    }, "D1", "statement.jpg")
    statement = store.get_distributor("D1").statements[0]
    assert [entry.bill_id for entry in statement.entries] == ["good", "unknown-status"]
    assert statement.entries[1].status == "unknown"


def test_rate_list_without_valid_effective_date_is_not_applied(tmp_path):
    pdf_path = tmp_path / "rates.pdf"
    pdf_path.write_bytes(b"placeholder")
    sidecar = pdf_path.with_name(pdf_path.name + ".json")
    sidecar.write_text(json.dumps({
        "version": "v1", "entries": [{"product": "Tea", "rate": 10}],
    }), encoding="utf-8")
    assert parse_rate_list(None, str(pdf_path), "D1") is None

    sidecar.write_text(json.dumps({
        "version": "v1", "effective_date": "2026-02-30",
        "entries": [{"product": "Tea", "rate": 10}],
    }), encoding="utf-8")
    assert parse_rate_list(None, str(pdf_path), "D1") is None


def test_model_api_key_alias_and_missing_key_message(monkeypatch):
    monkeypatch.delenv("LLAMA_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "test-token")
    assert LlamaConfig.from_env().api_key == "test-token"

    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    client = LlamaClient(LlamaConfig(api_key=""))
    try:
        client.complete("should fail before making a request")
    except RuntimeError as exc:
        assert "LLAMA_API_KEY" in str(exc)
    else:
        raise AssertionError("Missing key should fail with a clear configuration error")
