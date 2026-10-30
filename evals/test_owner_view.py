"""The owner-facing outputs: the Hinglish summary, the web page and the one-shot `check` command."""

import json
import subprocess
import sys
from pathlib import Path

from harness.html_report import render
from harness.summary import owner_summary, template_summary

ROOT = Path(__file__).resolve().parent.parent


def _report(tmp_path):
    state = tmp_path / "state"
    r = subprocess.run([sys.executable, str(ROOT / "harness" / "harness.py"), "check", str(ROOT / "data/practice/week_01"),
                        "--state-dir", str(state), "--out", str(tmp_path / "out"), "--as-of", "2026-10-15"],
                       capture_output=True, encoding="utf-8", errors="replace", cwd=ROOT,
                       env={**__import__("os").environ, "KHATA_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8")), r.stdout, tmp_path / "out"


def test_check_command_writes_json_and_page(tmp_path):
    rep, stdout, out = _report(tmp_path)
    assert "23,490 rupees to claim back" in stdout and (out / "report.html").exists()
    page = (out / "report.html").read_text(encoding="utf-8")
    for bit in ("₹23,490", "GT/1187", "Ganga Traders", "Blocked messages", "Copy"):
        assert bit in page
    assert "<script" in page and "http" not in page.split("<script>")[1]   # works offline
    assert rep["distributors"]["GT"] == "Ganga Traders"


def test_summary_numbers_come_from_the_report(tmp_path):
    rep, _, _ = _report(tmp_path)
    text = template_summary(rep)
    assert "₹23,490" in text and "Sharma Products: ₹21,400" in text and "Ganga Traders: ₹1,090" in text
    assert rep["owner_summary"]["written_by"] == "khata template"


class FakeMuse:
    available = True

    class config:
        text_model = "muse-spark-1.3"

    def __init__(self, message):
        self.message = message

    def chat_json(self, prompt, **kw):
        return {"message": self.message}


def test_muse_summary_kept_only_when_numbers_check_out(tmp_path):
    rep, _, _ = _report(tmp_path)
    good = owner_summary(rep, FakeMuse("Namaste! Is hafte ₹23,490 wapas mil sakte hain. Sharma Products ka ₹21,400 "
                                       "aur Ganga Traders ka ₹1,090 (bill GT/1187). Kisi ko UPI PIN mat bhejiye."))
    assert good["written_by"].startswith("Muse Spark")
    made_up = owner_summary(rep, FakeMuse("Namaste! Is hafte ₹50,000 wapas mil sakte hain."))
    assert made_up["written_by"] == "khata template"
    unknown_bill = owner_summary(rep, FakeMuse("Bill XY/999 mein ₹23,490 ki galti hai."))
    assert unknown_bill["written_by"] == "khata template"
    asks_pin = owner_summary(rep, FakeMuse("₹23,490 wapas chahiye to apna PIN batao."))
    assert asks_pin["written_by"] == "khata template"


def test_page_escapes_chat_text():
    rep = {"as_of": "2026-10-15", "mode": "supervised", "shop": "<b>Shop</b>", "summary": {
        "to_claim_back_inr": 0, "confirmed": 0, "conflicts": 0, "cannot_determine": 0, "interest_at_risk_inr": 0,
        "by_distributor": {}}, "findings": [],
        "safety_alerts": [{"source": "_chat.txt:L1", "reason": "x", "text": "<script>alert(1)</script>"}]}
    page = render(rep)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)" in page and "&lt;b&gt;Shop" in page
