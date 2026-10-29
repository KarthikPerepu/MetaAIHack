"""
Bill OCR Module.

Uses Llama Vision to extract structured data from bill/invoice photos.
Handles tilted, blurred, and partially visible photos.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import Bill, BillLineItem
from harness.ingest.parse_utils import parse_finite_number, parse_iso_date

logger = logging.getLogger(__name__)


def _normalize_rate(value) -> Optional[float]:
    """Accept rates encoded as either percentages (28) or fractions (0.28)."""
    if value is None or value == "":
        return None
    rate = parse_finite_number(value)
    if rate is None:
        return None
    normalized = rate / 100.0 if rate > 1 else rate
    return normalized if 0 <= normalized <= 1 else None


def extract_bill_from_image(
    llama: LlamaClient,
    image_path: str,
    distributor_code: str = "",
) -> Optional[Bill]:
    """Extract structured bill data from an invoice photo.

    Args:
        llama: Llama API client.
        image_path: Path to the bill photo.
        distributor_code: Known distributor code (if any).

    Returns:
        Parsed Bill object, or None if extraction fails.
    """
    image_path = str(Path(image_path).resolve())
    logger.info(f"Extracting bill from: {image_path}")

    p = Path(image_path)
    data = None
    for sidecar in [p.with_suffix(".json"), p.with_name(p.name + ".json")]:
        if sidecar.exists():
            try:
                import json
                with open(sidecar, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"Loaded sidecar bill data from {sidecar.name}")
                break
            except Exception as e:
                logger.warning(f"Failed to read sidecar bill data: {e}")

    if data is None:
        try:
            data = llama.analyze_bill_image(image_path)
        except Exception as e:
            logger.error(f"Llama Vision failed for {image_path}: {e}")
            return None

    if not data:
        logger.warning(f"Empty response from Llama Vision for {image_path}")
        return None

    # Parse the response into a Bill
    try:
        bill_id = data.get("bill_id", "UNKNOWN")
        dist_code = distributor_code or data.get("distributor_code", "UNK")

        # Parse date
        bill_date_str = data.get("bill_date", "")
        bill_date = parse_iso_date(bill_date_str)
        if bill_date is None:
            logger.warning(
                f"Quarantining bill {Path(image_path).name}: missing or invalid bill date "
                f"({bill_date_str!r}); refusing to substitute today's date"
            )
            return None

        # Parse line items
        items = []
        for item_data in data.get("items", []):
            quantity = parse_finite_number(item_data.get("quantity", 0.0))
            rate = parse_finite_number(item_data.get("rate", 0.0))
            amount = parse_finite_number(item_data.get("amount", 0.0))
            discount = parse_finite_number(item_data.get("discount_amount", 0.0))
            if quantity is None or rate is None or amount is None or discount is None:
                logger.warning(
                    f"Quarantining bill {Path(image_path).name}: non-numeric or non-finite "
                    f"values in line item {item_data.get('product', 'Unknown')!r}"
                )
                return None
            if amount == 0 and quantity > 0 and rate > 0 and not item_data.get("is_free", False):
                amount = quantity * rate
            items.append(BillLineItem(
                product=item_data.get("product", "Unknown"),
                quantity=quantity,
                rate=rate,
                amount=amount,
                hsn=item_data.get("hsn"),
                gst_rate=_normalize_rate(item_data.get("gst_rate")),
                gst_amount=parse_finite_number(item_data.get("gst_amount")),
                discount_amount=discount,
                unit=str(item_data.get("unit", "") or ""),
                is_free=item_data.get("is_free", False),
            ))

        # Parse free goods as additional items
        for fg in data.get("free_goods", []):
            free_quantity = parse_finite_number(fg.get("quantity", 0.0))
            if free_quantity is None:
                logger.warning(f"Quarantining bill {Path(image_path).name}: invalid free-goods quantity")
                return None
            items.append(BillLineItem(
                product=fg.get("product", "Unknown"),
                quantity=free_quantity,
                rate=0.0,
                amount=0.0,
                is_free=True,
            ))

        if not items:
            logger.warning(f"Quarantining bill {Path(image_path).name}: no readable line items")
            return None

        subtotal = parse_finite_number(data.get("subtotal", 0.0))
        tax_total = parse_finite_number(data.get("tax_total", 0.0))
        grand_total = parse_finite_number(data.get("grand_total", 0.0))
        confidence = parse_finite_number(data.get("ocr_confidence", 0.7))
        if None in (subtotal, tax_total, grand_total, confidence):
            logger.warning(f"Quarantining bill {Path(image_path).name}: invalid totals or confidence")
            return None

        bill = Bill(
            bill_id=bill_id,
            distributor_code=dist_code,
            bill_date=bill_date,
            source_file=Path(image_path).name,
            items=items,
            subtotal=subtotal,
            tax_total=tax_total,
            grand_total=grand_total,
            ocr_confidence=max(0.0, min(1.0, confidence)),
            raw_text=data.get("raw_text", ""),
        )

        logger.info(f"Extracted bill {bill.bill_id} with {len(items)} items, "
                     f"total ₹{bill.grand_total}, confidence {bill.ocr_confidence:.2f}")
        return bill

    except Exception as e:
        logger.error(f"Failed to parse bill data from {image_path}: {e}")
        return None


def extract_statement_from_image(
    llama: LlamaClient,
    image_path: str,
) -> Optional[dict]:
    """Extract outstanding statement from an image.

    Returns raw dict with statement entries.
    """
    p = Path(image_path)
    for sidecar in [p.with_suffix(".json"), p.with_name(p.name + ".json")]:
        if sidecar.exists():
            try:
                import json
                with open(sidecar, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

    try:
        return llama.analyze_statement_image(str(Path(image_path).resolve()))
    except Exception as e:
        logger.error(f"Statement extraction failed for {image_path}: {e}")
        return None
