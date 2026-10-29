# Demo script (about 3 minutes)

Before recording: put your key in `.env` (see README), then run once so the model answers are
cached and the recording is fast:

```powershell
.\run.ps1 demo
```

## 0:00 — The problem (20 s)
Open `evals/scenarios/annapurna/week_01/_chat.txt` and one bill photo (`IMG-20261202-WA0003.jpg`).
> "A kirana shop pays ~40 distributor bills a month over WhatsApp: bill photos, Hinglish voice
> notes, PDF rate lists, UPI screenshots. Nobody re-reads the chat before paying, so money leaks."

## 0:20 — Read real media with Meta's models (50 s)
```powershell
$env:KHATA_IGNORE_SIDECARS = "1"
python harness\harness.py ingest evals\scenarios\annapurna\week_01 --state-dir state\video
```
Point at the `models` block in the output: Muse Spark (vision + text) and Muse Voice Transcribe on
the Meta Model API. Show `state\video\distributors\RKS.json` or `RK.json`: bills, rate list, the
voice-note scheme, all with the chat line they came from.
> "No names are hard-coded: the owner and distributors are worked out from the chat itself."

## 1:10 — Report with proof (40 s)
```powershell
python harness\harness.py report --as-of 2026-12-07 --state-dir state\video -o week1.json
```
Open `week1.json`: the scheme claim (voice note "50 peti lo to 5 peti free"), the GST slab error,
`safety_alerts` showing the forwarded "₹50,000 bhejo" message quarantined, and the Hinglish
`draft_message`.
> "Every finding cites the exact chat line or bill row. Supervised mode only drafts; it never pays."

## 1:50 — Things change next week (50 s)
```powershell
python harness\harness.py feedback evals\scenarios\annapurna\feedback_after_week_01.json --state-dir state\video
python harness\harness.py ingest evals\scenarios\annapurna\week_02 --state-dir state\video
python harness\harness.py report --as-of 2026-12-14 --mode autonomous --state-dir state\video -o week2.json
```
Show in `week2.json`:
* the corrected rate list (sent 9 Dec, "3 Dec se lagu") re-prices week-1 bills, but the 2 Dec bill stays clean;
* the one-time scheme is not claimed again on the second order;
* the salesman's "₹24" vs the rate list's ₹22 is a **conflict** left to the owner;
* a duplicate billed a week later, carrying the original delivery date (the photo was sideways);
* autonomous mode sends only the small, fully proven claims; the rest wait (`safety_check`).

## 2:40 — Score and close (20 s)
```powershell
python grader\grader.py week2.json evals\scenarios\annapurna\answers --data evals\scenarios\annapurna\week_01 --data evals\scenarios\annapurna\week_02
```
> "Khata: Meta's Muse models read the shop's WhatsApp; the harness finds the leaks with proof and
> acts only when it is safe."
