# Music High Res 2.1.1 — Linux quick start

Download lossless **ALAC (up to 24-bit/192kHz)**, **AAC 256kbps**, or **Dolby
Atmos** music from Apple Music — plus **Spotify** and **YouTube Music** —
into an organized local library for Plex / Jellyfin / Navidrome.

> **⬆ Updating from an older version?** Just copy the new files over your
> existing Music High Res folder — keep your `config.json`, `data/` and
> cookies; they carry over untouched. From v1.3+ the app also updates itself
> via the ⬆ pill in the header (Settings → Check for app updates).

## 1. Install (one command)

```bash
./install_linux.sh       # apt/dnf/pacman: python3-venv, ffmpeg, gamdl via pip
```

Or by hand: `sudo apt install python3 python3-venv ffmpeg` (Debian/Ubuntu) and
`pip install gamdl`.

## 2. Set up + launch

```bash
./setup.sh          # creates .venv + installs Python dependencies
./start.sh          # one-click launcher: checks deps, starts the app, opens
                    # the browser (works on GNOME/KDE/xfce via xdg-open)
```

## 3. Cookies (Apple Music)

Export cookies from `music.apple.com` ("Get cookies.txt LOCALLY" extension)
→ save as `cookies.txt` in this folder. Needed for AAC without the wrapper.

## 4. Lossless ALAC / Atmos (wrapper, optional)

1. Make sure Docker is running (install Docker Engine, or Docker Desktop on
   distros that support it).
2. `./setup_wrapper.sh /path/to/apple-music.apk` (an Apple Music **Android**
   APK, 3.6.0-beta build 1109+).
3. In the app: Settings → enable **Use wrapper** → pick **ALAC** or **Atmos**.

Or use the in-app wizard: **5 · Wrapper & login** → **⚙ Setup the wrapper**.

The **`wrapper`** command (installed by setup to `~/.local/bin`, or
`./wrapper` from this folder) shows the login state and submits your Apple
code from the terminal: `wrapper status`, `wrapper 2fa 123456`.

## 5. Download

Paste Apple Music / Spotify / YouTube Music links, pick quality, hit
**Download**. Everything lands in `~/Music/Apple Music` organized as
`Artist/Album/Track`, with cover art + synced lyrics (.lrc).
