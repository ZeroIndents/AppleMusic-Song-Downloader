#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
#  Music High Res — one-command installer (Linux)
#
#  Usage:
#    Run inside the repo:          ./install_linux.sh
#    Fresh machine (bootstrap):    curl -fsSL <raw-url>/install_linux.sh | bash
#
#  What it does:
#    1. Checks you're on Linux (macOS users should use install.sh)
#    2. Installs python3, python3-venv, pip, ffmpeg via your package manager
#    3. Installs gamdl with pip (the cross-platform way on Linux)
#    4. Downloads the repo when run as a bootstrap one-liner
#    5. Creates the Python environment + installs app dependencies
#    6. Prints what's left: export cookies, optional ALAC wrapper (Docker)
#
#  AAC 256kbps works with cookies alone. Lossless ALAC / Dolby Atmos need
#  Docker + an Apple Music Android APK (see README Step 3) — the wrapper
#  build scripts are bash, so Linux handles them natively.
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'; RED=$'\033[1;31m'; BLUE=$'\033[1;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
say()  { echo "${BLUE}→${NC} $*"; }
ok()   { echo "${GREEN}✓${NC} $*"; }
warn() { echo "${YELLOW}!${NC} $*"; }
die()  { echo "${RED}✗${NC} $*" >&2; exit 1; }

REPO_URL="https://github.com/gavinraspberrypi/AppleMusic-Song-Downloader.git"

echo "${BOLD}Music High Res — Linux installer${NC}"
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  die "This is the Linux installer. macOS users: use install.sh instead."
fi
if [ "$OS" != "Linux" ]; then
  die "Unsupported OS: $OS — this installer supports Linux (and macOS via install.sh)."
fi
ok "Linux detected"

# ── 2. Inside the repo or bootstrapping? ────────────────────────────────
if [ -f app.py ] && [ -f README.md ]; then
  IN_REPO=1
  say "Running from inside the repo: $(pwd)"
else
  IN_REPO=0
  say "Bootstrap mode — will download the repo."
fi

# ── 3. System packages (python3, venv, pip, ffmpeg, git) ───────────────
MISSING=""
for tool in python3 git; do
  command -v "$tool" >/dev/null 2>&1 || MISSING="$MISSING $tool"
done
if command -v ffmpeg >/dev/null 2>&1; then
  :
else
  MISSING="$MISSING ffmpeg"
fi

if [ -n "$MISSING" ]; then
  say "Installing:$MISSING via your package manager (may ask for sudo)…"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y -q python3 python3-pip python3-virtualenv ffmpeg git
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm --needed python python-pip ffmpeg git
  else
    die "No supported package manager (apt/dnf/pacman) found — install python3, pip, ffmpeg and git manually, then re-run."
  fi
fi
command -v python3 >/dev/null 2>&1 || die "python3 still missing."
ok "python3, git, ffmpeg present"

# ── 4. gamdl via pip (cross-platform on Linux) ──────────────────────────
if ! command -v gamdl >/dev/null 2>&1; then
  say "Installing gamdl (pip install gamdl)…"
  pip install --user --quiet --upgrade gamdl || pip install --quiet --upgrade gamdl
  # ~/.local/bin may not be on PATH for fresh users.
  if ! command -v gamdl >/dev/null 2>&1 && [ -x "$HOME/.local/bin/gamdl" ]; then
    export PATH="$HOME/.local/bin:$PATH"
    warn "Added ~/.local/bin to PATH for this session — add it to your shell profile:  export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi
command -v gamdl >/dev/null 2>&1 || die "gamdl not on PATH — try:  pip install gamdl"
ok "gamdl $(gamdl --version 2>/dev/null | head -1 || echo 'installed')"

# ── 5. Get the repo ─────────────────────────────────────────────────────
if [ "$IN_REPO" = "0" ]; then
  if [ -d music-high-res ]; then
    say "music-high-res/ exists — pulling latest…"
    git -C music-high-res pull --ff-only --quiet || warn "could not pull; continuing with what's there"
  else
    say "Downloading Music High Res…"
    git clone --depth 1 "$REPO_URL" music-high-res
  fi
  cd music-high-res
  ok "Repo ready: $(pwd)"
fi
[ -f app.py ] || die "app.py not found — something's off with the repo checkout"

# ── 6. Python environment + dependencies ────────────────────────────────
say "Creating Python environment (.venv) + installing dependencies…"
python3 -m venv .venv || die "python3-venv missing — install it (apt: python3-venv, dnf: python3-virtualenv)"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
chmod +x start.sh 2>/dev/null || true
ok "App dependencies installed"

# ── 7. Docker check (needed later only for ALAC / Atmos) ───────────────
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "Docker is running — you can set up the ALAC wrapper whenever you want"
else
  warn "Docker not running/installed — fine for AAC 256kbps; needed later for
       lossless ALAC / Dolby Atmos (see README Step 3)."
fi

# ── 8. Next steps ───────────────────────────────────────────────────────
cat <<EOF

${BOLD}━━━ Setup complete ━━━${NC}
To start using it:

  1. ${BOLD}Export your Apple Music cookies${NC} (one-time):
     Sign in at https://music.apple.com, then export cookies for that site with
     the "Get cookies.txt LOCALLY" browser extension → save as ${BOLD}cookies.txt${NC}
     in this folder:  $(pwd)

  2. ${BOLD}Start the app${NC} (one click):
       ./start.sh                             →  http://127.0.0.1:8741

  3. ${BOLD}Use the CLI${NC} (same engine):
       .venv/bin/python cli.py "https://music.apple.com/us/album/<id>"

  4. ${BOLD}Optional — lossless ALAC / Dolby Atmos${NC} (needs Docker + an
     Apple Music Android APK you obtain yourself):
       ./setup_wrapper.sh /path/to/apple-music.apk
     then enable "Use wrapper" in the app's Settings.

start.sh works on both macOS and Linux. (The .command launcher is macOS-only.)
EOF
