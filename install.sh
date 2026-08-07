#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
#  Music High Res — one-command installer  (macOS only)
#
#  Usage:
#    Run inside the repo:          ./install.sh
#    Fresh machine (bootstrap):    curl -fsSL <raw-url>/install.sh | bash
#
#  What it does:
#    1. Checks you're on macOS (Linux support is coming soon)
#    2. Installs Homebrew + git, python3, ffmpeg, gamdl when missing
#    3. Downloads the repo when run as a bootstrap one-liner
#    4. Creates the Python environment + installs app dependencies
#    5. Prints what's left: export cookies, optional ALAC wrapper
#
#  It does NOT touch your Apple ID, cookies, or the wrapper — those stay
#  manual by design (see README "Quick start").
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'; RED=$'\033[1;31m'; BLUE=$'\033[1;34m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'
say()  { echo "${BLUE}→${NC} $*"; }
ok()   { echo "${GREEN}✓${NC} $*"; }
warn() { echo "${YELLOW}!${NC} $*"; }
die()  { echo "${RED}✗${NC} $*" >&2; exit 1; }

REPO_URL="https://github.com/gavinraspberrypi/AppleMusic-Song-Downloader.git"

# ── 1. Platform gate ────────────────────────────────────────────────────
echo "${BOLD}Music High Res — installer${NC}"
OS="$(uname -s)"
if [ "$OS" != "Darwin" ]; then
  die "This installer currently supports macOS only.
  Linux support is coming soon — the app itself is plain Python, so it will
  likely run there; the wrapper (ALAC/Atmos) needs Docker + Android libs."
fi
ARCH="$(uname -m)"   # arm64 (Apple Silicon) | x86_64 (Intel)
case "$ARCH" in
  arm64) ARCH_LABEL="Apple Silicon (arm64)" ;;
  x86_64) ARCH_LABEL="Intel (x86_64)" ;;
  *) ARCH_LABEL="$ARCH" ;;
esac
ok "macOS detected — $ARCH_LABEL"

# ── 2. Are we inside the repo already, or bootstrapping? ───────────────
if [ -f app.py ] && [ -f README.md ]; then
  IN_REPO=1
  say "Running from inside the repo: $(pwd)"
else
  IN_REPO=0
  say "Bootstrap mode — will download the repo."
fi

# ── 3. Homebrew ─────────────────────────────────────────────────────────
if ! command -v brew >/dev/null 2>&1; then
  warn "Homebrew not found — installing it (this can take a few minutes, may ask for your password)…"
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Homebrew's bin dir differs per-arch; re-source so `brew` works immediately.
  if [ "$ARCH" = "arm64" ] && [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
  command -v brew >/dev/null 2>&1 || die "Homebrew install failed — install it manually from https://brew.sh"
fi
ok "Homebrew ready"

# ── 4. Prerequisites (git, python3, ffmpeg, gamdl) ─────────────────────
# gamdl is the fragile one: brew can silently swap it on `brew upgrade`, so we
# pin it after install (README explains). ffmpeg is needed for FLAC conversion.
MISSING=""
for tool in git python3 ffmpeg gamdl; do
  command -v "$tool" >/dev/null 2>&1 || MISSING="$MISSING $tool"
done
if [ -n "$MISSING" ]; then
  say "Installing:$MISSING via Homebrew…"
  brew install git python3 ffmpeg gamdl
fi
for tool in git python3 ffmpeg gamdl; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool still not on PATH — try:  brew install $tool"
done
ok "git, python3, ffmpeg, gamdl all present"
say "  gamdl version: $(gamdl --version 2>/dev/null | head -1 || echo 'unknown')"
if command -v brew >/dev/null 2>&1 && brew list --versions gamdl >/dev/null 2>&1; then
  brew pin gamdl 2>/dev/null && say "  pinned gamdl (brew pin) — won't auto-update on brew upgrade"
fi

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
if ! python3 -c 'import venv' >/dev/null 2>&1; then
  die "python3 has no venv module. Install the full Python via:  brew install python3"
fi
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
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

  2. ${BOLD}Start the app${NC}:
       open 'Start Music High Res.command'      (double-click friendly)
     or run:
       .venv/bin/python app.py                  →  http://127.0.0.1:8741

  3. ${BOLD}Use the CLI${NC} (same engine):
       .venv/bin/python cli.py "https://music.apple.com/us/album/<id>"

  4. ${BOLD}Optional — lossless ALAC / Dolby Atmos${NC} (needs Docker + an
     Apple Music Android APK you obtain yourself):
       ./setup_wrapper.sh /path/to/apple-music.apk
     then enable "Use wrapper" in the app's Settings.

Remember: macOS only for now — Linux support is coming soon.
EOF
