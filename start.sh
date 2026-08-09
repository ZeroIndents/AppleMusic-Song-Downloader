#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
#  Music High Res — one-click launcher  (macOS + Linux)
#
#  Usage:
#    ./start.sh                          # normal launch (recommended)
#    ./start.sh --min                    # AAC-only: skip Docker + wrapper
#    ./start.sh --no-browser             # start server, don't open a browser
#    ./start.sh --app-style              # open in a standalone app window
#                                        # (used internally by Music High Res.app)
#    ./start.sh --no-docker              # don't wait for Docker
#
#  What it does:
#    1. Runs setup.sh automatically on first run (creates .venv + deps)
#    2. Prints a friendly checklist of what's installed (gamdl, ffmpeg, …)
#    3. Starts Docker Desktop (macOS) if it isn't running
#    4. Starts the ALAC wrapper (wrapper-v2) when present — and stops it
#       again when you close the app (Docker Desktop itself stays running)
#    5. Starts the app server (or reuses one that's already running)
#    6. Opens the browser at http://127.0.0.1:8741
#
#  Everything it prints is also written to logs/launcher.log.
# ═══════════════════════════════════════════════════════════════════════
set -u
cd "$(dirname "$0")"

# Homebrew's bin dirs aren't on the default PATH (especially inside .app
# bundles); gamdl/ffmpeg/docker live there. Add them + the Linux user bin.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

say()  { echo "▪ $1"; }
ok()   { echo "✓ $1"; }
warn() { echo "! $1"; }
fail() { echo "✗ $1"; }

MIN_MODE=0; NO_BROWSER=0; APP_STYLE=0; NO_DOCKER=0
WRAPPER_STARTED=0   # we started the wrapper this session (stop it on close)
for arg in "$@"; do
  case "$arg" in
    --min)        MIN_MODE=1 ;;
    --no-browser) NO_BROWSER=1 ;;
    --app-style)  APP_STYLE=1 ;;
    --no-docker)  NO_DOCKER=1 ;;
    -h|--help)
      sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 1 ;;
  esac
done

OS="$(uname -s)"

# Log everything to logs/launcher.log as well as the terminal.
LOG_DIR="logs"; mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_DIR/launcher.log") 2>&1

echo
echo "━━━ Music High Res ━━━"
echo

# ── 1. Python + first-run setup ───────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
  fail "Python 3 is not installed."
  echo "  Install it, then re-run this script:"
  echo "    macOS:   brew install python3      (or python.org)"
  echo "    Debian:  sudo apt install python3 python3-venv"
  echo "    Fedora:  sudo dnf install python3 python3-virtualenv"
  echo "    Arch:    sudo pacman -S python python-pip"
  exit 1
fi

# venv_ok — is the Python environment actually usable? Checks the interpreter
# AND that a core dependency imports. A half-created .venv (e.g. an interrupted
# `pip install`) passes `[ -d .venv ]` but crashes the app with
# ModuleNotFoundError — so verify instead of trusting the directory exists.
venv_ok() {
  [ -x .venv/bin/python ] && .venv/bin/python -c 'import flask' >/dev/null 2>&1
}

if ! venv_ok; then
  echo "First run — setting up (this creates .venv + installs dependencies)…"
  if ! bash setup.sh; then
    fail "Setup failed — see the messages above."
    exit 1
  fi
fi

# ── 2. Prerequisite checklist ─────────────────────────────────────────
echo "Checking your setup…"
have()  { command -v "$1" >/dev/null 2>&1 && echo yes || echo no; }
check() { # check <name> <tool|path> <yes-msg> <no-msg>
  if [ "$(have "$2")" = "yes" ]; then ok  "$1: $3"; else warn "$1: $4"; fi
}
check "gamdl  (Apple Music engine)" gamdl "found" "missing — install:  brew install gamdl  (or pip install gamdl)"
check "ffmpeg (FLAC / player)"      ffmpeg "found" "missing — install:  brew install ffmpeg"
if [ -x .venv/bin/gytmdl ]; then ok "gytmdl (YouTube Music): found"; else warn "gytmdl (YouTube Music): missing — run:  .venv/bin/pip install gytmdl"; fi
if [ -x .venv/bin/votify ]; then ok "votify (Spotify): found"; else warn "votify (Spotify): missing — run:  .venv/bin/pip install 'votify[librespot]'"; fi
if [ -f cookies.txt ]; then ok "Apple Music cookies: found"; else warn "Apple Music cookies: missing — export cookies.txt from music.apple.com (needed for AAC without the wrapper)"; fi
if [ "$(have docker)" = "yes" ]; then ok "Docker: found"; else warn "Docker: not installed — fine for AAC 256kbps, needed for lossless ALAC / Atmos"; fi

