# Music High Res 2.1.0 — Windows quick start

Download lossless **ALAC (up to 24-bit/192kHz)**, **AAC 256kbps**, or **Dolby
Atmos** music from Apple Music — plus **Spotify** and **YouTube Music** —
into an organized local library for Plex / Jellyfin / Navidrome.

> **⬆ Updating from an older version?** Just copy the new files over your
> existing Music High Res folder — keep your `config.json`, `data/` and
> cookies; they carry over untouched. From v2.0+ the app also updates itself
> via the ⬆ pill in the header (Settings → Check for app updates).

## 1. Install (one command, PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

This installs Python + ffmpeg (via winget), gamdl (via pip), and the app.

## 2. Launch

Double-click **`Start Music High Res.bat`** — or run `start.ps1`
(`-Min` = AAC only, `-NoDocker`, `-NoBrowser`). First launch runs `setup.bat`
automatically and starts Docker Desktop when installed.

## 3. Cookies (Apple Music)

Export cookies from `music.apple.com` ("Get cookies.txt LOCALLY" extension)
→ save as `cookies.txt` in this folder. Needed for AAC without the wrapper.

## 4. Lossless ALAC / Atmos (wrapper, optional)

1. Make sure Docker Desktop is running (WSL2 backend).
2. Install **Git for Windows** (git-scm.com) if you don't have it — the
   wizard uses its **Git Bash** to run the wrapper setup. (WSL also works,
   but Git Bash is preferred: it inherits Windows' `docker` + `jq`.)
3. `setup.bat` → then follow the in-app wizard: **5 · Wrapper & login** →
   **⚙ Setup the wrapper** (give it the Apple Music Android APK path/URL).
   Windows paths like `C:\Users\you\Downloads\apple-music.apk` are handled
   automatically.
4. In the app: Settings → enable **Use wrapper** → pick **ALAC** or **Atmos**.

In **Git Bash**, the **`wrapper`** command works too (`./wrapper status`,
`./wrapper 2fa 123456`) for checking the login state and submitting your
Apple code.

## 5. Download

Paste Apple Music / Spotify / YouTube Music links, pick quality, hit
**Download**. Everything lands in `~/Music/Apple Music` organized as
`Artist/Album/Track`, with cover art + synced lyrics (.lrc).
