"""
PDF Rate List Parser.

Extracts structured rate information from distributor PDF rate lists.
Uses pdfplumber for table extraction and Llama for interpretation.
"""

from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Optional

from harness.agents.llama_client import LlamaClient
from harness.knowledge.models import RateList, RateEntry
from harness.ingest.safety import SafetyChecker
from harness.ingest.parse_utils import parse_finite_number, parse_iso_date

logger = logging.getLogger(__name__)


def _try_pdfplumber(pdf_path: str) -> Optional[str]:
    """Try to extract text and tables using pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # Extract text
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

                # Extract tables
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if row:
                            text_parts.append(" | ".join(
                                str(cell).strip() for cell in row if cell
                            ))
        return "\n".join(text_parts) if text_parts else None
    except Exception as e:
        logger.warning(f"pdfplumber failed: {e}")
        return None


def _try_pypdf2(pdf_path: str) -> Optional[str]:
    """Fallback: extract text with PyPDF2."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n".join(text_parts) if text_parts else None
    except Exception as e:
        logger.warning(f"PyPDF2 failed: {e}")
        return None


def extract_pdf_text(pdf_path: str) -> str:
    """Extract text from a PDF using multiple methods."""
    text = _try_pdfplumber(pdf_path)
    if text:
        return text

    text = _try_pypdf2(pdf_path)
    if text:
        return text

    logger.error(f"Could not extract text from PDF: {pdf_path}")
    return ""


def parse_rate_list(
    llama: LlamaClient,
    pdf_path: str,
    distributor_code: str = "",
) -> Optional[RateList]:
    """Parse a PDF rate list into a structured RateList.

    Args:
        llama: Llama API client for interpretation.
        pdf_path: Path to the PDF file.
        distributor_code: Known distributor code.

    Returns:
        Parsed RateList, or None if parsing fails.
    """
    pdf_path = str(Path(pdf_path).resolve())
    logger.info(f"Parsing rate list: {pdf_path}")

    p = Path(pdf_path)
    data = None
    for sidecar in [p.with_suffix(".json"), p.with_name(p.name + ".json")]:
        if sidecar.exists():
            try:
                import json
                with open(sidecar, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"Loaded sidecar rate list data from {sidecar.name}")
                break
            except Exception as e:
                logger.warning(f"Failed to read sidecar rate list data: {e}")

    if data is None:
        # Step 1: Extract raw text
        raw_text = extract_pdf_text(pdf_path)
        if not raw_text:
            logger.error(f"No text extracted from {pdf_path}")
            return None
        if not SafetyChecker().check_message(raw_text)["is_safe"]:
            logger.warning(f"Quarantined PDF containing unsafe instructions: {Path(pdf_path).name}")
            return None

        # Step 2: Use Llama to interpret the rate list
        prompt = f"""Parse this Indian distributor rate list and extract all product rates.

Raw text from PDF:
---
{raw_text[:4000]}
---

Return JSON:
{{
    "distributor_name": "string",
    "distributor_code": "short code like GT, SP etc.",
    "version": "v1, v2 etc. (from filename or content)",
    "effective_date": "YYYY-MM-DD (when this rate list comes into effect)",
    "entries": [
        {{
            "product": "product name",
            "rate": number (price per unit),
            "hsn": "HSN code if available",
            "unit": "carton/piece/kg etc.",
            "gst_rate": number (GST percentage, e.g. 18 for 18%) or null
        }}
    ]
}}

The effective date might be mentioned as "w.e.f.", "effective from", "in force from", 
"valid from" or similar. If not found, use the document date."""

        system = (
            "You parse Indian distributor rate lists. Extract all products with their "
            "rates, HSN codes, and GST rates. Be precise with numbers."
        )

        try:
            data = llama.extract_json(prompt, system_prompt=system)
        except Exception as e:
            logger.error(f"Llama failed to parse rate list: {e}")
            return None

    # Step 3: Build RateList
    try:
        # Determine version from filename
        filename = Path(pdf_path).stem.lower()
        version = "v1"
        version_match = re.search(r'v(\d+)', filename)
        if version_match:
            version = f"v{version_match.group(1)}"
        elif data.get("version"):
            version = data["version"]

        # Parse effective date
        eff_date_str = data.get("effective_date", "")
        if not eff_date_str:
            logger.warning(f"Rate list {Path(pdf_path).name} has no effective date; cannot safely apply it")
            return None
        effective_date = parse_iso_date(eff_date_str)
        if effective_date is None:
            logger.warning(f"Invalid effective date in {Path(pdf_path).name}: {eff_date_str}")
            return None

        dist_code = data.get("distributor_code") or distributor_code or "UNK"

        entries = []
        for entry in data.get("entries", []):
            gst_rate = None
            if entry.get("gst_rate") is not None:
                gst_val = parse_finite_number(entry["gst_rate"])
                if gst_val is None or gst_val < 0:
                    raise ValueError(f"Invalid GST rate for {entry.get('product', 'Unknown')}")
                gst_rate = gst_val / 100.0 if gst_val > 1 else gst_val

            rate = parse_finite_number(entry.get("rate"))
            if rate is None or rate < 0:
                raise ValueError(f"Invalid product rate for {entry.get('product', 'Unknown')}")

            entries.append(RateEntry(
                product=entry.get("product", "Unknown"),
                rate=rate,
                hsn=entry.get("hsn"),
                unit=entry.get("unit", "carton"),
                gst_rate=gst_rate,
            ))

        rate_list = RateList(
            distributor_code=dist_code,
            version=version,
            effective_date=effective_date,
            source_file=Path(pdf_path).name,
            entries=entries,
        )

        logger.info(f"Parsed rate list {dist_code}/{version} effective {effective_date} "
                     f"with {len(entries)} entries")
        return rate_list

    except Exception as e:
        logger.error(f"Failed to build RateList from {pdf_path}: {e}")
        return None
