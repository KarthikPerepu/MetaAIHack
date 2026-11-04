"""`doctor` should tell you, before a run, which files can't be read."""

from evals.fixtures.scenario import Week
from harness.doctor import examine


def test_doctor_spots_placeholders_and_missing_files(tmp_path):
    w = Week(tmp_path / "w", shop="Laxmi Stores")
    w.rate_list("2026-10-10 10:00", "Vijay Kashi Provisions", "rl.pdf", "Kashi Provisions",
                [{"product": "Fortune Atta 10kg", "rate": 390, "hsn": "1101", "unit": "bag"}], effective="2026-10-10")
    w.attach("2026-10-11 10:00", "Vijay Kashi Provisions", "IMG-gone.jpg")          # mentioned, not in the folder
    (w.path / "IMG-fake.jpg").write_bytes(b"dummy_image_data\n")                    # placeholder, no reading
    w.attach("2026-10-12 10:00", "Vijay Kashi Provisions", "IMG-fake.jpg")
    w.write()
    r = examine(w.path)
    assert not r["ok"]
    assert any("IMG-gone.jpg" in p for p in r["problems"])
    assert any("IMG-fake.jpg" in p and "can't be read" in p for p in r["problems"])
    assert r["attachments"] == {"pdf": 1, "image": 1}


def test_doctor_is_happy_with_a_clean_folder(tmp_path):
    w = Week(tmp_path / "w", shop="Laxmi Stores")
    w.rate_list("2026-10-10 10:00", "Vijay Kashi Provisions", "rl.pdf", "Kashi Provisions",
                [{"product": "Fortune Atta 10kg", "rate": 390, "hsn": "1101", "unit": "bag"}], effective="2026-10-10")
    w.write()
    r = examine(w.path)
    assert r["ok"] and r["chats"][0]["messages"] == 1
