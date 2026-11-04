"""The rules the brief leaves open are settings. These check each alternative really changes the answer,
and that the report says which reading it used."""

from evals.fixtures.scenario import Week, ingest, pick, report
from harness.config import Policy

SHOP = "Gupta Kirana Bhandar"
AT = "Suresh Ashoka Traders"


def _rates(w):
    return w.rate_list("2026-11-01 09:00", AT, "rates.pdf", "Ashoka Traders",
                       [{"product": "Tata Tea Premium 250g", "rate": 100, "hsn": "0902", "unit": "box"}],
                       effective="2026-11-01")


def _credit_week(tmp_path):
    w = Week(tmp_path / "w", shop=SHOP)
    w.bill("2026-10-10 12:00", AT, "AT-1.jpg", "AT/1", "2026-10-10", [("Surf Excel 1kg", 100, 120, "3402")])
    w.write()
    return w


def test_interest_days_setting(tmp_path, monkeypatch):
    w = _credit_week(tmp_path)
    ingest(w.path, tmp_path / "s")
    # due 9 Nov (30 days); report on 6 Nov, window ends 13 Nov
    default = pick(report(tmp_path / "s", "2026-11-06"), "credit_period_risk")[0]
    assert abs(default["amount_inr"] - 12000 * 0.18 / 365 * 4) < 0.01
    monkeypatch.setenv("KHATA_INTEREST_DAYS", "one_day")
    assert abs(pick(report(tmp_path / "s", "2026-11-06"), "credit_period_risk")[0]["amount_inr"] - 12000 * 0.18 / 365) < 0.01
    monkeypatch.setenv("KHATA_INTEREST_DAYS", "full_window")
    rep = report(tmp_path / "s", "2026-11-06")
    assert abs(pick(rep, "credit_period_risk")[0]["amount_inr"] - 12000 * 0.18 / 365 * 7) < 0.01
    assert rep["policy"]["rules"]["interest_days"] == "full_window"


def _scheme_week(tmp_path, offer, qty):
    w = Week(tmp_path / "w", shop=SHOP)
    _rates(w)
    w.msg("2026-11-02 11:00", AT, offer)
    w.bill("2026-11-04 12:00", AT, "AT-2.jpg", "AT/2", "2026-11-04", [("Tata Tea Premium 250g", qty, 100, "0902")])
    w.write()
    return w


def test_scheme_scaling_setting(tmp_path, monkeypatch):
    w = _scheme_week(tmp_path, "Tata Tea Premium 10 box pe 1 box free", 20)
    ingest(w.path, tmp_path / "s")
    assert pick(report(tmp_path / "s", "2026-11-05"), "scheme_not_given")[0]["amount_inr"] == 200.0   # scales: 2 free
    monkeypatch.setenv("KHATA_SCHEME_SCALING", "never")
    assert pick(report(tmp_path / "s", "2026-11-05"), "scheme_not_given")[0]["amount_inr"] == 100.0   # once: 1 free


def test_scheme_scaling_always(tmp_path, monkeypatch):
    w = _scheme_week(tmp_path, "Tata Tea Premium 20 box lo, 2 box free milega", 40)
    ingest(w.path, tmp_path / "s")
    assert pick(report(tmp_path / "s", "2026-11-05"), "scheme_not_given")[0]["amount_inr"] == 200.0   # "lo": 2 once
    monkeypatch.setenv("KHATA_SCHEME_SCALING", "always")
    assert pick(report(tmp_path / "s", "2026-11-05"), "scheme_not_given")[0]["amount_inr"] == 400.0   # 40 bought: 4 free


def test_voice_only_autosend_setting(tmp_path, monkeypatch):
    w = Week(tmp_path / "w", shop=SHOP)
    _rates(w)
    w.voice("2026-11-02 11:00", AT, "PTT-1.opus", "Tata Tea Premium 10 box pe 1 box free")
    w.bill("2026-11-04 12:00", AT, "AT-3.jpg", "AT/3", "2026-11-04", [("Tata Tea Premium 250g", 10, 100, "0902")])
    w.write()
    ingest(w.path, tmp_path / "s")
    f = pick(report(tmp_path / "s", "2026-11-05", mode="autonomous"), "scheme_not_given")[0]
    assert f["action"]["autonomous"] == "draft_whatsapp_reply"            # default: a voice note alone waits
    monkeypatch.setenv("KHATA_VOICE_ONLY_AUTOSEND", "yes")
    f = pick(report(tmp_path / "s", "2026-11-05", mode="autonomous"), "scheme_not_given")[0]
    assert f["action"]["autonomous"] == "send_whatsapp_reply"


def test_duplicate_window_setting(tmp_path, monkeypatch):
    w = Week(tmp_path / "w", shop=SHOP)
    items = [("Tata Tea Premium 250g", 12, 100, "0902")]
    w.bill("2026-11-03 12:00", AT, "AT-4.jpg", "AT/4", "2026-11-03", items, delivery="2026-11-03")
    w.bill("2026-11-05 12:00", AT, "AT-5.jpg", "AT/5", "2026-11-05", items, delivery="2026-11-05")
    w.write()
    ingest(w.path, tmp_path / "s")
    assert pick(report(tmp_path / "s", "2026-11-06"), "duplicate_bill")               # 2 days apart: duplicate
    monkeypatch.setenv("KHATA_DUPLICATE_WINDOW_DAYS", "1")
    assert not pick(report(tmp_path / "s", "2026-11-06"), "duplicate_bill")           # stricter window: re-order


def test_bad_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("KHATA_INTEREST_DAYS", "forever")
    monkeypatch.setenv("KHATA_AUTOSEND_LIMIT", "lots")
    monkeypatch.setenv("KHATA_INTEREST_RATE", "18")
    p = Policy.from_env()
    assert p.interest_days == "to_window_end" and p.autonomous_amount_limit == 2000.0 and p.default_interest_rate == 0.18
