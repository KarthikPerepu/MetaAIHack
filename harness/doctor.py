"""
`harness.py doctor <folder or .zip>`: look at a data folder before running on it, and say plainly what
Khata will and won't be able to read.

We wrote this after finding that the data we started with had 16-byte placeholder "photos". It checks:
the chat files, every attachment the chat mentions (present? a real photo / PDF / audio file, or a
placeholder?), saved .json/.txt readings, answer files, and whether a model or the offline readers
are available to read what's there.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from harness.ingest.chat_parser import is_chat_file, media_kind, parse_chat_file

PLACEHOLDER_BYTES = 200


def _real(path: Path, kind: str) -> tuple[bool, str]:
    size = path.stat().st_size
    if size < PLACEHOLDER_BYTES:
        return False, f"only {size} bytes, looks like a placeholder"
    head = path.read_bytes()[:16]
    if kind == "image":
        try:
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
            return True, ""
        except Exception:
            return False, "not a readable image"
    if kind == "pdf":
        if not head.startswith(b"%PDF"):
            return False, "not a PDF"
        try:
            from harness.ingest.media import pdf_text_and_tables
            text, _ = pdf_text_and_tables(path)
            return True, "" if text.strip() else "scanned (no text layer): needs Muse or Tesseract"
        except Exception:
            return False, "PDF couldn't be opened"
    if kind == "audio":
        known = head.startswith(b"OggS") or head[4:8] == b"ftyp" or head.startswith((b"RIFF", b"ID3", b"#!AMR")) \
            or head[:2] == b"\xff\xf1" or head[:2] == b"\xff\xfb"
        return (True, "") if known else (False, "not a recognised audio file")
    return True, ""


def examine(target: str | Path) -> dict:
    target = Path(target)
    tmp = None
    if target.is_file() and target.suffix.lower() == ".zip":
        from harness.ingest.zip_export import unpack
        tmp = Path(tempfile.mkdtemp(prefix="khata-doctor-"))
        folder = unpack(target, tmp)
    else:
        folder = target
    try:
        return _examine_folder(folder, target)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _examine_folder(folder: Path, shown: Path) -> dict:
    if not folder.is_dir():
        return {"folder": str(shown), "ok": False, "problems": ["folder not found"], "notes": []}
    problems, notes = [], []
    chats = sorted(p for p in folder.rglob("*.txt") if is_chat_file(p))
    report = {"folder": str(shown), "chats": [], "attachments": Counter(), "placeholders": [], "missing": [],
              "sidecars": 0, "answers": 0}
    referenced = set()
    for chat in chats:
        msgs = parse_chat_file(chat)
        if not msgs:
            continue
        senders = Counter(m.sender for m in msgs if m.sender)
        report["chats"].append({"file": str(chat.relative_to(folder)), "messages": len(msgs),
                                "from": msgs[0].timestamp.date().isoformat(), "to": msgs[-1].timestamp.date().isoformat(),
                                "senders": len(senders), "forwarded": sum(m.is_forwarded for m in msgs)})
        for m in msgs:
            for att in m.attachments:
                hits = [p for p in (chat.parent / att, folder / att) if p.exists()] or sorted(folder.rglob(Path(att).name))
                if not hits:
                    report["missing"].append(att)
                    continue
                p = hits[0]
                if p in referenced:
                    continue
                referenced.add(p)
                kind = media_kind(p.name)
                report["attachments"][kind] += 1
                real, why = _real(p, kind)
                sidecar = any(c.exists() for c in (p.with_name(p.name + ".json"), p.with_name(p.name + ".txt")))
                report["sidecars"] += sidecar
                if not real:
                    report["placeholders"].append(f"{p.name}: {why}" + (" (has a saved reading)" if sidecar else ""))
                elif why:
                    notes.append(f"{p.name}: {why}")
    answers = folder / "answers"
    if answers.is_dir():
        for f in answers.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8-sig"))
                report["answers"] += len(data) if isinstance(data, list) else 1
            except Exception:
                problems.append(f"answers/{f.name} isn't valid JSON")

    if not report["chats"]:
        problems.append("no WhatsApp chat export found (a .txt with lines like '[09/10/26, 11:30:25] Name: ...')")
    if report["missing"]:
        problems.append(f"{len(report['missing'])} attachment(s) mentioned in the chat aren't in the folder: "
                        + ", ".join(report["missing"][:5]))
    if report["placeholders"]:
        no_reading = [x for x in report["placeholders"] if "saved reading" not in x]
        msg = f"{len(report['placeholders'])} attachment(s) aren't real files"
        if no_reading:
            problems.append(msg + "; these have no saved reading either, so they can't be read: " + ", ".join(no_reading[:5]))
        else:
            notes.append(msg + "; results for them will come only from their saved .json/.txt readings")

    from harness.config import LlamaConfig
    from harness.ingest.media import tesseract_langs
    cfg = LlamaConfig.from_env()
    model_on = bool(cfg.api_key) and not cfg.offline
    if model_on:
        notes.append(f"model: {cfg.vision_model} at {cfg.base_url} will read photos and messages")
    else:
        notes.append("no API key: photos need Tesseract" + (" (installed)" if tesseract_langs() else " (NOT installed)")
                     + ", voice notes need local Whisper, messages use our own rules")
    report["attachments"] = dict(report["attachments"])
    report["problems"], report["notes"] = problems, notes
    report["ok"] = not problems
    return report


def print_report(r: dict) -> None:
    print(f"Checking {r['folder']}")
    for c in r.get("chats", []):
        print(f"  chat {c['file']}: {c['messages']} messages, {c['from']} to {c['to']}, {c['senders']} senders"
              + (f", {c['forwarded']} forwarded" if c["forwarded"] else ""))
    if r.get("attachments"):
        print("  attachments: " + ", ".join(f"{n} {k}" for k, n in sorted(r["attachments"].items()))
              + (f" ({r['sidecars']} with saved readings)" if r.get("sidecars") else ""))
    if r.get("answers"):
        print(f"  answer files: {r['answers']} expected findings")
    for n in r.get("notes", []):
        print(f"  note: {n}")
    for p in r.get("problems", []):
        print(f"  PROBLEM: {p}")
    print("Ready to run." if r.get("ok") else "Fix the problems above first (or expect missing results).")
