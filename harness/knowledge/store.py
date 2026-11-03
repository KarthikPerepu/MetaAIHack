"""
Saving and loading Khata's records. Everything lives under --state-dir:

    state_dir/
        distributors/<CODE>.json   each distributor's bills, rate lists, schemes, payments ...
        shop.json                  who the owner is, which sender belongs to which distributor
        feedback.json              the owner's answers, used by every later report
        alerts.json                messages that were set aside as scams
        ingest_log.json            which weeks have been read
        cache/                     saved model answers and readings, so reruns are free

Files are written to a temp file first and then swapped in, so a crash never leaves half a file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from harness.knowledge.models import DistributorCard

logger = logging.getLogger(__name__)


def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # corrupted file: keep going, but say so
        logger.error(f"Could not read {path}: {e}")
        return default


class KnowledgeStore:
    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.dist_dir = self.state_dir / "distributors"
        self.cache_dir = self.state_dir / "cache"
        self.dist_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cards: dict[str, DistributorCard] = {}
        for p in sorted(self.dist_dir.glob("*.json")):
            data = _read_json(p, None)
            if not data:
                continue
            try:
                card = DistributorCard.from_dict(data)
                self.cards[card.code] = card
            except Exception as e:
                logger.error(f"Skipping unreadable card {p.name}: {e}")
        self.shop: dict = _read_json(self.state_dir / "shop.json", {})
        self.shop.setdefault("owner_names", [])
        self.shop.setdefault("sender_map", {})        # display name -> distributor code
        self.shop.setdefault("shop_name", "")
        self.feedback: list = _read_json(self.state_dir / "feedback.json", [])
        self.alerts: list = _read_json(self.state_dir / "alerts.json", [])
        self.ingest_log: dict = _read_json(self.state_dir / "ingest_log.json", {"weeks": [], "files": {}})

    # Cards
    def card(self, code: str) -> Optional[DistributorCard]:
        return self.cards.get(code)

    def get_or_create(self, code: str, name: str = "") -> DistributorCard:
        card = self.cards.get(code)
        if card is None:
            card = DistributorCard(code=code, name=name or code)
            self.cards[code] = card
            logger.info(f"New distributor card: {code} ({card.name})")
        elif name and (not card.name or card.name == code):
            card.name = name
        if name and name not in card.aliases and name != card.name:
            card.aliases.append(name)
        return card

    def all_cards(self) -> list[DistributorCard]:
        return [self.cards[c] for c in sorted(self.cards)]

    # Persistence
    def save(self) -> None:
        for code, card in self.cards.items():
            _atomic_write_json(self.dist_dir / f"{code}.json", card.to_dict())
        _atomic_write_json(self.state_dir / "shop.json", self.shop)
        _atomic_write_json(self.state_dir / "feedback.json", self.feedback)
        _atomic_write_json(self.state_dir / "alerts.json", self.alerts)
        _atomic_write_json(self.state_dir / "ingest_log.json", self.ingest_log)

    def add_alert(self, alert: dict) -> None:
        key = (alert.get("source"), alert.get("reason"))
        if not any((a.get("source"), a.get("reason")) == key for a in self.alerts):
            self.alerts.append(alert)
