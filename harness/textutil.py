"""
Text helpers shared by every module.

Everything here is deterministic and offline: number and date parsing for
Hinglish / Devanagari text, bill-id normalisation and product-name matching.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Iterable, Optional

try:  # rapidfuzz is optional; difflib is the fallback
    from rapidfuzz import fuzz as _rf_fuzz
except Exception:  # pragma: no cover - depends on environment
    _rf_fuzz = None

# ─────────────────────────────────────────────────────────────
# Digits and numbers
# ─────────────────────────────────────────────────────────────
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿​"), None)


def clean_text(text: str) -> str:
    """Remove invisible direction marks, normalise unicode and digits."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).translate(_INVISIBLE)
    text = text.replace(" ", " ").replace(" ", " ")
    return text.translate(_DEVANAGARI_DIGITS)


# Hindi / Hinglish number words. Only applied right before a unit or a
# scheme word so that verbs such as "bhej do" are never read as "2".
NUMBER_WORDS = {
    "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhe": 6, "chhah": 6, "chah": 6, "che": 6, "saat": 7, "sat": 7, "aath": 8, "ath": 8,
    "nau": 9, "das": 10, "dus": 10, "gyarah": 11, "barah": 12, "baarah": 12, "terah": 13,
    "chaudah": 14, "pandrah": 15, "pandra": 15, "solah": 16, "satrah": 17, "atharah": 18,
    "unnis": 19, "bees": 20, "bis": 20, "pachees": 25, "pachchis": 25, "tees": 30, "tis": 30,
    "chalis": 40, "chaalis": 40, "pachas": 50, "pachaas": 50, "sau": 100,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "twenty-five": 25, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "पंद्रह": 15, "पन्द्रह": 15,
    "बीस": 20, "पच्चीस": 25, "तीस": 30, "चालीस": 40, "पचास": 50, "सौ": 100,
}

UNIT_WORDS = {
    "carton": "carton", "cartons": "carton", "ctn": "carton", "ctns": "carton", "cartoon": "carton",
    "कार्टन": "carton", "कार्टून": "carton", "peti": "carton", "पेटी": "carton", "case": "case",
    "cases": "case", "box": "box", "boxes": "box", "dabba": "box", "डिब्बा": "box", "डिब्बे": "box",
    "crate": "crate", "crates": "crate", "bag": "bag", "bags": "bag", "bori": "bag", "बोरी": "bag",
    "बैग": "bag", "packet": "packet", "packets": "packet", "pkt": "packet", "pkts": "packet",
    "पैकेट": "packet", "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece",
    "nos": "piece", "dozen": "dozen", "darjan": "dozen", "दर्जन": "dozen", "kg": "kg",
    "kilo": "kg", "किलो": "kg", "litre": "litre", "liter": "litre", "ltr": "litre",
    "bottle": "bottle", "bottles": "bottle", "jar": "jar", "tin": "tin", "pouch": "pouch",
    "strip": "strip", "bundle": "bundle", "sack": "bag", "unit": "unit", "units": "unit",
}

_NUM_RE = re.compile(r"(?<![\w.])[-−]?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?")


def words_to_digits(text: str) -> str:
    """Replace number words that sit before a unit / scheme word with digits."""
    tokens = re.split(r"(\s+)", text)
    words = [t for t in tokens]
    out = []
    for i, tok in enumerate(words):
        key = tok.lower().strip(",.!?")
        if key in NUMBER_WORDS:
            nxt = ""
            for j in range(i + 1, len(words)):
                if words[j].strip():
                    nxt = words[j].lower().strip(",.!?")
                    break
            if nxt in UNIT_WORDS or nxt in {"free", "फ्री", "muft", "मुफ्त", "%", "percent", "pe",
                                             "par", "पर", "पे", "din", "दिन", "days", "day"}:
                out.append(str(NUMBER_WORDS[key]))
                continue
        out.append(tok)
    return "".join(out)


