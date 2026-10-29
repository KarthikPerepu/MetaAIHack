"""
Ingest one week of WhatsApp exports into the khata (``harness.py ingest``).

1. Find chat exports (any *.txt that looks like one, in any sub-folder) and parse them.
2. Read every attachment once (vision / PDF / OCR / speech-to-text, cached by file hash).
3. Work out who is the shop owner and which distributor each sender belongs to,
   from display names, bill numbers on the documents they send, payee names and
   chat-file names. Nothing is hard-coded to the practice shops.
4. Screen every message, document text and transcript for injected instructions.
   Quarantined content never becomes a fact.
5. Turn messages and documents into dated records on each distributor's card:
   bills, rate lists, schemes, spoken rates, credit terms, payments, payment
   claims, statements and deliveries.
6. Save state atomically. Re-ingesting the same week is idempotent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from harness.agents.llama_client import LlamaClient
from harness.agents.message_agent import MessageAgent, MessageFacts
from harness.config import HarnessConfig
from harness.ingest.chat_parser import ChatMessage, is_chat_file, media_kind, parse_chat_file
from harness.ingest.documents import DocResult, DocumentReader, file_hash
from harness.ingest.safety import SafetyScreen
from harness.knowledge.models import (Authority, Bill, BillLine, Contact, CreditTerms, DeliveryEvent,
                                      DistributorCard, Payment, PaymentClaim, RateEntry, RateList, RateQuote,
                                      Scheme, SourceRef, Statement, StatementEntry)
from harness.knowledge.store import KnowledgeStore
from harness.textutil import (bill_prefix, best_product_match, clean_text, end_of_day, find_bill_ids,
                              initials, name_tokens, norm_product, normalize_bill_id, product_similarity,
                              split_person_company)

logger = logging.getLogger(__name__)

MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf", ".opus", ".ogg", ".mp3", ".m4a", ".aac",
                  ".wav", ".amr"}
UNKNOWN_SENDER = re.compile(r"^(unknown|\+?\d[\d\s\-]{6,}|~.*|forwarded)$", re.I)
SHOP_WORDS = re.compile(r"\b(store|stores|kirana|general\s+store|mart|shop|dukaan|bhandar)\b", re.I)


def _rid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


class IngestPipeline:
    def __init__(self, config: HarnessConfig, store: KnowledgeStore, llama: Optional[LlamaClient] = None):
        self.config = config
        self.store = store
        self.llama = llama or LlamaClient(config.llama, cache_dir=store.cache_dir)
        self.reader = DocumentReader(self.llama, store.cache_dir, ignore_sidecars=config.ignore_sidecars,
                                     whisper_model=config.whisper_model)
        self.safety = SafetyScreen(self.llama)
        self.agent = MessageAgent(self.llama)
        self.stats: Counter = Counter()

    # ─────────────────────────────────────────────────────────
    def ingest(self, week_dir: str) -> dict:
        week = Path(week_dir).resolve()
        if not week.is_dir():
            raise FileNotFoundError(f"Week directory not found: {week}")
        self.week = week
        self.week_label = week.name
        chats = sorted((p for p in week.rglob("*.txt") if is_chat_file(p)), key=lambda p: str(p.relative_to(week)))
        messages: list[ChatMessage] = []
        for chat in chats:
            rel = str(chat.relative_to(week)).replace("\\", "/")
            msgs = parse_chat_file(chat, chat_name=rel)
            for m in msgs:
                m._dir = chat.parent  # type: ignore[attr-defined]
                m._chat_title = _chat_title(chat)  # type: ignore[attr-defined]
            messages.extend(msgs)
        messages.sort(key=lambda m: (m.timestamp, m.chat_file, m.line))
        self.stats["messages"] = len(messages)
        for i, m in enumerate(messages):
            m._context = m.body  # type: ignore[attr-defined]
            if m.attachments and len(m.body) < 6:
                for prev in reversed(messages[max(0, i - 4):i]):
                    if prev.sender == m.sender and prev.chat_file == m.chat_file and \
                            timedelta(0) <= m.timestamp - prev.timestamp <= timedelta(minutes=15) and prev.body:
                        m._context = (prev.body + " " + m.body).strip()  # type: ignore[attr-defined]
                        break
        self.ref_year = messages[0].timestamp.year if messages else date.today().year

        self._load_contracts(week)

        # Phase A: read every attachment referenced in chat
        docs: dict[int, list[tuple[str, Path, DocResult]]] = defaultdict(list)
        referenced: set[Path] = set()
        for m in messages:
            for att in m.attachments:
                p = self._resolve(att, m)
                if p is None:
                    self.stats["attachments_missing"] += 1
                    logger.warning(f"{m.cite()}: attachment not found: {att}")
                    continue
                referenced.add(p.resolve())
                kind = media_kind(p.name)
                res = self.reader.read(p, kind, hint=getattr(m, "_context", m.body), ref_year=self.ref_year)
                docs[id(m)].append((att, p, res))
                self.stats[f"read_{res.doc_type}"] += 1
                if not res.readable:
                    self.stats["unreadable"] += 1
        loose = [p for p in sorted(week.rglob("*")) if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
                 and p.resolve() not in referenced]

        # Phase B: identities
        self._identify_people(messages, docs)

        # Phase C: messages in time order
        last_dist_by_chat: dict[str, tuple[str, datetime]] = {}
        for m in messages:
            if m.is_system or not m.sender:
                continue
            self._handle_message(m, docs.get(id(m), []), last_dist_by_chat)

        # Phase D: files nobody referenced in chat
        for p in loose:
            kind = media_kind(p.name)
            res = self.reader.read(p, kind, ref_year=self.ref_year)
            if not res.readable:
                continue
            src = SourceRef(file=p.name, week=self.week_label)
            self._handle_document(res, src, sender_code=self._code_from_filename(p.name), message=None)
            self.stats["loose_files"] += 1

        self._post_link()
        self.store.ingest_log["weeks"] = sorted(set(self.store.ingest_log.get("weeks", [])) | {self.week_label})
        self.store.save()
        out = dict(sorted(self.stats.items()))
        out["distributors"] = [f"{c.code} ({c.name})" for c in self.store.all_cards()]
        out["owner"] = self.store.shop.get("owner_names", [])
        cfg = getattr(self.llama, "config", None)
        out["models"] = ({"vision": cfg.vision_model, "text": cfg.text_model,
                          "voice": cfg.voice_model if cfg.is_meta_model_api else f"whisper-{self.config.whisper_model} (local)",
                          "endpoint": cfg.base_url, "model_calls": getattr(self.llama, "calls", 0)}
                         if self.llama.available and cfg else
                         "on" if self.llama.available else "off: no MODEL_API_KEY (deterministic readers only)")
        return out

    # ─────────────────────────────────────────────────────────
    # Identity
    # ─────────────────────────────────────────────────────────
    def _identify_people(self, messages: list[ChatMessage], docs) -> None:
        shop = self.store.shop
        senders = Counter(m.sender for m in messages if m.sender and not m.is_system)
        known_owner = set(shop.get("owner_names", []))
        # owner scoring
        scores: Counter = Counter()
        all_text = " \n".join(m.text for m in messages)
        low_text = all_text.lower()
        for s in senders:
            if UNKNOWN_SENDER.match(s.strip()):
                scores[s] -= 10
                continue
            if s in known_owner:
                scores[s] += 10
            first = s.split()[0].lower()
            others = " ".join(m.text.lower() for m in messages if m.sender != s)
            if s.lower() in others:
                scores[s] += 4
            elif len(first) > 2 and re.search(rf"\b{re.escape(first)}\s*(ji|bhai|sir)\b", others):
                scores[s] += 2
            if SHOP_WORDS.search(s):
                scores[s] += 2
            for m in messages:
                if m.sender != s:
                    continue
                for _, _, res in docs.get(id(m), []):
                    if res.doc_type == "upi_payment":
                        scores[s] += 2
                    if res.doc_type in ("bill", "rate_list", "statement"):
                        scores[s] -= 3
                if re.search(r"\b(bhej\s*do|bhejiye|kar\s*diya|theek\s*hai|order)\b", m.text, re.I):
                    scores[s] += 1
        owners = [s for s, sc in scores.most_common() if sc >= 3]
        owner = owners[0] if owners else None
        if owner is None and not known_owner:
            # fallback: the most active person who is neither a company nor sends bills
            plain = [s for s, sc in scores.most_common() if sc >= 0 and not split_person_company(s)[1]
                     and not UNKNOWN_SENDER.match(s.strip())]
            if plain:
                owner = max(plain, key=lambda s: (senders[s], scores[s], s))
        if owner and owner not in known_owner:
            shop["owner_names"] = sorted(known_owner | {owner})
        if owner and not shop.get("shop_name"):
            shop["shop_name"] = owner
        owner_set = set(shop.get("owner_names", []))

        # bill prefixes each sender is associated with
        prefixes: dict[str, Counter] = defaultdict(Counter)
        doc_names: dict[str, Counter] = defaultdict(Counter)
        for m in messages:
            if m.sender in owner_set:
                continue
            for bid in find_bill_ids(m.text):
                prefixes[m.sender][bill_prefix(bid)] += 1
            for _, _, res in docs.get(id(m), []):
                d = res.data or {}
                if res.doc_type == "bill" and d.get("bill_id"):
                    prefixes[m.sender][bill_prefix(d["bill_id"])] += 3
                if res.doc_type == "statement":
                    for e in d.get("entries", []):
                        prefixes[m.sender][bill_prefix(e.get("bill_id", ""))] += 1
                if d.get("distributor_name"):
                    doc_names[m.sender][d["distributor_name"]] += 1
                if d.get("distributor_code"):
                    prefixes[m.sender][str(d["distributor_code"]).upper()] += 2

        smap = shop["sender_map"]
        for s in sorted(senders):
            if s in owner_set or UNKNOWN_SENDER.match(s.strip()) or s in smap:
                if s in smap and s not in owner_set:
                    self._ensure_contact(self.store.card(smap[s]), s)
                continue
            person, company = split_person_company(s)
            chat_titles = {getattr(m, "_chat_title", "") for m in messages if m.sender == s}
            if not company:
                # 1:1 chat export "WhatsApp Chat with Ganga Traders.txt"
                title = next((t for t in chat_titles if t and t.lower() not in {x.lower() for x in owner_set}), "")
                if title:
                    company = title
                elif doc_names[s]:
                    company = doc_names[s].most_common(1)[0][0]
            code = self._match_card(company) if company else None
            pref = [p for p, _ in prefixes[s].most_common() if p]
            if not code and pref:
                code = pref[0] if (self.store.card(pref[0]) or not self._code_taken(pref[0], company)) else None
            if not code and company:
                code = self._new_code(company)
            if not code:
                # a named person with no company and no documents: unknown authority
                continue
            card = self.store.get_or_create(code, company or code)
            if company and (card.name == card.code):
                card.name = company
            smap[s] = code
            self._ensure_contact(card, s, person or s)
        self.owner_set = owner_set

    def _ensure_contact(self, card: Optional[DistributorCard], display: str, person: str = "") -> None:
        if card is None:
            return
        person = person or split_person_company(display)[0] or display
        if card.contact(display) or card.contact(person):
            c = card.contact(display) or card.contact(person)
            if not c.display_name:
                c.display_name = display
            return
        card.contacts.append(Contact(name=person, display_name=display, role="salesman", can_offer_schemes=True,
                                     source_kind="chat"))

    def _match_card(self, company: str) -> Optional[str]:
        want = name_tokens(company)
        best, best_score = None, 0.0
        for card in self.store.all_cards():
            for nm in [card.name] + card.aliases:
                have = name_tokens(nm)
                if not want or not have:
                    continue
                inter = len(want & have) / max(1, min(len(want), len(have)))
                if inter > best_score:
                    best, best_score = card.code, inter
        return best if best_score >= 0.99 else None

    def _code_taken(self, code: str, company: str) -> bool:
        card = self.store.card(code)
        return bool(card and company and self._match_card(company) != code)

    def _new_code(self, company: str) -> str:
        base = initials(company)
        code, n = base, 2
        while self.store.card(code) and self._match_card(company) != code:
            code = f"{base}{n}"
            n += 1
        return code

    def _load_contracts(self, week: Path) -> None:
        """Optional contract cards: contract*.json / contracts.json in the week folder."""
        for p in sorted(week.rglob("*contract*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Unreadable contract file {p.name}: {e}")
                continue
            for c in data if isinstance(data, list) else data.get("distributors", [data]):
                code = str(c.get("code") or c.get("distributor_code") or "").upper()
                name = c.get("name") or c.get("distributor") or code
                if not code:
                    code = self._match_card(name) or self._new_code(name)
                card = self.store.get_or_create(code, name)
                for sm in c.get("salesmen") or c.get("contacts") or []:
                    if isinstance(sm, str):
                        sm = {"name": sm}
                    existing = card.contact(sm["name"])
                    if existing:
                        existing.can_offer_schemes = bool(sm.get("can_offer_schemes", True))
                        existing.source_kind = "contract"
                    else:
                        card.contacts.append(Contact(name=sm["name"], role=sm.get("role", "salesman"),
                                                     can_offer_schemes=bool(sm.get("can_offer_schemes", True)),
                                                     source_kind="contract"))
                if c.get("credit_days"):
                    rate = c.get("interest_rate", self.config.policy.default_interest_rate)
                    rate = float(rate) / 100 if float(rate) > 1 else float(rate)
                    card.credit_terms.append(CreditTerms(days=int(c["credit_days"]), interest_rate=rate,
                                                         source_kind="contract",
                                                         source=SourceRef(file=p.name, week=self.week_label)))

    def _code_for_sender(self, sender: str) -> Optional[str]:
        return self.store.shop["sender_map"].get(sender)

    def _authority(self, sender: str, code: Optional[str], forwarded: bool) -> Authority:
        if forwarded:
            return Authority.FORWARDED
        if sender in self.owner_set:
            return Authority.OWNER
        if code:
            card = self.store.card(code)
            c = card.contact(sender) if card else None
            if c and c.can_offer_schemes:
                return Authority.NAMED_SALESMAN
            if c:
                return Authority.IDENTIFIED_PERSON
        if UNKNOWN_SENDER.match(sender.strip()):
            return Authority.UNKNOWN
        return Authority.IDENTIFIED_PERSON

    # ─────────────────────────────────────────────────────────
    # Messages
    # ─────────────────────────────────────────────────────────
    def _resolve(self, att: str, m: ChatMessage) -> Optional[Path]:
        base: Path = getattr(m, "_dir", self.week)
        for cand in (base / att, self.week / att):
            if cand.exists():
                return cand
        hits = sorted(self.week.rglob(Path(att).name))
        return hits[0] if hits else None

    def _addressed_code(self, m: ChatMessage, context: dict) -> Optional[str]:
        """Which distributor an owner message is about."""
        text = getattr(m, "_context", m.text) or m.text
        for bid in find_bill_ids(text):
            pre = bill_prefix(bid)
            if self.store.card(pre):
                return pre
            for card in self.store.all_cards():
                if any(b.bill_id == bid for b in card.bills):
                    return card.code
        toks = name_tokens(text)
        for card in self.store.all_cards():
            for c in card.contacts:
                if name_tokens(c.name) and name_tokens(c.name) <= toks:
                    return card.code
            if name_tokens(card.name) and name_tokens(card.name) <= toks:
                return card.code
        title = getattr(m, "_chat_title", "")
        if title:
            code = self._match_card(title)
            if code:
                return code
        last = context.get(m.chat_file)
        if last and (m.timestamp - last[1]) <= timedelta(hours=36):
            return last[0]
        return None

    def _handle_message(self, m: ChatMessage, attachments, context: dict) -> None:
        sender = m.sender
        is_owner = sender in self.owner_set
        code = self._code_for_sender(sender)
        trusted = is_owner or bool(code)
        screen = self.safety.check(m.body or m.text, forwarded=m.is_forwarded, trusted_sender=trusted)
        src = SourceRef(file=m.chat_file, line=m.line, sender=sender, received_at=m.timestamp,
                        week=self.week_label, chat_file=m.chat_file, text=m.body[:300])
        if screen["threat"]:
            self.stats["quarantined"] += 1
            self.store.add_alert({
                "source": m.cite(), "sender": sender, "received_at": m.timestamp.isoformat(),
                "reason": ", ".join(screen["categories"]), "detail": screen["reason"],
                "text": m.text[:300], "forwarded": m.is_forwarded,
                "action": "quarantined: not followed, no record created, owner informed",
            })
            for att, p, _ in attachments:
                self.store.add_alert({"source": f"{m.cite()} ({att})", "sender": sender,
                                      "reason": "attachment of quarantined message", "detail": "",
                                      "text": att, "action": "not processed"})
            return
        if is_owner:
            code = self._addressed_code(m, context) or code
        elif code:
            context[m.chat_file] = (code, m.timestamp)

        for att, p, res in attachments:
            asrc = SourceRef(file=p.name, line=m.line, sender=sender, received_at=m.timestamp,
                             week=self.week_label, chat_file=m.chat_file, text=m.body[:300])
            if res.doc_type == "transcript":
                self._handle_transcript(res, asrc, code, m)
            elif res.readable:
                got = self._handle_document(res, asrc, sender_code=code, message=m)
                if got and not code:
                    code = got
            else:
                self.store.add_alert({"source": f"{m.cite()} ({att})", "sender": sender,
                                      "reason": "unreadable attachment", "detail": res.note, "text": att,
                                      "action": "logged; ask sender to resend a clearer copy"})

        if m.is_forwarded:
            if m.body:
                self.stats["forwarded_ignored"] += 1
            return
        if not m.body:
            return
        if not code:
            return
        card = self.store.get_or_create(code)
        facts = self.agent.understand(m.body, sender_role="shop owner" if is_owner else "distributor salesman",
                                      ref_date=m.timestamp.date(), known_products=self._products(card),
                                      is_owner=is_owner)
        self._apply_facts(card, facts, src, self._authority(sender, code, False), kind="chat", stt_conf=1.0,
                          is_owner=is_owner, message=m)

    def _handle_transcript(self, res: DocResult, src: SourceRef, code: Optional[str], m: ChatMessage) -> None:
        text = res.text or (res.data or {}).get("text", "")
        if not text:
            return
        self.stats["voice_notes"] += 1
        screen = self.safety.check(text, forwarded=m.is_forwarded, trusted_sender=bool(code))
        if screen["threat"]:
            self.stats["quarantined"] += 1
            self.store.add_alert({"source": src.file, "sender": src.sender, "reason": ", ".join(screen["categories"]),
                                  "detail": screen["reason"], "text": text[:300],
                                  "action": "voice note quarantined: not followed"})
            return
        if not code or m.is_forwarded:
            return
        card = self.store.get_or_create(code)
        facts = self.agent.understand(text, sender_role="distributor salesman (voice note)",
                                      ref_date=m.timestamp.date(), known_products=self._products(card))
        vsrc = SourceRef(**{**src.__dict__})
        self._apply_facts(card, facts, vsrc, self._authority(src.sender, code, False), kind="voice",
                          stt_conf=res.confidence or 0.6, is_owner=src.sender in self.owner_set, message=m, raw=text)

    # ─────────────────────────────────────────────────────────
    # Facts from text
    # ─────────────────────────────────────────────────────────
    def _products(self, card: DistributorCard) -> list[str]:
        names = []
        for rl in card.rate_lists:
            names += [e.product for e in rl.entries]
        for b in card.bills:
            names += [l.product for l in b.lines if not l.is_free]
        seen, out = set(), []
        for n in names:
            k = norm_product(n)
            if k not in seen:
                seen.add(k)
                out.append(n)
        return out

    def _match_known_product(self, card: DistributorCard, text: str) -> Optional[str]:
        if not text:
            return None
        products = self._products(card)
        direct, score = best_product_match(text, products, threshold=85)
        if direct:
            return direct
        toks = set(norm_product(text).split())
        hits = []
        for p in products:
            distinctive = {t for t in norm_product(p).split() if len(t) >= 4 and not t.isdigit()}
            if distinctive and distinctive & toks:
                hits.append((len(distinctive & toks), p))
        hits.sort(key=lambda t: (-t[0], t[1]))
        if hits and (len(hits) == 1 or hits[0][0] > hits[1][0]):
            return hits[0][1]
        return None

    def _apply_facts(self, card: DistributorCard, f: MessageFacts, src: SourceRef, authority: Authority, *,
                     kind: str, stt_conf: float, is_owner: bool, message: ChatMessage, raw: str = "") -> None:
        d = src.received_at.date() if src.received_at else None
        base_id = f"{src.file}:{src.line}"
        if f.cancels_scheme:
            for s in card.schemes:
                if s.cancelled_at is None and (s.source.received_at or datetime.min) <= src.received_at:
                    s.cancelled_at = src.received_at
            self.stats["schemes_cancelled"] += 1
        for i, s in enumerate(f.schemes):
            product = self._match_known_product(card, s.product_text)
            free_product = self._match_known_product(card, s.free_product_text) if s.free_product_text else None
            scheme = Scheme(
                record_id=_rid("scheme", card.code, base_id, i), distributor=card.code, source=src,
                kind=s.kind, product=product, buy_qty=s.buy_qty, free_qty=s.free_qty, free_product=free_product,
                unit=s.unit, discount_pct=s.discount_pct, discount_per_unit=s.discount_per_unit,
                per_multiple=s.per_multiple, recurrence=s.recurrence, valid_from=d, valid_until=s.valid_until,
                agreed_by=src.sender, authority=authority, source_kind=kind, raw_text=raw or src.text,
                stt_confidence=stt_conf, extraction=s.source)
            if card.upsert("schemes", scheme):
                self.stats["schemes"] += 1
        for i, q in enumerate(f.rate_quotes):
            product = self._match_known_product(card, q["product_text"])
            if not product or is_owner:
                continue
            quote = RateQuote(record_id=_rid("quote", card.code, base_id, i), distributor=card.code,
                              product=product, rate=q["rate"], source=src, authority=authority, said_on=d,
                              raw_text=raw or src.text, source_kind=kind, stt_confidence=stt_conf)
            if card.upsert("rate_quotes", quote):
                self.stats["rate_quotes"] += 1
        if f.credit_days:
            rate = f.interest_rate if f.interest_rate is not None else None
            card.credit_terms = [t for t in card.credit_terms if not (t.source and t.source.file == src.file
                                                                       and t.source.line == src.line)]
            card.credit_terms.append(CreditTerms(days=f.credit_days, interest_rate=rate if rate is not None else -1.0,
                                                 source_kind="chat", source=src, stated_on=src.received_at,
                                                 raw_text=src.text))
            self.stats["credit_terms"] += 1
        elif f.interest_rate is not None and card.credit_terms:
            card.credit_terms[-1].interest_rate = f.interest_rate
        if f.payment_claim and (is_owner or kind == "chat"):
            claim = PaymentClaim(record_id=_rid("claim", card.code, base_id), distributor=card.code, source=src,
                                 amount=f.payment_claim.get("amount"),
                                 bill_refs=[normalize_bill_id(b) for b in f.payment_claim.get("bill_refs") or []],
                                 raw_text=src.text)
            card.upsert("payment_claims", claim)
        if f.delivery and not is_owner and d:
            ev = DeliveryEvent(record_id=_rid("delivery", card.code, base_id), distributor=card.code, source=src,
                               on_date=d, bill_refs=f.bill_refs, raw_text=src.text)
            card.upsert("deliveries", ev)
        if is_owner and f.accepts:
            for s in card.schemes:
                r = s.source.received_at
                if r and src.received_at and timedelta(0) <= src.received_at - r <= timedelta(days=2):
                    s.owner_acknowledged = True

    # ─────────────────────────────────────────────────────────
    # Documents
    # ─────────────────────────────────────────────────────────
    def _code_from_filename(self, name: str) -> Optional[str]:
        for part in re.split(r"[-_. ]", name.upper()):
            if self.store.card(part):
                return part
        return None

    def _doc_code(self, res: DocResult, sender_code: Optional[str], message: Optional[ChatMessage]) -> Optional[str]:
        d = res.data or {}
        if res.doc_type == "bill" and d.get("bill_id"):
            pre = bill_prefix(d["bill_id"])
            if self.store.card(pre):
                return pre
        name = d.get("distributor_name") or d.get("payee") or ""
        if name:
            code = self._match_card(name) or self._fuzzy_card(name)
            if code:
                return code
        if res.doc_type == "upi_payment" and message is not None:
            for bid in find_bill_ids(getattr(message, "_context", message.text)):
                pre = bill_prefix(bid)
                if self.store.card(pre):
                    return pre
        if res.doc_type == "statement":
            pres = Counter(bill_prefix(e.get("bill_id", "")) for e in d.get("entries", []))
            for pre, _ in pres.most_common():
                if self.store.card(pre):
                    return pre
        if sender_code:
            return sender_code
        if res.doc_type == "bill" and d.get("bill_id") and bill_prefix(d["bill_id"]):
            pre = bill_prefix(d["bill_id"])
            self.store.get_or_create(pre, d.get("distributor_name") or pre)
            return pre
        if name:
            code = self._new_code(name)
            self.store.get_or_create(code, name)
            return code
        return None

    def _fuzzy_card(self, name: str) -> Optional[str]:
        want = name_tokens(name)
        for card in self.store.all_cards():
            for nm in [card.name] + card.aliases:
                have = name_tokens(nm)
                if want and have and (have <= want or want <= have):
                    return card.code
        return None

    def _handle_document(self, res: DocResult, src: SourceRef, sender_code: Optional[str],
                         message: Optional[ChatMessage]) -> Optional[str]:
        code = self._doc_code(res, sender_code, message)
        d = res.data or {}
        injected = str(d.get("instructions_found") or "")
        if not injected:
            text = res.text or d.get("raw_text", "")
            if text:
                scr = self.safety.check(text, trusted_sender=bool(sender_code))
                if scr["threat"]:
                    injected = scr["reason"]
        if injected and injected.lower() not in ("null", "none", ""):
            self.store.add_alert({"source": src.file, "sender": src.sender, "reason": "instruction text inside a document",
                                  "detail": injected[:200], "text": injected[:200],
                                  "action": "instructions ignored; numbers on the document still checked"})
            self.stats["document_injections"] += 1
        if not code:
            logger.warning(f"Could not tell which distributor {src.file} belongs to")
            return None
        card = self.store.get_or_create(code)
        if src.received_at is None:
            dd = d.get("bill_date") or d.get("statement_date") or d.get("date") or d.get("effective_from")
            if dd:
                src.received_at = end_of_day(dd)
        if res.doc_type == "bill":
            self._add_bill(card, res, src, injected)
        elif res.doc_type == "rate_list":
            self._add_rate_list(card, res, src)
        elif res.doc_type == "upi_payment":
            self._add_payment(card, res, src, message)
        elif res.doc_type == "statement":
            self._add_statement(card, res, src)
        return code

    def _repair_bill_id(self, raw: str, card: DistributorCard, context: str) -> str:
        """OCR misreads ('G1/1187'): trust the bill number written in the chat, else the sender's code."""
        if not raw:
            ids = find_bill_ids(context)
            return ids[0] if len(ids) == 1 else raw
        num = raw.split("/")[-1]
        for bid in find_bill_ids(context):
            if bid.split("/")[-1] == num:
                return bid
        pre = bill_prefix(raw) or raw.split("/")[0]
        if "/" in raw and pre != card.code and not self.store.card(pre) and len(pre) == len(card.code):
            confusable = str.maketrans("01258", "OIZSB")
            if pre.translate(confusable) == card.code or any(not ch.isalpha() for ch in pre):
                return f"{card.code}/{num}"
        return raw

    def _add_bill(self, card: DistributorCard, res: DocResult, src: SourceRef, injected: str) -> None:
        d = res.data
        if res.method.endswith("ocr") or res.method.startswith("llama"):
            d["bill_id"] = self._repair_bill_id(d.get("bill_id", ""), card, src.text)
            known = self._products(card)
            for l in d.get("lines", []):
                hit, score = best_product_match(l["product"], known, threshold=88)
                if not hit and res.method.endswith("ocr"):
                    hit = self._match_known_product(card, l["product"])
                if hit and hit != l["product"]:
                    l["printed_as"] = l["product"]
                    l["product"] = hit
        if not d.get("bill_id") or not d.get("bill_date") or not d.get("lines"):
            self.store.add_alert({"source": src.file, "sender": src.sender, "reason": "bill could not be read fully",
                                  "detail": f"bill_id={d.get('bill_id')!r} date={d.get('bill_date')}", "text": "",
                                  "action": "ask the distributor for a clearer photo"})
            return
        lines = [BillLine(row=l["row"], product=l["product"], quantity=l["quantity"], rate=l["rate"], amount=l["amount"],
                          unit=l.get("unit", ""), hsn=l.get("hsn"), discount=l.get("discount") or 0.0,
                          free_qty=l.get("free_qty") or 0.0, is_free=l.get("is_free", False),
                          gst_rate=l.get("gst_rate"), gst_amount=l.get("gst_amount"),
                          taxable_value=l.get("taxable_value")) for l in d["lines"]]
        conf = res.confidence or 0.8
        arith = d.get("arith_score", 1.0)
        bill = Bill(record_id=f"bill:{card.code}:{d['bill_id']}", bill_id=d["bill_id"], distributor=card.code,
                    bill_date=d["bill_date"], source=src, lines=lines, delivery_date=d.get("delivery_date"),
                    challan_no=d.get("challan_no", ""), distributor_name=d.get("distributor_name", ""),
                    subtotal=d.get("subtotal"), discount_total=d.get("discount_total") or 0.0,
                    tax_total=d.get("tax_total"), tax_rate=d.get("tax_rate"), grand_total=d.get("grand_total"),
                    ocr_confidence=round(min(conf, 0.5 + 0.5 * arith) if res.method != "sidecar" else conf, 3),
                    arithmetic_ok=arith >= 0.99, warnings=d.get("warnings", []), extraction=res.method,
                    file_hash=res.file_hash, raw_text=(res.text or "")[:2000], injected_text=injected[:300])
        old = next((b for b in card.bills if b.record_id == bill.record_id), None)
        if old and old.file_hash == bill.file_hash:
            bill.source = old.source if (old.source.received_at or datetime.max) <= (src.received_at or datetime.max) else src
        card.upsert("bills", bill)
        self.stats["bills"] += 1

    def _add_rate_list(self, card: DistributorCard, res: DocResult, src: SourceRef) -> None:
        d = res.data
        if not d.get("entries"):
            return
        eff = d.get("effective_from")
        inferred = False
        if not eff:
            eff = d.get("document_date") or (src.received_at.date() if src.received_at else None)
            inferred = True
        context = (src.text or "").lower()
        is_rev = d.get("is_revision") or bool(re.search(r"(corrected|correction|revised|sahi\s*wala|updated|galti|"
                                                         r"संशोधित|सही)", context))
        entries = [RateEntry(product=e["product"], rate=e["rate"], unit=e.get("unit", ""), hsn=e.get("hsn"),
                             gst_rate=e.get("gst_rate"), mrp=e.get("mrp"), scheme_text=e.get("scheme_text", ""),
                             row=e.get("row")) for e in d["entries"]]
        rl = RateList(record_id=f"ratelist:{card.code}:{res.file_hash[:12]}:{src.file}", distributor=card.code, source=src,
                      effective_from=eff, effective_to=d.get("effective_to"), effective_inferred=inferred,
                      version=d.get("version") or _version_from_name(src.file), is_revision=bool(is_rev),
                      entries=entries, distributor_name=d.get("distributor_name", ""), extraction=res.method,
                      confidence=res.confidence or 0.9)
        card.upsert("rate_lists", rl)
        self.stats["rate_lists"] += 1
        # schemes printed on the rate list are the highest-authority scheme source
        from harness.agents.message_agent import heuristic_facts
        for e in entries:
            if not e.scheme_text:
                continue
            f = heuristic_facts(e.scheme_text if re.search(r"free|\+", e.scheme_text, re.I) else e.scheme_text + " free")
            for i, s in enumerate(f.schemes):
                card.upsert("schemes", Scheme(
                    record_id=_rid("rl-scheme", rl.record_id, e.product, i), distributor=card.code, source=src,
                    kind=s.kind, product=e.product, buy_qty=s.buy_qty, free_qty=s.free_qty, unit=s.unit or e.unit,
                    discount_pct=s.discount_pct, discount_per_unit=s.discount_per_unit, per_multiple=True,
                    recurrence="until" if rl.effective_to else "permanent", valid_from=eff,
                    valid_until=rl.effective_to, agreed_by=card.name, authority=Authority.SIGNED_RATE_LIST,
                    source_kind="rate_list", raw_text=f"{e.product}: {e.scheme_text}"))

    def _add_payment(self, card: DistributorCard, res: DocResult, src: SourceRef, message: Optional[ChatMessage]) -> None:
        d = res.data
        if not d.get("amount") or not d.get("date"):
            self.store.add_alert({"source": src.file, "sender": src.sender, "reason": "payment screenshot unreadable",
                                  "detail": "", "text": "", "action": "ask owner for UPI reference"})
            return
        refs = find_bill_ids((getattr(message, "_context", message.text) if message else "") + " " + (d.get("note") or ""))
        p = Payment(record_id=f"pay:{card.code}:{res.file_hash[:12]}:{src.file}", distributor=card.code, amount=d["amount"],
                    payment_date=d["date"], source=src, upi_ref=d.get("upi_ref", ""), payee=d.get("payee", ""),
                    status=d.get("status", "unknown"), bill_refs=refs, note=d.get("note", ""),
                    confidence=res.confidence or 0.85, extraction=res.method)
        card.upsert("payments", p)
        self.stats["payments"] += 1

    def _add_statement(self, card: DistributorCard, res: DocResult, src: SourceRef) -> None:
        d = res.data
        sdate = d.get("statement_date") or (src.received_at.date() if src.received_at else None)
        if not sdate:
            return
        st = Statement(record_id=f"stmt:{card.code}:{res.file_hash[:12]}:{src.file}", distributor=card.code, statement_date=sdate,
                       source=src, entries=[StatementEntry(row=e["row"], bill_id=e["bill_id"], bill_date=e.get("bill_date"),
                                                           amount=e.get("amount") or 0.0, status=e.get("status", "due"),
                                                           balance=e.get("balance")) for e in d.get("entries", [])],
                       total_outstanding=d.get("total_outstanding"), extraction=res.method,
                       confidence=res.confidence or 0.85)
        card.upsert("statements", st)
        self.stats["statements"] += 1

    def _post_link(self) -> None:
        """Resolve products of schemes that were stated before any rate list / bill named them."""
        for card in self.store.all_cards():
            for s in card.schemes:
                if s.product is None and s.raw_text:
                    s.product = self._match_known_product(card, s.raw_text)


def _chat_title(path: Path) -> str:
    m = re.match(r"whatsapp\s*chat\s*(with|-)\s*(.+?)\.txt$", path.name, re.I)
    if m:
        return m.group(2).strip()
    if path.parent.name and not path.parent.name.lower().startswith(("week", "data", "exam")):
        return ""
    return ""


def _version_from_name(name: str) -> str:
    m = re.search(r"v(\d+)", name, re.I)
    return f"v{m.group(1)}" if m else ""
