# Music High Res 2.1.5 — macOS quick start

Download lossless **ALAC (up to 24-bit/192kHz)**, **AAC 256kbps**, or **Dolby
Atmos** music from Apple Music — plus **Spotify** and **YouTube Music** —
into an organized local library for Plex / Jellyfin / Navidrome.

> **⬆ Updating from an older version?** Just copy the new files over your
> existing Music High Res folder — keep your `config.json`, `data/` and
> cookies; they carry over untouched. From v1.3+ the app also updates itself
> via the ⬆ pill in the header (Settings → Check for app updates).

## 1. Prerequisites (one command)

```bash
brew install gamdl ffmpeg
```

(`install.sh` does Homebrew + gamdl + ffmpeg + the app all at once.)

## 2. Set up + launch

```bash
./setup.sh          # creates .venv + installs Python dependencies
./start.sh          # one-click launcher: checks deps, starts Docker,
                    # starts the app, opens the browser
```

Or double-click **`Start Music High Res.command`** (macOS), or run
`./make_app.sh` once and double-click **`Music High Res.app`**.

The ALAC wrapper (for lossless / Atmos) runs **only while the app is open** —
launching the app starts it, closing the app stops it. **Docker Desktop
itself stays running.**

## 3. Cookies (Apple Music)

Export cookies from `music.apple.com` ("Get cookies.txt LOCALLY" extension)
→ save as `cookies.txt` in this folder. Needed for AAC without the wrapper.

## 4. Lossless ALAC / Atmos (wrapper, optional)

1. Make sure Docker Desktop is running.
2. `./setup_wrapper.sh /path/to/apple-music.apk` (an Apple Music **Android**
   APK, 3.6.0-beta build 1109+).
3. In the app: Settings → enable **Use wrapper** → pick **ALAC** or **Atmos**.

**Control it from the terminal** — the `wrapper` command is installed by
setup (`~/.local/bin/wrapper`): `wrapper status` shows the login state,
`wrapper 2fa 123456` submits your Apple code, `wrapper start`/`stop`
start/stop the container (Docker Desktop stays running).

Or use the in-app wizard: **5 · Wrapper & login** → **⚙ Setup the wrapper**
(no Terminal needed — it even accepts the APK as a URL).

## 5. Download

Paste Apple Music / Spotify / YouTube Music links, pick quality, hit
**Download**. Everything lands in `~/Music/Apple Music` organized as
`Artist/Album/Track`, with cover art + synced lyrics (.lrc).