# ── 3. Docker (skip in --min / --no-docker) ───────────────────────────
# docker_ok — bounded readiness check: `docker info` can hang forever on a
# wedged daemon (e.g. after the Mac sleeps). Never let the launcher stall.
docker_ok() {
  local DPID
  docker info >/dev/null 2>&1 &
  DPID=$!
  for _ in 1 2 3 4 5; do
    if ! kill -0 "$DPID" 2>/dev/null; then
      wait "$DPID"; return $?
    fi
    sleep 1
  done
  kill "$DPID" 2>/dev/null; wait "$DPID" 2>/dev/null
  return 1
}

if [ "$MIN_MODE" = "1" ] || [ "$NO_DOCKER" = "1" ]; then
  say "Skipping Docker + wrapper (AAC mode)."
elif command -v docker >/dev/null 2>&1 && docker_ok; then
  ok "Docker is already running"
elif command -v docker >/dev/null 2>&1; then
  if [ "$OS" = "Darwin" ]; then
    say "Launching Docker Desktop… (first launch after a reboot takes a minute)"
    open -a Docker 2>/dev/null || open -a /Applications/Docker.app 2>/dev/null
  else
    warn "Docker is installed but not running. Start the daemon, or re-run with:  ./start.sh --no-docker"
  fi
  WAIT=0
  until docker_ok; do
    WAIT=$((WAIT+1))
    if [ "$WAIT" -ge 120 ]; then
      warn "Docker did not start in time. Open Docker Desktop manually, or run:  ./start.sh --no-docker"
      break
    fi
    sleep 2
  done
  docker_ok && ok "Docker is up"
else
  warn "Docker isn't installed — fine for AAC; for lossless ALAC/Atmos see README Step 3."
fi

