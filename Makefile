# ─────────────────────────────────────────────────────────────
# Khata: find the money leaking out of a shop's WhatsApp
# ─────────────────────────────────────────────────────────────

PYTHON  ?= python
PIP     ?= pip
VENV    := .venv
STATE   := state
PRACTICE := data/practice/week_01
EXAM     := data/exam

ifeq ($(OS),Windows_NT)
	ACTIVATE := $(VENV)\Scripts\activate &&
	SEP := \\
else
	ACTIVATE := . $(VENV)/bin/activate &&
	SEP := /
endif

.PHONY: setup grade sheet eval clean help

help: ## Show this help
	@echo ──────────────────────────────────────────
	@echo   Khata Harness — available targets
	@echo ──────────────────────────────────────────
	@echo   make setup   — create venv, install deps
	@echo   make grade   — replay practice, print score
	@echo   make sheet   — run exam, write answer_sheet.json
	@echo   make eval    — run your own eval cases
	@echo   make clean   — wipe venv and state
	@echo ──────────────────────────────────────────

setup: ## Create virtualenv and install dependencies
ifeq ($(OS),Windows_NT)
	$(PYTHON) -m venv $(VENV)
	$(ACTIVATE) $(PIP) install --upgrade pip
	$(ACTIVATE) $(PIP) install -r requirements.txt
else
	$(PYTHON) -m venv $(VENV)
	$(ACTIVATE) $(PIP) install --upgrade pip
	$(ACTIVATE) $(PIP) install -r requirements.txt
endif
	@echo ✅ Setup complete

grade: ## Replay practice chats and print the score card
	@echo ═══════════════════════════════════════════
	@echo   Khata Grader — Practice Set
	@echo ═══════════════════════════════════════════
	$(PYTHON) harness/harness.py ingest $(PRACTICE) --state-dir $(STATE)/practice
	$(PYTHON) harness/harness.py report --as-of 2026-10-15 --mode supervised --state-dir $(STATE)/practice -o report_practice.json
	$(PYTHON) grader/grader.py report_practice.json data/practice/answers/ --verbose
	@echo ═══════════════════════════════════════════

sheet: ## Run the exam chats and write answer_sheet.json
	@echo ═══════════════════════════════════════════
	@echo   Khata — Exam Run
	@echo ═══════════════════════════════════════════
	$(PYTHON) harness/harness.py ingest $(EXAM) --state-dir $(STATE)/exam
	$(PYTHON) harness/harness.py report --as-of 2026-10-20 --mode supervised --state-dir $(STATE)/exam -o answer_sheet.json
	@echo ✅ answer_sheet.json written

eval: ## Run your own eval cases
	@echo ═══════════════════════════════════════════
	@echo   Khata — Custom Eval Suite
	@echo ═══════════════════════════════════════════
	$(PYTHON) -m pytest evals/ -v --tb=short
	@echo ═══════════════════════════════════════════

clean: ## Remove venv and state
ifeq ($(OS),Windows_NT)
	if exist $(VENV) rmdir /s /q $(VENV)
	if exist $(STATE) rmdir /s /q $(STATE)
else
	rm -rf $(VENV) $(STATE)
endif
	@echo 🧹 Cleaned
