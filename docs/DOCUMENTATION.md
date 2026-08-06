# AppleMusic Song Downloader — Technical Documentation

This document explains how the whole system works end-to-end: the download path,
the wrapper (FairPlay decryption), the FLAC conversion pipeline, the app, the
CLI, and day-to-day operations. It is the "how it actually works" companion to
the README's "how to use it".

---

## 1. Architecture overview

```
┌────────────────────────────────────────────────────────────────────┐
│  Browser (http://127.0.0.1:8741)                                    │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  static/index.html — single-page app (vanilla JS)            │   │
│  └──────────────────────────────────────────────────────────────┘   │
└───────────────┬────────────────────────────────────────────────────┘
                │ HTTP/JSON
┌───────────────▼────────────────────────────────────────────────────┐
│  app.py — Flask server (waitress) on 127.0.0.1:8741                │
│   /api/status · /api/config · /api/download · /api/convert         │
│   /api/jobs · /api/wrapper (+ /2fa, /restart)                      │
└───────────────┬────────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────────┐
│  downloader.py — JobManager (threads)                              │
│   Job → subprocess: gamdl -c cookies.txt -o <out> … <urls>         │
│   Job (convert) → subprocess: ffmpeg (ALAC → FLAC)                 │
└───────┬───────────────────────────────┬────────────────────────────┘
        │                               │
┌───────▼───────────────┐   ┌───────────▼──────────────────────────┐
│  gamdl CLI (brew)     │   │  wrapper-v2 (Docker container)       │
│  — downloads m4a files│   │  — Android runtime + Apple libs      │
│  — for ALAC/Atmos it  │──▶│  — FairPlay license + decryption     │
│    calls the wrapper  │   │  — HTTP on :80, decrypt port :10020  │
└───────────────────────┘   └───────────┬──────────────────────────┘
        │                               │
┌───────▼───────────────────────────────▼──────────────────────────┐
│  ~/Music/Apple Music/{Artist}/{Album}/{Track}.m4a (+ .flac)      │
│  → point your music server (Navidrome/Jellyfin/Plex) at this dir │
└───────────────────────────────────────────────────────────────────┘
```

### Key files

| File | Purpose |
|---|---|
| `app.py` | Flask web server + JSON API |
| `downloader.py` | Config, Job manager, gamdl + ffmpeg subprocess orchestration |
| `cli.py` | Terminal interface using the same `downloader` core |
| `wrapperctl.py` | Wrapper status, log tail, 2FA submit, login restart |
| `static/index.html` | The web UI (no build step — plain HTML/CSS/JS) |
| `config.json` | User settings (created from defaults on first run) |
| `setup.sh` | One-time app setup (venv + pip deps) |
| `setup_wrapper.sh` | One-time wrapper setup (clone, extract libs, stage, build) |
| `fix_wrapper_libs.sh` | Repairs the Intel-Mac FairPlay symbol bug |
| `Start Music High Res.command` | One-click boot: Docker → wrapper → app → browser |

---

## 2. The download path

### 2.1 Cookies mode (AAC 256kbps — no wrapper)
1. You paste an Apple Music URL into the app.
2. `JobManager.start()` creates a `Job` and runs `gamdl -n -c cookies.txt -o <out> …` in a thread.
3. gamdl resolves the URL, gets track metadata + stream URLs using your browser
   cookies (`media-user-token`), then downloads **AAC 256kbps** `.m4a` files,
   embedded artwork, and synced lyrics.
4. The job streams gamdl's stdout into the job log; the UI polls
   `/api/jobs/<id>` and renders it live.

### 2.2 Wrapper mode (ALAC lossless / Dolby Atmos)
1. Same as above, but the command gains `--use-wrapper --wrapper-url http://127.0.0.1`.
2. When gamdl needs a track, it calls the wrapper:
   - `GET /playback?adam_id=<track>` → returns the FairPlay license + keys
   - The wrapper's Android worker performs the **FairPlay decryption** using the
     Apple Music Android libraries staged in its rootfs
3. gamdl assembles the lossless **ALAC** `.m4a` (up to 24-bit/192kHz where the
   label offers it).

> The wrapper is authenticated with your Apple ID (cached session in
> `wrapper-v2/data/`). This is why wrapper mode **does not need cookies** — the
> README note "Cookies can be skipped when using the wrapper".

---

## 3. The wrapper (wrapper-v2) deep dive

### 3.1 What it is
`wrapper-v2` (glomatico) is a Dockerized server that runs the actual Apple Music
**Android** app libraries in a container. It handles the DRM side of lossless
downloads:
- **Login / session** — logs into Apple with your Apple ID, keeps a device-bound
  session (the "device" is the staged Android runtime).
