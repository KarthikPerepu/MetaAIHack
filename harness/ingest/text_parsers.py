"""
Deterministic parsers for document *text* (PDF text layers and OCR output).

They do not depend on one layout: a bill row is any line holding three
numbers with qty x rate = amount; a rate-list row is mapped through the
header's column order; totals are found by their labels in English, Hinglish
or Hindi. Used for PDFs (main path) and as the offline fallback for photos.
"""

from __future__ import annotations

import re
from typing import Optional

from harness.textutil import (clean_text, find_bill_ids, find_dates, normalize_bill_id, parse_date,
                              parse_number)

NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_NUM_RE = re.compile(NUM)

TOTAL_LABELS = {
    "subtotal": r"(sub\s*-?\s*total|taxable\s*(value|amount)|gross\s*(amount|total)|उप\s*योग|कुल\s*मूल्य)",
    "discount_total": r"(discount|less\s*:?\s*disc|छूट)",
    "cgst": r"\bcgst\b", "sgst": r"\b(sgst|utgst)\b", "igst": r"\bigst\b",
    "tax_total": r"(total\s*(gst|tax)|\bgst\s*(amount|total)?\b|tax\s*amount|कर)",
    "grand_total": r"(grand\s*total|net\s*(amount|payable|total)|total\s*(amount|payable)|amount\s*payable|bill\s*amount|कुल\s*(राशि|योग)|^\s*total\b)",
}


def classify_text(text: str, filename: str = "") -> str:
    t = clean_text(text).lower()
    f = filename.lower()
    score = {"upi_payment": 0, "statement": 0, "rate_list": 0, "bill": 0}
    score["upi_payment"] += 3 * bool(re.search(r"(upi\s*(ref|transaction|id)|utr|paid\s*to|payment\s*(successful|success)|"
                                               r"google\s*pay|phonepe|paytm|bhim|transaction\s*id|money\s*sent)", t))
    score["statement"] += 3 * bool(re.search(r"(statement\s*of\s*account|account\s*statement|ledger|outstanding|"
                                             r"balance\s*due|बकाया|खाता\s*विवरण)", t))
    score["rate_list"] += 3 * bool(re.search(r"(rate\s*list|price\s*list|dealer\s*price|w\.?\s*e\.?\s*f|effective\s*(from|date)|"
                                             r"रेट\s*लिस्ट|दर\s*सूची|भाव\s*सूची)", t))
    score["bill"] += 3 * bool(re.search(r"(tax\s*invoice|invoice\s*(no|number)|bill\s*(no|number)|बिल\s*(नं|संख्या)|"
                                        r"cash\s*memo|estimate)", t))
    for k, words in (("upi_payment", ("upi", "gpay", "phonepe", "paytm", "payment")),
                     ("statement", ("statement", "ledger", "soa", "outstanding")),
                     ("rate_list", ("rate", "price", "ratelist", "pricelist")),
                     ("bill", ("bill", "invoice", "inv"))):
        score[k] += any(w in f for w in words)
    best = max(score, key=lambda k: score[k])
    return best if score[best] > 0 else "other"


def _numbers(line: str) -> list[tuple[float, int, int, str]]:
    out = []
    for m in _NUM_RE.finditer(line):
        try:
            out.append((float(m.group(0).replace(",", "")), m.start(), m.end(), m.group(0)))
        except ValueError:
            pass
    return out


def _label_value(lines: list[str], pattern: str) -> Optional[float]:
    pat = re.compile(pattern, re.I)
    for line in reversed(lines):
        if pat.search(line):
            nums = [n for n, s, e, raw in _numbers(line) if not raw.endswith("%")]
            # prefer the last number on the line; skip percentages like "9%"
            line_wo_pct = re.sub(r"\d+(\.\d+)?\s*%", " ", line)
            nums = [n for n, *_ in _numbers(line_wo_pct)]
            if nums:
                return nums[-1]
    return None


