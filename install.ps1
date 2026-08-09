# ═══════════════════════════════════════════════════════════════════════
#  Music High Res — one-command installer (Windows / PowerShell)
#
#  Usage:
#    Inside the repo:       .\install.ps1
#    Fresh machine:         irm https://raw.githubusercontent.com/ZeroIndents/AppleMusic-Song-Downloader/main/install.ps1 | iex
#
#  What it does:
#    1. Checks you're on Windows (macOS → install.sh, Linux → install_linux.sh)
#    2. Installs Python, git, ffmpeg via winget when missing
#    3. Installs gamdl with pip (the cross-platform way on Windows)
#    4. Downloads the repo when run as a bootstrap one-liner
#    5. Creates the Python environment + installs app dependencies
#    6. Prints what's left: export cookies, optional ALAC wrapper (Docker)
#
#  AAC 256kbps works with cookies alone. Lossless ALAC / Dolby Atmos need
#  Docker Desktop + an Apple Music Android APK (see README Step 3).
# ═══════════════════════════════════════════════════════════════════════
$ErrorActionPreference = "Stop"
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

function say  { Write-Host "→ $args" -ForegroundColor Blue }
function ok   { Write-Host "✓ $args" -ForegroundColor Green }
function warn { Write-Host "! $args" -ForegroundColor Yellow }
function die  { Write-Host "✗ $args" -ForegroundColor Red; exit 1 }

$RepoUrl = "https://github.com/ZeroIndents/AppleMusic-Song-Downloader.git"

Write-Host ""
Write-Host "Music High Res — Windows installer" -ForegroundColor White
Write-Host ""

# ── 1. Platform gate ────────────────────────────────────────────────────
# Works on both Windows PowerShell 5.1 (double-clicked .bat) and pwsh 7+:
# $IsMacOS/$IsLinux only exist on pwsh, so detect via $env:OS instead.
if ($env:OS -eq "Windows_NT") {
    ok "Windows detected"
} elseif ($IsMacOS -or $IsLinux) {
    if ($IsMacOS) { die "This is the Windows installer. macOS users: use install.sh instead." }
    die "This is the Windows installer. Linux users: use install_linux.sh instead."
} else {
    warn "Couldn't confirm Windows — continuing anyway (this installer targets Windows)."
}

# ── 2. Are we inside the repo already, or bootstrapping? ───────────────
$InRepo = (Test-Path (Join-Path $Root "app.py")) -and (Test-Path (Join-Path $Root "README.md"))
if ($InRepo) {
    say "Running from inside the repo: $Root"
} else {
    say "Bootstrap mode — will download the repo."
}

# ── 3. Prerequisites via winget (python, git, ffmpeg) ──────────────────
$Winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $Winget) {
    die "winget not found. Install the App Installer from the Microsoft Store (or install Python, git and ffmpeg manually), then re-run."
}

# winget updates the *user/machine* PATH env vars, not this session's — refresh
# so freshly-installed python/git are visible without restarting the terminal.
function Update-ProcessPath {
    $Machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $User = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = ($Machine + ";" + $User + ";" + $env:Path)
}

function Install-Winget([string]$Id) {
    say "Installing $Id via winget…"
    winget install --id $Id --accept-package-agreements --accept-source-agreements --silent | Out-Null
    Update-ProcessPath
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Install-Winget "Python.Python.3.12" }
if (-not (Get-Command git -ErrorAction SilentlyContinue))    { Install-Winget "Git.Git" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Install-Winget "Gyan.FFmpeg" }
# jq is needed by the wrapper setup (it runs inside Git Bash, which ships
# unzip but not jq). winget's jq lands on PATH so Git Bash can see it.
if (-not (Get-Command jq -ErrorAction SilentlyContinue))     { Install-Winget "jqlang.jq" }

foreach ($tool in @("python", "git")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { die "$tool still not found — try:  winget install $tool" }
}
ok "python, git present (ffmpeg optional but recommended)"