def parse_number(value) -> Optional[float]:
    """Parse '₹ 1,450.00/-', 'Rs.395', '(120)', '३९५' into a float. None if impossible."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    s = clean_text(str(value)).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"(?i)(inr|rs\.?|₹|/-|\s)", "", s.strip("()"))
    s = s.replace("−", "-")
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        num = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    if not math.isfinite(num):
        return None
    return -num if negative else num


def find_numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(clean_text(text)):
        try:
            out.append(float(m.group(0).replace(",", "").replace("−", "-")))
        except ValueError:
            pass
    return out


def money(x: float) -> float:
    """Round a rupee amount to paise."""
    return float(round((x or 0.0) + 0.0, 2))


def fmt_inr(x: float) -> str:
    x = money(x)
    return f"₹{x:,.0f}" if abs(x - round(x)) < 0.005 else f"₹{x:,.2f}"


def fmt_num(x: float) -> str:
    return f"{x:g}" if x is not None else "?"


# ─────────────────────────────────────────────────────────────
# Dates
# ─────────────────────────────────────────────────────────────
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4,
    "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11,
    "november": 11, "dec": 12, "december": 12,
    "जनवरी": 1, "फरवरी": 2, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6,
    "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10, "अक्तूबर": 10,
    "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12,
}
_MONTH_ALT = "|".join(sorted((re.escape(k) for k in MONTHS), key=len, reverse=True))

_DATE_PATTERNS = [
    ("iso", re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")),
    ("dmy", re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")),
    ("d_mon_y", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-./]*({_MONTH_ALT})[a-z]*[\s\-.,/']*(\d{{2,4}})?", re.I)),
    ("mon_d_y", re.compile(rf"\b({_MONTH_ALT})[a-z]*[\s\-.]+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})?", re.I)),
]


def _mk_date(y: int, m: int, d: int) -> Optional[date]:
    if y < 100:
        y += 2000
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str, ref_year: Optional[int] = None, day_first: bool = True) -> list[tuple[date, int, int]]:
    """Find all dates in free text. Returns (date, start, end) tuples in text order."""
    text = clean_text(text)
    found: list[tuple[date, int, int]] = []
    taken: list[tuple[int, int]] = []

    def overlaps(a, b):
        return any(not (b <= s or a >= e) for s, e in taken)

    for kind, pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            d = None
            if kind == "iso":
                d = _mk_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif kind == "dmy":
                a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if day_first or a > 12:
                    d = _mk_date(y, b, a) or _mk_date(y, a, b)
                else:
                    d = _mk_date(y, a, b) or _mk_date(y, b, a)
            elif kind == "d_mon_y":
                mon = MONTHS.get(m.group(2).lower()[:3]) or MONTHS.get(m.group(2).lower()) or MONTHS.get(m.group(2))
                y = int(m.group(3)) if m.group(3) else ref_year
                if mon and y:
                    d = _mk_date(y, mon, int(m.group(1)))
            elif kind == "mon_d_y":
                mon = MONTHS.get(m.group(1).lower()[:3]) or MONTHS.get(m.group(1).lower()) or MONTHS.get(m.group(1))
                y = int(m.group(3)) if m.group(3) else ref_year
                if mon and y:
                    d = _mk_date(y, mon, int(m.group(2)))
            if d:
                found.append((d, m.start(), m.end()))
                taken.append((m.start(), m.end()))
    found.sort(key=lambda t: t[1])
    return found


def parse_date(value, ref_year: Optional[int] = None, day_first: bool = True) -> Optional[date]:
    """Parse one date from a value (date, ISO string, Indian formats, '3 Oct')."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = clean_text(str(value)).strip()
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return date.fromisoformat(s)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ].*", s):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    hits = find_dates(s, ref_year=ref_year, day_first=day_first)
    return hits[0][0] if hits else None


def iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def from_iso(s) -> Optional[date]:
    if not s:
        return None
    if isinstance(s, date):
        return s if not isinstance(s, datetime) else s.date()
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return parse_date(s)


