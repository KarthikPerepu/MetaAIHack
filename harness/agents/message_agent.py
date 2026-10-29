"""
Message understanding agent (chat lines and voice-note transcripts).

Turns one Hinglish / Devanagari / English message into structured facts:
schemes, spoken rate quotes, credit terms, payment claims, deliveries.

Two layers:
1. deterministic parser (always runs, offline);
2. the Meta text model, Muse Spark (when available), for wordings the rules miss.

Model output is accepted only if every number it reports appears in the
message (no invented quantities), and a scheme is only ever "permanent" when
the message itself contains a recurrence phrase. Treating a one-time scheme
as permanent is a capping mistake, so the default is always one_time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from harness.agents.llama_client import LlamaClient, wrap
from harness.textutil import (NUMBER_WORDS, UNIT_WORDS, clean_text, end_of_month, find_bill_ids,
                              find_dates, find_numbers, parse_number, words_to_digits)

logger = logging.getLogger(__name__)

_U = "|".join(sorted((re.escape(u) for u in UNIT_WORDS), key=len, reverse=True))
UNIT = rf"(?:\s*({_U})(?![a-z]))?"
FREE = r"(?:free|फ्री|फ़्री|muft|mufat|मुफ्त|मुफ़्त|extra|bonus|बोनस|foc)"
PER = r"(?:pe|par|per|on|पर|पे|ke\s*saath|ke\s*upar|के\s*साथ)"
TAKE = r"(?:lo|lijiye|lijie|le\s*lo|lene\s*par|lene\s*pe|order\s*karo|order\s*karne\s*par|kharido|mangao|लो|लीजिए|लेने\s*पर|लेने\s*पे|मंगाओ)"

SCHEME_PATTERNS = [
    # 10 pe 1 free / 10 carton par 1 carton free / 10 के साथ 1 फ्री
    ("per", re.compile(rf"(\d+(?:\.\d+)?){UNIT}\s*(?:[a-zऀ-ॿ]+\s+){{0,3}}?{PER}\s*(\d+(?:\.\d+)?){UNIT}\s*(?:[a-zऀ-ॿ]+\s+){{0,2}}?{FREE}", re.I)),
    # 20 carton lo, 2 carton free milega
    ("take", re.compile(rf"(\d+(?:\.\d+)?){UNIT}\s*(?:[a-zऀ-ॿ]+\s+){{0,3}}?{TAKE}(?![a-z])[^\d]{{0,40}}?(\d+(?:\.\d+)?){UNIT}\s*(?:[a-zऀ-ॿ]+\s+){{0,2}}?{FREE}", re.I)),
    # 2 carton free denge agar 20 carton loge
    ("rev", re.compile(rf"(\d+(?:\.\d+)?){UNIT}\s*{FREE}.{{0,40}}?(?:agar|if|jab|on|पर|अगर)\s*(\d+(?:\.\d+)?){UNIT}\s*(?:loge|lenge|lo|order|kharidoge|buy|purchase|लोगे|लेंगे)", re.I)),
    # buy 10 get 1
    ("buyget", re.compile(rf"buy\s*(\d+(?:\.\d+)?){UNIT}.{{0,20}}?get\s*(\d+(?:\.\d+)?){UNIT}", re.I)),
    # 10+1 scheme
    ("plus", re.compile(r"(?<![\d.])(\d{1,3})\s*\+\s*(\d{1,2})(?![\d.%])")),
]
PCT_DISCOUNT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent|pratishat|प्रतिशत|टका)\s*(?:ka\s*|की\s*|का\s*)?"
    r"(?:discount|disc|off|chhut|chhoot|chut|छूट|kam|less|cd|cash\s*discount|scheme)", re.I)
UNIT_DISCOUNT = re.compile(
    rf"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(?:rupaye|rupees|rs|₹|/-|रुपये)?\s*(?:per|prati|pe|har|प्रति|हर)\s*({_U})\s*"
    r"(?:ka\s*|ki\s*)?(?:discount|off|kam|chhut|less|छूट|कम)", re.I)
PERMANENT = re.compile(
    r"\b(hamesha|humesha|har\s*(order|bill|baar|bar|delivery|mahine|month|time)|every\s*(order|bill|time|delivery|month)|"
    r"always|standing|permanent(ly)?|regular(ly)?|lagataar|jab\s*bhi|aage\s*se\s*har|from\s*now\s*on|"
    r"till\s*further\s*notice|agle\s*notice\s*tak)\b|(हमेशा|हर\s*(ऑर्डर|बिल|बार|डिलीवरी|महीने)|आगे\s*से\s*हर)", re.I)
THIS_MONTH = re.compile(r"\b(is\s*mahine|iss\s*mahine|this\s*month|poore\s*mahine|is\s*month)\b|(इस\s*महीने)", re.I)
ONCE = re.compile(r"\b(sirf\s*(is|ek|iss)|only\s*(this|once|for\s*this)|ek\s*(hi\s*)?baar|one[\s-]*time|is\s*order|"
                  r"iss\s*order|agle\s*order|next\s*order|abhi\s*wale)\b|(सिर्फ\s*(इस|एक)|एक\s*(ही\s*)?बार|इस\s*ऑर्डर)", re.I)
CANCEL = re.compile(
    r"\b(scheme|offer|free)\b.{0,25}\b(band|khatam|khatm|cancel+(ed)?|withdrawn?|nahi\s*milega|nahin\s*milega|"
    r"nhi\s*milega|off\s*ho|bandh|over|ended)\b|(स्कीम|ऑफर).{0,20}(बंद|खत्म|ख़त्म|नहीं\s*मिलेगा)", re.I)
RATE_WORD = r"(?:rate|bhav|bhaav|daam|dam|price|keemat|kimat|dar|दाम|भाव|रेट|कीमत|दर)"
RATE_PATTERNS = [
    re.compile(rf"{RATE_WORD}\s*(?:[a-zऀ-ॿ]+\s+){{0,3}}?(?:hai|is|:|=|ab|ka|ki|hoga|rahega|lagega|lagaya|chalega|है|होगा|रहेगा)?\s*(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)", re.I),
    re.compile(rf"(?:₹|rs\.?)\s*(\d+(?:\.\d+)?)\s*(?:/|per|ka|ki|प्रति|का|की)\s*({_U})", re.I),
    re.compile(rf"(\d+(?:\.\d+)?)\s*(?:rupaye|rupees|rs|₹|रुपये)\s*(?:/|per|ka|ki|प्रति|का|की)\s*({_U})", re.I),
]
CREDIT_PATTERNS = [
    re.compile(r"(\d{1,3})\s*(?:din|days?|दिन)\s*(?:ka|ke|ki|का|के|की|of)?\s*(?:credit|udhaar|udhar|उधार|क्रेडिट|payment\s*time|time|ka\s*time)", re.I),
    re.compile(r"(?:credit|udhaar|udhar|उधार|क्रेडिट)\s*(?:period|time|terms|limit|days|ka\s*time)?\s*(?:hai|is|:|=|of|है)?\s*(\d{1,3})\s*(?:din|days?|दिन)", re.I),
    re.compile(r"(?:payment|bhugtan|भुगतान)\s*(\d{1,3})\s*(?:din|days?|दिन)\s*(?:mein|me|में|within|tak)", re.I),
]
INTEREST = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(p\.?\s*a\.?|per\s*annum|annual|yearly|saalana|salana|सालाना|monthly|per\s*month|p\.?m\.?|mahina|mahine|महीना|माहवार)?\s*"
    r"(?:ka\s*|ki\s*)?(?:byaj|byaaj|interest|ब्याज|sood|सूद)"
    r"|(?:byaj|byaaj|interest|ब्याज|sood)\s*(?:rate)?\s*(?:hai|is|:|@)?\s*(\d+(?:\.\d+)?)\s*%\s*"
    r"(p\.?\s*a\.?|per\s*annum|annual|yearly|saalana|salana|monthly|per\s*month|p\.?m\.?|mahina|mahine)?", re.I)
PAYMENT_DONE = re.compile(
    r"\b(upi|payment|paise|paisa|amount|rupaye|neft|imps|rtgs|gpay|phonepe|paytm|cash|transfer)\b.{0,40}"
    r"\b(kar\s*diya|kar\s*di|kar\s*diye|bhej\s*diya|bhej\s*diye|transfer(red)?|paid|de\s*diya|de\s*diye|jama\s*kar|ho\s*gaya|done|sent)\b"
    r"|\b(paid|transferred)\b|(भुगतान\s*कर\s*दिया|पेमेंट\s*कर\s*दिया|भेज\s*दिया)", re.I)
DELIVERY = re.compile(
    r"\b(deliver(y|ed)?|samaan|saman|maal|gaadi|gadi|supply|consignment|stock)\b.{0,40}"
    r"\b(ho\s*gaya|ho\s*gayi|aa\s*gaya|aa\s*gayi|aaya|aayi|aai|pahunch|pahuch|bhej\s*diya|nikal|delivered|mil\s*gaya|utar)"
    r"|\bdelivered\b|(डिलीवरी|माल|सामान|गाड़ी).{0,30}(हो\s*गई|हो\s*गया|आ\s*गया|आ\s*गई|पहुंच|पहुँच|भेज\s*दिया)", re.I)
ACCEPT = re.compile(r"\b(theek|thik|ok|okay|haan|han|done|accha|achha|chalega|sahi|bhej\s*do|bhejo|confirm)\b|(ठीक|हाँ|हां|भेज\s*दो)", re.I)
ORDER = re.compile(rf"(\d+(?:\.\d+)?){UNIT}\s*([a-zऀ-ॿ][a-zऀ-ॿ ]{{0,30}}?)?\s*(?:bhej\s*do|bhejo|bhejiye|bhej\s*dijiye|chahiye|mangwa|order|भेज\s*दो|भेजिए|चाहिए)", re.I)


@dataclass
class SchemeFact:
    kind: str = "free_goods"
    buy_qty: Optional[float] = None
    free_qty: Optional[float] = None
    unit: str = ""
    product_text: str = ""
    free_product_text: str = ""
    discount_pct: Optional[float] = None
    discount_per_unit: Optional[float] = None
    per_multiple: bool = True
    recurrence: str = "one_time"
    valid_until: Optional[date] = None
    source: str = "heuristic"


@dataclass
class MessageFacts:
    schemes: list[SchemeFact] = field(default_factory=list)
    rate_quotes: list[dict] = field(default_factory=list)       # {product_text, rate, unit}
    credit_days: Optional[int] = None
    interest_rate: Optional[float] = None                        # fraction p.a.
    payment_claim: Optional[dict] = None                         # {amount, bill_refs}
    delivery: bool = False
    cancels_scheme: bool = False
    accepts: bool = False
    order: Optional[dict] = None                                 # {qty, unit, product_text}
    bill_refs: list[str] = field(default_factory=list)
    translation: str = ""
    used_model: bool = False

    @property
    def empty(self) -> bool:
        return not (self.schemes or self.rate_quotes or self.credit_days or self.payment_claim
                    or self.delivery or self.cancels_scheme)


def normalize(text: str) -> str:
    t = clean_text(text).lower()
    t = t.replace("₹ ", "₹")
    return words_to_digits(t)


def _unit(u: Optional[str]) -> str:
    return UNIT_WORDS.get((u or "").lower(), "")


def _context_words(t: str, start: int, end: int) -> str:
    """Words around a scheme mention, used later to guess the product."""
    left = t[max(0, start - 60):start]
    right = t[end:end + 60]
    return (left + " " + t[start:end] + " " + right).strip()


def _recurrence(t: str, ref: Optional[date]) -> tuple[str, Optional[date]]:
    if ONCE.search(t):
        return "one_time", None
    if THIS_MONTH.search(t) and ref:
        return "until", end_of_month(ref)
    if PERMANENT.search(t):
        return "permanent", None
    m = re.search(r"(tak|till|until|upto|up\s*to|तक)", t)
    if m and ref:
        dates = [d for d, s, e in find_dates(t, ref_year=ref.year) if d >= ref]
        if dates:
            return "until", dates[0]
    return "one_time", None


def heuristic_facts(text: str, *, ref_date: Optional[date] = None, is_owner: bool = False) -> MessageFacts:
    t = normalize(text)
    facts = MessageFacts(bill_refs=find_bill_ids(clean_text(text)))
    rec, until = _recurrence(t, ref_date)

    seen_spans: list[tuple[int, int]] = []
    for kind, pat in SCHEME_PATTERNS:
        for m in pat.finditer(t):
            if any(not (m.end() <= s or m.start() >= e) for s, e in seen_spans):
                continue
            if kind == "plus" and not re.search(r"(scheme|free|offer|milega|milenge|denge|dega|स्कीम|फ्री|ऑफर|मिलेगा|bonus|deal)", t):
                continue
            groups = m.groups()
            if kind in ("per", "take", "buyget"):
                buy, ubuy, free, ufree = groups[0], groups[1], groups[2], groups[3]
            elif kind == "rev":
                free, ufree, buy, ubuy = groups[0], groups[1], groups[2], groups[3]
            else:
                buy, ubuy, free, ufree = groups[0], None, groups[1], None
            b, f = parse_number(buy), parse_number(free)
            if not b or not f or f >= b * 2 or b > 10000:
                continue
            seen_spans.append((m.start(), m.end()))
            facts.schemes.append(SchemeFact(
                kind="free_goods", buy_qty=b, free_qty=f, unit=_unit(ubuy) or _unit(ufree),
                product_text=_context_words(t, m.start(), m.end()),
                per_multiple=(kind not in ("take", "rev")), recurrence=rec, valid_until=until))
    for m in PCT_DISCOUNT.finditer(t):
        pct = parse_number(m.group(1))
        if pct and 0 < pct < 60:
            facts.schemes.append(SchemeFact(kind="discount_pct", discount_pct=pct,
                                            product_text=_context_words(t, m.start(), m.end()),
                                            recurrence=rec, valid_until=until))
    for m in UNIT_DISCOUNT.finditer(t):
        amt = parse_number(m.group(1))
        if amt and amt > 0:
            facts.schemes.append(SchemeFact(kind="discount_per_unit", discount_per_unit=amt, unit=_unit(m.group(2)),
                                            product_text=_context_words(t, m.start(), m.end()),
                                            recurrence=rec, valid_until=until))
    if CANCEL.search(t):
        facts.cancels_scheme = True
        facts.schemes = []

    if not facts.schemes:
        for pat in RATE_PATTERNS:
            for m in pat.finditer(t):
                rate = parse_number(m.group(1))
                if not rate or rate <= 0:
                    continue
                window = t[max(0, m.start() - 50):m.end() + 10]
                # "5000 due hai", "bill ka total" are not rates
                if re.search(r"\b(due|baki|baaki|total|bill\s*ka|outstanding|payment|upi|बाकी)\b", window):
                    continue
                unit = _unit(m.group(2)) if m.lastindex and m.lastindex >= 2 else ""
                facts.rate_quotes.append({"product_text": window, "rate": rate, "unit": unit})
            if facts.rate_quotes:
                break

    for pat in CREDIT_PATTERNS:
        m = pat.search(t)
        if m:
            d = int(m.group(1))
            if 1 <= d <= 180:
                facts.credit_days = d
                break
    m = INTEREST.search(t)
    if m:
        val = parse_number(m.group(1) or m.group(3))
        period = (m.group(2) or m.group(4) or "").lower()
        if val:
            monthly = any(k in period for k in ("month", "mahin", "p.m", "pm", "महीना", "माहवार"))
            facts.interest_rate = round((val * 12 if monthly else val) / 100.0, 6)

    if PAYMENT_DONE.search(t):
        amts = [n for n in find_numbers(re.sub(r"[a-z]{1,5}/\d+", " ", t)) if n >= 50]
        facts.payment_claim = {"amount": max(amts) if amts else None, "bill_refs": facts.bill_refs}
    if DELIVERY.search(t):
        facts.delivery = True
    if is_owner and ACCEPT.search(t):
        facts.accepts = True
    om = ORDER.search(t)
    if om:
        facts.order = {"qty": parse_number(om.group(1)), "unit": _unit(om.group(2)),
                       "product_text": (om.group(3) or "").strip()}
    return facts


def _numbers_in(text: str) -> set[float]:
    t = normalize(text)
    nums = set(find_numbers(t))
    for w, n in NUMBER_WORDS.items():
        if re.search(rf"(?<![\wऀ-ॿ]){re.escape(w)}(?![\wऀ-ॿ])", t):
            nums.add(float(n))
    return nums


def _grounded(value, nums: set[float]) -> bool:
    if value in (None, ""):
        return True
    v = parse_number(value)
    return v is not None and any(abs(v - n) < 1e-6 for n in nums)


class MessageAgent:
    def __init__(self, llama: Optional[LlamaClient] = None):
        self.llama = llama

    def understand(self, text: str, *, sender_role: str, ref_date: Optional[date],
                   known_products: list[str], is_owner: bool = False) -> MessageFacts:
        facts = heuristic_facts(text, ref_date=ref_date, is_owner=is_owner)
        if not self.llama or not self.llama.available:
            return facts
        if not self._worth_asking(text, facts):
            return facts
        model = self._ask_model(text, sender_role, ref_date, known_products)
        if model is None:
            return facts
        return self._merge(text, facts, model, ref_date)

    @staticmethod
    def _worth_asking(text: str, facts: MessageFacts) -> bool:
        t = normalize(text)
        keywords = r"(free|scheme|offer|discount|chhut|rate|bhav|daam|price|credit|udhaar|byaj|interest|%|\d|" \
                   r"फ्री|स्कीम|छूट|दाम|भाव|रेट|उधार|ब्याज|मुफ्त)"
        return bool(re.search(keywords, t)) and len(t) >= 8

    def _ask_model(self, text: str, role: str, ref_date: Optional[date], products: list[str]) -> Optional[dict]:
        prompt = (
            "Extract trade facts from one WhatsApp message (Hinglish, Hindi in Devanagari, or English) "
            f"sent by the {role} on {ref_date.isoformat() if ref_date else 'unknown date'}.\n"
            f"Products this distributor sells: {products[:40]}\n"
            f"{wrap(text[:1500])}\n"
            "Return JSON:\n"
            '{"translation": "English meaning",\n'
            ' "schemes": [{"kind": "free_goods|discount_pct|discount_per_unit", "product": "name from the list or null",'
            ' "buy_qty": n|null, "free_qty": n|null, "unit": "carton|bag|...|null", "free_product": null,'
            ' "discount_pct": n|null, "discount_per_unit": n|null, "per_multiple": true|false,'
            ' "recurrence": "one_time|permanent|until", "recurrence_phrase": "exact words that say it repeats, or null",'
            ' "valid_until": "YYYY-MM-DD|null"}],\n'
            ' "cancels_scheme": true|false,\n'
            ' "rate_quotes": [{"product": "name or null", "rate": n, "unit": "..."}],\n'
            ' "credit_days": n|null, "interest_rate_percent_per_year": n|null,\n'
            ' "payment_claim": {"amount": n|null, "bill_ids": []} | null,\n'
            ' "delivery_confirmed": true|false}\n'
            "Rules: a scheme is one_time unless the message clearly says it repeats (hamesha, har order, "
            "every order, standing). 'X pe Y free' means per X units (per_multiple true). Only report a "
            "rate_quote when a price per unit for a product is stated. Do not report bill totals or dues as rates."
        )
        return self.llama.chat_json(prompt, max_tokens=900)

    def _merge(self, text: str, facts: MessageFacts, data: dict, ref_date: Optional[date]) -> MessageFacts:
        nums = _numbers_in(text)
        t = normalize(text)
        out = MessageFacts(bill_refs=facts.bill_refs, accepts=facts.accepts, order=facts.order,
                           delivery=facts.delivery or bool(data.get("delivery_confirmed")),
                           used_model=True, translation=str(data.get("translation") or "")[:300])
        schemes = []
        for s in data.get("schemes") or []:
            if not isinstance(s, dict):
                continue
            vals = [s.get("buy_qty"), s.get("free_qty"), s.get("discount_pct"), s.get("discount_per_unit")]
            if not all(_grounded(v, nums) for v in vals):
                logger.info("Model scheme rejected: numbers not found in message")
                continue
            kind = s.get("kind") or "free_goods"
            if kind == "free_goods" and not (parse_number(s.get("buy_qty")) and parse_number(s.get("free_qty"))):
                continue
            if kind == "discount_pct" and not parse_number(s.get("discount_pct")):
                continue
            if kind == "discount_per_unit" and not parse_number(s.get("discount_per_unit")):
                continue
            rec = str(s.get("recurrence") or "one_time")
            phrase = str(s.get("recurrence_phrase") or "").lower().strip()
            heur_rec, heur_until = _recurrence(t, ref_date)
            if rec == "permanent" and heur_rec != "permanent" and not (phrase and phrase in t):
                rec = "one_time"          # never permanent without words that say so
            until = None
            if rec == "until":
                until = heur_until
                if s.get("valid_until"):
                    from harness.textutil import from_iso
                    until = from_iso(s.get("valid_until")) or until
                if not until:
                    rec = "one_time"
            schemes.append(SchemeFact(
                kind=kind, buy_qty=parse_number(s.get("buy_qty")), free_qty=parse_number(s.get("free_qty")),
                unit=_unit(s.get("unit")) or str(s.get("unit") or "").lower(),
                product_text=str(s.get("product") or ""), free_product_text=str(s.get("free_product") or ""),
                discount_pct=parse_number(s.get("discount_pct")),
                discount_per_unit=parse_number(s.get("discount_per_unit")),
                per_multiple=bool(s.get("per_multiple", True)), recurrence=rec, valid_until=until, source="llama"))
        # The rules are the floor: used only when the model gave no valid scheme, so one
        # offer is never counted twice under two readings.
        if not schemes:
            schemes = list(facts.schemes)
        out.schemes = schemes
        out.cancels_scheme = bool(data.get("cancels_scheme")) or facts.cancels_scheme
        if out.cancels_scheme and not schemes:
            out.schemes = []
        quotes = []
        for q in data.get("rate_quotes") or []:
            if isinstance(q, dict) and _grounded(q.get("rate"), nums) and parse_number(q.get("rate")):
                quotes.append({"product_text": str(q.get("product") or ""), "rate": parse_number(q.get("rate")),
                               "unit": _unit(q.get("unit"))})
        out.rate_quotes = quotes or facts.rate_quotes
        cd = data.get("credit_days")
        out.credit_days = int(parse_number(cd)) if cd and _grounded(cd, nums) else facts.credit_days
        ir = data.get("interest_rate_percent_per_year")
        out.interest_rate = facts.interest_rate
        if ir and facts.interest_rate is None and (_grounded(ir, nums) or _grounded(float(parse_number(ir) or 0) / 12, nums)):
            out.interest_rate = round(parse_number(ir) / 100.0, 6)
        pc = data.get("payment_claim")
        out.payment_claim = facts.payment_claim
        if isinstance(pc, dict) and facts.payment_claim is None and _grounded(pc.get("amount"), nums) and pc.get("amount"):
            out.payment_claim = {"amount": parse_number(pc.get("amount")),
                                 "bill_refs": [b for b in (pc.get("bill_ids") or []) if b] or facts.bill_refs}
        return out


def _same_scheme(a: SchemeFact, b: SchemeFact) -> bool:
    return (a.kind == b.kind and a.buy_qty == b.buy_qty and a.free_qty == b.free_qty
            and a.discount_pct == b.discount_pct and a.discount_per_unit == b.discount_per_unit)
