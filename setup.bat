@echo off
REM Music High Res — one-time setup (Windows). Thin wrapper around setup.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