def from_iso_dt(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        d = from_iso(s)
        return datetime(d.year, d.month, d.day) if d else None


def end_of_day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59)


def end_of_month(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


# ─────────────────────────────────────────────────────────────
# Bill ids
# ─────────────────────────────────────────────────────────────
_BILL_ID_RE = re.compile(r"\b([A-Z]{1,5})\s?[/\-_]\s?(\d{2,7})\b")
_NOT_BILL_PREFIX = {"UPI", "REF", "UTR", "TXN", "HSN", "GST", "GSTIN", "PIN", "OTP", "NO", "SR", "SL",
                    "V", "RS", "INR", "AC", "IFSC", "PH", "MOB", "DT", "AM", "PM", "KG", "ML", "L", "G"}


def normalize_bill_id(raw) -> str:
    """'gt-1187', 'GT 1187', 'GT/1187' -> 'GT/1187'. Unknown shapes are upper-cased."""
    if raw is None:
        return ""
    s = clean_text(str(raw)).strip().upper()
    s = re.sub(r"^(BILL|INVOICE|INV)\s*(NO\.?|NUMBER|#)?\s*[:.\-]?\s*", "", s)
    m = re.fullmatch(r"([A-Z]{1,5})\s*[/\-_ ]\s*(\d{1,7})", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return s.replace(" ", "")


def find_bill_ids(text: str) -> list[str]:
    ids = []
    for m in _BILL_ID_RE.finditer(clean_text(text).upper()):
        if m.group(1) in _NOT_BILL_PREFIX:
            continue
        bid = f"{m.group(1)}/{m.group(2)}"
        if bid not in ids:
            ids.append(bid)
    return ids


def bill_prefix(bill_id: str) -> str:
    m = re.match(r"([A-Z]{1,5})/", bill_id or "")
    return m.group(1) if m else ""


def bill_file_stem(bill_id: str) -> str:
    return (bill_id or "").replace("/", "-")


# ─────────────────────────────────────────────────────────────
# Products
# ─────────────────────────────────────────────────────────────
_PRODUCT_NOISE = re.compile(r"[^a-z0-9ऀ-ॿ ]+")
_PRODUCT_UNIT_FIX = [
    (re.compile(r"(\d+)\s*(gms?|grams?|gm)\b"), r"\1g"),
    (re.compile(r"(\d+)\s*(kgs?|kilo(gram)?s?)\b"), r"\1kg"),
    (re.compile(r"(\d+)\s*(ltrs?|litres?|liters?|lt)\b"), r"\1l"),
    (re.compile(r"(\d+)\s*ml\b"), r"\1ml"),
]
_PRODUCT_STOP = {"the", "of", "and", "pack", "new", "free", "foc", "scheme", "item", "ltd"}


def norm_product(name: str) -> str:
    s = clean_text(name).lower()
    for pat, rep in _PRODUCT_UNIT_FIX:
        s = pat.sub(rep, s)
    s = _PRODUCT_NOISE.sub(" ", s)
    toks = [t for t in s.split() if t not in _PRODUCT_STOP]
    return " ".join(toks)


def product_similarity(a: str, b: str) -> float:
    """0-100 similarity that tolerates word order, abbreviations and OCR noise."""
    na, nb = norm_product(a), norm_product(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    ta, tb = set(na.split()), set(nb.split())
    # sizes must not contradict ("500g" vs "1kg" are different products)
    sa = {t for t in ta if re.fullmatch(r"\d+(g|kg|l|ml)", t)}
    sb = {t for t in tb if re.fullmatch(r"\d+(g|kg|l|ml)", t)}
    if sa and sb and not (sa & sb):
        return 0.0
    if _rf_fuzz is not None:
        score = max(_rf_fuzz.token_set_ratio(na, nb), _rf_fuzz.token_sort_ratio(na, nb))
    else:
        inter = " ".join(sorted(ta & tb))
        ra = " ".join(sorted(ta - tb))
        rb = " ".join(sorted(tb - ta))
        cands = [SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()]
        if inter:
            cands.append(SequenceMatcher(None, inter, (inter + " " + ra).strip()).ratio())
            cands.append(SequenceMatcher(None, inter, (inter + " " + rb).strip()).ratio())
        score = 100.0 * max(cands)
    # a short name fully contained in a longer one ("Sunrise Glucose" in
    # "Sunrise Glucose Biscuit") is a strong match
    if ta <= tb or tb <= ta:
        score = max(score, 92.0)
    return float(score)


def best_product_match(name: str, candidates: Iterable[str], threshold: float = 80.0,
                       margin: float = 4.0) -> tuple[Optional[str], float]:
    """Return the unique best candidate above threshold, else (None, best_score)."""
    scored = sorted(((product_similarity(name, c), c) for c in candidates), key=lambda t: (-t[0], t[1]))
    if not scored or scored[0][0] < threshold:
        return None, (scored[0][0] if scored else 0.0)
    if len(scored) > 1 and scored[1][0] >= threshold and scored[0][0] - scored[1][0] < margin \
            and norm_product(scored[0][1]) != norm_product(scored[1][1]):
        return None, scored[0][0]
    return scored[0][1], scored[0][0]


# ─────────────────────────────────────────────────────────────
# Names
# ─────────────────────────────────────────────────────────────
COMPANY_WORDS = {
    "traders", "trader", "trading", "products", "product", "confectionery", "confectionary",
    "provisions", "provision", "wholesale", "wholesalers", "wholesaler", "distributors",
    "distributor", "distribution", "agencies", "agency", "enterprises", "enterprise", "suppliers",
    "supplier", "supply", "sales", "corporation", "corp", "foods", "food", "brothers", "bros",
    "industries", "marketing", "pvt", "ltd", "limited", "company", "co", "associates", "depot",
    "emporium", "bhandar", "mart", "stores", "store", "general", "kirana", "sons", "and", "&",
    "fmcg", "beverages", "dairy", "oils", "spices", "masala", "udyog", "vyapar", "group",
}
HONORIFICS = {"ji", "bhaiya", "bhai", "sir", "sirji", "shri", "sri", "mr", "mrs", "seth", "babu",
              "saheb", "sahab", "didi", "madam"}


def split_person_company(display_name: str) -> tuple[str, str]:
    """'Ramesh Ganga Traders' -> ('Ramesh', 'Ganga Traders').

    The company is the longest suffix that ends with a company word; the
    person is what precedes it. Names without company words return ('name', '').
    """
    name = re.sub(r"\s+", " ", clean_text(display_name)).strip(" -:()")
    name = re.sub(r"\(([^)]*)\)", r" \1 ", name).strip()
    toks = name.split()
    if not toks:
        return "", ""
    low = [t.lower().strip(".,") for t in toks]
    last_company = max((i for i, t in enumerate(low) if t in COMPANY_WORDS), default=-1)
    if last_company < 0:
        return name, ""
    # company starts after the first token (a person's first name) when there
    # are at least two tokens before the company word, else at token 1.
    start = 1 if len(toks) >= 3 else 0
    if start == 0:
        return "", name
    person = " ".join(toks[:start])
    company = " ".join(toks[start:])
    if person.lower() in COMPANY_WORDS:
        return "", name
    return person, company


def initials(company: str, size: int = 2) -> str:
    words = [w for w in re.findall(r"[A-Za-z]+", company) if w.lower() not in {"and", "pvt", "ltd", "the", "of", "co"}]
    if not words:
        return "XX"
    code = "".join(w[0] for w in words[:3]).upper()
    if len(code) == 1:
        code = (words[0][:2]).upper()
    return code


def name_tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-zऀ-ॿ]+", clean_text(name).lower())
            if t not in HONORIFICS and t not in COMPANY_WORDS and len(t) > 1}
