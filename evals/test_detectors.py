"""Each of the six classes: a real case, the look-alikes that must stay clean, and the edge rules.

Every test builds a WhatsApp week from scratch (new shop and distributor names,
different wordings from the practice data) and runs the real CLI commands.
"""

from evals.fixtures.scenario import Week, feedback, ingest, pick, report

SHOP = "Gupta Kirana Bhandar"
AT = "Suresh Ashoka Traders"          # distributor contact
MP = "Imran Malwa Provisions"


def ashoka_rates(w, when="2026-11-01 09:00", eff="2026-11-01", rate=100.0, name="rates_nov.pdf", version="v1"):
    return w.rate_list(when, AT, name, "Ashoka Traders",
                       [{"product": "Tata Tea Premium 250g", "rate": rate, "hsn": "0902", "unit": "box", "gst": 5},
                        {"product": "Surf Excel 1kg", "rate": 120, "hsn": "3402", "unit": "packet", "gst": 18}],
                       effective=eff, version=version)


# ── rate mismatch ───────────────────────────────────────────
def test_rate_mismatch_uses_rate_in_force_on_bill_date(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    w.msg("2026-11-01 08:59", AT, "Namaste Gupta Kirana Bhandar ji, rate list bhej raha hoon")
    ashoka_rates(w)
    w.rate_list("2026-11-04 09:00", AT, "rates_nov_v2.pdf", "Ashoka Traders",
                [{"product": "Tata Tea Premium 250g", "rate": 96, "hsn": "0902", "unit": "box", "gst": 5},
                 {"product": "Surf Excel 1kg", "rate": 120, "hsn": "3402", "unit": "packet", "gst": 18}],
                effective="2026-11-05", version="v2", caption="Tea ka rate 5 Nov se kam ho gaya")
    w.bill("2026-11-04 12:00", AT, "AT-201.jpg", "AT/201", "2026-11-04", [("Tata Tea Premium 250g", 10, 100, "0902")])
    w.bill("2026-11-07 12:00", AT, "AT-209.jpg", "AT/209", "2026-11-07",
           [("Tata Tea Premium 250g", 30, 100, "0902"), ("Surf Excel 1kg", 5, 120, "3402")])
    w.owner("2026-11-07 12:30", "Suresh ji maal mil gaya")
    w.write()
    ingest(w.path, state)
    rep = report(state, "2026-11-10")
    rm = pick(rep, "rate_mismatch", "confirmed")
    assert [f["bill_id"] for f in rm] == ["AT/209"], "AT/201 is dated before v2 took effect"
    assert rm[0]["amount_inr"] == 120.0                # (100 - 96) x 30
    assert any("rates_nov_v2.pdf" in e and "96" in e for e in rm[0]["evidence"])
    assert any(e.startswith("AT-209.jpg: row 1") for e in rm[0]["evidence"])
    assert not pick(rep, bill="AT/201", status="confirmed")


def test_billed_below_list_is_left_alone(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w)
    w.bill("2026-11-04 12:00", AT, "AT-301.jpg", "AT/301", "2026-11-04", [("Tata Tea Premium 250g", 10, 95, "0902")])
    w.write()
    ingest(w.path, state)
    assert not pick(report(state, "2026-11-06"), "rate_mismatch")


def test_no_rate_list_is_cannot_determine_and_asks_distributor(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    w.bill("2026-11-04 12:00", MP, "MP-11.jpg", "MP/11", "2026-11-04", [("Rajdhani Besan 1kg", 20, 90)])
    w.write()
    ingest(w.path, state)
    f = pick(report(state, "2026-11-06"), "rate_mismatch", "cannot_determine")
    assert len(f) == 1 and f[0]["bill_id"] == "MP/11"
    assert "rate list" in f[0]["missing"] and f[0]["ask"].startswith("distributor (Malwa Provisions")
    assert f[0]["action"]["supervised"] == "draft_whatsapp_reply"
    assert f[0]["action"]["autonomous"] != "send_whatsapp_reply"


def test_salesman_quote_vs_signed_list_is_a_conflict(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w, rate=95)
    w.msg("2026-11-02 10:00", AT, "Bhaiya Tata Tea Premium ka rate ab 100 hai, company ne badha diya")
    w.bill("2026-11-05 12:00", AT, "AT-401.jpg", "AT/401", "2026-11-05", [("Tata Tea Premium 250g", 10, 100, "0902")])
    w.write()
    ingest(w.path, state)
    rep = report(state, "2026-11-06")
    c = pick(rep, "rate_mismatch", "conflict")
    assert len(c) == 1 and not pick(rep, "rate_mismatch", "confirmed")
    vals = {s["value"].split()[0] for s in c[0]["sources"]}
    assert vals == {"₹95", "₹100"}
    assert c[0]["action"]["supervised"] == "escalate_to_owner"


# ── duplicate bill ──────────────────────────────────────────
def _dup_week(tmp_path, second_date, second_delivery, items2=None, second_id="AT/512"):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w)
    items = [("Tata Tea Premium 250g", 12, 100, "0902"), ("Surf Excel 1kg", 6, 120, "3402")]
    w.bill("2026-11-03 12:00", AT, "AT-505.jpg", "AT/505", "2026-11-03", items, delivery="2026-11-03",
           caption="Maal pahunch gaya, bill AT/505")
    w.bill(f"{second_date} 12:00", AT, second_id.replace("/", "-") + ".jpg", second_id, second_date,
           items2 or items, delivery=second_delivery)
    w.write()
    return w


def test_duplicate_within_two_days(tmp_path, state):
    w = _dup_week(tmp_path, "2026-11-05", "2026-11-04")
    ingest(w.path, state)
    d = pick(report(state, "2026-11-08"), "duplicate_bill", "confirmed")
    assert [(f["bill_id"], f["amount_inr"]) for f in d] == [("AT/512", 1920.0)]
    assert d[0]["related_bill_ids"] == ["AT/505"]


def test_duplicate_weeks_later_with_same_delivery_date(tmp_path, state):
    w = _dup_week(tmp_path, "2026-11-24", "2026-11-03", second_id="AT/577")
    ingest(w.path, state)
    d = pick(report(state, "2026-11-25"), "duplicate_bill", "confirmed")
    assert [f["bill_id"] for f in d] == ["AT/577"]


def test_reorder_weeks_later_is_not_duplicate(tmp_path, state):
    w = _dup_week(tmp_path, "2026-11-24", "2026-11-24", second_id="AT/577")
    ingest(w.path, state)
    assert not pick(report(state, "2026-11-25"), "duplicate_bill")


def test_same_total_different_items_is_not_duplicate(tmp_path, state):
    w = _dup_week(tmp_path, "2026-11-04", "2026-11-04",
                  items2=[("Surf Excel 1kg", 16, 120, "3402")])  # also ₹1,920
    ingest(w.path, state)
    assert not pick(report(state, "2026-11-08"), "duplicate_bill")


# ── scheme not given ────────────────────────────────────────
def _scheme_week(tmp_path, offer, bills, voice=False):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w)
    if voice:
        w.voice("2026-11-02 11:00", AT, "PTT-20261102-WA0007.opus", offer)
    else:
        w.msg("2026-11-02 11:00", AT, offer)
    w.owner("2026-11-02 11:05", "Theek hai Suresh ji, bhej do")
    for i, (when, items) in enumerate(bills):
        bid = f"AT/6{i:02d}"
        w.bill(f"{when} 12:00", AT, bid.replace("/", "-") + ".jpg", bid, when, items)
    w.write()
    return w