def parse_bill_text(text: str, ref_year: Optional[int] = None) -> dict:
    t = clean_text(text)
    lines = [l for l in t.splitlines() if l.strip()]
    bill_id = ""
    m = re.search(r"(?:bill|invoice|inv|बिल)\s*(?:no\.?|number|#|नं\.?|संख्या)?\s*[:.\-]?\s*([A-Z]{1,5}\s*[/\-]\s*\d{2,7})", t, re.I)
    if m:
        bill_id = normalize_bill_id(m.group(1))
    elif find_bill_ids(t):
        bill_id = find_bill_ids(t)[0]
    bill_date = None
    m = re.search(r"(?:bill|invoice|inv)?\s*(?:date|dated|dt|दिनांक|तारीख)\s*[:.\-]?\s*(.{6,20})", t, re.I)
    if m:
        bill_date = parse_date(m.group(1), ref_year=ref_year)
    dates = find_dates(t, ref_year=ref_year)
    if not bill_date and dates:
        bill_date = dates[0][0]
    delivery = None
    m = re.search(r"(?:delivery|dispatch|challan)\s*(?:date|dt)?\s*[:.\-]?\s*(.{6,20})", t, re.I)
    if m:
        delivery = parse_date(m.group(1), ref_year=ref_year)
    challan = ""
    m = re.search(r"(?:challan|d\.?c\.?|order)\s*(?:no\.?|#)\s*[:.\-]?\s*([A-Z0-9/\-]{2,15})", t, re.I)
    if m:
        challan = m.group(1)
    distributor = ""
    for l in lines[:4]:
        if re.search(r"(traders|products|confectionery|provisions|wholesale|distributors|agencies|enterprises|"
                     r"suppliers|sales|foods|brothers|marketing|company|stores)", l, re.I):
            distributor = re.sub(r"\s{2,}.*$", "", l.strip())
            break

    items = []
    for line in lines:
        low = line.lower()
        if re.search(TOTAL_LABELS["grand_total"] + "|" + TOTAL_LABELS["subtotal"] + r"|\bcgst\b|\bsgst\b|\bigst\b", low):
            continue
        clean = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"PCT\1", line)
        nums = _numbers(clean)
        if len(nums) < 3:
            free = re.search(r"(free|foc|scheme|bonus|फ्री)", low)
            if free and nums:
                name = re.sub(_NUM_RE, " ", line).strip(" |-:")
                qty = nums[-1][0] if len(nums) == 1 else nums[1][0]
                items.append({"product": re.sub(r"\s+", " ", name), "quantity": qty, "rate": 0, "amount": 0, "is_free": True})
            continue
        best = None
        vals = [n[0] for n in nums]
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                for k in range(j + 1, len(vals)):
                    q, r, a = vals[i], vals[j], vals[k]
                    if q > 0 and r > 0 and abs(q * r - a) <= max(1.0, 0.01 * a):
                        cand = (k - i, i, j, k)
                        if best is None or cand < best:
                            best = cand
        if not best:
            continue
        _, i, j, k = best
        q, r, a = vals[i], vals[j], vals[k]
        first_num_start = nums[0][1]
        # leading serial number
        lead = clean[:first_num_start]
        name_end = nums[i][1]
        name_part = clean[:name_end]
        hsn = None
        for idx, (n, s, e, raw) in enumerate(nums):
            if idx in (i, j, k):
                continue
            if re.fullmatch(r"\d{4}|\d{6}|\d{8}", raw):
                hsn = raw
        if hsn:
            name_part = name_part.replace(hsn, " ")
        name = re.sub(r"^\s*\d{1,3}[.)]?\s+", "", name_part)
        name = re.sub(_NUM_RE, " ", name) if not re.search(r"[a-zऀ-ॿ]", name, re.I) else name
        name = re.sub(r"[|]+", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" -:")
        pct = re.search(r"PCT(\d+(?:\.\d+)?)", clean)
        tail = clean[nums[k][2]:]
        tail_nums = [n for n, *_ in _numbers(re.sub(r"PCT\d+(\.\d+)?", " ", tail))]
        unit = ""
        um = re.search(r"\b(carton|ctn|crate|bag|box|packet|pkt|pcs?|piece|kg|case|tin|bottle|jar|dozen)s?\b", line, re.I)
        if um:
            unit = um.group(1).lower()
        item = {"product": name or "item", "quantity": q, "rate": r, "amount": a, "hsn": hsn, "unit": unit}
        if pct:
            item["gst_rate"] = float(pct.group(1))
            if tail_nums:
                item["gst_amount"] = tail_nums[0]
        sno = re.match(r"^\s*(\d{1,3})[.)]?\s", lead + " ")
        if sno:
            item["row"] = int(sno.group(1))
        items.append(item)

    data = {"bill_id": bill_id, "bill_date": bill_date.isoformat() if bill_date else None,
            "delivery_date": delivery.isoformat() if delivery else None, "challan_no": challan,
            "distributor_name": distributor, "items": items, "raw_text": t[:4000]}
    for key in ("subtotal", "discount_total", "cgst", "sgst", "igst", "tax_total", "grand_total"):
        v = _label_value(lines, TOTAL_LABELS[key])
        if v is not None:
            data[key] = v
    if "tax_total" not in data and ("cgst" in data or "sgst" in data or "igst" in data):
        data["tax_total"] = data.get("cgst", 0) + data.get("sgst", 0) + data.get("igst", 0)
    m = re.search(r"(?:gst|tax)\s*@?\s*(\d+(?:\.\d+)?)\s*%", t, re.I)
    if m:
        data["tax_rate"] = float(m.group(1))
    return data