# ── 4. ALAC wrapper (wrapper-v2) ──────────────────────────────────────
if [ "$MIN_MODE" != "1" ] && [ -d wrapper-v2 ] && docker_ok; then
  say "Starting the ALAC wrapper…"
  ( cd wrapper-v2 && docker compose up -d )
  WRAPPER_STARTED=1
  sleep 3
  WAIT=0; STATE=""
  until [ "$STATE" = "authenticated" ]; do
    WAIT=$((WAIT+1))
    STATE=$(curl -s -m 3 http://127.0.0.1/me 2>/dev/null | grep -o '"state":"[a-z_]*"' | head -1 | cut -d'"' -f4)
    if [ "$WAIT" -ge 45 ]; then break; fi
    sleep 2
  done
  if [ "$STATE" = "authenticated" ]; then
    ok "Wrapper up — Apple session restored (lossless ALAC ready)"
  else
    warn "Wrapper is running but not authenticated yet (state: ${STATE:-unknown})."
    say "Open the app → '5 · Wrapper & login' and follow the hints there."
  fi
elif [ "$MIN_MODE" = "1" ]; then
  :
elif [ -d wrapper-v2 ]; then
  warn "Wrapper present but Docker isn't ready — skipping. Lossless ALAC / Atmos will be unavailable until it's up."
fi

# ── 5. App server (reuse if already running) ──────────────────────────
say "Starting the Music High Res app…"
SERVER_PID=""
if curl -s -m 2 -o /dev/null http://127.0.0.1:8741/api/status 2>/dev/null; then
  ok "App server already running"
else
  .venv/bin/python app.py &
  SERVER_PID=$!
fi

# open_url — macOS uses `open`, Linux uses xdg-open. Never fails loudly.
open_url() {
  if [ "$OS" = "Darwin" ]; then
    open "$1" 2>/dev/null
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$1" >/dev/null 2>&1
  fi
}

# When the app closes (Ctrl+C, closing the terminal window, or macOS
# right-click → Quit on the .app bundle — all send TERM/INT), clean up:
#   1. Kill the server *we* started (never a reused one) so app.py doesn't
#      stay orphaned on port 8741.
#   2. Stop the ALAC wrapper — it should only run while the app is open.
#      Docker Desktop itself is left running.
# cleanup() is idempotent, so it's safe to run on EXIT as well.
cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null
    sleep 1   # let our server free port 8741 before probing below
  fi
  # Stop the wrapper only if no OTHER launcher session is still holding the
  # app open (it would still need the wrapper). This covers the reuse case
  # and double-launched sessions uniformly.
  if [ "$WRAPPER_STARTED" = "1" ] && [ -d wrapper-v2 ] && command -v docker >/dev/null 2>&1 \
     && ! curl -s -m 2 -o /dev/null http://127.0.0.1:8741/api/status 2>/dev/null; then
    say "Closing the app — stopping the ALAC wrapper (Docker Desktop stays running)…"
    # Bounded stop: `-t 3` limits the container's grace period to 3s, and the
    # CLI is capped at 8s so a wedged Docker daemon can never hang the close.
    ( cd wrapper-v2 && docker compose stop -t 3 ) >/dev/null 2>&1 &
    local STOPPID=$!
    for _ in 1 2 3 4 5 6 7 8; do
      kill -0 "$STOPPID" 2>/dev/null || break
      sleep 1
    done
    kill "$STOPPID" 2>/dev/null; wait "$STOPPID" 2>/dev/null
  fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT

# Wait for the server before opening the browser.
for _ in $(seq 1 30); do
  if curl -s -m 2 -o /dev/null http://127.0.0.1:8741/api/status 2>/dev/null; then
    break
  fi
  sleep 1
done

if [ "$NO_BROWSER" = "1" ]; then
  say "Skipping browser (--no-browser). UI is at http://127.0.0.1:8741"
elif [ "$APP_STYLE" = "1" ]; then
  # Standalone app-style window (used by the Music High Res.app bundle).
  for BROWSER in "Brave Browser" "Google Chrome" "Microsoft Edge" "Arc"; do
    if [ -d "/Applications/$BROWSER.app" ]; then
      open -na "$BROWSER" --args --app=http://127.0.0.1:8741 2>/dev/null && break
    fi
  done
  open_url http://127.0.0.1:8741
else
  open_url http://127.0.0.1:8741
  say "If the browser didn't open, go to:  http://127.0.0.1:8741"
fi
ok "Running → http://127.0.0.1:8741"

# ── 6. Desktop shortcut (macOS only) ──────────────────────────────────
# On macOS, put a "Music High Res.app" shortcut on the Desktop pointing at the
# app bundle. The bundle is built once (./make_app.sh) if missing; the shortcut
# is just a symlink, so it costs nothing and always launches the latest build.
# Skipped entirely on Linux (where `open` and the .app format don't exist) or
# when MHR_NO_DESKTOP=1 (e.g. automated tests).
if [ "$OS" = "Darwin" ] && [ -d "$HOME/Desktop" ] && [ "${MHR_NO_DESKTOP:-0}" != "1" ]; then
  DESKTOP_APP="$HOME/Desktop/Music High Res.app"
  # `! -e` is true for a *broken* symlink too (target moved/deleted), so a
  # stale shortcut gets repaired. rm -f is safe here: `! -e` guarantees the
  # name is a broken link, never a real file (which would satisfy -e).
  if [ ! -e "$DESKTOP_APP" ]; then
    if [ ! -d "Music High Res.app" ]; then
      say "First run on macOS — building the Music High Res app bundle…"
      if bash make_app.sh; then
        ok "Built Music High Res.app"
      else
        warn "Could not build Music High Res.app — you can still use start.sh / the .command launcher."
      fi
    fi
    if [ -d "Music High Res.app" ]; then
      rm -f "$DESKTOP_APP" 2>/dev/null
      ln -s "$PWD/Music High Res.app" "$DESKTOP_APP" 2>/dev/null && \
        ok "Desktop shortcut created: ~/Desktop/Music High Res.app"
    fi
  fi
fi

# Keep the terminal/app alive while the server runs (so Ctrl+C / Dock-Quit
# stops it). If we reused an existing server, just wait on it too.
if [ -n "$SERVER_PID" ]; then
  wait "$SERVER_PID"
elif curl -s -m 2 -o /dev/null http://127.0.0.1:8741/api/status 2>/dev/null; then
  sleep 999999 &
  HOLD=$!
  wait "$HOLD"
fi
