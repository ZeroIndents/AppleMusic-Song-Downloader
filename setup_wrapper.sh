#!/bin/bash
# Music High Res — wrapper-v2 setup (required for lossless ALAC / Dolby Atmos)
#
# Usage:  ./setup_wrapper.sh /path/to/apple-music.apk [--ui] [--fix-libs]
#
#   --ui         Non-interactive mode (used by the web app wizard): never
#                prompts. Credentials, when provided, come from the
#                WRAPPER_USERNAME / WRAPPER_PASSWORD environment variables.
#   --fix-libs   After building, apply fix_wrapper_libs.sh (the Intel-Mac
#                FairPlay symbol fix).
#
# Prerequisites:
#   1. Docker Desktop for Mac installed and running
#   2. An Apple Music for Android APK (3.6.0-beta build 1109 or newer)
#      - legally obtain from a trusted source (e.g. APKMirror, 'Apple Music' app)
#
# This script clones glomatico/wrapper-v2, extracts the Apple Music native
# libraries from your APK, stages the Android runtime, and starts the wrapper
# with Docker Compose. gamdl then connects to it for ALAC decryption.

set -e
cd "$(dirname "$0")"

APK=""
UI_MODE=0
FIX_LIBS=0
for arg in "$@"; do
  case "$arg" in
    --ui)       UI_MODE=1 ;;
    --fix-libs) FIX_LIBS=1 ;;
    *)          APK="$arg" ;;
  esac
done

if [ -z "$APK" ]; then
  echo "Usage: $0 /path/to/apple-music.apk [--ui] [--fix-libs]"
  echo "  (You must supply your own Apple Music for Android APK.)"
  exit 1
fi
if [ ! -f "$APK" ]; then
  echo "✗ APK not found: $APK"
  exit 1
fi

echo "━━━ wrapper-v2 setup ━━━"

if ! command -v docker >/dev/null 2>&1; then
  echo "✗ Docker not found. Install Docker Desktop first:"
  echo "    https://www.docker.com/products/docker-desktop/"
  echo "  Then start Docker Desktop and re-run this script."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "✗ Docker is installed but not running. Start Docker Desktop, wait for"
  echo "  the whale icon to settle, then re-run this script."
  exit 1
fi

echo "→ Cloning wrapper-v2…"
if [ ! -d wrapper-v2 ]; then
  git clone --depth 1 https://github.com/glomatico/wrapper-v2.git
else
  echo "  wrapper-v2 already present, updating…"
  git -C wrapper-v2 pull --quiet || true
fi
cd wrapper-v2

echo "→ Extracting Apple Music libraries from APK (x86_64)…"
bash tools/extract-libs.sh --bundle "$APK" --arch x86_64

echo "→ Staging Android system runtime…"
bash tools/stage-system.sh --arch x86_64

# ---- Apple ID credentials ----
if [ "$UI_MODE" = "1" ]; then
  # Headless (web-app wizard): credentials come from the environment only.
  if [ -n "$WRAPPER_USERNAME" ] && [ -n "$WRAPPER_PASSWORD" ]; then
    echo "→ Writing .env with credentials from the environment…"
    cat > .env <<EOF
WRAPPER_USERNAME=$WRAPPER_USERNAME
WRAPPER_PASSWORD=$WRAPPER_PASSWORD
EOF
  else
    echo "→ No credentials supplied — you can log in later from the app."
  fi
elif [ -n "$WRAPPER_USERNAME" ] && [ -n "$WRAPPER_PASSWORD" ]; then
  echo "→ Writing .env with credentials from your environment…"
  cat > .env <<EOF
WRAPPER_USERNAME=$WRAPPER_USERNAME
WRAPPER_PASSWORD=$WRAPPER_PASSWORD
EOF
else
  if [ ! -f .env ]; then
    echo
    read -r -p "Apple ID email (leave empty to skip): " AID
    if [ -n "$AID" ]; then
      read -r -s -p "Apple ID password: " APW
      echo
      cat > .env <<EOF
WRAPPER_USERNAME=$AID
WRAPPER_PASSWORD=$APW
EOF
    fi
  fi
fi

# The upstream wrapper ships `restart: unless-stopped`, which boots the
# wrapper whenever Docker Desktop starts. Music High Res wants the wrapper to
# run ONLY while the app is open (the launcher starts/stops it), so pin the
# restart policy to "no" — Docker Desktop itself keeps running regardless.
echo "→ Forcing the wrapper to NOT auto-start with Docker…"
if grep -qE '^[[:space:]]*restart:' compose.yaml 2>/dev/null; then
  # Anchored to the line start so comments containing "restart:" are safe.
  sed -i '' 's/^[[:space:]]*restart:.*/    restart: "no"/' compose.yaml 2>/dev/null \
    || sed -i 's/^[[:space:]]*restart:.*/    restart: "no"/' compose.yaml
else
  echo "! compose.yaml has no restart policy — add 'restart: \"no\"' to the wrapper service"
  echo "  if you don't want it to auto-start with Docker."
fi

echo "→ Building and starting wrapper (this first build may take a few minutes)…"
docker compose up --build -d

echo
echo "→ Waiting for the wrapper API…"
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1/me >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo
echo "━━━ wrapper status ━━━"
curl -s http://127.0.0.1/me || echo "(wrapper not answering on http://127.0.0.1/me — check: docker compose logs)"

if [ "$FIX_LIBS" = "1" ]; then
  echo
  echo "→ Applying the Intel-Mac library fix…"
  ( cd .. && bash fix_wrapper_libs.sh )
fi

if [ "$UI_MODE" != "1" ]; then
  echo
  echo "Next:"
  echo "  1. If your account has two-factor auth, you'll get a code — send it once:"
  echo "       curl -X POST http://127.0.0.1/login/2fa -H 'Content-Type: application/json' -d '{\"code\":\"000000\"}'"
  echo "  2. In the Music High Res app, enable 'Use wrapper' in Settings."
  echo "  3. Download ALAC lossless:   gamdl --use-wrapper --song-codec-priority alac \"<url>\""
fi
