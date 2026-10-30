# Khata on Windows PowerShell (same targets as the Makefile)
#   .\run.ps1 setup | grade | sheet | eval | eval-shop | demo | clean
param([Parameter(Position = 0)][ValidateSet("setup", "grade", "sheet", "eval", "eval-shop", "demo", "clean", "help")][string]$Target = "help")
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
function Invoke-Khata { & $py harness\harness.py @args; if ($LASTEXITCODE -ne 0) { throw "harness failed: $args" } }
$shop = "evals\scenarios\annapurna"

switch ($Target) {
  "setup" {
    python -m venv .venv
    & .venv\Scripts\python.exe -m pip install --upgrade pip
    & .venv\Scripts\python.exe -m pip install -r harness\requirements.txt
    Write-Host "Optional: install Tesseract OCR for the offline photo fallback."
  }
  "grade" {
    if (Test-Path state\practice) { Remove-Item -Recurse -Force state\practice }
    Get-ChildItem data\practice -Directory -Filter "week_*" | Sort-Object Name | ForEach-Object {
      Invoke-Khata ingest $_.FullName --state-dir state\practice | Out-Null
      if ($_.Name -eq "week_01" -and (Test-Path data\practice\feedback\feedback.json)) {
        Invoke-Khata feedback data\practice\feedback\feedback.json --state-dir state\practice | Out-Null
      }
    }
    Invoke-Khata report --as-of 2026-10-15 --mode supervised --state-dir state\practice -o report_practice.json
    & $py grader\grader.py report_practice.json data\practice\answers --data data\practice --verbose
  }
  "sheet" {
    if (Test-Path state\exam) { Remove-Item -Recurse -Force state\exam }
    Invoke-Khata ingest data\exam --state-dir state\exam | Out-Null
    Invoke-Khata sheet --as-of 2026-10-20 --mode supervised --state-dir state\exam -o answer_sheet.json
    Invoke-Khata report --as-of 2026-10-20 --mode supervised --state-dir state\exam -o report_exam.json
    Write-Host "answer_sheet.json written (full report: report_exam.json)"
  }
  "eval" {
    & $py -m pytest evals -q
    & $PSCommandPath eval-shop
  }
  "eval-shop" {
    if (Test-Path state\annapurna) { Remove-Item -Recurse -Force state\annapurna }
    Invoke-Khata ingest "$shop\week_01" --state-dir state\annapurna | Out-Null
    Invoke-Khata feedback "$shop\feedback_after_week_01.json" --state-dir state\annapurna | Out-Null
    Invoke-Khata ingest "$shop\week_02" --state-dir state\annapurna | Out-Null
    Invoke-Khata report --as-of 2026-12-14 --state-dir state\annapurna -o report_annapurna.json
    & $py grader\grader.py report_annapurna.json "$shop\answers" --data "$shop\week_01" --data "$shop\week_02" --verbose
  }
  "demo" {
    $env:KHATA_IGNORE_SIDECARS = "1"
    if (Test-Path state\demo) { Remove-Item -Recurse -Force state\demo }
    Invoke-Khata ingest "$shop\week_01" --state-dir state\demo
    Invoke-Khata feedback "$shop\feedback_after_week_01.json" --state-dir state\demo
    Invoke-Khata ingest "$shop\week_02" --state-dir state\demo
    Invoke-Khata report --as-of 2026-12-14 --mode autonomous --state-dir state\demo -o report_demo.json
    & $py grader\grader.py report_demo.json "$shop\answers" --data "$shop\week_01" --data "$shop\week_02" --verbose
    Remove-Item Env:\KHATA_IGNORE_SIDECARS
  }
  "clean" {
    foreach ($p in ".venv", "state") { if (Test-Path $p) { Remove-Item -Recurse -Force $p } }
    Remove-Item -Force report_*.json -ErrorAction SilentlyContinue
  }
  default { Get-Content Makefile | Select-String "^\s+@echo" | ForEach-Object { $_.Line.Trim().Replace('@echo ', '').Trim('"') } }
}
