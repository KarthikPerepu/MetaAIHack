# Annapurna Kirana Store: our own test shop

A made-up shop in Indore with two weeks of WhatsApp chats, the owner's feedback after week 1, and
the answers we expect. We built it to test the harness on something completely different from
the practice and exam data: a new shop, new distributors, the Android export format in week 2,
WhatsApp-style file names (`IMG-20261210-WA0004.jpg`), a voice note in Hindi, and real photos,
PDFs and audio. Each file also has a saved `.json`/`.txt` reading next to it, so it runs without
an API key too.

What's hidden in it:

- a rate list sent in week 2 that applies back to 3 Dec, which changes week 1's answers
- a bill dated 2 Dec, just before that, which should stay fine
- a scheme meant only once, and a second order that would qualify if we got it wrong
- a forwarded "send ₹50,000" scam, and an unknown number asking for a UPI PIN
- the salesman quoting ₹24 for salt when the rate list says ₹22 (a conflict for the owner)
- a distributor who never sent a rate list; the owner answers that in feedback
- a duplicate bill sent a week later with the original delivery date, photographed sideways
- a GST mistake, a payment still shown as due, and a credit period about to run out

The expected answers are in `answers/`, one file per finding.

To run it the way the judges would, one command at a time:

```
make eval-shop          # or .\run.ps1 eval-shop on Windows

python harness/harness.py ingest evals/scenarios/annapurna/week_01 --state-dir state/annapurna
python harness/harness.py report --as-of 2026-12-07 --state-dir state/annapurna
python harness/harness.py feedback evals/scenarios/annapurna/feedback_after_week_01.json --state-dir state/annapurna
python harness/harness.py ingest evals/scenarios/annapurna/week_02 --state-dir state/annapurna
python harness/harness.py report --as-of 2026-12-14 --state-dir state/annapurna -o report_annapurna.json
python grader/grader.py report_annapurna.json evals/scenarios/annapurna/answers --data evals/scenarios/annapurna/week_01 --data evals/scenarios/annapurna/week_02 --verbose
```

`scripts/make_eval_shop.py` rebuilds this folder from scratch.
