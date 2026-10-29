"""
Replace placeholder media in data/ with real files rendered from their sidecars.

The supplied practice / exam folders had 16-byte placeholder "photos", "PDFs" and
"voice notes" next to hand-written JSON. This renders a real bill photo, UPI
screenshot, statement, rate-list PDF and (with espeak-ng + ffmpeg) a spoken voice
note for each, so the real readers can be run with KHATA_IGNORE_SIDECARS=1.
Files that are already real media are left alone.

    python scripts/render_data_media.py data/practice/week_01 data/exam
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.fixtures import media  # noqa: E402

SIDEWAYS = {"BW-3019.jpg"}   # "one bill is a sideways photo"
NAMES = {"GT": "Ganga Traders", "SP": "Sharma Products", "BC": "Bengal Confectionery", "KP": "Kashi Provisions",
         "BW": "Banaras Wholesale"}


def is_placeholder(p: Path) -> bool:
    return p.exists() and p.stat().st_size < 200


def render(folder: Path) -> None:
    for sc in sorted(folder.glob("*.json")):
        target = folder / sc.name[:-5]
        if not target.suffix or not is_placeholder(target):
            continue
        d = json.loads(sc.read_text(encoding="utf-8"))
        name = target.name
        if target.suffix == ".pdf":
            media.rate_list_pdf(target, d.get("distributor_name", ""),
                                [{"product": e["product"], "hsn": e.get("hsn", ""), "unit": e.get("unit", ""),
                                  "rate": e["rate"], "gst": e.get("gst_rate", "")} for e in d["entries"]],
                                date.fromisoformat(d["effective_date"]), version=d.get("version", ""))
        elif "upi" in name.lower() or "payment_date" in d:
            media.upi_image(target, amount=d["amount"], paid_on=date.fromisoformat(d["payment_date"]),
                            payee=d.get("payee_name", ""), ref=d.get("upi_ref", ""))
        elif "statement_date" in d:
            code = d["entries"][0]["bill_id"].split("/")[0] if d.get("entries") else ""
            media.statement_image(target, distributor=NAMES.get(code, ""), as_on=date.fromisoformat(d["statement_date"]),
                                  rows=[{"bill_id": e["bill_id"], "date": date.fromisoformat(e["bill_date"]),
                                         "amount": e["amount"], "status": e.get("status", "due").title()} for e in d["entries"]])
        elif "bill_id" in d:
            gst = None
            if any(i.get("gst_rate") for i in d["items"]):
                g = d["items"][0]["gst_rate"]
                gst = g * 100 if g < 1 else g
            media.bill_image(target, distributor=NAMES.get(d.get("distributor_code", ""), d.get("distributor_code", "")),
                             bill_id=d["bill_id"], bill_date=date.fromisoformat(d["bill_date"]),
                             delivery_date=date.fromisoformat(d["delivery_date"]) if d.get("delivery_date") else None,
                             lines=[{"product": i["product"], "hsn": i.get("hsn", ""), "qty": i["quantity"], "rate": i["rate"]}
                                    for i in d["items"]],
                             gst=gst, rotate=90 if name in SIDEWAYS else 0, tilt=2.5, blur=0.5, seed=hash(name) % 1000)
        print(f"rendered {target.relative_to(ROOT)}")
    for tr in sorted(folder.glob("*.opus.txt")):
        target = folder / tr.name[:-4]
        if not is_placeholder(target) or not (shutil.which("espeak-ng") and shutil.which("ffmpeg")):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "v.wav"
            subprocess.run(["espeak-ng", "-v", "hi", "-s", "140", "-w", str(wav), tr.read_text(encoding="utf-8")], check=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libopus", "-b:a", "24k",
                            str(target)], check=True)
        print(f"rendered {target.relative_to(ROOT)}")


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["data/practice/week_01", "data/exam"]:
        render(ROOT / arg)
