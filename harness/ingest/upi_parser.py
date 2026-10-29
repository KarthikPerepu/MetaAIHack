"""
UPI Screenshot Parser.

Extracts payment information from UPI payment screenshots.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import Payment
from harness.ingest.parse_utils import parse_finite_number, parse_iso_date

logger = logging.getLogger(__name__)


def extract_upi_payment(
    llama: LlamaClient,
    image_path: str,
    distributor_code: str = "",
) -> Optional[Payment]:
    """Extract UPI payment details from a screenshot.

    Args:
        llama: Llama API client.
        image_path: Path to the UPI screenshot.
        distributor_code: Known distributor code.

    Returns:
        Parsed Payment object, or None.
    """
    image_path = str(Path(image_path).resolve())
    logger.info(f"Extracting UPI payment from: {image_path}")

    p = Path(image_path)
    data = None
    for sidecar in [p.with_suffix(".json"), p.with_name(p.name + ".json")]:
        if sidecar.exists():
            try:
                import json
                with open(sidecar, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"Loaded sidecar UPI data from {sidecar.name}")
                break
            except Exception as e:
                logger.warning(f"Failed to read sidecar UPI data: {e}")

    if data is None:
        try:
            data = llama.analyze_upi_screenshot(image_path)
        except Exception as e:
            logger.error(f"UPI extraction failed for {image_path}: {e}")
            return None

    if not data:
        return None

    try:
        # Parse date
        pay_date_str = data.get("payment_date", "")
        pay_date = parse_iso_date(pay_date_str)
        if pay_date is None:
            logger.warning(
                f"Ignoring UPI image {Path(image_path).name}: missing or invalid payment date "
                f"({pay_date_str!r}); refusing to substitute today's date"
            )
            return None

        status = str(data.get("status", "")).strip().lower()
        success = {"success", "successful", "paid", "completed", "credited"}
        reference = str(data.get("upi_ref", "") or "").strip()
        # OCR confidence and a UPI reference do not prove transaction success.
        # Only an explicitly extracted success state can be treated as paid.
        verified = status in success

        amount = parse_finite_number(data.get("amount"))
        if amount is None or amount <= 0:
            logger.warning(f"Ignoring UPI image without a positive payment amount: {Path(image_path).name}")
            return None

        payment = Payment(
            payment_id=f"UPI-{Path(image_path).stem}",
            distributor_code=distributor_code,
            amount=amount,
            payment_date=pay_date,
            upi_ref=reference,
            source_file=Path(image_path).name,
            is_verified=verified,
        )

        logger.info(f"Extracted UPI payment: ₹{payment.amount} on {payment.payment_date} "
                     f"ref={payment.upi_ref}")
        return payment

    except Exception as e:
        logger.error(f"Failed to parse UPI data from {image_path}: {e}")
        return None
