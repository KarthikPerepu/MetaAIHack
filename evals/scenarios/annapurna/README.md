    python scripts/make_eval_shop.py            -> evals/scenarios/annapurna/

Annapurna Kirana Store, Indore. Nothing here overlaps the practice / exam data:
new shop, new distributors, Android export format in week 2, WhatsApp-style file
names, Devanagari voice note, real rendered photos / PDFs / audio (plus sidecars
so it also runs without a model).

Traps covered:
  * backdated rate list (week 2, effective inside week 1) changes week-1 answers
  * one bill dated before the backdated list's effective date stays clean
  * a scheme meant only once, followed by a second qualifying order
  * forwarded "pay ₹50,000" injection and an unknown-number credential request
  * salesman quote vs rate list -> conflict
  * distributor with no rate list -> cannot_determine, answered by owner feedback
  * duplicate billed a week later carrying the original delivery date
  * GST slab error, paid-but-shown-due, credit-period risk with chat-stated terms

Run it (fresh process per command, as the judges do):

    make eval-shop        # or the commands below

    python harness/harness.py ingest evals/scenarios/annapurna/week_01 --state-dir state/annapurna
    python harness/harness.py report --as-of 2026-12-07 --state-dir state/annapurna
    python harness/harness.py feedback evals/scenarios/annapurna/feedback_after_week_01.json --state-dir state/annapurna
    python harness/harness.py ingest evals/scenarios/annapurna/week_02 --state-dir state/annapurna
    python harness/harness.py report --as-of 2026-12-14 --state-dir state/annapurna -o report_annapurna.json
    python grader/grader.py report_annapurna.json evals/scenarios/annapurna/answers \
        --data evals/scenarios/annapurna/week_01 --data evals/scenarios/annapurna/week_02 --verbose
