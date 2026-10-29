"""
WhatsApp Chat Parser.

Parses exported WhatsApp _chat.txt files into structured messages.
Handles the standard WhatsApp export format and identifies attached files.
"""

from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# WhatsApp export patterns (handles multiple date formats)
# Format: "10/3/26, 10:30 AM - Sender: Message"
# Format: "03/10/2026, 10:30 am - Sender: Message"
# Format: "[10/3/26, 10:30:00 AM] Sender: Message"
CHAT_LINE_PATTERNS = [
    # Bracketed format: [DD/MM/YY, HH:MM:SS] or [DD/MM/YY, HH:MM AM]
    re.compile(
        r'^\[(\d{1,2}/\d{1,2}/\d{2,4}),?\s+'
        r'(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\]\s*'
        r'(.+?):\s*(.*)'
    ),
    # Standard format: DD/MM/YY, HH:MM - Sender: Message
    re.compile(
        r'^(\d{1,2}/\d{1,2}/\d{2,4}),?\s+'
        r'(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\s*[-–]\s*'
        r'(.+?):\s*(.*)'
    ),
]

# System messages
SYSTEM_MSG_PATTERNS = [
    re.compile(r'Messages and calls are end-to-end encrypted', re.IGNORECASE),
    re.compile(r'created group', re.IGNORECASE),
    re.compile(r'changed the subject', re.IGNORECASE),
    re.compile(r'added \+', re.IGNORECASE),
]

# Attachment patterns
ATTACHMENT_PATTERN = re.compile(
    r'<attached:\s*(.+?)>|'
    r'(\S+\.(?:jpg|jpeg|png|pdf|opus|mp3|mp4|webp))\s*\(file attached\)',
    re.IGNORECASE,
)

# Forwarded message indicator
FORWARDED_PATTERN = re.compile(r'^\u200e?\[?Forwarded\]?', re.IGNORECASE)


@dataclass
class ChatMessage:
    """A single parsed WhatsApp chat message."""
    line_number: int
    timestamp: datetime
    sender: str
    text: str
    attachment: Optional[str] = None
    is_forwarded: bool = False
    is_system: bool = False
    raw_line: str = ""

    @property
    def has_attachment(self) -> bool:
        return self.attachment is not None

    @property
    def attachment_type(self) -> Optional[str]:
        if not self.attachment:
            return None
        ext = Path(self.attachment).suffix.lower()
        type_map = {
            ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
            ".pdf": "pdf",
            ".opus": "voice", ".mp3": "voice",
            ".mp4": "video",
        }
        return type_map.get(ext, "unknown")

    def to_dict(self) -> dict:
        return {
            "line_number": self.line_number,
            "timestamp": self.timestamp.isoformat(),
            "sender": self.sender,
            "text": self.text,
            "attachment": self.attachment,
            "is_forwarded": self.is_forwarded,
            "is_system": self.is_system,
        }


def _parse_date(date_str: str, time_str: str) -> datetime:
    """Parse various date/time formats from WhatsApp exports."""
    date_str = date_str.strip()
    time_str = time_str.strip()

    # Try various date formats (Indian WhatsApp exports use DD/MM/YY)
    date_formats = [
        "%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y",
        "%m/%d/%y", "%m/%d/%Y", "%m-%d-%y", "%m-%d-%Y",
    ]
    time_formats = [
        "%I:%M %p", "%I:%M:%S %p", "%I:%M%p", "%I:%M:%S%p",
        "%H:%M", "%H:%M:%S",
    ]

    for df in date_formats:
        for tf in time_formats:
            try:
                return datetime.strptime(f"{date_str} {time_str}", f"{df} {tf}")
            except ValueError:
                continue

    # Fallback
    logger.warning(f"Could not parse date/time: '{date_str}' '{time_str}'")
    return datetime.now()


def parse_chat_file(chat_path: str | Path) -> list[ChatMessage]:
    """Parse a WhatsApp _chat.txt file into structured messages.

    Args:
        chat_path: Path to the _chat.txt file.

    Returns:
        List of ChatMessage objects, in chronological order.
    """
    chat_path = Path(chat_path)
    if not chat_path.exists():
        logger.error(f"Chat file not found: {chat_path}")
        return []

    # Read with UTF-8 (WhatsApp exports use UTF-8)
    with open(chat_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    messages: list[ChatMessage] = []
    current_msg: Optional[ChatMessage] = None

    for line_num, raw_line in enumerate(lines, start=1):
        raw_line = raw_line.rstrip("\n")

        # Skip empty lines
        if not raw_line.strip():
            if current_msg:
                current_msg.text += "\n"
            continue

        # Check for system messages
        is_system = any(pat.search(raw_line) for pat in SYSTEM_MSG_PATTERNS)

        # Try to match as a new message
        matched = False
        for pattern in CHAT_LINE_PATTERNS:
            m = pattern.match(raw_line)
            if m:
                # Save previous message
                if current_msg:
                    current_msg.text = current_msg.text.strip()
                    messages.append(current_msg)

                date_str, time_str, sender, text = m.groups()
                timestamp = _parse_date(date_str, time_str)

                # Check for attachment
                attachment = None
                att_match = ATTACHMENT_PATTERN.search(text)
                if att_match:
                    attachment = att_match.group(1) or att_match.group(2)

                # Check for forwarded
                is_forwarded = bool(FORWARDED_PATTERN.search(text))

                current_msg = ChatMessage(
                    line_number=line_num,
                    timestamp=timestamp,
                    sender=sender.strip(),
                    text=text.strip(),
                    attachment=attachment,
                    is_forwarded=is_forwarded,
                    is_system=is_system,
                    raw_line=raw_line,
                )
                matched = True
                break

        if not matched:
            # Continuation of previous message
            if current_msg:
                current_msg.text += "\n" + raw_line.strip()
            else:
                logger.debug(f"Orphan line {line_num}: {raw_line[:80]}")

    # Don't forget the last message
    if current_msg:
        current_msg.text = current_msg.text.strip()
        messages.append(current_msg)

    logger.info(f"Parsed {len(messages)} messages from {chat_path.name}")
    return messages


def extract_distributor_info(messages: list[ChatMessage]) -> dict[str, set[str]]:
    """Extract distributor names and their senders from chat messages.

    Returns:
        Dict mapping probable distributor identifiers to sets of sender names.
    """
    senders: dict[str, int] = {}
    for msg in messages:
        if not msg.is_system:
            senders[msg.sender] = senders.get(msg.sender, 0) + 1

    # Group by sender (simple heuristic: the owner is the most frequent sender)
    if not senders:
        return {}

    # The owner is likely the one who sends the most messages
    owner = max(senders, key=senders.get)

    distributors: dict[str, set[str]] = {}
    for sender in senders:
        if sender != owner:
            # Try to identify distributor from sender name
            distributors.setdefault("unknown", set()).add(sender)

    return distributors


def get_messages_in_range(
    messages: list[ChatMessage],
    start: datetime,
    end: datetime,
) -> list[ChatMessage]:
    """Filter messages within a date range."""
    return [m for m in messages if start <= m.timestamp <= end]
