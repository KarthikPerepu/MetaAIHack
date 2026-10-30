# Demo video plan (about 3 minutes)

Before recording, put your key in `.env` and run `.\run.ps1 demo` once. The model answers get
saved, so the recording runs quickly. Swap the robot-voice test note for a real recording first:
say "Didi is baar pachaas peti Dhara lo to paanch peti free, sirf isi order pe" and save it as
`evals\scenarios\annapurna\week_01\PTT-20261203-WA0005.opus`.

**0:00, the problem (20 sec).** Open `evals\scenarios\annapurna\week_01\_chat.txt` and one of the
bill photos. Explain that a kirana shop pays dozens of bills a month over WhatsApp, nobody re-reads
the chat before paying, and money slips away.

**0:20, reading the files with Muse (50 sec).**

```powershell
$env:KHATA_IGNORE_SIDECARS = "1"
python harness\harness.py ingest evals\scenarios\annapurna\week_01 --state-dir state\video
```

Point at the `models` part of the output (Muse Spark and Muse Voice Transcribe on the Meta Model
API). Open `state\video\distributors\RK.json` to show the bills, the rate list and the voice-note
scheme, each with the chat line it came from. Mention that no shop or distributor names are hard-coded.

**1:10, the report (40 sec).**

```powershell
python harness\harness.py report --as-of 2026-12-07 --state-dir state\video -o week1.json
```

In `week1.json`, show the scheme claim from the voice note, the GST mistake, the `safety_alerts` entry
where the forwarded "₹50,000 bhejo" message was blocked, and one of the Hinglish draft replies.

**1:50, the next week changes things (40 sec).**

```powershell
python harness\harness.py feedback evals\scenarios\annapurna\feedback_after_week_01.json --state-dir state\video
python harness\harness.py ingest evals\scenarios\annapurna\week_02 --state-dir state\video
python harness\harness.py report --as-of 2026-12-14 --mode autonomous --state-dir state\video -o week2.json --html week2.html
```

In `week2.json`, show:

- the corrected rate list (sent 9 Dec, "3 Dec se lagu") changing last week's bills, while the 2 Dec
  bill stays fine
- the one-time scheme not being claimed again on the second order
- the salesman's ₹24 against the rate list's ₹22 left as a conflict for the owner
- the duplicate bill sent a week later with the old delivery date (read from a sideways photo)
- in autonomous mode, only the small, fully proven claims being sent; the rest wait for the owner

**2:30, the owner's view (15 sec).** Open `week2.html` in the browser: the
total to claim back, the bars per distributor, the Hinglish summary written by Muse Spark, and press
"Copy" on one of the WhatsApp drafts.

**2:45, wrap up (15 sec).**

```powershell
python grader\grader.py week2.json evals\scenarios\annapurna\answers --data evals\scenarios\annapurna\week_01 --data evals\scenarios\annapurna\week_02
```

Show the score and close with one line: Muse reads the shop's WhatsApp, and Khata finds the money
that's leaking, shows the proof, and only acts when it's safe.