EFFECTIVE = re.compile(
    r"(w\.?\s*e\.?\s*f\.?|with\s*effect\s*from|effective\s*(from|date|on)?|valid\s*from|in\s*force\s*(from)?|"
    r"applicable\s*(from|w\.e\.f)?|lagu|लागू|से\s*प्रभावी|प्रभावी\s*तिथि)\s*[:.\-]?\s*(.{0,30})", re.I)
VALID_TO = re.compile(r"(valid\s*(till|upto|up\s*to|until)|till|upto)\s*[:.\-]?\s*(.{0,25})", re.I)


def parse_rate_list_text(text: str, tables: Optional[list[list[list[str]]]] = None,
                         ref_year: Optional[int] = None) -> dict:
    t = clean_text(text)
    lines = [l for l in t.splitlines() if l.strip()]
    eff = None
    for m in EFFECTIVE.finditer(t):
        tail = m.group(4) or ""
        eff = parse_date(tail, ref_year=ref_year)
        if not eff:
            # "lagu" in Hindi often follows the date: "3 Oct 2026 se lagu"
            before = t[max(0, m.start() - 25):m.start()]
            eff = parse_date(before, ref_year=ref_year)
        if eff:
            break
    to = None
    m = VALID_TO.search(t)
    if m:
        to = parse_date(m.group(3), ref_year=ref_year)
    dates = find_dates(t, ref_year=ref_year)
    doc_date = dates[0][0] if dates else None
    version = ""
    m = re.search(r"\b(v(?:ersion)?\s*\.?\s*(\d+)|(?:rate|price)\s*list\s*(?:no\.?|#)\s*(\d+))\b", t, re.I)
    if m:
        version = f"v{m.group(2) or m.group(3)}"
    distributor = ""
    for l in lines[:4]:
        if re.search(r"(traders|products|confectionery|provisions|wholesale|distributors|agencies|enterprises|"
                     r"suppliers|sales|foods|brothers|marketing|company)", l, re.I):
            distributor = l.strip()
            break

    entries = []
    if tables:
        for table in tables:
            entries.extend(_rate_rows_from_table(table))
    if not entries:
        entries = _rate_rows_from_lines(lines)
    return {"distributor_name": distributor, "version": version,
            "effective_from": eff.isoformat() if eff else None,
            "effective_to": to.isoformat() if to else None,
            "document_date": doc_date.isoformat() if doc_date else None,
            "raw_text": t[:3000], "entries": entries}


_COLS = {
    "product": r"(product|item|description|particulars|name|sku|उत्पाद|सामान|माल|वस्तु)",
    "hsn": r"(hsn|sac)",
    "unit": r"(unit|uom|pack|packing|इकाई)",
    "mrp": r"\bmrp\b",
    "gst": r"(gst|tax|कर)",
    "rate": r"(rate|price|dealer|net|₹|rs|दर|भाव|दाम|कीमत)",
    "scheme": r"(scheme|offer|स्कीम)",
}