# ── 4. gamdl via pip (cross-platform on Windows) ────────────────────────
if (-not (Get-Command gamdl -ErrorAction SilentlyContinue)) {
    say "Installing gamdl (pip install gamdl)…"
    python -m pip install --quiet --upgrade gamdl
    if (-not (Get-Command gamdl -ErrorAction SilentlyContinue)) {
        # pip's Scripts dir may not be on PATH for a fresh install — locate it.
        # sys.prefix is the Python install dir itself (gamdl.exe lands in its
        # Scripts subfolder), so do NOT Split-Path it.
        $Prefix = python -c "import sys; print(sys.prefix)"
        $Scripts = Join-Path $Prefix "Scripts"
        if (Test-Path (Join-Path $Scripts "gamdl.exe")) {
            warn "gamdl installed to $Scripts — add it to PATH (or re-run this in a new terminal):"
            warn "  [Environment]::SetEnvironmentVariable('PATH', `"`$env:PATH;$Scripts`", 'User')"
        } else {
            die "gamdl not found — try:  pip install gamdl"
        }
    }
}
ok "gamdl installed"

# ── 5. Get the repo ─────────────────────────────────────────────────────
if (-not $InRepo) {
    if (Test-Path "music-high-res") {
        say "music-high-res/ exists — pulling latest…"
        git -C music-high-res pull --ff-only --quiet 2>$null
    } else {
        say "Downloading Music High Res…"
        git clone --depth 1 $RepoUrl music-high-res
    }
    Set-Location music-high-res
    $Root = (Get-Location).Path
    ok "Repo ready: $Root"
}
if (-not (Test-Path (Join-Path $Root "app.py"))) { die "app.py not found — something's off with the repo checkout" }

# ── 6. Python environment + dependencies ────────────────────────────────
say "Creating Python environment (.venv) + installing dependencies…"
python -m venv .venv
& (Join-Path $Root ".venv\Scripts\python.exe") -m pip install --quiet --upgrade pip
& (Join-Path $Root ".venv\Scripts\python.exe") -m pip install --quiet -r requirements.txt
ok "App dependencies installed"

# ── 7. Docker check (needed later only for ALAC / Atmos) ───────────────
if (Get-Command docker -ErrorAction SilentlyContinue) {
    ok "Docker is installed — you can set up the ALAC wrapper whenever you want"
} else {
    warn "Docker not installed — fine for AAC 256kbps; needed later for"
    warn "lossless ALAC / Dolby Atmos (see README Step 3)."
}

# ── 8. Next steps ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "━━━ Setup complete ━━━" -ForegroundColor White
Write-Host ""
Write-Host "  1. Export your Apple Music cookies (one-time):"
Write-Host "     Sign in at https://music.apple.com, then export cookies for that site with"
Write-Host "     the ""Get cookies.txt LOCALLY"" browser extension → save as cookies.txt"
Write-Host "     in this folder:  $Root"
Write-Host ""
Write-Host "  2. Start the app (one click):"
Write-Host "       double-click Start Music High Res.bat     →  http://127.0.0.1:8741"
Write-Host ""
Write-Host "  3. Use the CLI (same engine):"
Write-Host "       .venv\Scripts\python.exe cli.py ""https://music.apple.com/us/album/<id>"""
Write-Host ""
Write-Host "  4. Optional — lossless ALAC / Dolby Atmos (needs Docker Desktop + an"
Write-Host "     Apple Music Android APK you obtain yourself):"
Write-Host "       use the in-app wizard (Wrapper & login → Setup) — it needs Git for"
Write-Host "       Windows (Git Bash) since the wrapper setup script is bash:"
Write-Host "       .\setup_wrapper.sh /path/to/apple-music.apk"
Write-Host "     then enable ""Use wrapper"" in the app's Settings."
Write-Host ""
