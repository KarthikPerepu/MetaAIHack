# Khata on Windows PowerShell (same targets as the Makefile)
#   .\run.ps1 setup | grade | sheet | eval | eval-shop | demo | clean
#   .\run.ps1 check <folder with a WhatsApp export>
param(
  [Parameter(Position = 0)][ValidateSet("setup", "grade", "sheet", "eval", "eval-shop", "demo", "check", "clean", "help")][string]$Target = "help",
  [Parameter(Position = 1)][string]$Folder = ""
)
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
function Invoke-Khata { & $py harness\harness.py @args; if ($LASTEXITCODE -ne 0) { throw "harness failed: $args" } }
$shop = "evals\scenarios\annapurna"

# Start a run fresh but keep cache\ (saved model answers), so re-running the same files costs no credit.
function Reset-State($dir) {
  if (Test-Path $dir) { Get-ChildItem $dir -Exclude cache | Remove-Item -Recurse -Force }
}

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
    Reset-State state\demo
    Invoke-Khata ingest "$shop\week_01" --state-dir state\demo
    Invoke-Khata feedback "$shop\feedback_after_week_01.json" --state-dir state\demo
    Invoke-Khata ingest "$shop\week_02" --state-dir state\demo
    Invoke-Khata report --as-of 2026-12-14 --mode autonomous --state-dir state\demo -o report_demo.json --html report_demo.html
    & $py grader\grader.py report_demo.json "$shop\answers" --data "$shop\week_01" --data "$shop\week_02" --verbose
    Remove-Item Env:\KHATA_IGNORE_SIDECARS
    Start-Process (Resolve-Path report_demo.html)
  }
  "check" {
    if (-not $Folder -or -not (Test-Path $Folder)) { throw "Usage: .\run.ps1 check <folder with _chat.txt and attachments>" }
    $name = Split-Path $Folder -Leaf
    Invoke-Khata check $Folder --state-dir "state\check-$name" --out "khata_report\$name"
    Start-Process (Resolve-Path "khata_report\$name\report.html")
  }
  "clean" {
    foreach ($p in ".venv", "state") { if (Test-Path $p) { Remove-Item -Recurse -Force $p } }
    Remove-Item -Force report_*.json, report_*.html -ErrorAction SilentlyContinue
    if (Test-Path khata_report) { Remove-Item -Recurse -Force khata_report }
  }
  default { Get-Content Makefile | Select-String "^\s+@echo" | ForEach-Object { $_.Line.Trim().Replace('@echo ', '').Trim('"') } }
}