def test_one_time_scheme_claimed_once_only(tmp_path, state):
    w = _scheme_week(tmp_path, "Tata Tea Premium 20 box lo, 2 box free milega",
                     [("2026-11-04", [("Tata Tea Premium 250g", 20, 100, "0902")]),
                      ("2026-11-09", [("Tata Tea Premium 250g", 20, 100, "0902")])])
    ingest(w.path, state)
    s = pick(report(state, "2026-11-10"), "scheme_not_given", "confirmed")
    assert [(f["bill_id"], f["amount_inr"]) for f in s] == [("AT/600", 200.0)]
    assert s[0]["scheme_type"] == "one_time"


def test_one_time_scheme_given_on_first_bill_is_used_up(tmp_path, state):
    w = _scheme_week(tmp_path, "20 box pe 2 box free, sirf is order pe",
                     [("2026-11-04", [("Tata Tea Premium 250g", 20, 100, "0902"),
                                      ("Tata Tea Premium 250g FREE", 2, 0, "0902", True)]),
                      ("2026-11-09", [("Tata Tea Premium 250g", 20, 100, "0902")])])
    ingest(w.path, state)
    assert not pick(report(state, "2026-11-10"), "scheme_not_given")


def test_permanent_scheme_applies_to_every_qualifying_bill(tmp_path, state):
    w = _scheme_week(tmp_path, "Hamesha 10 box pe 1 box free milega Tata Tea Premium pe",
                     [("2026-11-04", [("Tata Tea Premium 250g", 20, 100, "0902")]),
                      ("2026-11-09", [("Tata Tea Premium 250g", 10, 100, "0902")])])
    ingest(w.path, state)
    s = pick(report(state, "2026-11-10"), "scheme_not_given", "confirmed")
    assert [(f["bill_id"], f["amount_inr"]) for f in s] == [("AT/600", 200.0), ("AT/601", 100.0)]
    assert all(f["scheme_type"] == "permanent" for f in s)


