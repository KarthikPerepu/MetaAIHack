"""
The as-of view of the khata, plus the shared reasoning the detectors use.

A report for ``--as-of D`` only sees records that had *arrived* by the end
of day D. On top of that view this module answers:

* which rate was in force for a product on a bill date
  (latest-received list whose effective window covers the date; a backdated
  list received later wins; two lists with the same effective date that
  disagree, neither marked as a correction, are a conflict);
* whether a named salesman's spoken rate disagrees with the signed list;
* which bills are paid (UPI reference, statement, owner feedback, or a unique
  amount match) and what is still outstanding;
* the credit terms that apply to a distributor;
* owner decisions from feedback that override or suppress results.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from harness.config import HarnessConfig
from harness.knowledge.models import (AUTHORITY_WEIGHT, Authority, Bill, BillLine, CreditTerms, DistributorCard,
                                      Payment, RateEntry, RateList, RateQuote, Scheme, Statement)
from harness.knowledge.store import KnowledgeStore
from harness.textutil import (best_product_match, end_of_day, fmt_num, from_iso, norm_product, normalize_bill_id,
                              product_similarity, split_person_company)

logger = logging.getLogger(__name__)
NEVER = datetime.min


def recv(rec) -> datetime:
    src = getattr(rec, "source", None)
    return (src.received_at if src and src.received_at else None) or NEVER


@dataclass
class RateResolution:
    product: str
    entry: Optional[RateEntry] = None
    rate_list: Optional[RateList] = None
    match_score: float = 0.0
    conflict: list = field(default_factory=list)    # [(source_label, rate, ref), ...]
    owner_rate: Optional[dict] = None
    lists_in_force: int = 0
    note: str = ""

    @property
    def rate(self) -> Optional[float]:
        if self.owner_rate:
            return self.owner_rate["rate"]
        return self.entry.rate if self.entry else None


class View:
    def __init__(self, store: KnowledgeStore, as_of: date, config: HarnessConfig, llama=None):
        self.store = store
        self.as_of = as_of
        self.cutoff = end_of_day(as_of)
        self.config = config
        self.policy = config.policy
        self.llama = llama
        self.feedback = [f for f in store.feedback if isinstance(f, dict)]
        self.owner_name = (store.shop.get("shop_name") or (store.shop.get("owner_names") or ["the shop"])[0])
        self._paid_cache: dict = {}
        self._assign_payments()

    # ── visibility ───────────────────────────────────────────
    def known(self, rec) -> bool:
        return recv(rec) <= self.cutoff

    def cards(self) -> list[DistributorCard]:
        return self.store.all_cards()

    def bills(self, card: DistributorCard) -> list[Bill]:
        out = [b for b in card.bills if self.known(b) and b.bill_date <= self.as_of]
        return sorted(out, key=lambda b: (b.bill_date, recv(b), b.bill_id))

    def rate_lists(self, card: DistributorCard) -> list[RateList]:
        return sorted((r for r in card.rate_lists if self.known(r)), key=lambda r: (recv(r), r.effective_from or date.min))

    def quotes(self, card: DistributorCard) -> list[RateQuote]:
        return [q for q in card.rate_quotes if self.known(q)]

    def schemes(self, card: DistributorCard) -> list[Scheme]:
        out = []
        for s in card.schemes:
            if not self.known(s):
                continue
            if s.cancelled_at and s.cancelled_at <= self.cutoff:
                continue
            out.append(s)
        return sorted(out, key=lambda s: (recv(s), s.record_id))

    def payments(self, card: DistributorCard) -> list[Payment]:
        out, refs = [], set()
        for p in sorted((p for p in card.payments if self.known(p)), key=lambda p: (p.payment_date, recv(p), p.record_id)):
            if p.upi_ref and p.upi_ref in refs:
                continue  # the same screenshot forwarded twice
            refs.add(p.upi_ref) if p.upi_ref else None
            out.append(p)
        return out

    def statements(self, card: DistributorCard) -> list[Statement]:
        return sorted((s for s in card.statements if self.known(s) and s.statement_date <= self.as_of),
                      key=lambda s: (s.statement_date, recv(s)))

    # ── people ───────────────────────────────────────────────
    def salesman(self, card: DistributorCard) -> str:
        for c in card.contacts:
            if c.name:
                return c.name
        return card.name

    def contact_allowed(self, card: DistributorCard, name: str) -> Optional[bool]:
        for fb in self.feedback:
            if fb.get("type") == "person" and fb.get("distributor") == card.code and \
                    product_similarity(fb.get("name", ""), name) >= 90:
                return bool(fb.get("can_offer_schemes", True))
        c = card.contact(name)
        if c is None:
            return None
        return c.can_offer_schemes

    # ── feedback lookups ─────────────────────────────────────
    def verdict(self, bill_id: str, cls: str) -> Optional[dict]:
        hit = None
        for fb in self.feedback:
            if fb.get("type") == "verdict" and normalize_bill_id(fb.get("bill_id")) == bill_id and \
                    fb.get("class") in (cls, None, "", "any"):
                hit = fb
        return hit

    def owner_rate(self, card: DistributorCard, product: str, on: date, bill_id: str = "") -> Optional[dict]:
        best = None
        for fb in self.feedback:
            if fb.get("type") != "rate" or fb.get("distributor") != card.code:
                continue
            if fb.get("bill_id") and normalize_bill_id(fb["bill_id"]) != bill_id and not fb.get("applies_forward"):
                continue
            if product_similarity(fb.get("product", ""), product) < 85:
                continue
            start = from_iso(fb.get("from"))
            if start and on < start:
                continue
            best = fb
        return best

    def resolution_for(self, bill_id: str, cls: str) -> Optional[dict]:
        hit = None
        for fb in self.feedback:
            if fb.get("type") == "conflict_resolution" and normalize_bill_id(fb.get("bill_id")) == bill_id \
                    and fb.get("class") in (cls, None, ""):
                hit = fb
        return hit

    def owner_hsn(self, card: DistributorCard, product: str) -> Optional[dict]:
        for fb in reversed(self.feedback):
            if fb.get("type") == "hsn" and fb.get("distributor") == card.code and \
                    product_similarity(fb.get("product", ""), product) >= 85:
                return fb
        return None

    def not_duplicate(self, a: str, b: str) -> bool:
        for fb in self.feedback:
            if fb.get("type") == "not_duplicate":
                ids = {normalize_bill_id(x) for x in fb.get("bill_ids", [])}
                if {a, b} <= ids or (len(ids) == 1 and (a in ids or b in ids)):
                    return True
        return False

    # ── product matching ─────────────────────────────────────
    def match_entry(self, card: DistributorCard, rl: RateList, product: str) -> tuple[Optional[RateEntry], float]:
        names = [e.product for e in rl.entries]
        name, score = best_product_match(product, names, threshold=80)
        if name is None and self.llama is not None and self.llama.available and names:
            name = self._llm_map(product, names)
            score = 85.0 if name else score
        if name is None:
            return None, score
        entries = [e for e in rl.entries if e.product == name]
        return (entries[0] if entries else None), score

    def _llm_map(self, product: str, names: list[str]) -> Optional[str]:
        from harness.agents.llama_client import wrap
        prompt = ("A bill line names a product, possibly in Hindi, Hinglish or abbreviated. Which entry of the "
                  "distributor's rate list is the same product (same brand and pack size)? "
                  f"Bill line: {wrap(product)}\nRate list entries: {names}\n"
                  'Return {"match": "exact entry text or null"}')
        data = self.llama.chat_json(prompt, max_tokens=100)
        m = (data or {}).get("match")
        return m if m in names else None

    # ── rates ────────────────────────────────────────────────
    def resolve_rate(self, card: DistributorCard, product: str, on: date, bill_id: str = "") -> RateResolution:
        res = RateResolution(product=product)
        own = self.owner_rate(card, product, on, bill_id)
        if own:
            res.owner_rate = own
            res.match_score = 100.0
            return res
        lists = [rl for rl in self.rate_lists(card)
                 if rl.effective_from and rl.effective_from <= on and (rl.effective_to is None or on <= rl.effective_to)]
        res.lists_in_force = len(lists)
        cands = []
        for rl in lists:
            e, score = self.match_entry(card, rl, product)
            if e:
                cands.append((rl, e, score))
        if not cands:
            return res
        cands.sort(key=lambda t: (recv(t[0]), t[0].effective_from))
        gov_rl, gov_e, gov_score = cands[-1]
        res.entry, res.rate_list, res.match_score = gov_e, gov_rl, gov_score
        tol = self.policy.rate_tolerance
        for rl, e, _ in cands[:-1]:
            same_window = rl.effective_from == gov_rl.effective_from
            if same_window and abs(e.rate - gov_e.rate) > tol and not gov_rl.is_revision:
                res.conflict.append((rl.source.file, e.rate, rl))
        if res.conflict:
            res.conflict.append((gov_rl.source.file, gov_e.rate, gov_rl))
        if len({rl.record_id for rl, _, _ in cands}) > 1 and gov_rl.effective_from < max(rl.effective_from for rl, _, _ in cands):
            res.note = (f"{gov_rl.source.file} (received {recv(gov_rl).date()}) is backdated to "
                        f"{gov_rl.effective_from} and supersedes earlier lists")
        return res

    def quotes_against(self, card: DistributorCard, product: str, on: date, res: RateResolution) -> list[RateQuote]:
        """Spoken / chat rates from named salesmen that disagree with the governing list."""
        if res.owner_rate or res.rate is None:
            return []
        gov_time = recv(res.rate_list) if res.rate_list else NEVER
        out = []
        for q in self.quotes(card):
            if q.authority not in (Authority.NAMED_SALESMAN, Authority.OWNER):
                continue
            if not q.said_on or q.said_on > on or recv(q) < gov_time:
                continue
            if product_similarity(q.product, product) < 85 and product_similarity(q.product, res.entry.product) < 85:
                continue
            if abs(q.rate - res.rate) > self.policy.rate_tolerance:
                out.append(q)
        return out

    def rate_without_list(self, card: DistributorCard, product: str, on: date) -> Optional[RateQuote]:
        best = None
        for q in self.quotes(card):
            if q.authority in (Authority.NAMED_SALESMAN, Authority.OWNER) and q.said_on and q.said_on <= on and \
                    product_similarity(q.product, product) >= 85:
                if best is None or recv(q) > recv(best):
                    best = q
        return best

    # ── credit terms ─────────────────────────────────────────
    def credit_terms(self, card: DistributorCard) -> tuple[int, float, str, Optional[CreditTerms]]:
        days, rate, kind, chosen = self.policy.default_credit_days, self.policy.default_interest_rate, "default", None
        ordered = sorted((t for t in card.credit_terms if t.source is None or self.known(t)),
                         key=lambda t: ({"default": 0, "chat": 1, "contract": 2, "owner_feedback": 3}.get(t.source_kind, 1),
                                        t.stated_on or NEVER))
        for t in ordered:
            days, kind, chosen = t.days, t.source_kind, t
            if t.interest_rate is not None and t.interest_rate >= 0:
                rate = t.interest_rate
        return days, rate, kind, chosen

    # ── payments ─────────────────────────────────────────────
    def _assign_payments(self) -> None:
        """Match each successful payment to at most one bill / statement line."""
        self.payment_for: dict[tuple[str, str], list[tuple[Payment, str]]] = {}
        self.payment_used: dict[str, str] = {}
        for card in self.cards():
            pays = [p for p in self.payments(card) if p.status == "success"]
            targets: dict[str, tuple[float, Optional[date]]] = {}
            for b in self.bills(card):
                targets[b.bill_id] = (b.total_value, b.bill_date)
            for st in self.statements(card):
                for e in st.entries:
                    targets.setdefault(e.bill_id, (e.amount, e.bill_date))
            # 1. explicit references (chat text with the screenshot / UPI note)
            for p in pays:
                for ref in p.bill_refs:
                    ref = normalize_bill_id(ref)
                    if ref:
                        self.payment_for.setdefault((card.code, ref), []).append((p, "reference"))
                        self.payment_used[p.record_id] = ref
                        break
            # 2. unique amount + date match
            for p in pays:
                if p.record_id in self.payment_used:
                    continue
                matches = [bid for bid, (amt, bd) in targets.items()
                           if amt and abs(amt - p.amount) <= 1.0 and (bd is None or bd <= p.payment_date <= bd + timedelta(days=90))
                           and (card.code, bid) not in self.payment_for]
                if len(matches) == 1:
                    self.payment_for.setdefault((card.code, matches[0]), []).append((p, "amount"))
                    self.payment_used[p.record_id] = matches[0]

    def paid_amount(self, card: DistributorCard, bill_id: str, by: Optional[date] = None) -> tuple[float, list[str]]:
        """Amount paid against a bill (and how we know)."""
        total, how = 0.0, []
        for p, kind in self.payment_for.get((card.code, bill_id), []):
            if by is None or p.payment_date <= by:
                total += p.amount
                how.append(f"{p.source.file}: UPI ₹{fmt_num(p.amount)} on {p.payment_date}"
                           + (f", ref {p.upi_ref}" if p.upi_ref else ""))
        for fb in self.feedback:
            if fb.get("type") == "paid" and normalize_bill_id(fb.get("bill_id")) == bill_id:
                amt = fb.get("amount")
                total = max(total, float(amt) if amt else float("inf"))
                how.append(f"owner feedback: {bill_id} paid" + (f" ({fb.get('method')})" if fb.get("method") else ""))
        for st in self.statements(card):
            for e in st.entries:
                if e.bill_id == bill_id and e.status == "paid":
                    total = max(total, e.amount or float("inf"))
                    how.append(f"{st.source.file}: row {e.row}: {bill_id} shown paid")
        return total, how


def bill_line_cite(bill: Bill, line: BillLine) -> str:
    unit = f" {line.unit}" if line.unit else ""
    return (f"{bill.source.file}: row {line.row}: {line.product} {fmt_num(line.quantity)}{unit} x "
            f"{fmt_num(line.rate)} = {fmt_num(line.amount)}")


def chat_cite(src) -> Optional[str]:
    if src and src.line:
        text = src.text or ""
        if src.file and src.file != src.chat_file and src.file not in text:
            text = (text + f" <attached: {src.file}>").strip()
        return f"{src.chat_file or src.file}:L{src.line}: {src.sender}: {text}"[:260]
    return None


def first_name(card: DistributorCard) -> str:
    for c in card.contacts:
        if c.name:
            return c.name.split()[0]
    return card.name
