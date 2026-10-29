"""
Realistic test media: rate-list PDFs, bill photos (tilted / sideways / blurred,
optionally Devanagari), UPI screenshots and statements.

Used by the eval suite to exercise the real readers (pdfplumber, tesseract,
Llama vision) instead of only sidecar JSON, and by `make media` to give the
practice / exam folders viewable files.
"""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path
from typing import Optional

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/noto", "/Library/Fonts",
             "C:/Windows/Fonts"]


def _font(size: int, bold: bool = False, devanagari: bool = False):
    from PIL import ImageFont
    names = (["NotoSansDevanagari-Bold.ttf" if bold else "NotoSansDevanagari-Regular.ttf"] if devanagari else []) + \
            (["DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf", "arialbd.ttf"] if bold else
             ["DejaVuSans.ttf", "NotoSans-Regular.ttf", "arial.ttf"])
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def money(x: float) -> str:
    return f"{x:,.2f}"


def rate_list_pdf(path: Path, distributor: str, entries: list[dict], effective: Optional[date],
                  version: str = "", note: str = "", doc_date: Optional[date] = None) -> Path:
    """entries: {product, hsn, unit, rate, gst}"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    story = [Paragraph(f"<b>{distributor}</b>", styles["Title"]),
             Paragraph(f"Dealer Rate List {version}".strip(), styles["Heading2"])]
    if doc_date:
        story.append(Paragraph(f"Date: {doc_date.strftime('%d/%m/%Y')}", styles["Normal"]))
    if effective:
        story.append(Paragraph(f"Rates w.e.f. {effective.strftime('%d/%m/%Y')}", styles["Normal"]))
    if note:
        story.append(Paragraph(note, styles["Normal"]))
    story.append(Spacer(1, 12))
    rows = [["S.No", "Product", "HSN", "Unit", "Rate (Rs)", "GST %"]]
    for i, e in enumerate(entries, 1):
        rows.append([str(i), e["product"], e.get("hsn", ""), e.get("unit", ""), money(e["rate"]),
                     str(e.get("gst", ""))])
    t = Table(rows)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    story += [t, Spacer(1, 24), Paragraph("Authorised Signatory", styles["Normal"])]
    doc.build(story)
    return path


def bill_image(path: Path, *, distributor: str, bill_id: str, bill_date: date, lines: list[dict],
               delivery_date: Optional[date] = None, gst: Optional[float] = None, rotate: int = 0,
               blur: float = 0.0, tilt: float = 0.0, devanagari: bool = False, footer: str = "",
               seed: int = 1) -> Path:
    """lines: {product, hsn, qty, unit, rate, free (bool)}; gst = percent applied to every line."""
    from PIL import Image, ImageDraw, ImageFilter
    rnd = random.Random(seed)
    W, H = 1240, 1500
    img = Image.new("RGB", (W, H), (250, 248, 240))
    d = ImageDraw.Draw(img)
    big, mid, small = _font(46, True), _font(28), _font(26)
    dev = _font(28, devanagari=True)
    y = 50
    d.text((60, y), distributor.upper(), font=big, fill=(20, 20, 60))
    y += 70
    d.text((60, y), "TAX INVOICE" if gst else "BILL / CASH MEMO", font=mid, fill=(0, 0, 0))
    if devanagari:
        d.text((700, y), "बिल", font=dev, fill=(0, 0, 0))
    y += 50
    d.text((60, y), f"Bill No: {bill_id}", font=mid, fill=(0, 0, 0))
    d.text((700, y), f"Date: {bill_date.strftime('%d/%m/%Y')}", font=mid, fill=(0, 0, 0))
    y += 45
    if delivery_date:
        d.text((60, y), f"Delivery Date: {delivery_date.strftime('%d/%m/%Y')}", font=mid, fill=(0, 0, 0))
        y += 45
    y += 15
    cols = [60, 120, 560, 690, 820, 990] if not gst else [60, 110, 470, 590, 700, 840, 990]
    head = ["No", "Item", "HSN", "Qty", "Rate", "Amount"] if not gst else ["No", "Item", "HSN", "Qty", "Rate", "Amount", "GST"]
    for x, h in zip(cols, head):
        d.text((x, y), h, font=mid, fill=(0, 0, 0))
    y += 45
    d.line((50, y, W - 50, y), fill=(0, 0, 0), width=2)
    y += 15
    sub = tax = 0.0
    for i, l in enumerate(lines, 1):
        amt = 0.0 if l.get("free") else l["qty"] * l["rate"]
        sub += amt
        name = l["product"] + (" (FREE)" if l.get("free") else "")
        vals = [str(i), name, l.get("hsn", ""), f"{l['qty']:g}", money(0 if l.get("free") else l["rate"]), money(amt)]
        if gst:
            t = round(amt * gst / 100.0, 2)
            tax += t
            vals.append(f"{gst:g}% {money(t)}")
        for x, v in zip(cols, vals):
            d.text((x, y), v, font=small, fill=(10, 10, 10))
        y += 42
    y += 10
    d.line((50, y, W - 50, y), fill=(0, 0, 0), width=2)
    y += 20
    d.text((640, y), f"Sub Total: {money(sub)}", font=mid, fill=(0, 0, 0))
    y += 45
    if gst:
        d.text((640, y), f"Total GST: {money(tax)}", font=mid, fill=(0, 0, 0))
        y += 45
    d.text((640, y), f"Grand Total: {money(sub + tax)}", font=_font(32, True), fill=(0, 0, 0))
    y += 70
    if footer:
        d.text((60, y), footer, font=small, fill=(60, 0, 0))
    # phone-photo effects
    for _ in range(1500):
        x, yy = rnd.randrange(W), rnd.randrange(H)
        img.putpixel((x, yy), (200, 200, 200))
    if tilt:
        img = img.rotate(tilt, expand=True, fillcolor=(90, 80, 70))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if rotate:
        img = img.rotate(-rotate, expand=True)
    img.save(path, quality=88)
    return path


def upi_image(path: Path, *, amount: float, paid_on: date, payee: str, ref: str, note: str = "",
              status: str = "Payment Successful") -> Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (720, 1280), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 720, 260), fill=(16, 120, 70))
    d.text((60, 90), status, font=_font(44, True), fill=(255, 255, 255))
    d.text((60, 320), f"Rs {money(amount)}", font=_font(64, True), fill=(0, 0, 0))
    d.text((60, 440), f"Paid to {payee}", font=_font(32), fill=(0, 0, 0))
    d.text((60, 520), f"Date: {paid_on.strftime('%d %b %Y')}", font=_font(30), fill=(0, 0, 0))
    d.text((60, 590), f"UPI Ref No: {ref}", font=_font(30), fill=(0, 0, 0))
    if note:
        d.text((60, 660), f"Note: {note}", font=_font(30), fill=(0, 0, 0))
    img.save(path, quality=90)
    return path


def statement_image(path: Path, *, distributor: str, as_on: date, rows: list[dict]) -> Path:
    """rows: {bill_id, date, amount, status}"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1240, 900), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((60, 50), distributor.upper(), font=_font(44, True), fill=(0, 0, 0))
    d.text((60, 120), f"Statement of Account as on {as_on.strftime('%d/%m/%Y')}", font=_font(30), fill=(0, 0, 0))
    y = 200
    for x, h in zip((60, 400, 700, 950), ("Bill No", "Bill Date", "Amount", "Status")):
        d.text((x, y), h, font=_font(30, True), fill=(0, 0, 0))
    y += 60
    total = 0.0
    for r in rows:
        for x, v in zip((60, 400, 700, 950), (r["bill_id"], r["date"].strftime("%d/%m/%Y"), money(r["amount"]),
                                               r.get("status", "Due"))):
            d.text((x, y), v, font=_font(28), fill=(0, 0, 0))
        total += r["amount"] if r.get("status", "Due").lower() != "paid" else 0
        y += 50
    d.text((600, y + 40), f"Total Outstanding: {money(total)}", font=_font(30, True), fill=(0, 0, 0))
    img.save(path, quality=90)
    return path