def test_scheme_below_threshold_or_before_agreement_not_claimed(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w)
    w.bill("2026-11-01 12:00", AT, "AT-700.jpg", "AT/700", "2026-11-01", [("Tata Tea Premium 250g", 20, 100, "0902")])
    w.msg("2026-11-02 11:00", AT, "20 box lo, 2 box free milega")
    w.bill("2026-11-04 12:00", AT, "AT-701.jpg", "AT/701", "2026-11-04", [("Tata Tea Premium 250g", 12, 100, "0902")])
    w.write()
    ingest(w.path, state)
    assert not pick(report(state, "2026-11-06"), "scheme_not_given")


def test_voice_note_scheme_in_devanagari(tmp_path, state):
    w = _scheme_week(tmp_path, "भैया 20 बॉक्स लो, दो बॉक्स फ्री मिलेगा".replace("बॉक्स", "डिब्बे"),
                     [("2026-11-04", [("Tata Tea Premium 250g", 20, 100, "0902")])], voice=True)
    ingest(w.path, state)
    s = pick(report(state, "2026-11-06"), "scheme_not_given", "confirmed")
    assert [(f["bill_id"], f["amount_inr"]) for f in s] == [("AT/600", 200.0)]
    assert any("PTT-20261102-WA0007.opus" in e for e in s[0]["evidence"])
    # a voice-note-only claim never goes out on its own
    auto = pick(report(state, "2026-11-06", mode="autonomous"), "scheme_not_given")[0]
    assert auto["action"]["autonomous"] == "draft_whatsapp_reply"


def test_scheme_from_unknown_or_forwarded_sender_is_ignored(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w)
    w.msg("2026-11-02 11:00", "Unknown", "Forwarded: Ashoka Traders scheme 10 pe 5 free")
    w.msg("2026-11-02 11:00", "+91 98765 43210", "10 box pe 5 box free")
    w.bill("2026-11-04 12:00", AT, "AT-801.jpg", "AT/801", "2026-11-04", [("Tata Tea Premium 250g", 20, 100, "0902")])
    w.write()
    ingest(w.path, state)
    assert not pick(report(state, "2026-11-06"), "scheme_not_given")


