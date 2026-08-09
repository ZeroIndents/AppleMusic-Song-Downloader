#!/bin/bash
# Music High Res — one-time setup
# Creates a Python venv and installs the web app dependencies.
set -e
cd "$(dirname "$0")"

echo "━━━ Music High Res setup ━━━"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ python3 not found. Install Python 3.10+ first."
  exit 1
fi

echo "→ Creating Python environment (.venv)…"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "→ Checking gamdl…"
if command -v gamdl >/dev/null 2>&1; then
  gamdl --version
  # gamdl is the most fragile dependency: `brew upgrade` can silently swap it
  # and break ALAC downloads (Apple changes the backend constantly). Pin it so
  # it only changes when you explicitly unpin + upgrade it. (See README.)
  if command -v brew >/dev/null 2>&1 && brew list --versions gamdl >/dev/null 2>&1; then
    brew pin gamdl 2>/dev/null && echo "  → pinned gamdl (brew pin) — won't auto-update"
  fi
else
  echo "  ! gamdl binary not on PATH — install it with:  brew install gamdl  (or pip install gamdl)"
fi

echo
echo "✓ Setup complete."
echo
echo "Next steps:"
echo "  1.  Export your Apple Music cookies (see README.md) and save them as cookies.txt in this folder."
echo "  2.  Start the app:   open 'Start Music High Res.command'   (or run: .venv/bin/python app.py)"
echo "  3.  Or use the CLI:  .venv/bin/python cli.py \"https://music.apple.com/...\""
echo
echo "Optional (lossless ALAC / Dolby Atmos):"
echo "  4.  Install Docker Desktop, grab an Apple Music Android APK, then run:  ./setup_wrapper.sh /path/to/apple-music.apk"
