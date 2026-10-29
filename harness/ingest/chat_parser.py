"""
WhatsApp export parser.

Handles the formats WhatsApp actually produces:

    iOS      [09/10/26, 11:30:25] Ramesh: text            <attached: 00000012-PHOTO-....jpg>
    Android  09/10/26, 11:30 am - Ramesh: text            IMG-20261009-WA0012.jpg (file attached)
    either   12- or 24-hour clocks, narrow no-break spaces, LRM marks, 2- or 4-digit years,
             multi-line messages, captions on the line after an attachment, system lines.

Day/month order is detected from the whole file (Indian exports are day-first;
a US-format export is recognised when a "month" field exceeds 12).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from harness.textutil import clean_text

logger = logging.getLogger(__name__)

_TS = r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp]\.?\s?[Mm]\.?)?"
LINE_PATTERNS = [
    re.compile(r"^\[" + _TS + r"\]\s*(.*)$"),            # iOS
    re.compile(r"^" + _TS + r"\s*[-–—]\s*(.*)$"),       # Android
]
SENDER_SPLIT = re.compile(r"^([^:]{1,80}?):\s?(.*)$", re.S)

ATTACH_PATTERNS = [
    re.compile(r"<\s*attached:\s*([^>]+?)\s*>", re.I),
    re.compile(r"([\w\-. ()ऀ-ॿ]+?\.(?:jpe?g|png|webp|heic|pdf|opus|ogg|mp3|m4a|aac|wav|amr|mp4|xlsx?|docx?))\s*\(file attached\)", re.I),
    re.compile(r"^\s*([\w\-.()]+\.(?:jpe?g|png|webp|heic|pdf|opus|ogg|mp3|m4a|aac|wav|amr))\s*$", re.I),
]
OMITTED = re.compile(r"(<media omitted>|image omitted|audio omitted|document omitted|video omitted|sticker omitted)", re.I)
FORWARD_MARK = re.compile(
    r"^\s*(\*?\[?forwarded( many times| message| as received)?\]?\*?\s*[:\-]?|fwd\s*:|fw\s*:|↪|➡️?\s*forwarded)",
    re.I)
SYSTEM_HINTS = re.compile(
    r"(end-to-end encrypted|created group|changed the subject|changed this group|added|left$|removed|"
    r"joined using this group|security code changed|changed the group|is a contact|deleted this message|"
    r"this message was deleted|disappearing messages)", re.I)

_SYSTEM_BODY = re.compile(
    r"^(messages and calls are end-to-end encrypted|.*created (this )?group|.*changed the subject|"
    r".*security code (with .* )?changed|.*added you|.*joined using this group|you deleted this message|"
    r"this message was deleted|.*turned on disappearing messages)", re.I)

MEDIA_EXT = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".heic": "image",
    ".pdf": "pdf",
    ".opus": "audio", ".ogg": "audio", ".mp3": "audio", ".m4a": "audio", ".aac": "audio",
    ".wav": "audio", ".amr": "audio",
}


@dataclass
class ChatMessage:
    line: int
    timestamp: datetime
    sender: str
    text: str
    chat_file: str = "_chat.txt"
    attachments: list[str] = field(default_factory=list)
    is_forwarded: bool = False
    is_system: bool = False
    media_omitted: bool = False

    @property
    def attachment(self) -> Optional[str]:
        return self.attachments[0] if self.attachments else None

    def cite(self) -> str:
        return f"{self.chat_file}:L{self.line}"

    @property
    def body(self) -> str:
        """Text without attachment markers."""
        t = self.text
        for pat in ATTACH_PATTERNS[:2]:
            t = pat.sub(" ", t)
        t = FORWARD_MARK.sub("", t, count=1)
        return re.sub(r"\s+", " ", t).strip()


def media_kind(filename: str) -> str:
    return MEDIA_EXT.get(Path(filename).suffix.lower(), "other")


def _detect_day_first(lines: list[str]) -> bool:
    first_big = second_big = 0
    for raw in lines:
        s = clean_text(raw)
        for pat in LINE_PATTERNS:
            m = pat.match(s)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                first_big += a > 12
                second_big += b > 12
                break
    if second_big and not first_big:
        return False
    return True


def _mk_ts(m, day_first: bool) -> Optional[datetime]:
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mm, ss = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
    ampm = (m.group(7) or "").lower().replace(".", "").replace(" ", "")
    if ampm == "pm" and hh < 12:
        hh += 12
    if ampm == "am" and hh == 12:
        hh = 0
    if y < 100:
        y += 2000
    day, month = (a, b) if day_first else (b, a)
    try:
        return datetime(y, month, day, hh, mm, ss)
    except ValueError:
        try:
            return datetime(y, day, month, hh, mm, ss)
        except ValueError:
            return None


def is_chat_file(path: Path) -> bool:
    """A .txt file is a chat export when several lines start with a timestamp."""
    if path.suffix.lower() != ".txt" or path.name.lower().endswith((".opus.txt", ".ogg.txt", ".mp3.txt",
                                                                     ".m4a.txt", ".wav.txt")):
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = [next(f, "") for _ in range(30)]
    except OSError:
        return False
    hits = sum(1 for line in head if any(p.match(clean_text(line)) for p in LINE_PATTERNS))
    return hits >= 1


def parse_chat_file(path: str | Path, chat_name: Optional[str] = None) -> list[ChatMessage]:
    path = Path(path)
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    day_first = _detect_day_first(lines)
    chat_name = chat_name or path.name
    messages: list[ChatMessage] = []
    current: Optional[ChatMessage] = None

    for idx, raw_line in enumerate(lines, start=1):
        line = clean_text(raw_line)
        match = None
        for pat in LINE_PATTERNS:
            match = pat.match(line)
            if match:
                break
        if match:
            ts = _mk_ts(match, day_first)
            rest = match.group(8)
            if ts is None:
                logger.warning(f"{chat_name}:L{idx}: unreadable timestamp")
                continue
            sm = SENDER_SPLIT.match(rest)
            if sm and not SYSTEM_HINTS.search(sm.group(1)):
                sender, body = sm.group(1).strip(), sm.group(2)
                is_system = False
            else:
                sender, body, is_system = "", rest, True
            if not is_system and _SYSTEM_BODY.search(body) and len(body) < 160:
                is_system = True
            current = ChatMessage(line=idx, timestamp=ts, sender=sender, text=body.strip(),
                                  chat_file=chat_name, is_system=is_system)
            messages.append(current)
            _scan_attachments(current, body)
            current.is_forwarded = bool(FORWARD_MARK.match(body))
        elif current is not None and line.strip():
            current.text = (current.text + "\n" + line.strip()).strip()
            _scan_attachments(current, line)
            if not current.is_forwarded and FORWARD_MARK.match(current.text):
                current.is_forwarded = True
    return messages


def _scan_attachments(msg: ChatMessage, text: str) -> None:
    for pat in ATTACH_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(1).strip()
            if name and name not in msg.attachments:
                msg.attachments.append(name)
    if OMITTED.search(text):
        msg.media_omitted = True
