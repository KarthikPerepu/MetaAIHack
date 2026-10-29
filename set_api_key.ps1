# Creates the private .env file with your Meta Model API key.
# Run from the project folder:   .\set_api_key.ps1
# The key is typed hidden, saved only to .env (git-ignored, never uploaded to GitHub).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$secure = Read-Host "Paste your Meta Model API key and press Enter" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim()
if (-not $key) { Write-Host "No key entered. Nothing changed." -ForegroundColor Yellow; exit 1 }

$lines = @(
  "# Private settings. Git-ignored: never uploaded to GitHub.",
  "MODEL_API_KEY=$key",
  "MODEL_BASE_URL=https://api.meta.ai/v1",
  "MODEL_VISION_MODEL=muse-spark-1.3",
  "MODEL_TEXT_MODEL=muse-spark-1.3",
  "MODEL_VOICE_MODEL=muse-voice-transcribe-1.0",
  "MODEL_REASONING_EFFORT=low"
)
# UTF-8 without BOM so Python reads it cleanly
[IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env"), $lines, (New-Object Text.UTF8Encoding $false))

$shown = if ($key.Length -gt 8) { $key.Substring(0, 4) + "..." + $key.Substring($key.Length - 4) } else { "****" }
Write-Host "Saved .env with key $shown" -ForegroundColor Green
Write-Host "Test it with:  .\run.ps1 demo"
