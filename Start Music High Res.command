#!/bin/bash
# Double-click me to start Music High Res: boots Docker + the ALAC wrapper,
# starts the app, and opens the UI in your browser. Everything is one click —
# nothing needs to be re-setup after a reboot.
#
# This is just a thin, double-click-friendly wrapper around the real launcher
# (start.sh), which works on both macOS and Linux. See start.sh for details.
cd "$(dirname "$0")"
exec bash start.sh "$@"
