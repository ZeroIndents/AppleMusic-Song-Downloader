#!/bin/bash
# Double-click me after a reboot: boots Docker Desktop, starts the ALAC wrapper,
# then starts the Music High Res app and opens the UI in your browser.
# Everything is one click — nothing needs to be re-setup after a reboot.
cd "$(dirname "$0")"

say()  { echo "▪ $1"; }
ok()   { echo "✓ $1"; }
fail() { echo "✗ $1"; }

if [ ! -d .venv ]; then
  echo "First run — setting up…"
  bash setup.sh
fi

# ── 1. Docker Desktop ─────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "Docker is already running"
else
  say "Launching Docker Desktop… (first launch after a reboot takes a minute)"
  open -a Docker 2>/dev/null || open -a /Applications/Docker.app 2>/dev/null
  WAIT=0
  until docker info >/dev/null 2>&1; do
    WAIT=$((WAIT+1))
    if [ "$WAIT" -ge 120 ]; then
      fail "Docker did not start in time. Open Docker Desktop manually, then re-run this script."
      break
    fi
    sleep 2
  done
  docker info >/dev/null 2>&1 && ok "Docker is up"
fi

# ── 2. Start the ALAC wrapper (session auto-restores, no 2FA) ────────
if docker info >/dev/null 2>&1; then
  say "Starting the ALAC wrapper…"
  ( cd wrapper-v2 && docker compose up -d )
  sleep 3
  WAIT=0
  STATE=""
  until [ "$STATE" = "authenticated" ]; do
    WAIT=$((WAIT+1))
    STATE=$(curl -s -m 3 http://127.0.0.1/me 2>/dev/null | grep -o '"state":"[a-z_]*"' | head -1 | cut -d'"' -f4)
    if [ "$WAIT" -ge 45 ]; then break; fi
    sleep 2
  done
  if [ "$STATE" = "authenticated" ]; then
    ok "Wrapper up — Apple session restored (lossless ALAC ready)"
  else
    fail "Wrapper is running but not authenticated yet (state: ${STATE:-unknown})."
    say "Open the app → '3 · Wrapper & login' and follow the hints there."
  fi
fi

# ── 3. Start the app + open browser ──────────────────────────────────
say "Starting the Music High Res app…"
.venv/bin/python app.py &
SERVER_PID=$!

# Wait until the server answers before opening the browser.
for _ in $(seq 1 30); do
  if curl -s -o /dev/null http://127.0.0.1:8741/api/status 2>/dev/null; then
    open http://127.0.0.1:8741 2>/dev/null
    break
  fi
  sleep 1
done

wait "$SERVER_PID"
