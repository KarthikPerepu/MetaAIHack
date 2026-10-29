# Khata PowerShell Runner (equivalent to Makefile targets on Windows)
param (
    [Parameter(Position=0)]
    [ValidateSet("setup", "grade", "sheet", "eval", "clean", "help")]
    [string]$Target = "help"
)

$ErrorActionPreference = "Stop"

switch ($Target) {
    "setup" {
        Write-Host "Creating virtual environment and installing dependencies..."
        python -m venv .venv
        & .venv\Scripts\pip install --upgrade pip
        & .venv\Scripts\pip install -r requirements.txt
        Write-Host "[OK] Setup complete" -ForegroundColor Green
    }
    "grade" {
        Write-Host "==============================================="
        Write-Host "  Khata Grader -- Practice Set"
        Write-Host "==============================================="
        python harness\harness.py ingest data\practice\week_01 --state-dir state\practice
        python harness\harness.py report --as-of 2026-10-15 --mode supervised --state-dir state\practice -o report_practice.json
        python grader\grader.py report_practice.json data\practice\answers\ --verbose
    }
    "sheet" {
        Write-Host "==============================================="
        Write-Host "  Khata -- Exam Run"
        Write-Host "==============================================="
        python harness\harness.py ingest data\exam --state-dir state\exam
        python harness\harness.py report --as-of 2026-10-20 --mode supervised --state-dir state\exam -o answer_sheet.json
        Write-Host "[OK] answer_sheet.json written" -ForegroundColor Green
    }
    "eval" {
        Write-Host "==============================================="
        Write-Host "  Khata -- Custom Eval Suite"
        Write-Host "==============================================="
        python -m pytest evals\ -v --tb=short
    }
    "clean" {
        Write-Host "Cleaning up state and virtual environment..."
        if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
        if (Test-Path "state") { Remove-Item -Recurse -Force "state" }
        Write-Host "[OK] Cleaned" -ForegroundColor Green
    }
    Default {
        Write-Host "----------------------------------------------"
        Write-Host "  Khata Harness -- available targets"
        Write-Host "----------------------------------------------"
        Write-Host "  .\run.ps1 setup  -- create venv, install deps"
        Write-Host "  .\run.ps1 grade  -- replay practice, print score"
        Write-Host "  .\run.ps1 sheet  -- run exam, write answer_sheet.json"
        Write-Host "  .\run.ps1 eval   -- run your own eval cases"
        Write-Host "  .\run.ps1 clean  -- wipe venv and state"
        Write-Host "----------------------------------------------"
    }
}
