"""
WhatsApp's "Export chat" (with media) gives you a .zip. This unpacks one safely so it can be read
like a normal folder.

A zip can contain tricks, so we only write files that stay inside the target folder (no "../" or
absolute paths, no links), and we stop at a sensible size.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_TOTAL_BYTES = 1_000_000_000      # 1 GB of unpacked files is far more than a week of chats
MAX_FILES = 5000


def unpack(zip_path: str | Path, dest: str | Path) -> Path:
    """Unpack zip_path into dest and return dest. Unsafe entries are skipped and logged."""
    zip_path, dest = Path(zip_path), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        if len(members) > MAX_FILES:
            raise ValueError(f"{zip_path.name} has {len(members)} files; that doesn't look like a chat export")
        for info in members:
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            is_link = (info.external_attr >> 16) & 0o170000 == 0o120000
            target = (root / name).resolve()
            if is_link or name.startswith("/") or ".." in Path(name).parts or not target.is_relative_to(root):
                logger.warning(f"Skipping unsafe path in {zip_path.name}: {info.filename}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                # count what is actually written; a zip's own size labels can't be trusted
                while chunk := src.read(1 << 20):
                    total += len(chunk)
                    if total > MAX_TOTAL_BYTES:
                        raise ValueError(f"{zip_path.name} unpacks to more than {MAX_TOTAL_BYTES // 1_000_000} MB")
                    out.write(chunk)
    return dest