- **Playback API** — for a given `adam_id` (track id) it obtains the FairPlay
  license (CKC) and returns keys.
- **Decryption** — decrypts the encrypted audio stream the license covers.

### 3.2 The stack
- `docker compose up --build` builds a container with:
  - `rootfs/system/lib64/` — 99 Android runtime libraries staged by `stage-system.sh`
  - 18 Apple Music native libraries extracted from your APK by `extract-libs.sh`
    (hash-verified against the repo's pinned `LIBS_VERSION.json`)
  - A supervisor + Android worker process
- Ports: **80** (HTTP API) and **10020** (decryption).
- State: `wrapper-v2/data/` bind-mounted volume persists the Apple session across
  restarts → **no 2FA after the first successful login**.

### 3.3 The Intel-Mac FairPlay bug (`-42812`)
**Symptom:** ALAC download fails with `Fairplay error KDProcessResponseCKC status: -42812`
right after a successful license exchange.

**Root cause:** the pinned Apple Music **3.6.0-beta-1109** libraries are missing a
critical symbol (`dlsym(...,"getPersistentKey"...)` reports `undefined symbol`).
The FairPlay decryption can't complete.

**Fix (in this repo):** `fix_wrapper_libs.sh` swaps 7 `.so` files
(`libCoreFP`, `libCoreLSKD`, `libandroidappmusic`, `libdaapkit`,
`libmedialibrarycore`, `libmediaplatform`, `libstoreservicescore`) with the
working builds from the WorldObservationLog wrapper project, then rebuilds.

**Verification:** after the fix, `docker logs wrapper-v2` no longer shows
`dlsym ... undefined symbol`, and downloads produce real ALAC
(verify: `ffprobe -show_entries stream=codec_name <file>` → `alac`).

### 3.4 2FA login flow
1. `./setup_wrapper.sh` writes `WRAPPER_USERNAME`/`WRAPPER_PASSWORD` to `wrapper-v2/.env`.
2. On container start, the worker auto-logs-in. If 2FA is required it returns
   `auth.state: awaiting_2fa` and asks for a code via `POST /login/2fa {"code": ...}`.
3. Apple delivers the code via **email / SMS / trusted device** — the wrapper
   does not control which channel (there is no "choose SMS" API).
4. ⚠️ Each code is only valid for the login session that issued it — if you let it
   expire, you must restart the login to get a fresh code (the app's
   "↻ Restart login" button does exactly that).
5. After a successful login the session is written to `wrapper-v2/data/` and
   restored on every subsequent start — **2FA is never needed again**.

> Hackintosh gotcha: if your Mac is signed into iCloud, Apple treats it as a
> trusted device and pushes the code *to the Mac*, where it never displays on a
> Hackintosh. Removing the Mac from trusted devices at appleid.apple.com forces
> the code to email/SMS instead.

---

## 4. FLAC conversion pipeline

### 4.1 Why
Apple delivers lossless as **ALAC**. Some servers/libraries prefer **FLAC**.
Converting is **lossless → lossless**: the PCM samples are identical, only the
container/codec wrapper changes. No quality is lost.

### 4.2 How
Per file, `downloader.py`:
1. **Detects** the codec with `ffprobe -select_streams a:0 -show_entries stream=codec_name`.
   Only true `alac` files are converted — AAC downloads are skipped (lossy → FLAC
   would waste disk for zero benefit).
2. Runs:
   ```
   ffmpeg -hide_banner -loglevel error -y -i input.m4a \
     -map 0:a -map 0:v? -c:a flac -c:v copy \
     -map_metadata 0 -map_chapters -1 output.flac
   ```
   - `-c:a flac` — lossless FLAC audio
   - `-c:v copy` — embedded cover art copied without re-encoding
   - `-map_metadata 0` — all tags carried over
   - `-map_chapters -1` — chapters dropped (FLAC has no writer in ffmpeg)
3. Original `.m4a` stays; a `.flac` sibling is created. Re-runs skip files that
   already have a `.flac` unless `--overwrite-flac`/Overwrite is set.

### 4.3 Automatic mode
With Settings → **"Auto-convert ALAC → FLAC"** enabled:
- Before launching gamdl, the job snapshots every existing `.m4a` path under the
  output folder.
- After a successful download, only the **new** files are converted
  (`auto_convert_new_files`). Existing libraries are never re-scanned.

---

## 4.5 The migration pipeline (Spotify / YouTube Music → Apple Music)

Card "5 · Migrate" in the UI turns a foreign-service link into Apple Music
downloads:

1. **Resolve** (`migrate.py::preview`):
   - **Spotify** — `open.spotify.com/embed/{album|playlist}/{id}` renders the
     track list server-side; its `__NEXT_DATA__` JSON is parsed for
     `title`/`subtitle` (artists) per track. No auth.
   - **YouTube / YouTube Music** — `yt-dlp` (venv) is invoked in flat mode
     (`extract_flat`, `skip_download`): it only reads playlist/album metadata,
     never downloads audio. Titles are normalized ("Artist — Song [HD]" →
     artist/title).
2. **Match** — each track is searched on the iTunes Search API
   (`itunes.apple.com/search?entity=song`). A score rewards an exact title
   match (+3) and an artist match (+2); punctuation is normalized so
   "Weird Fishes/Arpeggi" matches "Weird Fishes / Arpeggi". Tracks scoring ≥3
   are considered matched; the Apple `trackViewUrl` is returned.
3. **Download** — the UI hands the matched Apple Music URLs to the existing
   `/api/download` endpoint, so everything flows through the normal gamdl
   pipeline (ALAC via wrapper, auto-FLAC, lyrics, tags).

Errors are surfaced per-track ("no match") and the user can uncheck rows before
queuing. Both sources are metadata-only — no media is ever downloaded from
Spotify or YouTube.

---

## 5. The web app

- Flask + `waitress` on `127.0.0.1:8741` (loopback only — not exposed to your LAN).
- Single-page UI in `static/index.html` — no build step, no node_modules.
- Pills show live status: gamdl, FFmpeg, Cookies, Wrapper.
- The **Wrapper & login** panel polls `/api/wrapper` every 5s:
  auth state, playback readiness, 2FA code box, restart button, live logs.
- Downloads render as cards with expandable live logs; active jobs are polled
  every 1.5s; finished jobs can be cleared.
- Config is editable in Settings and saved to `config.json`.

---

## 6. One-click boot

Two launchers exist:

- **`Start Music High Res.command`** — Terminal-based boot.
- **`Music High Res.app`** (built by `./make_app.sh`) — a real macOS app bundle
  with a Dock icon. Its `Contents/MacOS/MusicHighRes` launcher boots the stack
  then opens the UI in a standalone app-style window via
  `open -na "<browser>" --args --app=http://127.0.0.1:8741` (Brave, then
  Chrome/Edge/Arc, then Safari). Killing the app (right-click → Quit) stops the
  server too.

After a reboot, one double-click:
1. **Docker Desktop** — if the daemon isn't up, launches the app and polls up to
   4 minutes (first boot is slow).
2. **Wrapper** — `docker compose up -d` in `wrapper-v2/`, then waits for
   `auth.state == "authenticated"` (session restores from disk — no 2FA).
3. **App** — starts `app.py` and opens the browser once the API responds.

Docker Desktop also **auto-starts at login** via the LaunchAgent
`~/Library/LaunchAgents/com.musichighres.docker.plist`
(`RunAtLoad` → `open -a Docker`). Remove it with
`launchctl bootout gui/$(id -u)/com.musichighres.docker`.

---

## 7. Operations reference

```bash
# Status
curl http://127.0.0.1/health            # wrapper alive?
curl http://127.0.0.1/me                # auth state + account
docker ps                               # is wrapper-v2 up?

# Wrapper logs
docker logs --tail 50 wrapper-v2        # (grep -v dlsym to silence noise)

# Restart wrapper cleanly
cd wrapper-v2 && docker compose up -d --force-recreate

# Rebuild after the lib fix
./fix_wrapper_libs.sh

# Stop everything
docker compose -f wrapper-v2/compose.yaml down   # wrapper
pkill -f 'app.py'                                # app
```

---

## 8. Security notes

- **`cookies.txt` is a live Apple session** — never commit it, never share it.
  It is in `.gitignore`.
- **`wrapper-v2/.env` holds your Apple ID password** — also gitignored
  (`wrapper-v2/` is entirely excluded from the repo).
- The wrapper's cached session (`wrapper-v2/data/`) is equally sensitive.
- The app binds to **127.0.0.1 only** — it is not reachable from other devices.

## 9. Compliance

This project exists for **personal, experimental interoperability** with content
you already pay to stream. It deliberately carries a non-commercial license (see
LICENSE). Use it only for your own library; comply with the Apple Media Services
Terms and your local laws. If Apple or any rightsholder objects, this project
will be withdrawn or modified accordingly.
