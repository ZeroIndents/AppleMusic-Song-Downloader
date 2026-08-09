# ═══════════════════════════════════════════════════════════════════════
#  Music High Res — one-click launcher  (Windows / PowerShell)
#
#  Usage:
#    .\start.ps1                          # normal launch (recommended)
#    .\start.ps1 -Min                     # AAC-only: skip Docker + wrapper
#    .\start.ps1 -NoBrowser               # start server, don't open a browser
#    .\start.ps1 -NoDocker                # don't wait for Docker
#
#  What it does (the Windows twin of start.sh):
#    1. Runs setup.ps1 automatically on first run (creates .venv + deps)
#    2. Prints a friendly checklist of what's installed (gamdl, ffmpeg, …)
#    3. Starts Docker Desktop if it isn't running (when installed)
#    4. Starts the ALAC wrapper (wrapper-v2) when present — and stops it
#       again when you close the app (Docker Desktop itself stays running)
#    5. Starts the app server (or reuses one that's already running)
#    6. Opens the browser at http://127.0.0.1:8741
#
#  Everything it prints is also written to logs\launcher.log.
# ═══════════════════════════════════════════════════════════════════════
param(
    [switch]$Min,          # AAC only: skip Docker + wrapper
    [switch]$NoBrowser,    # start server, don't open a browser
    [switch]$NoDocker      # don't wait for Docker
)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function say  { Write-Host "▪ $args" }
function ok   { Write-Host "✓ $args" -ForegroundColor Green }
function warn { Write-Host "! $args" -ForegroundColor Yellow }
function fail { Write-Host "✗ $args" -ForegroundColor Red }

# Wrapper lifecycle: the wrapper should only run while the app is open.
# NOTE: these are $global: (not $script:) on purpose — the engine-exit event
# action below runs in a separate runspace that can't see script scope.
$global:WrapperStarted = $false   # we started the wrapper this session
$global:AppStarted = $false       # we started the app server this session
$global:ServerPid = $null

function global:Stop-Cleanup {
    # 1. Kill the server we started (never a reused one) so app.py doesn't
    #    stay orphaned on port 8741.
    if ($global:ServerPid) { Stop-Process -Id $global:ServerPid -ErrorAction SilentlyContinue }
    # 2. Stop the ALAC wrapper — it only runs while the app is open.
    #    Docker Desktop itself is left running. Bounded stop (15s cap) so a
    #    wedged Docker daemon can't hang the close.
    if ($global:WrapperStarted -and $global:AppStarted -and (Test-Path (Join-Path $PSScriptRoot "wrapper-v2"))) {
        say "Closing the app — stopping the ALAC wrapper (Docker Desktop stays running)…"
        Push-Location (Join-Path $PSScriptRoot "wrapper-v2")
        # `-t 3` limits the container's grace period; Wait-Job caps a wedged
        # Docker daemon so the close can never hang.
        $Job = Start-Job { docker compose stop -t 3 2>$null | Out-Null }
        if (-not (Wait-Job $Job -Timeout 15)) {
            Stop-Job $Job -ErrorAction SilentlyContinue
        }
        Remove-Job $Job -Force -ErrorAction SilentlyContinue
        Pop-Location
    }
}
# Best-effort cleanup when the PowerShell engine exits (window closed, Ctrl+C).
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Stop-Cleanup } | Out-Null

# Log everything to logs\launcher.log as well as the console.
$LogDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "launcher.log"
Start-Transcript -Path $LogFile -Append -Force | Out-Null

Write-Host ""
Write-Host "━━━ Music High Res ━━━"
Write-Host ""

# ── 1. Python + first-run setup ───────────────────────────────────────
$Python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $Python) {
    fail "Python 3 is not installed."
    Write-Host "  Install it from https://python.org (or:  winget install Python.Python.3.12), then re-run this script."
    Stop-Transcript | Out-Null
    exit 1
}

# venv_ok — is the Python environment actually usable? A half-created .venv
# (interrupted pip install) passes `Test-Path` but crashes the app with
# ModuleNotFoundError — so verify instead of trusting the folder exists.
function Test-VenvOk {
    $Py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $Py)) { return $false }
    & $Py -c "import flask" 2>$null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-VenvOk)) {
    say "First run — setting up (this creates .venv + installs dependencies)…"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0 -or -not (Test-VenvOk)) {
        fail "Setup failed — see the messages above."
        Stop-Transcript | Out-Null
        exit 1
    }
}

