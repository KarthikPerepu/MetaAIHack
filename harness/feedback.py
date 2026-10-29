"""
Learning from the owner's answers (``harness.py feedback <file> --state-dir``).

Accepts a JSON list (or {"feedback": [...]}) of items such as

    {"bill_id": "GT/1187", "class": "rate_mismatch", "feedback": "agreed"}
    {"bill_id": "GT/1179", "class": "rate_mismatch", "feedback": "wrong", "comment": "410 was agreed"}
    {"bill_id": "GT/1187", "class": "rate_mismatch", "resolution": "ratelist_v2.pdf"}      # settle a conflict
    {"bill_id": "BW/3011", "class": "rate_mismatch", "provided": {"rate": 300}}            # answer cannot_determine
    {"distributor": "SP", "credit_days": 45, "interest_rate": 0.15}
    {"bill_id": "KP/8801", "status": "paid", "method": "cash"}
    {"distributor": "GT", "salesman": "Suresh", "can_offer_schemes": false}
    {"distributor": "BW", "product": "Amul Condensed Milk", "hsn": "0402", "gst_rate": 5}
    {"bill_id": "SP/4412", "class": "duplicate_bill", "feedback": "not a duplicate, two deliveries"}
    {"comment": "Sharma Products ka credit 45 din ka hai"}                                   # free text

Each item becomes a normalised decision in <state>/feedback.json that every
later report applies: rejected findings stay suppressed, confirmed ones get
high confidence, answered questions unblock the check, conflict choices are
remembered, and rates the owner says were agreed apply to later bills too.
Free text is parsed by rules and, when available, by the Llama text model.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from harness.agents.llama_client import LlamaClient, wrap
from harness.agents.message_agent import heuristic_facts
from harness.config import HarnessConfig
from harness.knowledge.models import Contact, CreditTerms, SourceRef
from harness.knowledge.store import KnowledgeStore
from harness.textutil import (bill_prefix, clean_text, find_bill_ids, name_tokens, normalize_bill_id, parse_number)

logger = logging.getLogger(__name__)

YES = re.compile(r"\b(agree[d]?|confirm(ed)?|correct|right|yes|approved?|valid|true|sahi|haan|han|theek|thik|ok|okay|"
                 r"claim|accepted?|genuine|real)\b|(सही|हाँ|हां|ठीक)", re.I)
NO = re.compile(r"\b(reject(ed)?|wrong|incorrect|not\s*(an?\s*)?(issue|error|problem|duplicate|valid)|false|no|invalid|"
                r"galat|nahi|nahin|mistake|ignore|fine|clean|ok\s*hai\s*bill|was\s*agreed|special\s*rate|"
                r"two\s*deliveries|alag\s*delivery)\b|(गलत|नहीं)", re.I)
PAID = re.compile(r"\b(paid|settled|cash\s*(mein|me|diya|de\s*diya)|de\s*diya|chuka\s*diya|jama\s*kar\s*diya)\b|(भुगतान)", re.I)
CLASS_WORDS = {
    "rate_mismatch": r"(rate|price|bhav|daam)", "duplicate_bill": r"(duplicate|double|same\s*bill|do\s*baar)",
    "scheme_not_given": r"(scheme|free|offer)", "paid_but_shown_due": r"(statement|already\s*paid|due\s*dikh)",
    "gst_error": r"(gst|tax|hsn)", "credit_period_risk": r"(credit|interest|byaj)",
}


class FeedbackLearner:
    def __init__(self, config: HarnessConfig, store: KnowledgeStore, llama: Optional[LlamaClient] = None):
        self.config = config
        self.store = store
        self.llama = llama

    def process_feedback_file(self, path: str) -> dict:
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("feedback") or data.get("answers") or data.get("items") or [data]
        return self.process(data, source=p.name)

    def process(self, items: list, source: str = "feedback") -> dict:
        stats = {"items": 0, "decisions": 0, "credit_terms": 0, "contacts": 0, "ignored": 0}
        stamp = datetime.now().isoformat(timespec="seconds")
        for item in items:
            stats["items"] += 1
            if isinstance(item, str):
                item = {"comment": item}
            if not isinstance(item, dict):
                stats["ignored"] += 1
                continue
            decisions = self.interpret(item)
            if not decisions:
                stats["ignored"] += 1
                logger.info(f"Feedback item not understood: {str(item)[:120]}")
            for d in decisions:
                d.setdefault("source", source)
                d["recorded_at"] = stamp
                d["original"] = item
                if d["type"] == "credit_terms":
                    self._apply_terms(d)
                    stats["credit_terms"] += 1
                elif d["type"] == "person":
                    self._apply_person(d)
                    stats["contacts"] += 1
                elif d["type"] == "scheme":
                    self._apply_scheme(d)
                if not any(_same(d, old) for old in self.store.feedback):
                    self.store.feedback.append(d)
                    stats["decisions"] += 1
        self.store.save()
        return stats

    # ── interpretation ───────────────────────────────────────
    def _dist(self, item: dict) -> Optional[str]:
        raw = item.get("distributor") or item.get("distributor_code") or item.get("supplier")
        if raw:
            raw = str(raw)
            if self.store.card(raw.upper()):
                return raw.upper()
            want = name_tokens(raw)
            for c in self.store.all_cards():
                if want and want & name_tokens(c.name):
                    return c.code
        bid = normalize_bill_id(item.get("bill_id") or "")
        if bid:
            for c in self.store.all_cards():
                if any(b.bill_id == bid for b in c.bills) or any(e.bill_id == bid for s in c.statements for e in s.entries):
                    return c.code
            if self.store.card(bill_prefix(bid)):
                return bill_prefix(bid)
        text = clean_text(str(item.get("comment") or item.get("text") or "")).lower()
        for c in self.store.all_cards():
            if name_tokens(c.name) and name_tokens(c.name) <= name_tokens(text):
                return c.code
            for p in c.contacts:
                if name_tokens(p.name) and name_tokens(p.name) <= name_tokens(text):
                    return c.code
        return None

    def interpret(self, item: dict) -> list[dict]:
        out: list[dict] = []
        dist = self._dist(item)
        bill_id = normalize_bill_id(item.get("bill_id") or "")
        comment = str(item.get("comment") or item.get("text") or item.get("note") or "")
        if not bill_id and comment:
            ids = find_bill_ids(comment)
            bill_id = ids[0] if ids else ""
            if bill_id and not dist:
                dist = self._dist({"bill_id": bill_id})
        cls = item.get("class") or item.get("finding_class")
        if not cls and comment:
            for c, pat in CLASS_WORDS.items():
                if re.search(pat, comment, re.I):
                    cls = c
                    break
        verdict_raw = str(item.get("feedback") or item.get("verdict") or item.get("decision") or item.get("answer")
                          or item.get("status") or "")

        # credit terms
        days = item.get("credit_days")
        rate = item.get("interest_rate")
        if days is None and comment:
            f = heuristic_facts(comment)
            if f.credit_days:
                days = f.credit_days
                rate = rate if rate is not None else f.interest_rate
        if days is not None and dist:
            r = parse_number(rate) if rate is not None else None
            if r is not None and r > 1:
                r = r / 100.0
            out.append({"type": "credit_terms", "distributor": dist, "days": int(parse_number(days)),
                        "interest_rate": r})

        # salesman permissions
        person = item.get("salesman") or item.get("person") or item.get("person_name")
        if person and dist:
            out.append({"type": "person", "distributor": dist, "name": str(person),
                        "can_offer_schemes": bool(item.get("can_offer_schemes", True))})

        # payment made outside UPI screenshots
        if bill_id and (verdict_raw.lower() in ("paid", "settled") or item.get("is_paid") or
                        (comment and PAID.search(comment) and not cls)):
            out.append({"type": "paid", "bill_id": bill_id, "distributor": dist,
                        "amount": parse_number(item.get("amount") or item.get("amount_inr")),
                        "method": item.get("method") or ("cash" if "cash" in comment.lower() else "")})

        # HSN / GST answers
        prov = item.get("provided") if isinstance(item.get("provided"), dict) else {}
        hsn = item.get("hsn") or prov.get("hsn")
        gst = item.get("gst_rate") if item.get("gst_rate") is not None else prov.get("gst_rate")
        product = item.get("product") or prov.get("product")
        if (hsn or gst is not None) and dist:
            g = parse_number(gst) if gst is not None else None
            out.append({"type": "hsn", "distributor": dist, "product": product or self._bill_product(dist, bill_id),
                        "hsn": str(hsn) if hsn else None, "gst_rate": (g / 100 if g and g > 1 else g)})

        # rates the owner says apply
        agreed = item.get("agreed_rate") or prov.get("rate") or (item.get("rate") if cls != "credit_period_risk" else None)
        if agreed is None and comment and cls in (None, "rate_mismatch") and NO.search(comment):
            m = re.search(r"(?:₹|rs\.?|rate|@)\s*(\d+(?:\.\d+)?)", comment, re.I) or \
                re.search(r"(\d+(?:\.\d+)?)\s*(?:pe|par|ka|rate|wala)", comment, re.I)
            if m:
                agreed = m.group(1)
        if agreed is not None and dist:
            prod = product or self._bill_product(dist, bill_id)
            if prod:
                out.append({"type": "rate", "distributor": dist, "product": prod, "rate": parse_number(agreed),
                            "bill_id": bill_id or None, "from": item.get("effective_date") or self._bill_date(dist, bill_id),
                            "applies_forward": True})

        # conflict choice
        resolution = item.get("resolution") or item.get("choose") or item.get("accept_source")
        if resolution and bill_id:
            out.append({"type": "conflict_resolution", "bill_id": bill_id, "class": cls, "choose": resolution})
            val = parse_number(resolution)
            src = str(resolution).lower()
            if cls in (None, "rate_mismatch") and dist:
                chosen = val if val else self._rate_from_source(dist, bill_id, src)
                prod = product or self._bill_product(dist, bill_id)
                if chosen and prod:
                    out.append({"type": "rate", "distributor": dist, "product": prod, "rate": chosen, "bill_id": bill_id,
                                "from": self._bill_date(dist, bill_id), "applies_forward": False})

        # scheme confirmation details
        if cls == "scheme_not_given" and (item.get("free_qty") or item.get("buy_qty")) and dist:
            out.append({"type": "scheme", "distributor": dist, "product": product, "bill_id": bill_id,
                        "buy_qty": parse_number(item.get("buy_qty")), "free_qty": parse_number(item.get("free_qty")),
                        "recurrence": "permanent" if item.get("is_permanent") or item.get("scheme_type") == "permanent"
                        else "one_time"})

        # duplicates the owner says were two deliveries
        if cls == "duplicate_bill" and bill_id and (NO.search(verdict_raw) or NO.search(comment)):
            ids = [bill_id] + [normalize_bill_id(x) for x in (item.get("related_bill_ids") or [])]
            out.append({"type": "not_duplicate", "bill_ids": ids})

        # verdict on a finding
        if bill_id:
            verdict = None
            text = f"{verdict_raw} {comment}"
            if verdict_raw and NO.search(verdict_raw):
                verdict = "rejected"
            elif verdict_raw and YES.search(verdict_raw):
                verdict = "confirmed"
            elif comment and NO.search(comment) and not YES.search(comment):
                verdict = "rejected"
            elif comment and YES.search(comment):
                verdict = "confirmed"
            if verdict and not any(d["type"] == "paid" for d in out):
                out.append({"type": "verdict", "bill_id": bill_id, "class": cls, "verdict": verdict,
                            "amount": parse_number(item.get("corrected_amount") or item.get("amount_inr")),
                            "comment": comment})

        if not out and comment and self.llama and self.llama.available:
            out = self._llm_interpret(item, dist)
        return out

    def _llm_interpret(self, item: dict, dist: Optional[str]) -> list[dict]:
        cards = [{"code": c.code, "name": c.name} for c in self.store.all_cards()]
        prompt = ("A kirana shop owner answered questions from the bookkeeping assistant. Turn the answer into "
                  "decisions. Distributors: " + json.dumps(cards, ensure_ascii=False) + "\n" + wrap(json.dumps(item, ensure_ascii=False))
                  + '\nReturn {"decisions": [{"type": "verdict|paid|credit_terms|rate|hsn|person|not_duplicate", '
                    '"bill_id": null, "class": null, "verdict": "confirmed|rejected|null", "distributor": "code|null", '
                    '"days": null, "interest_rate": null, "product": null, "rate": null, "hsn": null, "gst_rate": null, '
                    '"name": null, "can_offer_schemes": null}]}')
        data = self.llama.chat_json(prompt, max_tokens=600) or {}
        out = []
        for d in data.get("decisions") or []:
            if not isinstance(d, dict) or d.get("type") not in ("verdict", "paid", "credit_terms", "rate", "hsn",
                                                                  "person", "not_duplicate"):
                continue
            d = {k: v for k, v in d.items() if v is not None}
            d.setdefault("distributor", dist)
            if d.get("bill_id"):
                d["bill_id"] = normalize_bill_id(d["bill_id"])
            if d["type"] == "credit_terms" and not d.get("days"):
                continue
            d["parsed_by"] = "llama"
            out.append(d)
        return out

    # ── helpers ──────────────────────────────────────────────
    def _bill(self, dist, bill_id):
        card = self.store.card(dist) if dist else None
        return next((b for b in card.bills if b.bill_id == bill_id), None) if card and bill_id else None

    def _bill_product(self, dist, bill_id) -> Optional[str]:
        b = self._bill(dist, bill_id)
        paid = [l for l in b.lines if not l.is_free] if b else []
        return paid[0].product if len(paid) == 1 else None

    def _bill_date(self, dist, bill_id) -> Optional[str]:
        b = self._bill(dist, bill_id)
        return b.bill_date.isoformat() if b else None

    def _rate_from_source(self, dist, bill_id, src: str) -> Optional[float]:
        card = self.store.card(dist)
        b = self._bill(dist, bill_id)
        if not card or not b:
            return None
        prod = self._bill_product(dist, bill_id)
        from harness.textutil import product_similarity
        if "rate" in src or src.endswith(".pdf") or "list" in src:
            for rl in sorted(card.rate_lists, key=lambda r: r.source.received_at or datetime.min, reverse=True):
                if src.endswith(".pdf") and rl.source.file.lower() != src:
                    continue
                for e in rl.entries:
                    if prod and product_similarity(e.product, prod) >= 85:
                        return e.rate
        for q in card.rate_quotes:
            if src in (q.source.file.lower(), q.source.cite().lower()) or "voice" in src or "salesman" in src:
                return q.rate
        return None

    def _apply_terms(self, d: dict) -> None:
        card = self.store.card(d["distributor"])
        if not card:
            return
        rate = d.get("interest_rate")
        card.credit_terms.append(CreditTerms(days=int(d["days"]),
                                             interest_rate=rate if rate is not None else -1.0,
                                             source_kind="owner_feedback", source=None,
                                             stated_on=datetime.now(), raw_text=str(d.get("original", ""))[:200]))

    def _apply_scheme(self, d: dict) -> None:
        """Owner confirms a scheme: mark the matching offer as acknowledged, or record it."""
        card = self.store.card(d["distributor"])
        if not card or not d.get("buy_qty") or not d.get("free_qty"):
            return
        from harness.knowledge.models import Authority, Scheme
        from harness.textutil import from_iso, product_similarity
        ratio = d["free_qty"] / d["buy_qty"]
        for s in card.schemes:
            if s.kind == "free_goods" and s.buy_qty and abs((s.free_qty or 0) / s.buy_qty - ratio) < 1e-6:
                s.owner_acknowledged = True
                if d.get("product") and not s.product:
                    s.product = d["product"]
                return
        bill = self._bill(card.code, d.get("bill_id"))
        start = bill.bill_date if bill else None
        import hashlib
        card.schemes.append(Scheme(
            record_id="owner-" + hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:12],
            distributor=card.code, source=SourceRef(file="owner_feedback", sender="owner",
                                                    received_at=datetime.combine(start, datetime.min.time()) if start else None),
            kind="free_goods", product=d.get("product"), buy_qty=d["buy_qty"], free_qty=d["free_qty"],
            per_multiple=False, recurrence=d.get("recurrence", "one_time"), valid_from=start, agreed_by="owner",
            authority=Authority.OWNER, source_kind="owner_feedback", owner_acknowledged=True,
            raw_text=str(d.get("original", ""))[:200]))

    def _apply_person(self, d: dict) -> None:
        card = self.store.card(d["distributor"])
        if not card:
            return
        c = card.contact(d["name"])
        if c:
            c.can_offer_schemes = d["can_offer_schemes"]
            c.source_kind = "owner_feedback"
        else:
            card.contacts.append(Contact(name=d["name"], can_offer_schemes=d["can_offer_schemes"],
                                         source_kind="owner_feedback"))


def _same(a: dict, b: dict) -> bool:
    keys = [k for k in a if k not in ("recorded_at", "original", "source")]
    return all(a.get(k) == b.get(k) for k in keys) and a.get("type") == b.get("type")