def _map_header(cells: list[str]) -> dict[int, str]:
    mapping = {}
    for i, c in enumerate(cells):
        c = (c or "").strip().lower()
        for col in ("hsn", "mrp", "gst", "scheme", "unit", "product", "rate"):
            if col in mapping.values() and col != "rate":
                continue
            if re.search(_COLS[col], c):
                mapping[i] = col
                break
    return mapping


def _row_entry(cells: list[str], mapping: dict[int, str], row: int) -> Optional[dict]:
    e: dict = {"row": row}
    for i, col in mapping.items():
        if i >= len(cells):
            continue
        val = (cells[i] or "").strip()
        if col == "product":
            e["product"] = val
        elif col == "rate":
            if "rate" not in e and parse_number(val):
                e["rate"] = parse_number(val)
        elif col == "hsn":
            e["hsn"] = re.sub(r"\D", "", val) or None
        elif col == "gst":
            e["gst_rate"] = parse_number(val)
        elif col == "unit":
            e["unit"] = val
        elif col == "mrp":
            e["mrp"] = parse_number(val)
        elif col == "scheme":
            e["scheme"] = val
    if e.get("product") and e.get("rate"):
        return e
    return None


def _rate_rows_from_table(table: list[list[str]]) -> list[dict]:
    if not table:
        return []
    header_idx, mapping = None, {}
    for i, row in enumerate(table[:4]):
        m = _map_header([c or "" for c in row])
        if "product" in m.values() and "rate" in m.values():
            header_idx, mapping = i, m
            break
    if header_idx is None:
        return []
    out = []
    for n, row in enumerate(table[header_idx + 1:], start=1):
        e = _row_entry([c or "" for c in row], mapping, n)
        if e:
            out.append(e)
    return out


def _rate_rows_from_lines(lines: list[str]) -> list[dict]:
    header_cols: list[str] = []
    out = []
    for line in lines:
        low = line.lower()
        if not header_cols and re.search(_COLS["product"], low) and re.search(_COLS["rate"], low):
            order = []
            for col in ("hsn", "unit", "mrp", "gst", "rate"):
                m = re.search(_COLS[col], low)
                if m:
                    order.append((m.start(), col))
            header_cols = [c for _, c in sorted(order)]
            continue
        if not header_cols:
            continue
        if re.search(r"(authori[sz]ed|signature|signatory|note|terms|w\.?e\.?f)", low):
            continue
        clean = re.sub(r"(\d+(?:\.\d+)?)\s*%", r" \1 ", line)
        nums = list(_numbers(clean))
        if not nums:
            continue
        name = clean[:nums[0][1]]
        name = re.sub(r"^\s*\d{1,3}[.)]?\s+", "", name)
        # the product name may itself contain sizes ("Tata Salt 1kg", "Parle-G 250g")
        while nums and re.match(r"\s*(g|gm|kg|ml|l|ltr)\b", clean[nums[0][2]:], re.I):
            name = clean[:nums[1][1]] if len(nums) > 1 else clean
            name = re.sub(r"^\s*\d{1,3}[.)]?\s+", "", name)
            nums = nums[1:]
        name = re.sub(r"\s+", " ", name.replace("|", " ")).strip(" -:")
        if not name or not re.search(r"[a-zऀ-ॿ]", name, re.I):
            continue
        numeric_cols = [c for c in header_cols if c != "unit"]
        e: dict = {"product": name}
        um = re.search(r"\b(carton|ctn|crate|bag|box|packet|pkt|pcs?|piece|kg|case|tin|bottle|jar|dozen)s?\b",
                       clean[nums[0][1]:] if nums else "", re.I)
        if um:
            e["unit"] = um.group(1).lower()
        vals = [n for n, *_ in nums]
        if len(vals) >= len(numeric_cols):
            for col, v in zip(numeric_cols, vals[-len(numeric_cols):] if len(vals) > len(numeric_cols) else vals):
                key = {"gst": "gst_rate"}.get(col, col)
                e[key] = str(int(v)) if col == "hsn" else v
        else:
            e["rate"] = vals[-1]
        if e.get("rate"):
            out.append(e)
    for i, e in enumerate(out, start=1):
        e["row"] = i
    return out