# ── 2. Prerequisite checklist ─────────────────────────────────────────
Write-Host "Checking your setup…"
function Have([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Check([string]$Label, [string]$Tool, [string]$YesMsg, [string]$NoMsg) {
    # ${Label}: — the braces stop PowerShell parsing "$Label:" as a variable ref.
    if (Have $Tool) { ok "${Label}: $YesMsg" } else { warn "${Label}: $NoMsg" }
}
Check "gamdl  (Apple Music engine)" "gamdl" "found" "missing — install:  pip install gamdl"
Check "ffmpeg (FLAC / player)"      "ffmpeg" "found" "missing — install:  winget install Gyan.FFmpeg"
$Gyt = Join-Path $PSScriptRoot ".venv\Scripts\gytmdl.exe"
if (Have "gytmdl" -or (Test-Path $Gyt)) { ok "gytmdl (YouTube Music): found" } else { warn "gytmdl (YouTube Music): missing — run:  .venv\Scripts\pip install gytmdl" }
$Vot = Join-Path $PSScriptRoot ".venv\Scripts\votify.exe"
if (Have "votify" -or (Test-Path $Vot)) { ok "votify (Spotify): found" } else { warn "votify (Spotify): missing — run:  .venv\Scripts\pip install 'votify[librespot]'" }
if (Test-Path (Join-Path $PSScriptRoot "cookies.txt")) { ok "Apple Music cookies: found" } else { warn "Apple Music cookies: missing — export cookies.txt from music.apple.com (needed for AAC without the wrapper)" }
if (Have "docker") { ok "Docker: found" } else { warn "Docker: not installed — fine for AAC 256kbps, needed for lossless ALAC / Atmos" }

# ── 3. Docker (skip in -Min / -NoDocker) ──────────────────────────────
function Test-DockerOk {
    # Bounded readiness check: `docker info` can hang forever on a wedged
    # daemon. Probe in a background job with a 5s cap — never stall the launcher.
    # Note: `exit N` in a job does NOT appear on the output stream, so output
    # the exit code instead and read it back with Receive-Job.
    $Job = Start-Job { docker info *> $null; $LASTEXITCODE }
    if (-not (Wait-Job $Job -Timeout 5)) {
        Stop-Job $Job -ErrorAction SilentlyContinue
        Remove-Job $Job -Force -ErrorAction SilentlyContinue
        return $false
    }
    $Code = (Receive-Job $Job -ErrorAction SilentlyContinue)
    Remove-Job $Job -Force -ErrorAction SilentlyContinue
    return ($Code -eq 0)
}

if ($Min -or $NoDocker) {
    say "Skipping Docker + wrapper (AAC mode)."
} elseif (Have "docker") {
    if (Test-DockerOk) {
        ok "Docker is already running"
    } else {
        # Docker Desktop on Windows ships `docker` on PATH even when stopped.
        $DockerExe = (Get-Command docker -ErrorAction SilentlyContinue).Source
        if ($DockerExe -match "Docker\\Docker\\resources") {
            say "Launching Docker Desktop… (first launch after a reboot takes a minute)"
            Start-Process "Docker Desktop" -ErrorAction SilentlyContinue
        } else {
            warn "Docker is installed but not running. Start Docker Desktop, or re-run with:  .\start.ps1 -NoDocker"
        }
        $Waited = 0
        while (-not (Test-DockerOk) -and $Waited -lt 120) {
            Start-Sleep -Seconds 2; $Waited += 2
        }
        if (Test-DockerOk) { ok "Docker is up" } else { warn "Docker did not start in time. Open Docker Desktop manually, or run:  .\start.ps1 -NoDocker" }
    }
} else {
    warn "Docker isn't installed — fine for AAC; for lossless ALAC/Atmos see README Step 3."
}

# ── 4. ALAC wrapper (wrapper-v2) ──────────────────────────────────────
if (-not $Min -and (Test-Path (Join-Path $PSScriptRoot "wrapper-v2")) -and (Test-DockerOk)) {
    say "Starting the ALAC wrapper…"
    Push-Location (Join-Path $PSScriptRoot "wrapper-v2")
    & docker compose up -d 2>$null | Out-Null
    Pop-Location
    $global:WrapperStarted = $true
    Start-Sleep -Seconds 3
    $Waited = 0; $State = ""
    while ($Waited -lt 45) {
        try {
            $Me = Invoke-RestMethod -Uri "http://127.0.0.1/me" -TimeoutSec 3 -ErrorAction Stop
            $State = $Me.auth.state
            if ($State -eq "authenticated") { break }
        } catch { $State = "" }
        Start-Sleep -Seconds 2; $Waited += 2
    }
    if ($State -eq "authenticated") {
        ok "Wrapper up — Apple session restored (lossless ALAC ready)"
    } else {
        warn "Wrapper is running but not authenticated yet (state: $State)."
        say "Open the app → '5 · Wrapper & login' and follow the hints there."
    }
} elseif ($Min) {
    # nothing — AAC-only mode
} elseif (Test-Path (Join-Path $PSScriptRoot "wrapper-v2")) {
    warn "Wrapper present but Docker isn't ready — skipping. Lossless ALAC / Atmos will be unavailable until it's up."
}

# ── 5. App server (reuse if already running) ──────────────────────────
say "Starting the Music High Res app…"
$ServerPid = $null
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:8741/api/status" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    ok "App server already running"
} catch {
    $AppPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    $Proc = Start-Process -FilePath $AppPy -ArgumentList "app.py" -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Hidden
    $global:ServerPid = $Proc.Id
    $global:AppStarted = $true
}

# Wait for the server before opening the browser.
for ($i = 0; $i -lt 30; $i++) {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:8741/api/status" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        break
    } catch { Start-Sleep -Seconds 1 }
}

if ($NoBrowser) {
    say "Skipping browser (-NoBrowser). UI is at http://127.0.0.1:8741"
} else {
    Start-Process "http://127.0.0.1:8741" -ErrorAction SilentlyContinue
    say "If the browser didn't open, go to:  http://127.0.0.1:8741"
}
ok "Running → http://127.0.0.1:8741"

Write-Host ""
Write-Host "  Press Ctrl+C to stop the app (or close this window)."
Write-Host ""

# Keep the window alive while the server runs. If we reused an existing
# server, just wait on it too. When the script ends (Ctrl+C, window closed,
# or the app server exits), Stop-Cleanup stops the wrapper we started.
try {
    if ($global:ServerPid) {
        Wait-Process -Id $global:ServerPid -ErrorAction SilentlyContinue
    } else {
        while ($true) {
            try {
                $null = Invoke-WebRequest -Uri "http://127.0.0.1:8741/api/status" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
                Start-Sleep -Seconds 5
            } catch {
                Start-Sleep -Seconds 5
            }
        }
    }
} finally {
    Stop-Cleanup
}
