#!/bin/bash
# Music High Res — amdl wrapper setup (itouakirai/wrapper + zhaarey/apple-music-downloader)
#
# Usage:  ./setup_amdl_wrapper.sh [--login] [--start] [--ui]
#
#   --login   Log in with your Apple ID (prompts, or reads WRAPPER_USERNAME /
#             WRAPPER_PASSWORD from the environment). With 2FA, the wrapper
#             waits up to 60s for the code — write it to wrapper-amdl/rootfs/data/2fa.txt
#             (the web app's Wrapper panel does this for you).
#   --start   After setup, start the persistent wrapper (ports 10020 + 20020).
#   --ui      Non-interactive: no prompts; credentials come from the env only.
#
# Switches the Apple engine to "amdl" (zhaarey/apple-music-downloader). The
# amdl wrapper and the glomatico wrapper-v2 BOTH use port 10020, so only one
# can run at a time — this script stops wrapper-v2 when it starts the amdl
# wrapper. To go back, stop the amdl wrapper and restart wrapper-v2
# (cd wrapper-v2 && docker compose up -d), then set Settings → Apple engine →
# "gamdl".
#
# Requires: Docker Desktop for Mac running, an active Apple Music subscription.

set -e
cd "$(dirname "$0")"

LOGIN=0
START=0
UI_MODE=0
for arg in "$@"; do
  case "$arg" in
    --login) LOGIN=1 ;;
    --start) START=1 ;;
    --ui)    UI_MODE=1 ;;
    -h|--help)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 1 ;;
  esac
done

AMDL_DIR="wrapper-amdl"
DATA_DIR="$AMDL_DIR/rootfs/data"
WRAPPER_IMAGE="ghcr.io/itouakirai/wrapper:x86"
DL_IMAGE="ghcr.io/zhaarey/apple-music-downloader"

echo "━━━ amdl wrapper setup ━━━"

if ! command -v docker >/dev/null 2>&1; then
  echo "✗ Docker not found. Install Docker Desktop first:"
  echo "    https://www.docker.com/products/docker-desktop/"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "✗ Docker is installed but not running. Start Docker Desktop, wait for"
  echo "  the whale icon to settle, then re-run this script."
  exit 1
fi

# 1. Data dir + the 2FA code file (must exist so the /data bind mounts a file).
mkdir -p "$DATA_DIR"
touch "$DATA_DIR/2fa.txt"

# 2. Pull the images.
echo "→ Pulling the itouakirai wrapper (first run downloads ~1 GB)…"
docker pull "$WRAPPER_IMAGE"
echo "→ Pulling the amdl downloader image…"
docker pull "$DL_IMAGE"

# 3. Stop wrapper-v2 (port 10020 clash) if it's running.
if docker ps --format '{{.Names}}' | grep -qx 'wrapper-v2'; then
  echo "→ Stopping wrapper-v2 (it shares port 10020 with the amdl wrapper)…"
  if [ -d wrapper-v2 ]; then
    (cd wrapper-v2 && docker compose down) || docker rm -f wrapper-v2 || true
  else
    docker rm -f wrapper-v2 || true
  fi
fi

# 4. Login (optional).
if [ "$LOGIN" = "1" ]; then
  if [ "$UI_MODE" = "1" ]; then
    USERNAME="${WRAPPER_USERNAME:-}"
    PASSWORD="${WRAPPER_PASSWORD:-}"
  else
    read -rp "Apple ID email: " USERNAME
    read -rsp "Password (hidden): " PASSWORD; echo
  fi
  if [ -n "$USERNAME" ] && [ -n "$PASSWORD" ]; then
    echo "→ Logging in… (if 2FA is required, the wrapper waits for a code in"
    echo "  $DATA_DIR/2fa.txt — the app's Wrapper panel writes it for you)"
    # Fresh login ⇒ clear any stale code from a previous attempt.
    : > "$DATA_DIR/2fa.txt"
    # Credentials via a 0600 env file (not -e args=… on the CLI, which would
    # show the password in `ps` / `docker inspect`).
    printf 'args=-L %s:%s -F -H 0.0.0.0\n' "$USERNAME" "$PASSWORD" > "$AMDL_DIR/login.env"
    chmod 600 "$AMDL_DIR/login.env"
    docker rm -f amdl-login 2>/dev/null || true
    docker run --rm --name amdl-login \
      --env-file "$AMDL_DIR/login.env" \
      -v "$PWD/$DATA_DIR:/app/rootfs/data" \
      -v "$PWD/$DATA_DIR:/data" \
      "$WRAPPER_IMAGE" || true
    echo "✓ Login container finished."
  else
    echo "→ No credentials — skipping login. You can log in later from the app's Wrapper panel."
  fi
fi

# 5. Start the persistent wrapper (optional).
if [ "$START" = "1" ]; then
  echo "→ Starting the amdl wrapper (ports 10020 + 20020)…"
  docker rm -f amdl-wrapper amdl-login 2>/dev/null || true
  docker run -d --name amdl-wrapper \
    -v "$PWD/$DATA_DIR:/app/rootfs/data" \
    -v "$PWD/$DATA_DIR:/data" \
    -p 10020:10020 -p 20020:20020 \
    -e "args=-M 20020 -H 0.0.0.0" \
    "$WRAPPER_IMAGE"
  echo "✓ amdl wrapper running."
fi

echo
echo "━━━ done ━━━"
echo "Next:"
echo "  1. In the app, set Settings → Apple engine → amdl, then log in from the"
echo "     Wrapper panel (or ./setup_amdl_wrapper.sh --login) and hit Start."
echo "  2. Download ALAC / Atmos as usual — amdl streams + decrypts in one pass."
echo "  3. To go back to gamdl: stop the amdl wrapper (app panel), then"
echo "     cd wrapper-v2 && docker compose up -d, and set the engine back."
