"""
Data models for bills, rate lists, schemes, payments, and distributor cards.

All models are serializable to/from JSON for disk persistence.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional
from enum import Enum
import json


class SchemeType(str, Enum):
    ONE_TIME = "one_time"
    PERMANENT = "permanent"


class SourceType(str, Enum):
    RATE_LIST = "rate_list"
    BILL_PHOTO = "bill_photo"
    VOICE_NOTE = "voice_note"
    CHAT_MESSAGE = "chat_message"
    UPI_SCREENSHOT = "upi_screenshot"
    STATEMENT = "statement"
    FORWARDED = "forwarded"
    OWNER = "owner"


# ─────────────────────────────────────────────────────────────
# Source Authority (higher = more trusted)
# ─────────────────────────────────────────────────────────────
SOURCE_AUTHORITY = {
    SourceType.RATE_LIST: 1.0,
    SourceType.BILL_PHOTO: 0.7,
    SourceType.VOICE_NOTE: 0.6,
    SourceType.CHAT_MESSAGE: 0.5,
    SourceType.UPI_SCREENSHOT: 0.7,
    SourceType.STATEMENT: 0.7,
    SourceType.FORWARDED: 0.2,
    SourceType.OWNER: 1.0,
}


def _date_to_str(d: Optional[date]) -> Optional[str]:
    if d is None:
        return None
    return d.isoformat()


def _str_to_date(s: Optional[str]) -> Optional[date]:
    if s is None:
        return None
    return date.fromisoformat(s)


# ─────────────────────────────────────────────────────────────
# Rate List
# ─────────────────────────────────────────────────────────────
@dataclass
class RateEntry:
    """A single product rate from a rate list."""
    product: str
    rate: float
    hsn: Optional[str] = None
    unit: str = "carton"
    gst_rate: Optional[float] = None  # e.g. 0.18 for 18%

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RateEntry":
        return cls(**d)


@dataclass
class RateList:
    """A distributor's rate list with effective date."""
    distributor_code: str
    version: str  # e.g. "v1", "v2"
    effective_date: date
    source_file: str = ""  # e.g. "ratelist_v2.pdf"
    entries: list[RateEntry] = field(default_factory=list)
    superseded_by: Optional[str] = None  # version that replaces this

    def get_rate(self, product: str) -> Optional[RateEntry]:
        """Find rate entry by product name (fuzzy)."""
        product_lower = product.lower().strip()
        exact = [entry for entry in self.entries
                 if entry.product.lower().strip() == product_lower]
        if exact:
            rates = {round(entry.rate, 2) for entry in exact}
            return exact[0] if len(rates) == 1 else None
        # Only accept substring matches when they identify one unique product.
        matches = [entry for entry in self.entries
                   if product_lower and (product_lower in entry.product.lower()
                                         or entry.product.lower() in product_lower)]
        return matches[0] if len(matches) == 1 else None

    def to_dict(self) -> dict:
        return {
            "distributor_code": self.distributor_code,
            "version": self.version,
            "effective_date": _date_to_str(self.effective_date),
            "source_file": self.source_file,
            "entries": [e.to_dict() for e in self.entries],
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RateList":
        return cls(
            distributor_code=d["distributor_code"],
            version=d["version"],
            effective_date=_str_to_date(d["effective_date"]),
            source_file=d["source_file"],
            entries=[RateEntry.from_dict(e) for e in d.get("entries", [])],
            superseded_by=d.get("superseded_by"),
        )


@dataclass
class RateQuote:
    """An explicitly stated price from a named distributor contact."""
    product: str
    rate: float
    agreed_by: str
    agreed_date: date
    source_type: SourceType
    source_ref: str

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "rate": self.rate,
            "agreed_by": self.agreed_by,
            "agreed_date": _date_to_str(self.agreed_date),
            "source_type": self.source_type.value,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RateQuote":
        return cls(
            product=d["product"], rate=float(d["rate"]), agreed_by=d["agreed_by"],
            agreed_date=_str_to_date(d["agreed_date"]),
            source_type=SourceType(d.get("source_type", "chat_message")),
            source_ref=d.get("source_ref", ""),
        )


# ─────────────────────────────────────────────────────────────
# Bill / Invoice
# ─────────────────────────────────────────────────────────────
@dataclass
class BillLineItem:
    """A single line on a bill."""
    product: str
    quantity: float
    rate: float
    amount: float
    gst_rate: Optional[float] = None
    gst_amount: Optional[float] = None
    hsn: Optional[str] = None
    is_free: bool = False
    discount_amount: float = 0.0
    unit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BillLineItem":
        return cls(**d)


@dataclass
class Bill:
    """A parsed bill/invoice from a distributor."""
    bill_id: str  # e.g. "GT/1187"
    distributor_code: str
    bill_date: date
    source_file: str = ""  # e.g. "GT-1187.jpg"
    items: list[BillLineItem] = field(default_factory=list)
    subtotal: float = 0.0
    tax_total: float = 0.0
    grand_total: float = 0.0
    delivery_date: Optional[date] = None
    ocr_confidence: float = 0.8
    is_paid: bool = False
    payment_ref: Optional[str] = None
    raw_text: str = ""

    @property
    def total_items_value(self) -> float:
        return sum(item.amount for item in self.items)

    def to_dict(self) -> dict:
        return {
            "bill_id": self.bill_id,
            "distributor_code": self.distributor_code,
            "bill_date": _date_to_str(self.bill_date),
            "source_file": self.source_file,
            "items": [i.to_dict() for i in self.items],
            "subtotal": self.subtotal,
            "tax_total": self.tax_total,
            "grand_total": self.grand_total,
            "delivery_date": _date_to_str(self.delivery_date),
            "ocr_confidence": self.ocr_confidence,
            "is_paid": self.is_paid,
            "payment_ref": self.payment_ref,
            "raw_text": self.raw_text,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Bill":
        return cls(
            bill_id=d["bill_id"],
            distributor_code=d["distributor_code"],
            bill_date=_str_to_date(d["bill_date"]),
            source_file=d["source_file"],
            items=[BillLineItem.from_dict(i) for i in d.get("items", [])],
            subtotal=d.get("subtotal", 0.0),
            tax_total=d.get("tax_total", 0.0),
            grand_total=d.get("grand_total", 0.0),
            delivery_date=_str_to_date(d.get("delivery_date")),
            ocr_confidence=d.get("ocr_confidence", 0.8),
            is_paid=d.get("is_paid", False),
            payment_ref=d.get("payment_ref"),
            raw_text=d.get("raw_text", ""),
        )


# ─────────────────────────────────────────────────────────────
# Scheme
# ─────────────────────────────────────────────────────────────
@dataclass
class Scheme:
    """A free-goods or discount scheme agreed in chat/voice."""
    scheme_id: str
    distributor_code: str
    product: str
    scheme_type: SchemeType
    buy_qty: Optional[float] = None  # "buy X"
    free_qty: Optional[float] = None  # "get Y free"
    discount_pct: Optional[float] = None  # Alternative: percentage discount
    agreed_by: str = ""  # Salesman name
    agreed_date: Optional[date] = None
    source_type: SourceType = SourceType.CHAT_MESSAGE
    source_ref: str = ""  # e.g. "chat.txt:L212"
    is_used: bool = False  # Has this scheme been applied to a bill?
    expires_after_use: bool = True  # One-time schemes expire after use
    raw_text: str = ""
    is_active: bool = True

    @property
    def free_goods_value(self) -> Optional[float]:
        """Value of free goods, needs rate from rate list."""
        return None  # Computed externally with rate

    def compute_free_value(self, rate: float) -> float:
        """Compute value of free goods at given rate."""
        if self.free_qty:
            return self.free_qty * rate
        return 0.0

    def compute_discount_value(self, base_amount: float) -> float:
        """Compute discount value."""
        if self.discount_pct:
            return base_amount * (self.discount_pct / 100.0)
        return 0.0

    def to_dict(self) -> dict:
        return {
            "scheme_id": self.scheme_id,
            "distributor_code": self.distributor_code,
            "product": self.product,
            "scheme_type": self.scheme_type.value,
            "buy_qty": self.buy_qty,
            "free_qty": self.free_qty,
            "discount_pct": self.discount_pct,
            "agreed_by": self.agreed_by,
            "agreed_date": _date_to_str(self.agreed_date),
            "source_type": self.source_type.value,
            "source_ref": self.source_ref,
            "is_used": self.is_used,
            "expires_after_use": self.expires_after_use,
            "raw_text": self.raw_text,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Scheme":
        return cls(
            scheme_id=d["scheme_id"],
            distributor_code=d["distributor_code"],
            product=d["product"],
            scheme_type=SchemeType(d["scheme_type"]),
            buy_qty=d.get("buy_qty"),
            free_qty=d.get("free_qty"),
            discount_pct=d.get("discount_pct"),
            agreed_by=d.get("agreed_by", ""),
            agreed_date=_str_to_date(d.get("agreed_date")),
            source_type=SourceType(d.get("source_type", "chat_message")),
            source_ref=d.get("source_ref", ""),
            is_used=d.get("is_used", False),
            expires_after_use=d.get("expires_after_use", True),
            raw_text=d.get("raw_text", ""),
            is_active=d.get("is_active", True),
        )


# ─────────────────────────────────────────────────────────────
# Payment
# ─────────────────────────────────────────────────────────────
@dataclass
class Payment:
    """A UPI payment record."""
    payment_id: str
    distributor_code: str
    amount: float
    payment_date: date
    upi_ref: str = ""
    source_file: str = ""  # e.g. "upi_oct5.jpg"
    matched_bill_id: Optional[str] = None
    is_verified: bool = False

    def to_dict(self) -> dict:
        return {
            "payment_id": self.payment_id,
            "distributor_code": self.distributor_code,
            "amount": self.amount,
            "payment_date": _date_to_str(self.payment_date),
            "upi_ref": self.upi_ref,
            "source_file": self.source_file,
            "matched_bill_id": self.matched_bill_id,
            "is_verified": self.is_verified,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Payment":
        return cls(
            payment_id=d["payment_id"],
            distributor_code=d["distributor_code"],
            amount=d["amount"],
            payment_date=_str_to_date(d["payment_date"]),
            upi_ref=d.get("upi_ref", ""),
            source_file=d.get("source_file", ""),
            matched_bill_id=d.get("matched_bill_id"),
            is_verified=d.get("is_verified", False),
        )


# ─────────────────────────────────────────────────────────────
# Statement (Outstanding Balance)
# ─────────────────────────────────────────────────────────────
@dataclass
class StatementEntry:
    """An entry on a distributor's outstanding statement."""
    bill_id: str
    bill_date: date
    amount: float
    status: str = "due"  # "due", "partial", "paid"

    def to_dict(self) -> dict:
        return {
            "bill_id": self.bill_id,
            "bill_date": _date_to_str(self.bill_date),
            "amount": self.amount,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StatementEntry":
        return cls(
            bill_id=d["bill_id"],
            bill_date=_str_to_date(d["bill_date"]),
            amount=d["amount"],
            status=d.get("status", "due"),
        )


@dataclass
class Statement:
    """A distributor's statement of outstanding bills."""
    distributor_code: str
    statement_date: date
    source_file: str = ""
    entries: list[StatementEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "distributor_code": self.distributor_code,
            "statement_date": _date_to_str(self.statement_date),
            "source_file": self.source_file,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Statement":
        return cls(
            distributor_code=d["distributor_code"],
            statement_date=_str_to_date(d["statement_date"]),
            source_file=d["source_file"],
            entries=[StatementEntry.from_dict(e) for e in d.get("entries", [])],
        )


# ─────────────────────────────────────────────────────────────
# Distributor Card (Contract Card)
# ─────────────────────────────────────────────────────────────
@dataclass
class AuthorizedPerson:
    """A person authorized by the distributor."""
    name: str
    role: str = "salesman"
    phone: str = ""
    can_offer_schemes: bool = True
    permissions: list[str] = field(default_factory=lambda: ["schemes", "deliveries"])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AuthorizedPerson":
        valid_keys = {"name", "role", "phone", "can_offer_schemes", "permissions"}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})


@dataclass
class DistributorCard:
    """Contract card for a distributor — persistent knowledge."""
    code: str  # Short code like "GT"
    name: str  # Full name like "Ganga Traders"
    authorized_persons: list[AuthorizedPerson] = field(default_factory=list)
    credit_days: int = 30
    interest_rate: float = 0.18  # 18% p.a.
    rate_lists: list[RateList] = field(default_factory=list)
    rate_quotes: list[RateQuote] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)
    schemes: list[Scheme] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)
    owner_feedback: list[dict] = field(default_factory=list)

    def get_active_rate_list(self, on_date: date) -> Optional[RateList]:
        """Get the rate list in force on a given date.

        Returns the rate list with the latest effective_date that is
        on or before `on_date`.
        """
        applicable = [
            rl for rl in self.rate_lists
            if rl.effective_date <= on_date
        ]
        if not applicable:
            return None
        return max(applicable, key=lambda rl: (rl.effective_date, rl.source_file, rl.version))

    def get_rate_lists_on(self, on_date: date) -> list[RateList]:
        """Return all lists tied for the latest effective date on a given day."""
        active = self.get_active_rate_list(on_date)
        if active is None:
            return []
        return sorted(
            (rl for rl in self.rate_lists if rl.effective_date == active.effective_date),
            key=lambda rl: (rl.source_file, rl.version),
        )

    def get_rate_for_product(self, product: str, on_date: date) -> Optional[RateEntry]:
        """Get the rate for a product on a given date."""
        rate_list = self.get_active_rate_list(on_date)
        if rate_list is None:
            return None
        return rate_list.get_rate(product)

    def get_applicable_schemes(self, product: str, on_date: date) -> list[Scheme]:
        """Get schemes applicable for a product on a given date."""
        result = []
        for scheme in self.schemes:
            if not scheme.is_active:
                continue
            # Skip already-used one-time schemes
            if scheme.scheme_type == SchemeType.ONE_TIME and scheme.is_used:
                continue
            # Check product match (fuzzy)
            if (product.lower() in scheme.product.lower() or
                    scheme.product.lower() in product.lower() or
                    scheme.product.lower() in ("all", "general", "any", "")):
                result.append(scheme)
        return result

    def is_authorized(self, person_name: str) -> bool:
        """Check if a person is authorized by this distributor."""
        for ap in self.authorized_persons:
            if ap.name.lower() == person_name.lower():
                return True
        return False

    def get_unpaid_bills(self) -> list[Bill]:
        """Get bills that haven't been marked as paid."""
        return [b for b in self.bills if not b.is_paid]

    def get_bill(self, bill_id: str) -> Optional[Bill]:
        """Find a bill by id."""
        for b in self.bills:
            if b.bill_id == bill_id or b.bill_id.replace("/", "-") == bill_id.replace("/", "-"):
                return b
        return None

    def add_authorized_person(
        self,
        name: str,
        role: str = "salesman",
        can_offer_schemes: bool = True,
        phone: str = "",
    ) -> AuthorizedPerson:
        """Add or update an authorized person."""
        for ap in self.authorized_persons:
            if ap.name.lower() == name.lower():
                ap.role = role
                ap.can_offer_schemes = can_offer_schemes
                if phone:
                    ap.phone = phone
                return ap
        ap = AuthorizedPerson(
            name=name,
            role=role,
            phone=phone,
            can_offer_schemes=can_offer_schemes,
        )
        self.authorized_persons.append(ap)
        return ap

    def add_rate_list(
        self,
        version: str,
        effective_date: date,
        rates: dict[str, RateEntry],
        source: str = "",
    ) -> RateList:
        """Add a rate list to this card."""
        entries = list(rates.values())
        rl = RateList(
            distributor_code=self.code,
            version=version,
            effective_date=effective_date,
            source_file=source,
            entries=entries,
        )
        self.rate_lists.append(rl)
        return rl

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "authorized_persons": [p.to_dict() for p in self.authorized_persons],
            "credit_days": self.credit_days,
            "interest_rate": self.interest_rate,
            "rate_lists": [rl.to_dict() for rl in self.rate_lists],
            "rate_quotes": [q.to_dict() for q in self.rate_quotes],
            "bills": [b.to_dict() for b in self.bills],
            "schemes": [s.to_dict() for s in self.schemes],
            "payments": [p.to_dict() for p in self.payments],
            "statements": [s.to_dict() for s in self.statements],
            "owner_feedback": self.owner_feedback,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DistributorCard":
        return cls(
            code=d["code"],
            name=d["name"],
            authorized_persons=[AuthorizedPerson.from_dict(p) for p in d.get("authorized_persons", [])],
            credit_days=d.get("credit_days", 30),
            interest_rate=d.get("interest_rate", 0.18),
            rate_lists=[RateList.from_dict(rl) for rl in d.get("rate_lists", [])],
            rate_quotes=[RateQuote.from_dict(q) for q in d.get("rate_quotes", [])],
            bills=[Bill.from_dict(b) for b in d.get("bills", [])],
            schemes=[Scheme.from_dict(s) for s in d.get("schemes", [])],
            payments=[Payment.from_dict(p) for p in d.get("payments", [])],
            statements=[Statement.from_dict(s) for s in d.get("statements", [])],
            owner_feedback=d.get("owner_feedback", []),
        )


# ─────────────────────────────────────────────────────────────
# Finding (output of a detector)
# ─────────────────────────────────────────────────────────────
class FindingStatus(str, Enum):
    CONFIRMED = "confirmed"
    CONFLICT = "conflict"
    CANNOT_DETERMINE = "cannot_determine"
    CLEAN = "clean"


@dataclass
class Finding:
    """A single finding from a detector."""
    bill_id: str
    finding_class: str  # One of the six class names
    status: FindingStatus
    amount_inr: float = 0.0
    evidence: list[str] = field(default_factory=list)
    reasoning: str = ""
    confidence: float = 0.0
    action: dict = field(default_factory=dict)
    # For conflicts
    sources: list[dict] = field(default_factory=list)
    # For cannot_determine
    missing: str = ""
    ask: str = ""

    def to_dict(self) -> dict:
        result = {
            "bill_id": self.bill_id,
            "class": self.finding_class,
            "status": self.status.value,
            "amount_inr": round(self.amount_inr, 2),
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
        }
        if self.action:
            result["action"] = self.action
        if self.status == FindingStatus.CONFLICT:
            result["sources"] = self.sources
        if self.status == FindingStatus.CANNOT_DETERMINE:
            result["missing"] = self.missing
            result["ask"] = self.ask
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(
            bill_id=d["bill_id"],
            finding_class=d["class"],
            status=FindingStatus(d["status"]),
            amount_inr=d.get("amount_inr", 0.0),
            evidence=d.get("evidence", []),
            reasoning=d.get("reasoning", ""),
            confidence=d.get("confidence", 0.0),
            action=d.get("action", {}),
            sources=d.get("sources", []),
            missing=d.get("missing", ""),
            ask=d.get("ask", ""),
        )