def parse_upi_text(text: str, ref_year: Optional[int] = None) -> dict:
    t = clean_text(text)
    amt = None
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", t, re.I)
    if m:
        amt = parse_number(m.group(1))
    ref = ""
    m = re.search(r"(?:upi\s*(?:ref(?:erence)?|transaction)\s*(?:no\.?|id|number)?|utr|transaction\s*id|ref\s*no\.?)\s*[:.\-]?\s*([A-Z0-9]{8,22})", t, re.I)
    if m:
        ref = m.group(1)
    payee = ""
    m = re.search(r"(?:paid\s*to|to|payee|sent\s*to)\s*[:\-]?\s*([A-Za-z][A-Za-z .&]{2,40})", t)
    if m:
        payee = m.group(1).strip()
    status = "unknown"
    if re.search(r"(success|successful|completed|paid|sent)", t, re.I):
        status = "success"
    if re.search(r"(failed|declined|reversed)", t, re.I):
        status = "failed"
    if re.search(r"(pending|processing)", t, re.I):
        status = "pending"
    dates = find_dates(t, ref_year=ref_year)
    note = ""
    m = re.search(r"(?:note|message|remarks?)\s*[:\-]\s*(.{1,60})", t, re.I)
    if m:
        note = m.group(1).strip()
    return {"amount": amt, "payment_date": dates[0][0].isoformat() if dates else None, "upi_ref": ref,
            "payee_name": payee, "status": status, "note": note}


def parse_statement_text(text: str, ref_year: Optional[int] = None) -> dict:
    t = clean_text(text)
    lines = [l for l in t.splitlines() if l.strip()]
    sdate = None
    m = re.search(r"(?:as\s*(?:of|on)|statement\s*date|date|dated|till)\s*[:.\-]?\s*(.{6,20})", t, re.I)
    if m:
        sdate = parse_date(m.group(1), ref_year=ref_year)
    if not sdate:
        ds = find_dates(t, ref_year=ref_year)
        sdate = max(d for d, *_ in ds) if ds else None
    entries = []
    for line in lines:
        ids = find_bill_ids(line)
        if not ids:
            continue
        status = "due"
        if re.search(r"\b(paid|cleared|settled|received)\b", line, re.I):
            status = "paid"
        elif re.search(r"\bpartial\b", line, re.I):
            status = "partial"
        ds = find_dates(line, ref_year=ref_year)
        tmp = line
        for _, s, e in reversed(ds):
            tmp = tmp[:s] + " " + tmp[e:]
        for bid in ids:
            tmp = tmp.replace(bid, " ").replace(bid.replace("/", "-"), " ")
        nums = [n for n, *_ in _numbers(tmp)]
        entries.append({"bill_id": ids[0], "bill_date": ds[0][0].isoformat() if ds else None,
                        "amount": nums[0] if nums else 0, "status": status,
                        "balance": nums[1] if len(nums) > 1 else None})
    total = _label_value(lines, r"(total\s*(outstanding|due|balance)|closing\s*balance|net\s*due|कुल\s*बकाया)")
    return {"statement_date": sdate.isoformat() if sdate else None, "entries": entries, "total_outstanding": total}


def parse_text_document(text: str, filename: str = "", ref_year: Optional[int] = None,
                        tables=None) -> tuple[str, dict]:
    kind = classify_text(text, filename)
    if kind == "bill":
        return kind, parse_bill_text(text, ref_year)
    if kind == "rate_list":
        return kind, parse_rate_list_text(text, tables, ref_year)
    if kind == "upi_payment":
        return kind, parse_upi_text(text, ref_year)
    if kind == "statement":
        return kind, parse_statement_text(text, ref_year)
    # unknown: try a bill parse, it is the most common attachment
    data = parse_bill_text(text, ref_year)
    if data.get("items") and data.get("bill_id"):
        return "bill", data
    return "other", {"raw_text": text[:2000]}
