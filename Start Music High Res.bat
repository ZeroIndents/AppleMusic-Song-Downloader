@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM  Music High Res — double-click launcher (Windows)
REM  Thin, double-click-friendly wrapper around start.ps1 (the real logic).
REM  Boots Docker + the ALAC wrapper when present, starts the app, and opens
REM  the UI in your browser. Everything is one click.
REM ═══════════════════════════════════════════════════════════════════════
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
