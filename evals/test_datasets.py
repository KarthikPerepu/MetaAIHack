"""Replay the practice set, the exam set and our own two-week eval shop through the CLI (fresh
process per command, like the judges) and grade them with grader/grader.py.

The eval shop is also replayed with sidecars ignored, so every photo / PDF is read by the real
offline readers (pdfplumber + tesseract). Only the voice-note scheme needs a speech model.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from grader.grader import grade, load_answers  # noqa: E402

SHOP = ROOT / "evals" / "scenarios" / "annapurna"


def cli(*args, env=None):
    e = {**os.environ, "KHATA_OFFLINE": "1", "KHATA_LOG": "ERROR", **(env or {})}
    r = subprocess.run([sys.executable, str(ROOT / "harness" / "harness.py"), *map(str, args)], capture_output=True,
                       text=True, cwd=ROOT, env=e)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout) if r.stdout.strip() else {}


def test_practice_set(tmp_path):
    cli("ingest", ROOT / "data/practice/week_01", "--state-dir", tmp_path)
    cli("feedback", ROOT / "data/practice/feedback/feedback.json", "--state-dir", tmp_path)
    rep = cli("report", "--as-of", "2026-10-15", "--state-dir", tmp_path)
    res = grade(rep, load_answers(ROOT / "data/practice/answers"), [ROOT / "data/practice/week_01"])
    assert res["score"] == 100.0 and not res["capping_mistakes"], res
    gt = next(f for f in rep["findings"] if f["bill_id"] == "GT/1187" and f["class"] == "scheme_not_given")
    assert gt["confidence"] >= 0.97                        # owner confirmed it in feedback
    assert any(c["code"] == "SP" for c in [json.loads((tmp_path / "distributors" / "SP.json").read_text(encoding="utf-8"))])


def test_exam_sheet(tmp_path):
    cli("ingest", ROOT / "data/exam", "--state-dir", tmp_path)
    sheet = cli("sheet", "--as-of", "2026-10-20", "--state-dir", tmp_path)
    got = {c: (v["count"], v["amount_inr"], v["bill_ids"]) for c, v in sheet["classes"].items() if v["count"]}
    assert got == {"rate_mismatch": (1, 1000.0, ["KP/8801"]), "duplicate_bill": (1, 12000.0, ["BW/3019"]),
                   "scheme_not_given": (1, 780.0, ["KP/8801"]), "paid_but_shown_due": (1, 8000.0, ["BW/2900"]),
                   "gst_error": (1, 1300.0, ["BW/3025"])}
    assert {c["bill_id"] for c in sheet["cannot_determine"]} == {"BW/3011", "BW/3019", "BW/3025"}


@pytest.mark.parametrize("real_media", [False, True])
def test_own_eval_shop_two_weeks(tmp_path, real_media):
    if real_media and not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")
    env = {"KHATA_IGNORE_SIDECARS": "1"} if real_media else {}
    cli("ingest", SHOP / "week_01", "--state-dir", tmp_path, env=env)
    week1 = cli("report", "--as-of", "2026-12-07", "--state-dir", tmp_path, env=env)
    assert not [f for f in week1["findings"] if f["class"] == "rate_mismatch" and f["status"] == "confirmed"]
    assert len(week1["safety_alerts"]) == 2
    cli("feedback", SHOP / "feedback_after_week_01.json", "--state-dir", tmp_path, env=env)
    cli("ingest", SHOP / "week_02", "--state-dir", tmp_path, env=env)
    rep = cli("report", "--as-of", "2026-12-14", "--state-dir", tmp_path, env=env)
    res = grade(rep, load_answers(SHOP / "answers"), [SHOP / "week_01", SHOP / "week_02"])
    assert not res["capping_mistakes"]
    if real_media:
        # no speech model offline: the voice-note scheme is the only thing allowed to be missed
        assert set(res["missed"]) <= {"RK/7008 scheme_not_given"} and not res["false_positives"], res
    else:
        assert res["score"] == 100.0, res
    auto = cli("report", "--as-of", "2026-12-14", "--mode", "autonomous", "--state-dir", tmp_path, env=env)
    sends = [f for f in auto["findings"] if f["action"].get("autonomous") == "send_whatsapp_reply"]
    assert sends and all(f["amount_inr"] < 2000 and f["status"] == "confirmed" for f in sends)
