"""
Feedback Learner.

Processes the owner's feedback and answers to escalated findings,
updating the persistent KnowledgeStore so future evaluations reflect
the owner's corrections and agreements.
"""

from __future__ import annotations
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Any

from harness.config import HarnessConfig
from harness.agents.llama_client import LlamaClient
from harness.knowledge.store import KnowledgeStore
from harness.knowledge.models import (
    DistributorCard, RateEntry, Scheme, SchemeType, SourceType,
    AuthorizedPerson, Payment,
)

logger = logging.getLogger(__name__)


class FeedbackLearner:
    """Learns from the owner's answers and updates the knowledge store."""

    def __init__(
        self,
        config: HarnessConfig,
        store: KnowledgeStore,
        llama: Optional[LlamaClient] = None,
    ):
        self.config = config
        self.store = store
        self.llama = llama or LlamaClient(config.llama)

    def process_feedback_file(self, feedback_path: str) -> dict:
        """Load and apply feedback from a JSON file.

        Args:
            feedback_path: Path to feedback JSON file.

        Returns:
            Dictionary summarizing applied updates.
        """
        path = Path(feedback_path)
        if not path.exists():
            raise FileNotFoundError(f"Feedback file not found: {feedback_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return self.process_feedback(data)

    def process_feedback(self, data: Any) -> dict:
        """Process feedback data (dict or list of items).

        Returns:
            Summary of updates applied.
        """
        items = data if isinstance(data, list) else [data]
        stats = {
            "total_items": len(items),
            "rates_updated": 0,
            "terms_updated": 0,
            "schemes_updated": 0,
            "bills_updated": 0,
            "persons_updated": 0,
            "errors": 0,
        }

        for item in items:
            try:
                self._apply_feedback_item(item, stats)
                self.store.log_feedback(item)
            except Exception as e:
                logger.error(f"Failed to apply feedback item {item}: {e}")
                stats["errors"] += 1

        self.store.save()
        logger.info(f"Feedback applied: {stats}")
        return stats

    def _apply_feedback_item(self, item: dict, stats: dict):
        """Apply a single feedback item to the knowledge store."""
        # 1. Check for explicit distributor code or infer from bill_id
        dist_code = item.get("distributor") or item.get("distributor_code")
        bill_id = item.get("bill_id", "")
        if not dist_code and bill_id and "/" in bill_id:
            dist_code = bill_id.split("/")[0]

        card = self.store.get_distributor(dist_code) if dist_code else None

        # Feedback types:
        # A. Credit terms update (e.g. "Sharma Products credit days are 45")
        if "credit_days" in item or "interest_rate" in item:
            if card:
                if "credit_days" in item:
                    card.credit_days = int(item["credit_days"])
                if "interest_rate" in item:
                    card.interest_rate = float(item["interest_rate"])
                stats["terms_updated"] += 1
                logger.info(f"Updated credit terms for {card.code}: {card.credit_days} days, {card.interest_rate*100}% p.a.")

        # B. Rate override / agreement (e.g., owner agrees a special rate)
        product = item.get("product") or item.get("item")
        agreed_rate = item.get("agreed_rate") or item.get("rate")
        if product and agreed_rate is not None and card:
            rate_val = float(agreed_rate)
            eff_date = (
                date.fromisoformat(item["effective_date"])
                if "effective_date" in item
                else date.today()
            )
            # Find active rate list or create/update entry
            active_rl = card.get_active_rate_list(eff_date)
            if active_rl:
                replacement = RateEntry(
                    product=product,
                    rate=rate_val,
                    hsn=item.get("hsn") or None,
                )
                existing = next((entry for entry in active_rl.entries
                                 if entry.product.casefold() == product.casefold()), None)
                if existing:
                    active_rl.entries[active_rl.entries.index(existing)] = replacement
                else:
                    active_rl.entries.append(replacement)
            else:
                card.add_rate_list(
                    version="owner_override",
                    effective_date=eff_date,
                    rates={product: RateEntry(product=product, rate=rate_val, hsn=item.get("hsn") or None)},
                    source="owner_feedback",
                )
            stats["rates_updated"] += 1
            logger.info(f"Updated rate for {card.code} - {product} = ₹{rate_val}")

        # C. Scheme confirmation / rejection / override
        if "scheme" in item or item.get("class") == "scheme_not_given":
            scheme_status = item.get("status") or item.get("feedback")
            if scheme_status in ("rejected", "invalid", "cancel", "cancelled") and card:
                # Mark scheme as cancelled or remove
                if product:
                    for s in card.schemes:
                        if product.lower() in s.product.lower():
                            s.is_active = False
                stats["schemes_updated"] += 1
            elif scheme_status in ("approved", "valid", "confirmed") and card:
                free_qty = item.get("free_qty", 1)
                buy_qty = item.get("buy_qty", 10)
                existing_scheme = next((s for s in card.schemes
                    if s.product.casefold() == (product or "General").casefold()
                    and s.buy_qty == buy_qty and s.free_qty == free_qty
                    and s.scheme_type == (SchemeType.ONE_TIME if not item.get("is_permanent")
                                          else SchemeType.PERMANENT)), None)
                if existing_scheme:
                    existing_scheme.source_type = SourceType.OWNER
                    existing_scheme.is_active = True
                else:
                    card.schemes.append(Scheme(
                        scheme_id=f"OWNER-{bill_id or len(card.schemes) + 1}",
                        distributor_code=card.code,
                        product=product or "General",
                        scheme_type=SchemeType.ONE_TIME if not item.get("is_permanent") else SchemeType.PERMANENT,
                        buy_qty=buy_qty,
                        free_qty=free_qty,
                        source_type=SourceType.OWNER,
                        agreed_by="Owner Feedback",
                        agreed_date=(date.fromisoformat(item["effective_date"])
                                     if item.get("effective_date") else date.today()),
                        source_ref="owner_feedback",
                    ))
                stats["schemes_updated"] += 1

        # D. Bill marked as paid directly (cash/NEFT)
        if (item.get("status") == "paid" or item.get("is_paid") or item.get("feedback") == "paid") and bill_id:
            for c in self.store.list_distributors():
                bill = c.get_bill(bill_id)
                if bill:
                    bill.is_paid = True
                    bill.payment_ref = item.get("payment_ref", "manual_owner_paid")
                    stats["bills_updated"] += 1
                    logger.info(f"Marked bill {bill_id} as paid by owner feedback")
                    break

        # E. Personnel authorization update
        person_name = item.get("person_name") or item.get("salesman")
        if person_name and card:
            can_offer = item.get("can_offer_schemes", True)
            card.add_authorized_person(
                name=person_name,
                role=item.get("role", "salesman"),
                can_offer_schemes=can_offer,
                phone=item.get("phone", ""),
            )
            stats["persons_updated"] += 1
            logger.info(f"Updated authorized person {person_name} for {card.code}")

        # F. Natural language comment parsing via Llama (if comment given and no explicit keys)
        comment = item.get("comment") or item.get("text") or item.get("note")
        if comment and not any(k in item for k in ("credit_days", "agreed_rate", "product")):
            self._parse_natural_language_feedback(comment, card, stats)

    def _parse_natural_language_feedback(self, text: str, card: Optional[DistributorCard], stats: dict):
        """Use NLP/heuristic or Llama to extract updates from natural text feedback."""
        # Simple high-accuracy regex patterns first
        credit_match = re.search(r'credit\s*(?:days|period|terms)?\s*(?:is|are|=|:)?\s*(\d+)\s*days?', text, re.I)
        if credit_match and card:
            card.credit_days = int(credit_match.group(1))
            stats["terms_updated"] += 1
            return

        paid_match = re.search(r'bill\s*([A-Z0-9/\-_]+)\s*(?:is|was)?\s*paid', text, re.I)
        if paid_match:
            bid = paid_match.group(1)
            for c in self.store.list_distributors():
                b = c.get_bill(bid)
                if b:
                    b.is_paid = True
                    stats["bills_updated"] += 1
                    return
