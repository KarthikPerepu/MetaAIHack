"""
One normal form for extracted documents, whatever produced them
(Llama vision, PDF tables, OCR text, or a sidecar JSON).

    bill       {bill_id, distributor_name, bill_date, delivery_date, challan_no, lines[...],
                subtotal, discount_total, tax_total, tax_rate, grand_total, confidence}
    upi        {amount, date, upi_ref, payee, status, note, confidence}
    statement  {distributor_name, statement_date, entries[...], total_outstanding, confidence}
    rate_list  {distributor_name, version, effective_from, effective_to, is_revision, entries[...]}

Also validates bill arithmetic (qty x rate = amount, lines add up to the
subtotal, subtotal - discount + tax = total). The share of checks that pass
becomes the OCR-quality signal used in confidence.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from harness.textutil import clean_text, normalize_bill_id, parse_date, parse_number


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return default


def gst_fraction(v) -> Optional[float]:
    x = parse_number(v)
    if x is None:
        return None
    if x > 1.0:
        x = x / 100.0
    return round(x, 4) if 0 <= x <= 0.5 else None


def _hsn(v) -> Optional[str]:
    if v in (None, ""):
        return None
    s = re.sub(r"\D", "", clean_text(str(v)))
    return s if 4 <= len(s) <= 8 else None


def norm_bill(d: dict, ref_year: Optional[int] = None) -> dict:
    lines_in = _first(d, "lines", "items", "line_items", "rows", default=[]) or []
    lines = []
    for i, it in enumerate(lines_in, start=1):
        if not isinstance(it, dict):
            continue
        product = str(_first(it, "product", "item", "description", "name", "particulars", default="")).strip()
        qty = parse_number(_first(it, "quantity", "qty", "qnty"))
        rate = parse_number(_first(it, "rate", "unit_price", "price", "rate_per_unit"))
        amount = parse_number(_first(it, "amount", "value", "total", "line_total"))
        free_qty = parse_number(_first(it, "free_qty", "free", "free_quantity", "scheme_qty")) or 0.0
        is_free = bool(it.get("is_free")) or bool(re.search(r"\b(free|foc|scheme|bonus)\b", product, re.I)) and (not rate or rate == 0)
        if qty is None and rate is None and amount is None and not product:
            continue
        if qty is not None and rate is not None and amount is None:
            amount = qty * rate
        if rate is None and qty and amount is not None:
            rate = amount / qty
        if is_free:
            rate, amount = 0.0, 0.0
        gst_rate = gst_fraction(_first(it, "gst_rate", "gst_percent", "tax_rate", "gst"))
        cg, sg = gst_fraction(it.get("cgst_rate")), gst_fraction(it.get("sgst_rate"))
        if gst_rate is None and cg is not None and sg is not None:
            gst_rate = round(cg + sg, 4)
        gst_amount = parse_number(_first(it, "gst_amount", "tax_amount", "tax"))
        if gst_amount is None and it.get("cgst_amount") is not None and it.get("sgst_amount") is not None:
            gst_amount = (parse_number(it["cgst_amount"]) or 0) + (parse_number(it["sgst_amount"]) or 0)
        lines.append({
            "row": int(parse_number(_first(it, "row", "s_no", "sno", "sr")) or i),
            "product": product or f"row {i}",
            "quantity": qty or 0.0,
            "rate": rate or 0.0,
            "amount": amount or 0.0,
            "unit": str(_first(it, "unit", "uom", default="") or "").lower(),
            "hsn": _hsn(_first(it, "hsn", "hsn_code", "hsn_sac")),
            "discount": parse_number(_first(it, "discount", "discount_amount", "disc")) or 0.0,
            "free_qty": free_qty,
            "is_free": is_free,
            "gst_rate": gst_rate,
            "gst_amount": gst_amount,
            "taxable_value": parse_number(it.get("taxable_value")),
        })
    for fg in _first(d, "free_goods", default=[]) or []:
        if isinstance(fg, dict):
            lines.append({"row": len(lines) + 1, "product": str(fg.get("product", "")), "quantity": parse_number(fg.get("quantity")) or 0.0,
                          "rate": 0.0, "amount": 0.0, "unit": str(fg.get("unit", "") or ""), "hsn": None, "discount": 0.0,
                          "free_qty": 0.0, "is_free": True, "gst_rate": None, "gst_amount": None, "taxable_value": None})
    tax_total = parse_number(_first(d, "tax_total", "gst_total", "total_tax", "tax"))
    cg, sg, ig = parse_number(d.get("cgst")), parse_number(d.get("sgst")), parse_number(d.get("igst"))
    if tax_total is None and (cg is not None or sg is not None or ig is not None):
        tax_total = (cg or 0) + (sg or 0) + (ig or 0)
    out = {
        "bill_id": normalize_bill_id(_first(d, "bill_id", "bill_no", "invoice_no", "invoice_number", "bill_number", default="")),
        "distributor_name": str(_first(d, "distributor_name", "seller", "supplier", "from", default="") or ""),
        "distributor_code": str(_first(d, "distributor_code", default="") or ""),
        "bill_date": parse_date(_first(d, "bill_date", "invoice_date", "date"), ref_year=ref_year),
        "delivery_date": parse_date(_first(d, "delivery_date", "dispatch_date", "challan_date"), ref_year=ref_year),
        "challan_no": str(_first(d, "challan_no", "dc_no", "delivery_challan", "order_no", default="") or ""),
        "lines": lines,
        "subtotal": parse_number(_first(d, "subtotal", "sub_total", "taxable_total", "gross")),
        "discount_total": parse_number(_first(d, "discount_total", "discount", "bill_discount")) or 0.0,
        "tax_total": tax_total,
        "tax_rate": gst_fraction(_first(d, "tax_rate", "gst_rate")),
        "grand_total": parse_number(_first(d, "grand_total", "total", "net_amount", "invoice_total", "total_amount")),
        "confidence": parse_number(_first(d, "ocr_confidence", "confidence", "legibility")),
        "instructions_found": str(_first(d, "instructions_found", "injected_text", default="") or ""),
        "raw_text": str(_first(d, "raw_text", "text", default="") or ""),
    }
    out["arith_score"], out["warnings"] = bill_arithmetic(out)
    return out


def bill_arithmetic(b: dict) -> tuple[float, list[str]]:
    checks, passed, warnings = 0, 0, []
    paid_lines = [l for l in b["lines"] if not l["is_free"]]
    for l in paid_lines:
        if l["quantity"] and l["rate"] and l["amount"]:
            checks += 1
            if abs(l["quantity"] * l["rate"] - l["amount"]) <= max(1.0, 0.01 * l["amount"]):
                passed += 1
            else:
                warnings.append(f"row {l['row']}: {l['quantity']:g} x {l['rate']:g} != {l['amount']:g}")
    gross = sum(l["amount"] for l in paid_lines)
    if b.get("subtotal"):
        checks += 1
        net_lines = gross - sum(l["discount"] for l in paid_lines)
        if abs(gross - b["subtotal"]) <= 1.0 or abs(net_lines - b["subtotal"]) <= 1.0:
            passed += 1
        else:
            warnings.append(f"lines add to {gross:g}, subtotal printed {b['subtotal']:g}")
    if b.get("grand_total"):
        checks += 1
        sub = b.get("subtotal") or gross
        line_disc = sum(l["discount"] for l in paid_lines)
        cands = {sub - (b.get("discount_total") or 0) + (b.get("tax_total") or 0),
                 sub - line_disc - (b.get("discount_total") or 0) + (b.get("tax_total") or 0),
                 sub + (b.get("tax_total") or 0)}
        if any(abs(c - b["grand_total"]) <= 1.5 for c in cands):
            passed += 1
        else:
            warnings.append(f"total printed {b['grand_total']:g} does not add up")
    if not paid_lines:
        return 0.0, ["no billed lines read"]
    return (passed / checks if checks else 0.6), warnings


def norm_upi(d: dict, ref_year: Optional[int] = None) -> dict:
    status = str(_first(d, "status", "transaction_status", default="") or "").lower()
    if re.search(r"(success|successful|paid|completed|credited|sent|done)", status):
        status = "success"
    elif re.search(r"(fail|declin|revers)", status):
        status = "failed"
    elif re.search(r"(pend|process)", status):
        status = "pending"
    else:
        status = "unknown"
    return {
        "amount": parse_number(_first(d, "amount", "paid_amount", "value")),
        "date": parse_date(_first(d, "payment_date", "date", "transaction_date", "paid_on"), ref_year=ref_year),
        "upi_ref": re.sub(r"\s", "", str(_first(d, "upi_ref", "utr", "ref", "transaction_id", "upi_ref_no", default="") or "")),
        "payee": str(_first(d, "payee_name", "payee", "to", "paid_to", default="") or ""),
        "status": status,
        "note": str(_first(d, "note", "remarks", "message", default="") or ""),
        "confidence": parse_number(_first(d, "confidence", "ocr_confidence", "legibility")),
    }


def norm_statement(d: dict, ref_year: Optional[int] = None) -> dict:
    entries = []
    for i, e in enumerate(_first(d, "entries", "rows", "bills", "items", default=[]) or [], start=1):
        if not isinstance(e, dict):
            continue
        status = str(_first(e, "status", default="due") or "due").lower()
        if re.search(r"(paid|clear|settled|received)", status):
            status = "paid"
        elif re.search(r"partial", status):
            status = "partial"
        elif re.search(r"(credit|cr\b)", status):
            status = "credit"
        else:
            status = "due"
        bill_id = normalize_bill_id(_first(e, "bill_id", "bill_no", "invoice_no", "reference", default=""))
        if not bill_id:
            continue
        entries.append({
            "row": int(parse_number(e.get("row")) or i),
            "bill_id": bill_id,
            "bill_date": parse_date(_first(e, "bill_date", "date", "invoice_date"), ref_year=ref_year),
            "amount": parse_number(_first(e, "amount", "due", "outstanding", "balance_due", "debit")) or 0.0,
            "status": status,
            "balance": parse_number(e.get("balance")),
        })
    return {
        "distributor_name": str(_first(d, "distributor_name", "from", default="") or ""),
        "statement_date": parse_date(_first(d, "statement_date", "as_of", "date"), ref_year=ref_year),
        "entries": entries,
        "total_outstanding": parse_number(_first(d, "total_outstanding", "total_due", "closing_balance")),
        "confidence": parse_number(_first(d, "confidence", "ocr_confidence")),
    }


def norm_rate_list(d: dict, ref_year: Optional[int] = None) -> dict:
    entries = []
    for i, e in enumerate(_first(d, "entries", "items", "rates", "rows", "products", default=[]) or [], start=1):
        if not isinstance(e, dict):
            continue
        product = str(_first(e, "product", "item", "description", "name", default="")).strip()
        rate = parse_number(_first(e, "rate", "price", "dealer_rate", "net_rate"))
        if not product or rate is None or rate <= 0:
            continue
        entries.append({
            "row": int(parse_number(e.get("row")) or i),
            "product": product,
            "rate": rate,
            "unit": str(_first(e, "unit", "uom", "pack", default="") or "").lower(),
            "hsn": _hsn(_first(e, "hsn", "hsn_code")),
            "gst_rate": gst_fraction(_first(e, "gst_rate", "gst", "gst_percent", "tax_rate")),
            "mrp": parse_number(e.get("mrp")),
            "scheme_text": str(_first(e, "scheme", "scheme_text", "offer", default="") or ""),
        })
    eff = parse_date(_first(d, "effective_from", "effective_date", "wef", "valid_from", "in_force_from"), ref_year=ref_year)
    text_bits = " ".join(str(d.get(k, "")) for k in ("title", "version", "note", "raw_text"))
    return {
        "distributor_name": str(_first(d, "distributor_name", "from", default="") or ""),
        "distributor_code": str(_first(d, "distributor_code", default="") or ""),
        "version": str(_first(d, "version", "list_no", default="") or ""),
        "effective_from": eff,
        "effective_to": parse_date(_first(d, "effective_to", "valid_until", "valid_till"), ref_year=ref_year),
        "document_date": parse_date(_first(d, "document_date", "date", "issued_on"), ref_year=ref_year),
        "is_revision": bool(d.get("is_revision")) or bool(re.search(
            r"(revised|corrected|correction|supersed|replaces|amended|संशोधित|सुधार)", text_bits, re.I)),
        "entries": entries,
        "instructions_found": str(_first(d, "instructions_found", default="") or ""),
    }


def normalize_doc(doc_type: str, data: dict, ref_year: Optional[int] = None) -> dict:
    if doc_type == "bill":
        return norm_bill(data, ref_year)
    if doc_type == "upi_payment":
        return norm_upi(data, ref_year)
    if doc_type == "statement":
        return norm_statement(data, ref_year)
    if doc_type == "rate_list":
        return norm_rate_list(data, ref_year)
    return data


def guess_doc_type(data: dict) -> str:
    """Classify a sidecar / model JSON by its keys."""
    keys = set(data.keys())
    if data.get("doc_type") in ("bill", "upi_payment", "statement", "rate_list", "other"):
        return data["doc_type"]
    if keys & {"upi_ref", "utr", "payee_name", "payment_date", "transaction_id"}:
        return "upi_payment"
    if "statement_date" in keys or "total_outstanding" in keys:
        return "statement"
    if keys & {"effective_date", "effective_from", "wef"} or ("entries" in keys and "bill_id" not in keys):
        return "rate_list"
    if keys & {"bill_id", "items", "lines", "invoice_no", "bill_no"}:
        return "bill"
    return "other"
