# ═══════════════════════════════════════════════════════════════════════
#  Music High Res — one-time setup (Windows / PowerShell)
#  Creates a Python venv and installs the web app dependencies.
#  The Windows twin of setup.sh.
# ═══════════════════════════════════════════════════════════════════════
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "━━━ Music High Res setup ━━━"
Write-Host ""

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    Write-Host "✗ python3 not found. Install Python 3.10+ first (https://python.org or winget install Python.Python.3.12)." -ForegroundColor Red
    exit 1
}

Write-Host "→ Creating Python environment (.venv)…"
python -m venv .venv
& ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt

Write-Host "→ Checking gamdl…"
$Gamdl = Get-Command gamdl -ErrorAction SilentlyContinue
if ($Gamdl) {
    & gamdl --version
} else {
    Write-Host "  ! gamdl binary not on PATH — install it with:  pip install gamdl" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "✓ Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1.  Export your Apple Music cookies (see README.md) and save them as cookies.txt in this folder."
Write-Host "  2.  Start the app:   double-click 'Start Music High Res.bat'   (or run: .venv\Scripts\python.exe app.py)"
Write-Host "  3.  Or use the CLI:  .venv\Scripts\python.exe cli.py ""https://music.apple.com/..."""
Write-Host ""
Write-Host "Optional (lossless ALAC / Dolby Atmos):"
Write-Host "  4.  Install Docker Desktop, grab an Apple Music Android APK, then use the"
Write-Host "      in-app wizard (Wrapper & login → Setup) — it needs Git for Windows (Git Bash)"
Write-Host "      since the wrapper setup script is bash:  .\setup_wrapper.sh /path/to/apple-music.apk"