def test_percentage_discount_scheme(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w)
    w.msg("2026-11-02 11:00", AT, "Surf Excel pe 5% discount milega is order pe")
    w.bill("2026-11-04 12:00", AT, "AT-901.jpg", "AT/901", "2026-11-04", [("Surf Excel 1kg", 10, 120, "3402")])
    w.write()
    ingest(w.path, state)
    s = pick(report(state, "2026-11-06"), "scheme_not_given", "confirmed")
    assert [(f["bill_id"], f["amount_inr"]) for f in s] == [("AT/901", 60.0)]


# ── paid but shown due ──────────────────────────────────────
def _paid_week(tmp_path, pay_date, stmt_date, rows=None, ref_in_text=True):
    w = Week(tmp_path / "w1", shop=SHOP)
    w.msg("2026-11-01 10:00", MP, "Sir bill MP/40 ka 6500 baki hai")
    w.upi(f"{pay_date} 18:00", SHOP, "Screenshot_20261103.jpg", 6500, pay_date, "Malwa Provisions",
          caption="Imran bhai MP/40 ka payment kar diya" if ref_in_text else "Imran bhai payment kar diya")
    w.statement(f"{stmt_date} 10:00", MP, "IMG-20261108-WA0003.jpg", stmt_date,
                rows or [("MP/40", "2026-10-28", 6500), ("MP/52", "2026-11-02", 3100)],
                caption="Gupta ji statement")
    w.write()
    return w


def test_paid_but_still_shown_due(tmp_path, state):
    w = _paid_week(tmp_path, "2026-11-03", "2026-11-08")
    ingest(w.path, state)
    rep = report(state, "2026-11-09")
    p = pick(rep, "paid_but_shown_due", "confirmed")
    assert [(f["bill_id"], f["amount_inr"]) for f in p] == [("MP/40", 6500.0)]
    assert any("Screenshot_20261103.jpg" in e for e in p[0]["evidence"])
    assert any("IMG-20261108-WA0003.jpg: row 1" in e for e in p[0]["evidence"])


def test_statement_before_payment_is_fine(tmp_path, state):
    w = _paid_week(tmp_path, "2026-11-09", "2026-11-08")
    ingest(w.path, state)
    assert not pick(report(state, "2026-11-10"), "paid_but_shown_due")


def test_later_statement_that_clears_the_bill_removes_the_finding(tmp_path, state):
    w = _paid_week(tmp_path, "2026-11-03", "2026-11-08")
    w.statement("2026-11-12 10:00", MP, "IMG-20261112-WA0001.jpg", "2026-11-12", [("MP/52", "2026-11-02", 3100)])
    w.write()
    ingest(w.path, state)
    assert pick(report(state, "2026-11-10"), "paid_but_shown_due")          # before the fix arrived
    assert not pick(report(state, "2026-11-13"), "paid_but_shown_due")      # after


