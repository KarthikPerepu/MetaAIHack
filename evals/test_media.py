"""Real media, no sidecars: rendered bill photos (one sideways, tilted and blurred), a real rate-list
PDF, a UPI screenshot and a statement image, read end to end by the offline readers
(pdfplumber + tesseract). With a LLAMA_API_KEY the same files go to Llama vision first.
"""

import os
import shutil
from datetime import date

import pytest

from evals.fixtures import media
from evals.fixtures.scenario import Week, ingest, pick, report

needs_ocr = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
SHOP = "Laxmi Stores"
BW = "Rajesh Banaras Wholesale"
KP = "Vijay Kashi Provisions"


@needs_ocr
def test_real_photos_and_pdf_end_to_end(tmp_path, state, monkeypatch):
    monkeypatch.setenv("KHATA_IGNORE_SIDECARS", "1")
    w = Week(tmp_path / "exam_like", shop=SHOP)
    p = w.path
    w.msg("2026-10-10 10:00", KP, "Namaste Laxmi Stores ji, naya rate list")
    media.rate_list_pdf(p / "DOC-20261010-WA0001.pdf", "Kashi Provisions",
                        [{"product": "Fortune Atta 10kg", "hsn": "1101", "unit": "bag", "rate": 390, "gst": 5},
                         {"product": "Tata Salt 1kg", "hsn": "2501", "unit": "packet", "rate": 25, "gst": 0}],
                        date(2026, 10, 10), version="v1")
    w.attach("2026-10-10 10:00", KP, "DOC-20261010-WA0001.pdf")
    media.bill_image(p / "IMG-20261014-WA0007.jpg", distributor="Kashi Provisions", bill_id="KP/8801",
                     bill_date=date(2026, 10, 14), lines=[{"product": "Fortune Atta 10kg", "hsn": "1101", "qty": 50, "rate": 410}])
    w.attach("2026-10-14 15:45", KP, "IMG-20261014-WA0007.jpg", caption="50 bag Atta deliver ho gaya. Bill KP/8801")
    media.bill_image(p / "IMG-20261016-WA0011.jpg", distributor="Banaras Wholesale", bill_id="BW/3025",
                     bill_date=date(2026, 10, 16), lines=[{"product": "Amul Condensed Milk", "hsn": "0402", "qty": 50, "rate": 200}],
                     gst=18, rotate=90, tilt=2, blur=0.6)
    w.attach("2026-10-16 11:00", BW, "IMG-20261016-WA0011.jpg", caption="Dairy products delivery bill BW/3025")
    media.upi_image(p / "Screenshot_20261015.jpg", amount=8000, paid_on=date(2026, 10, 11), payee="Banaras Wholesale",
                    ref="428901239841")
    w.attach("2026-10-15 12:00", SHOP, "Screenshot_20261015.jpg", caption="Rajesh ji bill BW/2900 ka payment UPI kar diya")
    media.statement_image(p / "IMG-20261018-WA0002.jpg", distributor="Banaras Wholesale", as_on=date(2026, 10, 18),
                          rows=[{"bill_id": "BW/2900", "date": date(2026, 10, 5), "amount": 8000}])
    w.attach("2026-10-18 16:00", BW, "IMG-20261018-WA0002.jpg", caption="Laxmi Stores statement of account")
    w.write()

    stats = ingest(p, state)
    assert stats["bills"] == 2 and stats["rate_lists"] == 1 and stats["payments"] == 1 and stats["statements"] == 1
    rep = report(state, "2026-10-20")
    got = sorted((f["bill_id"], f["class"], f["amount_inr"]) for f in rep["findings"] if f["status"] == "confirmed")
    assert ("KP/8801", "rate_mismatch", 1000.0) in got
    assert ("BW/3025", "gst_error", 1300.0) in got          # read from the sideways photo
    assert ("BW/2900", "paid_but_shown_due", 8000.0) in got
    bill = next(f for f in rep["findings"] if f["bill_id"] == "KP/8801" and f["class"] == "rate_mismatch")
    assert any("DOC-20261010-WA0001.pdf: Fortune Atta 10kg | 390" == e for e in bill["evidence"])
    # OCR-read evidence carries lower confidence than a clean sidecar read would
    assert all(f["confidence"] < 0.98 for f in rep["findings"])


def test_pdf_without_wef_date_uses_arrival_date(tmp_path, state):
    w = Week(tmp_path / "w", shop=SHOP)
    media.rate_list_pdf(w.path / "list.pdf", "Kashi Provisions",
                        [{"product": "Fortune Atta 10kg", "hsn": "1101", "unit": "bag", "rate": 390, "gst": 5}], None)
    w.attach("2026-10-10 10:00", KP, "list.pdf")
    w.bill("2026-10-12 10:00", KP, "KP-1.jpg", "KP/1", "2026-10-12", [("Fortune Atta 10kg", 10, 400, "1101")])
    w.write()
    ingest(w.path, state)
    f = pick(report(state, "2026-10-13"), "rate_mismatch", "confirmed")
    assert f and f[0]["amount_inr"] == 100.0 and "arrived" in f[0]["reasoning"]


def test_unreadable_files_are_reported_not_fatal(tmp_path, state, monkeypatch):
    monkeypatch.setenv("KHATA_IGNORE_SIDECARS", "1")
    w = Week(tmp_path / "w", shop=SHOP)
    (w.path / "broken.jpg").write_bytes(b"not an image")
    (w.path / "voice.opus").write_bytes(b"OggS not audio")
    w.attach("2026-10-10 10:00", KP, "broken.jpg")
    w.attach("2026-10-10 10:01", KP, "voice.opus")
    w.attach("2026-10-10 10:02", KP, "missing_file.jpg")
    w.write()
    stats = ingest(w.path, state)
    assert stats.get("unreadable", 0) >= 2 and stats.get("attachments_missing") == 1
    rep = report(state, "2026-10-11")
    assert any("unreadable" in a["reason"] for a in rep["safety_alerts"])
