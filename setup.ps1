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

# ── Install the 'wrapper' command (PowerShell/cmd/Git Bash; needs Git Bash) ──
# The wrapper script is bash, so the installer writes a tiny wrapper.cmd shim
# that calls Git Bash on the project's wrapper script (POSIX path baked in),
# then adds the shim folder to the user PATH.
Write-Host "→ Installing the 'wrapper' command…"
$WrapperSrc = Join-Path $PSScriptRoot "wrapper"
$BashPath = $null
# Prefer Git Bash explicitly — it ships cygpath (needed to convert paths)
# and inherits the Windows PATH. `Get-Command bash` alone tends to find
# WSL's System32 shim first, which breaks the shim (no cygpath, needs
# /mnt/c paths).
foreach ($p in @("C:\Program Files\Git\bin\bash.exe", "C:\Program Files (x86)\Git\bin\bash.exe")) {
    if (Test-Path $p) { $BashPath = $p; break }
}
if (-not $BashPath) {
    $BashCmd = Get-Command bash -ErrorAction SilentlyContinue
    # Ignore the WSL shim (System32\bash.exe) — wrapper.cmd needs Git Bash.
    if ($BashCmd -and $BashCmd.Source -notlike "*\System32\*" -and $BashCmd.Source -notlike "*\system32\*") {
        $BashPath = $BashCmd.Source
    }
}
if (-not (Test-Path $WrapperSrc) -or -not $BashPath) {
    Write-Host "  ! 'wrapper' command skipped — it needs Git for Windows (Git Bash)." -ForegroundColor Yellow
    Write-Host "    Install it from https://git-scm.com, then re-run setup.ps1." -ForegroundColor Yellow
} else {
    $WrapperDir = Join-Path $HOME ".local\bin"
    New-Item -ItemType Directory -Force -Path $WrapperDir | Out-Null
    # Bake the POSIX (Git Bash) form of the project wrapper path into the shim.
    $Posix = ""
    try { $Posix = (& $BashPath -c 'cygpath -u "$1"' _ $WrapperSrc 2>$null).Trim() } catch {}
    if ($Posix -notlike "/*") {
        Write-Host "  ! 'wrapper' command skipped — Git Bash couldn't convert the project path." -ForegroundColor Yellow
        Write-Host "    Install Git for Windows from https://git-scm.com and re-run setup.ps1." -ForegroundColor Yellow
    } else {
        $Shim = Join-Path $WrapperDir "wrapper.cmd"
        @(
            "@echo off",
            "rem Music High Res - 'wrapper' command shim (installed by setup.ps1 / install.ps1)",
            "rem Requires Git Bash. Points at the project's wrapper script - re-run setup if the project moves.",
            "`"$BashPath`" `"$Posix`" %*",
            "exit /b %errorlevel%"
        ) | Set-Content -Path $Shim -Encoding ASCII
        # Add to the user PATH so new terminals get `wrapper`.
        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($null -eq $UserPath) { $UserPath = "" }
        if ($UserPath -notlike "*$WrapperDir*") {
            [Environment]::SetEnvironmentVariable("Path", "$UserPath;$WrapperDir", "User")
            Write-Host "  → added $WrapperDir to your user PATH (new terminals get the 'wrapper' command)"
        }
        Write-Host "  → installed wrapper.cmd → $Shim"
        Write-Host "    Open a NEW terminal and try:  wrapper status"
    }
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
