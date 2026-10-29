"""
Tiny builder for synthetic WhatsApp weeks used by the eval suite.

    w = Week(tmp / "week_01", shop="Gupta Kirana")
    w.msg("2026-11-02 10:00", "Suresh Ashoka Traders", "Naya rate list")
    w.rate_list("2026-11-02 10:01", "Suresh Ashoka Traders", "rates.pdf", "Ashoka Traders",
                [{"product": "Tea 250g", "rate": 100, "hsn": "0902", "unit": "box"}], effective="2026-11-01")
    w.bill("2026-11-05 11:00", "Suresh Ashoka Traders", "AT-101.jpg", "AT/101", "2026-11-05",
           [("Tea 250g", 10, 110)])
    w.write()

Bills and screenshots are written as an image placeholder plus a sidecar JSON
(the cached-extraction format), or rendered for real with ``render=True``.
Rate lists are real PDFs.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from evals.fixtures import media


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _d(s) -> Optional[date]:
    return date.fromisoformat(s) if isinstance(s, str) else s


class Week:
    def __init__(self, path: Path, shop: str = "Shanti General Store"):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.shop = shop
        self.lines: list[tuple[datetime, str, str]] = []

    # ── chat ────────────────────────────────────────────────
    def msg(self, when: str, sender: str, text: str) -> "Week":
        self.lines.append((_dt(when), sender, text))
        return self

    def owner(self, when: str, text: str) -> "Week":
        return self.msg(when, self.shop, text)

    def attach(self, when: str, sender: str, filename: str, caption: str = "") -> "Week":
        if caption:
            self.msg(when, sender, caption)
        return self.msg(when, sender, f"<attached: {filename}>")

    # ── documents ───────────────────────────────────────────
    def rate_list(self, when: str, sender: str, filename: str, distributor: str, entries: list[dict],
                  effective: Optional[str], version: str = "", note: str = "", caption: str = "") -> "Week":
        media.rate_list_pdf(self.path / filename, distributor,
                            [{"product": e["product"], "hsn": e.get("hsn", ""), "unit": e.get("unit", ""),
                              "rate": e["rate"], "gst": e.get("gst", "")} for e in entries],
                            _d(effective) if effective else None, version=version, note=note)
        return self.attach(when, sender, filename, caption)

    def bill(self, when: str, sender: str, filename: str, bill_id: str, bill_date: str,
             lines: list[tuple], *, delivery: Optional[str] = None, gst: Optional[float] = None,
             caption: str = "", render: bool = False, rotate: int = 0, distributor: str = "",
             tax_total: Optional[float] = None, discount: Optional[list[float]] = None,
             gst_amounts: Optional[list[float]] = None, extra: Optional[dict] = None,
             sidecar: bool = False) -> "Week":
        """lines: (product, qty, rate[, hsn[, free]])"""
        items, sub, tax = [], 0.0, 0.0
        for i, l in enumerate(lines):
            product, qty, rate = l[0], l[1], l[2]
            hsn = l[3] if len(l) > 3 else None
            free = bool(l[4]) if len(l) > 4 else False
            amt = 0.0 if free else qty * rate
            it = {"row": i + 1, "product": product, "quantity": qty, "rate": 0 if free else rate, "amount": amt,
                  "hsn": hsn, "is_free": free}
            if discount and i < len(discount):
                it["discount"] = discount[i]
            if gst is not None and not free:
                base = amt - (it.get("discount") or 0)
                ga = gst_amounts[i] if gst_amounts and i < len(gst_amounts) else round(base * gst / 100.0, 2)
                it["gst_rate"] = gst
                it["gst_amount"] = ga
                tax += ga
            sub += amt
            items.append(it)
        if tax_total is not None:
            tax = tax_total
        disc = sum(discount or [])
        data = {"bill_id": bill_id, "distributor_name": distributor, "bill_date": bill_date,
                "delivery_date": delivery, "items": items, "subtotal": sub, "tax_total": tax,
                "grand_total": sub - disc + tax, "ocr_confidence": 0.94}
        data.update(extra or {})
        if render:
            media.bill_image(self.path / filename, distributor=distributor or "Distributor", bill_id=bill_id,
                             bill_date=_d(bill_date),
                             lines=[{"product": l[0], "qty": l[1], "rate": l[2], "hsn": l[3] if len(l) > 3 else "",
                                     "free": bool(l[4]) if len(l) > 4 else False} for l in lines],
                             delivery_date=_d(delivery) if delivery else None, gst=gst, rotate=rotate,
                             tilt=1.5, blur=0.4, seed=sum(map(ord, filename)))
        else:
            (self.path / filename).write_bytes(b"placeholder image " + filename.encode())
        if not render or sidecar:
            (self.path / (filename + ".json")).write_text(json.dumps(data, indent=1), encoding="utf-8")
        return self.attach(when, sender, filename, caption)

    def upi(self, when: str, sender: str, filename: str, amount: float, paid_on: str, payee: str,
            ref: str = "4279182390", status: str = "success", note: str = "", caption: str = "",
            render: bool = False) -> "Week":
        if render:
            media.upi_image(self.path / filename, amount=amount, paid_on=_d(paid_on), payee=payee, ref=ref, note=note)
        else:
            (self.path / filename).write_bytes(b"placeholder upi " + filename.encode())
        (self.path / (filename + ".json")).write_text(json.dumps({
            "amount": amount, "payment_date": paid_on, "upi_ref": ref, "payee_name": payee, "status": status,
            "note": note}), encoding="utf-8")
        return self.attach(when, sender, filename, caption)

    def statement(self, when: str, sender: str, filename: str, as_on: str, rows: list[tuple],
                  caption: str = "", render: bool = False, distributor: str = "") -> "Week":
        """rows: (bill_id, bill_date, amount[, status])"""
        if render:
            media.statement_image(self.path / filename, distributor=distributor, as_on=_d(as_on),
                                  rows=[{"bill_id": r[0], "date": _d(r[1]), "amount": r[2],
                                         "status": (r[3] if len(r) > 3 else "due").title()} for r in rows])
        else:
            (self.path / filename).write_bytes(b"placeholder statement " + filename.encode())
        (self.path / (filename + ".json")).write_text(json.dumps({
            "statement_date": as_on,
            "entries": [{"bill_id": r[0], "bill_date": r[1], "amount": r[2], "status": r[3] if len(r) > 3 else "due"}
                        for r in rows]}), encoding="utf-8")
        return self.attach(when, sender, filename, caption)

    def voice(self, when: str, sender: str, filename: str, transcript: str, render: bool = False) -> "Week":
        import shutil
        import subprocess
        import tempfile
        if render and shutil.which("espeak-ng") and shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "v.wav"
                subprocess.run(["espeak-ng", "-v", "hi", "-s", "140", "-w", str(wav), transcript], check=True)
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libopus", "-b:a", "24k",
                                str(self.path / filename)], check=True)
        else:
            (self.path / filename).write_bytes(b"OggS placeholder")
        (self.path / (filename + ".txt")).write_text(transcript, encoding="utf-8")
        return self.attach(when, sender, filename)

    # ── output ──────────────────────────────────────────────
    def write(self, fmt: str = "ios", name: str = "_chat.txt") -> Path:
        out = []
        for ts, sender, text in sorted(self.lines, key=lambda t: t[0]):
            if fmt == "ios":
                out.append(f"[{ts.strftime('%d/%m/%y, %H:%M:%S')}] {sender}: {text}")
            else:
                hour = ts.strftime("%I").lstrip("0") or "12"
                ampm = ts.strftime("%p").lower()
                body = text
                if text.startswith("<attached: ") and text.endswith(">"):
                    body = text[len("<attached: "):-1] + " (file attached)"
                out.append(f"{ts.strftime('%d/%m/%Y')}, {hour}:{ts.strftime('%M')} {ampm} - {sender}: {body}")
        p = self.path / name
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        return p


def run(*argv: str) -> dict:
    """Run the CLI in-process and return parsed JSON output."""
    import io
    import sys
    from contextlib import redirect_stdout
    from harness.harness import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(list(argv))
    text = buf.getvalue().strip()
    return json.loads(text) if text else {}


def report(state: Path, as_of: str, mode: str = "supervised") -> dict:
    return run("report", "--as-of", as_of, "--mode", mode, "--state-dir", str(state))


def ingest(week: Path, state: Path) -> dict:
    return run("ingest", str(week), "--state-dir", str(state))


def feedback(items: list, state: Path, tmp: Path) -> dict:
    p = tmp / "fb.json"
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return run("feedback", str(p), "--state-dir", str(state))


def pick(rep: dict, cls: Optional[str] = None, status: Optional[str] = None, bill: Optional[str] = None) -> list[dict]:
    return [f for f in rep["findings"] if (cls is None or f["class"] == cls) and
            (status is None or f["status"] == status) and (bill is None or f["bill_id"] == bill)]
