"""
Records kept in the khata (the harness's on-disk memory).

Every record keeps its provenance: which file it came from, which chat line
or bill row, who sent it and *when it arrived* (``received_at``). Reports are
computed only from records that had arrived by the ``--as-of`` date, which is
what lets a later rate list or message change an answer without rewriting
history.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, get_type_hints

from harness.textutil import from_iso, from_iso_dt


# ─────────────────────────────────────────────────────────────
# Serialisation helpers
# ─────────────────────────────────────────────────────────────
def _dump(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {f.name: _dump(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    return value


class Record:
    """Mixin: dataclass <-> JSON dict with date/datetime/nested support."""

    def to_dict(self) -> dict:
        return _dump(self)

    @classmethod
    def from_dict(cls, data: dict):
        hints = get_type_hints(cls)
        kwargs = {}
        names = {f.name for f in dataclasses.fields(cls)}
        for key, value in (data or {}).items():
            if key not in names:
                continue
            kwargs[key] = _load(hints.get(key), value)
        return cls(**kwargs)


def _load(tp, value):
    if value is None or tp is None:
        return value
    origin = getattr(tp, "__origin__", None)
    args = getattr(tp, "__args__", ())
    if origin is list and args:
        return [_load(args[0], v) for v in value]
    if origin is not None and type(None) in args:  # Optional[X]
        inner = [a for a in args if a is not type(None)]
        return _load(inner[0], value) if inner else value
    if tp is datetime:
        return from_iso_dt(value)
    if tp is date:
        return from_iso(value)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if isinstance(tp, type) and issubclass(tp, Record):
        return tp.from_dict(value)
    return value


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────
class Authority(str, Enum):
    """Source authority, highest first (GLOSSARY §2)."""
    SIGNED_RATE_LIST = "signed_rate_list"
    OWNER = "owner"
    NAMED_SALESMAN = "named_salesman"
    IDENTIFIED_PERSON = "identified_person"
    UNKNOWN = "unknown"
    FORWARDED = "forwarded"


AUTHORITY_WEIGHT = {
    Authority.SIGNED_RATE_LIST: 0.97,
    Authority.OWNER: 0.93,
    Authority.NAMED_SALESMAN: 0.88,
    Authority.IDENTIFIED_PERSON: 0.70,
    Authority.UNKNOWN: 0.45,
    Authority.FORWARDED: 0.15,
}


class Status(str, Enum):
    CONFIRMED = "confirmed"
    CONFLICT = "conflict"
    CANNOT_DETERMINE = "cannot_determine"


CLASSES = [
    "rate_mismatch",
    "duplicate_bill",
    "scheme_not_given",
    "paid_but_shown_due",
    "gst_error",
    "credit_period_risk",
]


# ─────────────────────────────────────────────────────────────
# Provenance
# ─────────────────────────────────────────────────────────────
@dataclass
class SourceRef(Record):
    file: str = ""                 # e.g. "GT-1187.jpg" or "_chat.txt"
    line: Optional[int] = None     # chat line number
    sender: str = ""
    received_at: Optional[datetime] = None
    week: str = ""
    chat_file: str = ""            # chat file that carried the attachment
    text: str = ""                 # chat text around the attachment

    def cite(self) -> str:
        if self.line:
            return f"{self.chat_file or self.file}:L{self.line}"
        return self.file


# ─────────────────────────────────────────────────────────────
# Rate lists
# ─────────────────────────────────────────────────────────────
@dataclass
class RateEntry(Record):
    product: str
    rate: float
    unit: str = ""
    hsn: Optional[str] = None
    gst_rate: Optional[float] = None     # fraction, 0.18
    mrp: Optional[float] = None
    scheme_text: str = ""                # e.g. "10+1" printed on the list
    row: Optional[int] = None


@dataclass
class RateList(Record):
    record_id: str
    distributor: str
    source: SourceRef
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    effective_inferred: bool = False     # no printed w.e.f. date: used the arrival date
    version: str = ""
    is_revision: bool = False            # says corrected / revised / supersedes
    signed: bool = True
    entries: list[RateEntry] = field(default_factory=list)
    distributor_name: str = ""
    extraction: str = ""                 # sidecar / pdf_table / pdf_text / vision / ocr
    confidence: float = 0.9


@dataclass
class RateQuote(Record):
    """A price stated by a person in chat or a voice note."""
    record_id: str
    distributor: str
    product: str
    rate: float
    source: SourceRef
    authority: Authority = Authority.NAMED_SALESMAN
    said_on: Optional[date] = None
    raw_text: str = ""
    source_kind: str = "chat"            # chat | voice
    stt_confidence: float = 1.0


# ─────────────────────────────────────────────────────────────
# Bills
# ─────────────────────────────────────────────────────────────
@dataclass
class BillLine(Record):
    row: int
    product: str
    quantity: float
    rate: float
    amount: float
    unit: str = ""
    hsn: Optional[str] = None
    discount: float = 0.0
    free_qty: float = 0.0                # free units printed in a column of this row
    is_free: bool = False                # the whole row is a free-goods row
    gst_rate: Optional[float] = None     # fraction
    gst_amount: Optional[float] = None
    taxable_value: Optional[float] = None


@dataclass
class Bill(Record):
    record_id: str
    bill_id: str
    distributor: str
    bill_date: date
    source: SourceRef
    lines: list[BillLine] = field(default_factory=list)
    delivery_date: Optional[date] = None
    challan_no: str = ""
    distributor_name: str = ""
    subtotal: Optional[float] = None
    discount_total: float = 0.0
    tax_total: Optional[float] = None
    tax_rate: Optional[float] = None     # single bill-level rate if printed
    grand_total: Optional[float] = None
    ocr_confidence: float = 0.85
    arithmetic_ok: bool = True
    warnings: list[str] = field(default_factory=list)
    extraction: str = ""
    file_hash: str = ""
    raw_text: str = ""
    injected_text: str = ""              # instruction-like text found on the document

    @property
    def total_value(self) -> float:
        if self.grand_total:
            return self.grand_total
        base = sum(l.amount for l in self.lines if not l.is_free) - (self.discount_total or 0)
        return base + (self.tax_total or 0)


# ─────────────────────────────────────────────────────────────
# Schemes
# ─────────────────────────────────────────────────────────────
@dataclass
class Scheme(Record):
    record_id: str
    distributor: str
    source: SourceRef
    kind: str = "free_goods"             # free_goods | discount_pct | discount_per_unit
    product: Optional[str] = None        # None = not named; inferred at report time
    buy_qty: Optional[float] = None
    free_qty: Optional[float] = None
    free_product: Optional[str] = None   # when the free goods differ from the bought goods
    unit: str = ""
    discount_pct: Optional[float] = None
    discount_per_unit: Optional[float] = None
    per_multiple: bool = True            # "10 pe 1": 20 bought -> 2 free
    recurrence: str = "one_time"         # one_time | permanent | until
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    agreed_by: str = ""
    authority: Authority = Authority.NAMED_SALESMAN
    source_kind: str = "chat"            # chat | voice | rate_list | owner_feedback | contract
    raw_text: str = ""
    stt_confidence: float = 1.0
    cancelled_at: Optional[datetime] = None
    owner_acknowledged: bool = False     # owner replied accepting the offer
    extraction: str = "heuristic"


# ─────────────────────────────────────────────────────────────
# Payments and statements
# ─────────────────────────────────────────────────────────────
@dataclass
class Payment(Record):
    record_id: str
    distributor: str
    amount: float
    payment_date: date
    source: SourceRef
    upi_ref: str = ""
    payee: str = ""
    status: str = "success"              # success | pending | failed | unknown
    bill_refs: list[str] = field(default_factory=list)
    note: str = ""
    confidence: float = 0.9
    extraction: str = ""
    method: str = "upi"                  # upi | cash | neft | owner_feedback


@dataclass
class PaymentClaim(Record):
    """Somebody says a payment was made, without a screenshot."""
    record_id: str
    distributor: str
    source: SourceRef
    amount: Optional[float] = None
    bill_refs: list[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class StatementEntry(Record):
    row: int
    bill_id: str
    bill_date: Optional[date] = None
    amount: float = 0.0
    status: str = "due"                  # due | partial | paid | credit
    balance: Optional[float] = None


@dataclass
class Statement(Record):
    record_id: str
    distributor: str
    statement_date: date
    source: SourceRef
    entries: list[StatementEntry] = field(default_factory=list)
    total_outstanding: Optional[float] = None
    extraction: str = ""
    confidence: float = 0.9


# ─────────────────────────────────────────────────────────────
# Terms, contacts, deliveries
# ─────────────────────────────────────────────────────────────
@dataclass
class CreditTerms(Record):
    days: int
    interest_rate: float                 # fraction per annum
    source_kind: str = "default"         # default | chat | contract | owner_feedback
    source: Optional[SourceRef] = None
    stated_on: Optional[datetime] = None
    raw_text: str = ""


@dataclass
class Contact(Record):
    name: str                            # person name, e.g. "Ramesh"
    display_name: str = ""               # chat display name
    role: str = "salesman"
    can_offer_schemes: bool = True
    source_kind: str = "chat"            # chat | contract | owner_feedback


@dataclass
class DeliveryEvent(Record):
    record_id: str
    distributor: str
    source: SourceRef
    on_date: date
    bill_refs: list[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class DistributorCard(Record):
    """Contract card + everything learnt about one distributor."""
    code: str
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    credit_terms: list[CreditTerms] = field(default_factory=list)
    rate_lists: list[RateList] = field(default_factory=list)
    rate_quotes: list[RateQuote] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)
    schemes: list[Scheme] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    payment_claims: list[PaymentClaim] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)
    deliveries: list[DeliveryEvent] = field(default_factory=list)

    def contact(self, name: str) -> Optional[Contact]:
        from harness.textutil import name_tokens
        want = name_tokens(name)
        for c in self.contacts:
            if c.name and (c.name.lower() == name.lower() or (want and want <= name_tokens(c.display_name or c.name))
                           or (name_tokens(c.name) and name_tokens(c.name) <= want)):
                return c
        return None

    def upsert(self, attr: str, record) -> bool:
        """Insert or replace a record by record_id. Returns True when new."""
        items = getattr(self, attr)
        for i, old in enumerate(items):
            if getattr(old, "record_id", None) == record.record_id:
                items[i] = record
                return False
        items.append(record)
        return True


# ─────────────────────────────────────────────────────────────
# Findings
# ─────────────────────────────────────────────────────────────
@dataclass
class Finding:
    bill_id: str
    finding_class: str
    status: Status
    distributor: str = ""
    amount_inr: float = 0.0
    evidence: list[str] = field(default_factory=list)
    reasoning: str = ""
    confidence: float = 0.5
    sources: list[dict] = field(default_factory=list)
    missing: str = ""
    ask: str = ""
    ask_party: str = ""                  # distributor | owner | salesman
    evidence_complete: bool = True
    reversible: bool = True
    scheme_type: str = ""
    related_bill_ids: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)
    draft_message: str = ""
    key: str = ""                        # stable id used by feedback

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "bill_id": self.bill_id,
            "class": self.finding_class,
            "status": self.status.value,
            "distributor": self.distributor,
            "amount_inr": round(self.amount_inr, 2),
            "evidence": self.evidence,
            "action": self.action,
            "confidence": round(self.confidence, 2),
            "reasoning": self.reasoning,
        }
        if self.status == Status.CONFLICT:
            out["sources"] = self.sources
        if self.status == Status.CANNOT_DETERMINE:
            out["missing"] = self.missing
            out["ask"] = self.ask
        if self.scheme_type:
            out["scheme_type"] = self.scheme_type
        if self.related_bill_ids:
            out["related_bill_ids"] = self.related_bill_ids
        if self.draft_message:
            out["draft_message"] = self.draft_message
        out.update(self.extra)
        out["finding_id"] = self.key
        return out
