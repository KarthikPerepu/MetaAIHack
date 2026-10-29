# Khata: find the money leaking out of a shop's WhatsApp
# Windows without make: use .\run.ps1 <target> (same targets).

PYTHON   ?= python
VENV     := .venv
STATE    := state
PRACTICE := data/practice
EXAM     := data/exam
EXAM_AS_OF ?= 2026-10-20
PRACTICE_AS_OF ?= 2026-10-15
SHOP     := evals/scenarios/annapurna
H        := $(PYTHON) harness/harness.py

ifeq ($(OS),Windows_NT)
	VPY := $(VENV)\Scripts\python
else
	VPY := $(VENV)/bin/python
endif

.PHONY: help setup grade sheet eval eval-shop media demo clean

help:
	@echo "make setup      venv + dependencies (Python 3.11+)"
	@echo "make grade      replay the practice weeks (+ owner feedback) and print the score card"
	@echo "make sheet      run the exam chats and write answer_sheet.json"
	@echo "make eval       run our own eval suite (evals/) and the two-week eval shop"
	@echo "make demo       eval shop with real photos only (sidecars ignored)"
	@echo "make clean      remove venv, state and generated reports"

setup:
	$(PYTHON) -m venv $(VENV)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip install -r harness/requirements.txt
	@echo "Optional: install tesseract-ocr (offline photo fallback) and ffmpeg."

# Each command runs in a fresh process; everything learnt lives under --state-dir.
grade:
	rm -rf $(STATE)/practice
	@for w in $$(ls -d $(PRACTICE)/week_* | sort); do \
		$(H) ingest $$w --state-dir $(STATE)/practice > /dev/null || exit 1; \
		if [ "$$(basename $$w)" = "week_01" ] && [ -f $(PRACTICE)/feedback/feedback.json ]; then \
			$(H) feedback $(PRACTICE)/feedback/feedback.json --state-dir $(STATE)/practice > /dev/null || exit 1; fi; \
	done
	$(H) report --as-of $(PRACTICE_AS_OF) --mode supervised --state-dir $(STATE)/practice -o report_practice.json
	$(PYTHON) grader/grader.py report_practice.json $(PRACTICE)/answers --data $(PRACTICE) --verbose

sheet:
	rm -rf $(STATE)/exam
	$(H) ingest $(EXAM) --state-dir $(STATE)/exam > /dev/null
	$(H) sheet --as-of $(EXAM_AS_OF) --mode supervised --state-dir $(STATE)/exam -o answer_sheet.json
	$(H) report --as-of $(EXAM_AS_OF) --mode supervised --state-dir $(STATE)/exam -o report_exam.json
	@echo "answer_sheet.json written (full report: report_exam.json)"

eval:
	$(PYTHON) -m pytest evals/ -q
	$(MAKE) eval-shop

eval-shop:
	rm -rf $(STATE)/annapurna
	$(H) ingest $(SHOP)/week_01 --state-dir $(STATE)/annapurna > /dev/null
	$(H) feedback $(SHOP)/feedback_after_week_01.json --state-dir $(STATE)/annapurna > /dev/null
	$(H) ingest $(SHOP)/week_02 --state-dir $(STATE)/annapurna > /dev/null
	$(H) report --as-of 2026-12-14 --state-dir $(STATE)/annapurna -o report_annapurna.json
	$(PYTHON) grader/grader.py report_annapurna.json $(SHOP)/answers --data $(SHOP)/week_01 --data $(SHOP)/week_02 --verbose

demo:
	rm -rf $(STATE)/demo
	KHATA_IGNORE_SIDECARS=1 $(H) ingest $(SHOP)/week_01 --state-dir $(STATE)/demo
	KHATA_IGNORE_SIDECARS=1 $(H) feedback $(SHOP)/feedback_after_week_01.json --state-dir $(STATE)/demo
	KHATA_IGNORE_SIDECARS=1 $(H) ingest $(SHOP)/week_02 --state-dir $(STATE)/demo
	KHATA_IGNORE_SIDECARS=1 $(H) report --as-of 2026-12-14 --mode autonomous --state-dir $(STATE)/demo -o report_demo.json
	$(PYTHON) grader/grader.py report_demo.json $(SHOP)/answers --data $(SHOP)/week_01 --data $(SHOP)/week_02 --verbose

media:
	$(PYTHON) scripts/render_data_media.py $(PRACTICE)/week_01 $(EXAM)
	$(PYTHON) scripts/make_eval_shop.py

clean:
	rm -rf $(VENV) $(STATE) report_*.json