def test_owner_says_paid_without_screenshot_is_cannot_determine(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    w.msg("2026-11-01 10:00", MP, "Sir bill MP/40 ka 6500 baki hai")
    w.owner("2026-11-03 18:00", "Imran bhai MP/40 ka 6500 cash de diya tha")
    w.statement("2026-11-08 10:00", MP, "stmt.jpg", "2026-11-08", [("MP/40", "2026-10-28", 6500)])
    w.write()
    ingest(w.path, state)
    f = pick(report(state, "2026-11-09"), "paid_but_shown_due", "cannot_determine")
    assert len(f) == 1 and f[0]["ask"].startswith("owner")


# ── GST ─────────────────────────────────────────────────────
def test_gst_wrong_slab_and_wrong_base(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w)
    # Surf (HSN 3402) at 28% instead of 18%
    w.bill("2026-11-04 12:00", AT, "AT-1001.jpg", "AT/1001", "2026-11-04", [("Surf Excel 1kg", 10, 120, "3402")], gst=28)
    # tax at the right rate but on the amount before the ₹100 discount
    w.bill("2026-11-05 12:00", AT, "AT-1002.jpg", "AT/1002", "2026-11-05", [("Surf Excel 1kg", 10, 120, "3402")],
           gst=18, discount=[100], gst_amounts=[216.0])
    # correct GST -> clean
    w.bill("2026-11-06 12:00", AT, "AT-1003.jpg", "AT/1003", "2026-11-06", [("Surf Excel 1kg", 10, 120, "3402")], gst=18)
    w.write()
    ingest(w.path, state)
    g = {f["bill_id"]: f["amount_inr"] for f in pick(report(state, "2026-11-07"), "gst_error", "confirmed")}
    assert g == {"AT/1001": 120.0, "AT/1002": 18.0}


def test_gst_unknown_hsn_is_cannot_determine_but_untaxed_bill_is_silent(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    w.bill("2026-11-04 12:00", MP, "MP-61.jpg", "MP/61", "2026-11-04", [("Loose Masala Mix", 10, 50)], gst=12)
    w.bill("2026-11-04 13:00", MP, "MP-62.jpg", "MP/62", "2026-11-04", [("Loose Masala Mix", 10, 50)])
    w.write()
    ingest(w.path, state)
    rep = report(state, "2026-11-05")
    assert [f["bill_id"] for f in pick(rep, "gst_error", "cannot_determine")] == ["MP/61"]
    assert not pick(rep, "gst_error", bill="MP/62")


# ── credit period ───────────────────────────────────────────
def test_credit_period_window_terms_and_payment(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    ashoka_rates(w, when="2026-09-20 09:00", eff="2026-09-20")
    w.msg("2026-09-20 09:05", AT, "Hamara credit 21 din ka hai, uske baad 24% byaj")
    w.bill("2026-10-10 12:00", AT, "AT-1101.jpg", "AT/1101", "2026-10-10", [("Surf Excel 1kg", 100, 120, "3402")])  # due 31 Oct
    w.bill("2026-10-12 12:00", AT, "AT-1102.jpg", "AT/1102", "2026-10-12", [("Surf Excel 1kg", 50, 120, "3402")])   # due 2 Nov, paid
    w.bill("2026-10-30 12:00", AT, "AT-1103.jpg", "AT/1103", "2026-10-30", [("Surf Excel 1kg", 10, 120, "3402")])   # due 20 Nov
    w.upi("2026-10-25 12:00", SHOP, "pay.jpg", 6000, "2026-10-25", "Ashoka Traders", caption="Suresh ji AT/1102 ka payment")
    w.write()
    ingest(w.path, state)
    c = pick(report(state, "2026-10-28"), "credit_period_risk", "confirmed")
    assert [f["bill_id"] for f in c] == ["AT/1101"]
    f = c[0]
    assert f["credit_days"] == 21 and f["interest_rate_pa"] == 0.24
    # 12,000 x 24% / 365 x 4 days (31 Oct -> 4 Nov)
    assert abs(f["amount_inr"] - 12000 * 0.24 / 365 * 4) < 0.01
    assert f["action"] == {"supervised": "remind_owner", "autonomous": "remind_owner"}


def test_owner_feedback_changes_credit_terms(tmp_path, state):
    w = Week(tmp_path / "w1", shop=SHOP)
    w.bill("2026-10-10 12:00", MP, "MP-1.jpg", "MP/1", "2026-10-10", [("Rajdhani Besan 1kg", 100, 90)])
    w.write()
    ingest(w.path, state)
    assert pick(report(state, "2026-11-06"), "credit_period_risk")             # default 30 days -> 9 Nov
    feedback([{"distributor": "Malwa Provisions", "credit_days": 45, "interest_rate": 0.15}], state, tmp_path)
    assert not pick(report(state, "2026-11-06"), "credit_period_risk")         # now due 24 Nov
    c = pick(report(state, "2026-11-20"), "credit_period_risk")
    assert c and c[0]["credit_days"] == 45 and c[0]["terms_source"] == "owner_feedback"
