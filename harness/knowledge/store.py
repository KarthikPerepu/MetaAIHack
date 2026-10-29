"""
Persistent Knowledge Store.

Saves and loads distributor cards (rate lists, bills, schemes, payments)
as JSON files on disk under --state-dir.
"""

from __future__ import annotations
import json
import os
import logging
from pathlib import Path
from typing import Optional
from datetime import date

from harness.knowledge.models import DistributorCard

logger = logging.getLogger(__name__)


class KnowledgeStore:
    """On-disk persistent store for all distributor knowledge.

    Directory layout under state_dir:
        state_dir/
            distributors/
                GT.json      # Ganga Traders
                SP.json      # Sharma Products
                ...
            meta.json        # Global metadata (last ingest, etc.)
            feedback_log.json
    """

    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.distributors_dir = self.state_dir / "distributors"
        self.meta_file = self.state_dir / "meta.json"
        self.feedback_file = self.state_dir / "feedback_log.json"

        # Ensure directories exist
        self.distributors_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache
        self._distributors: dict[str, DistributorCard] = {}
        self._meta: dict = {}

        # Load existing state
        self._load_all()

    def _load_all(self):
        """Load all distributor cards from disk."""
        if self.meta_file.exists():
            with open(self.meta_file, "r", encoding="utf-8") as f:
                self._meta = json.load(f)

        for json_file in self.distributors_dir.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                card = DistributorCard.from_dict(data)
                self._distributors[card.code] = card
                logger.info(f"Loaded distributor: {card.code} ({card.name})")
            except Exception as e:
                logger.error(f"Failed to load {json_file}: {e}")

    def save(self):
        """Persist all state to disk."""
        for code, card in self._distributors.items():
            filepath = self.distributors_dir / f"{code}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(card.to_dict(), f, indent=2, ensure_ascii=False)

        with open(self.meta_file, "w", encoding="utf-8") as f:
            json.dump(self._meta, f, indent=2)

        logger.info(f"Saved {len(self._distributors)} distributors to {self.state_dir}")

    def get_distributor(self, code: str) -> Optional[DistributorCard]:
        """Get a distributor card by code."""
        return self._distributors.get(code)

    def get_or_create_distributor(self, code: str, name: str = "") -> DistributorCard:
        """Get or create a distributor card."""
        if code not in self._distributors:
            self._distributors[code] = DistributorCard(
                code=code,
                name=name or code,
            )
            logger.info(f"Created new distributor card: {code} ({name})")
        elif name and not self._distributors[code].name:
            self._distributors[code].name = name
        return self._distributors[code]

    def list_distributors(self) -> list[DistributorCard]:
        """List all known distributors."""
        return [self._distributors[code] for code in sorted(self._distributors)]

    def get_all_bills(self) -> list:
        """Get all bills across all distributors."""
        bills = []
        for card in self._distributors.values():
            bills.extend(card.bills)
        return bills

    def get_all_unpaid_bills(self, as_of: Optional[date] = None) -> list:
        """Get all unpaid bills, optionally filtered by date."""
        bills = []
        for card in self._distributors.values():
            for bill in card.get_unpaid_bills():
                if as_of is None or bill.bill_date <= as_of:
                    bills.append((card, bill))
        return bills

    def update_meta(self, key: str, value):
        """Update global metadata."""
        self._meta[key] = value

    def get_meta(self, key: str, default=None):
        """Get global metadata."""
        return self._meta.get(key, default)

    def log_feedback(self, feedback: dict):
        """Log owner feedback."""
        feedbacks = []
        if self.feedback_file.exists():
            with open(self.feedback_file, "r", encoding="utf-8") as f:
                feedbacks = json.load(f)
        feedbacks.append(feedback)
        with open(self.feedback_file, "w", encoding="utf-8") as f:
            json.dump(feedbacks, f, indent=2, ensure_ascii=False)
