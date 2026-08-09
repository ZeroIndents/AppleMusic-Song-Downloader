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
│   /api/status · /api/onboarding · /api/config · /api/download      │
│   /api/convert · /api/jobs (+ /retry) · /api/library(?q=, + /open) │
│   /api/url-preview (owned) · /api/wrapper (+ /2fa, /restart)       │
└───────────────┬────────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────────┐
│  downloader.py — JobManager (threads)                              │
│   Job → subprocess: gamdl -c cookies.txt -o <out> … <urls>         │
│                | amdl (docker run … <urls>) — apple engine #2    │
│                | votify -c <spotify cookies> -o <out> … <urls>     │
│                | gytmdl -i <itag> -o <out> … <urls>                │
│                (URL source auto-routes: apple|spotify|youtube)     │
│   Job (convert) → subprocess: ffmpeg (ALAC → FLAC)                 │
└───────┬───────────────────────────────┬────────────────────────────┘
        │                               │
┌───────▼───────────────┐   ┌───────────▼──────────────────────────┐
│  gamdl CLI (brew)     │   │  wrapper-v2 (Docker container)       │
│  — downloads m4a files│   │  — Android runtime + Apple libs      │
│  — for ALAC/Atmos it  │──▶│  — FairPlay license + decryption     │
│    calls the wrapper  │   │  — HTTP on :80, decrypt port :10020  │
│                       │   └───────────────────────────────┬──────┘
│  amdl (docker, alt.)  │   ┌───────────────────────────────▼──────┐
│  — Go engine + own    │   │  itouakirai/wrapper (amdl mode)      │
│    wrapper, only one  │◀──│  — TCP :10020 + :20020, no HTTP API  │
│    Apple wrapper runs │   │  — 2FA via code file wrapper-amdl/   │
│    at a time (10020)  │   │    rootfs/data/2fa.txt                │
└───────────────────────┘   └───────────┬──────────────────────────┘
        │                               │
┌───────▼───────────────┐   ┌───────────▼──────────────────────────┐
│  votify (pip, venv)   │   │  gytmdl (pip, venv)                  │
│  — Spotify downloads  │   │  — YouTube Music downloads           │
│  — OGG Vorbis 160/320k│   │  — m4a AAC 128k / Opus 256k          │
│  — needs Spotify      │   │  — premium itags need YouTube cookies │
│    cookies file       │   │    + the web_music client             │
└───────────────────────┘   └──────────────────────────────────────┘
        │                               │
┌───────▼───────────────────────────────▼──────────────────────────┐
│  ~/Music/Apple Music/{Artist}/{Album}/{Track}.m4a (+ .flac)      │
│  ~/Music/Apple Music/Playlists/{Artist}/{Title}.m3u (+ copied dir) │
│  → point your music server (Navidrome/Jellyfin/Plex) at this dir │
└───────────────────────────────────────────────────────────────────┘
```

### Key files

| File | Purpose |
|---|---|
| `app.py` | Flask web server + JSON API |
| `downloader.py` | Config, Job manager, gamdl/votify/gytmdl + ffmpeg subprocess orchestration |
| `cli.py` | Terminal interface using the same `downloader` core |
| `wrapperctl.py` | Wrapper status, log tail, 2FA submit, login restart (wrapper-v2 **and** amdl) |
| `static/index.html` | The web UI (no build step — plain HTML/CSS/JS) |
| `config.json` | User settings (created from defaults on first run) |
| `setup.sh` / `setup.ps1` | One-time app setup (venv + pip deps) — bash for macOS/Linux, PowerShell for Windows |
| `setup_wrapper.sh` | One-time wrapper setup (clone, extract libs, stage, build) — bash; on Windows needs Git Bash / WSL |
| `setup_amdl_wrapper.sh` | amdl engine setup (pull amdl + itouakirai wrapper images) |
| `fix_wrapper_libs.sh` | Repairs the Intel-Mac FairPlay symbol bug |
| `start.sh` | **Universal one-click launcher** (macOS + Linux) — first-run setup, prerequisite checklist, Docker → wrapper → app → browser |
| `start.ps1` | **Windows one-click launcher** (PowerShell twin of `start.sh`, flags `-Min` / `-NoBrowser` / `-NoDocker`) |
| `Start Music High Res.command` | macOS double-click launcher (thin wrapper around `start.sh`) |
| `Start Music High Res.bat` | Windows double-click launcher (thin wrapper around `start.ps1`) |
| `make_app.sh` | Builds `Music High Res.app` (Dock icon, standalone window; launcher delegates to `start.sh --app-style`) |
| `install.sh` / `install_linux.sh` / `install.ps1` | One-command installers — macOS / Linux / Windows (prereqs + repo + venv) |
| `logs/` | `app.log` (rotating server log) + `launcher.log` (startup output) |

---

## 2. The download path

### 2.0 Ownership detection, library URLs & retries

- **`/api/url-preview` returns `owned`** — `downloader.py::owned_info` first
  consults the **SQLite ledger** (`data/library.sqlite`, see §2.6): for songs
  and albums it matches per-track rows by (title + artist) / (album + artist)
  and counts the ones that still exist on disk — an **exact** ownership
  answer. Only when the ledger knows nothing about an item (pre-ledger
  libraries) does it fall back to the folder-scan heuristics
  (`{album_artist}/{album}` folders, `Playlists/{artist}/{title}.m3u`). Either
  way the UI shows green **✓ owned n/n** chips and the Download button can
  skip fully-owned links when Settings → **"Skip already-downloaded links"**
  is on.
- **Library URLs** (`music.apple.com/{cc}/library/{songs|albums|playlist}/{id}`)
  are recognized by the preview (chip says "library · …") and passed straight
  to gamdl, which supports them natively. Since gamdl skips files that already
  exist, re-running the same library URL later acts as a **new-additions delta**.
- **`POST /api/jobs/<id>/retry`** re-queues a finished job with its original
  URLs and codec — the UI exposes it as a **↻ Retry** button on failed/cancelled
  cards.

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

### 2.3 Multi-engine routing (Spotify / YouTube Music)

The same job system also drives glomatico's **votify** (Spotify) and **gytmdl**
(YouTube Music) CLIs — both pip-installed into the venv (pinned in
`requirements.txt`) and resolved via `downloader.py::venv_bin` (the app server
runs under `.venv/bin/python`, which does not put `.venv/bin` on PATH for child
processes, so commands use the absolute path).

1. **Classification** — `url_engine()` maps each URL: `open.spotify.com` /
   `spotify.link` → `spotify`; `youtube.com` / `youtu.be` → `youtube`; anything
   else → `apple`. A batch mixing sources is rejected with a clear error (one
   source per job). `Job.engine` is set and surfaced in `/api/jobs` and the UI.
2. **Command builders** — `build_votify_command` (`-n -c <cookies> -o <out>
   --audio-quality <q>` where q is a priority list, `160` free / `320,160`
   Premium) and `build_gytmdl_command` (`-n -i <itag> -o <out>`, itag `140` =
   AAC 128k free, `141`/`774` = Premium AAC/Opus 256k). Cookies:
   - **gytmdl** gets `-c` only when a YouTube-specific cookies path is
     configured — it never falls back to the main `cookies.txt` (that's an
     Apple export, and passing ANY cookies file makes gytmdl switch to the
     `web_music` client, which needs a PO token).
   - **votify** prefers its own Spotify cookies path, falls back to the main
     cookies file with a log note, and the job **fails fast** with a hint if
     the resolved file doesn't exist (votify cannot work without a session).
3. **Silent-failure guard** — votify/gytmdl exit 0 even when every track fails
   (they log errors and keep going). For non-Apple engines the job snapshots
   all audio files before starting, and after exit 0 verifies new files
   appeared: zero new files + error lines in the log ⇒ job is marked **failed**
   with a warning instead of a lying "Done". `_LOG_LINE_RE` also matches the
   `[LEVEL    HH:MM:SS] …` format both CLIs log, so job log levels are right.
4. **Post-processing** — the FLAC/playlist/quality steps are gamdl-only (they
   assume `.m4a`/`.m3u` output); votify (`.ogg`) and gytmdl (`.m4a`/`.opus`)
   jobs skip them and land in the same `Artist/Album` folders. `.ogg`/`.opus`
   were added to `AUDIO_EXTS`, so the new-files guard, the Library scan and
   the in-app player all see Spotify/YouTube output too.

`/api/url-preview` also understands non-Apple links: Spotify albums/playlists
resolve via the embed page (`migrate.resolve_spotify`), YouTube via yt-dlp flat
metadata (`migrate.resolve_youtube`) — chips show kind + track count with a
`source` tag, and the 30s-preview button stays Apple-only.

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

### 3.5 Guided setup + login from the web UI (no Terminal)

New users don't need Terminal at all. The **Wrapper & login** panel ships a
small wizard:

- `GET /api/wrapper/setup` — environment facts (Docker installed/running,
  Apple Silicon vs Intel, whether `wrapper-v2/` exists) + setup-job state.
- `POST /api/wrapper/setup {apk, email?, password?, apply_fix?}` — runs
  `setup_wrapper.sh --ui` (non-interactive; credentials come from the
  environment only) in a background thread (`wrapperctl.SetupManager`), with a
  capped log buffer the UI polls — the same pattern as download jobs. An APK
  **URL** is downloaded first (best-effort); otherwise it's treated as a local
  file path. `--fix-libs` appends `fix_wrapper_libs.sh` for Intel Macs.
- `POST /api/wrapper/login {email, password}` — writes `wrapper-v2/.env`
  (0600) and restarts the container: the browser version of `login_wrapper.sh`.

The UI then guides the 2FA step: the code box auto-focuses and auto-submits on
6 digits, shows a spinner while submitting, shakes on rejection, and the
"resend" button cools down 30s (Apple rate-limits rapid retries).

> Hackintosh gotcha: if your Mac is signed into iCloud, Apple treats it as a
> trusted device and pushes the code *to the Mac*, where it never displays on a
> Hackintosh. Removing the Mac from trusted devices at appleid.apple.com forces
> the code to email/SMS instead.

### 3.6 The amdl engine (Settings → Apple engine = "amdl")

**[amdl](https://github.com/zhaarey/apple-music-downloader)** is a Go-based Apple
Music downloader with its **own** Docker wrapper
(`ghcr.io/itouakirai/wrapper:x86`). It is integrated as a *second Apple engine*:
Settings → **Apple engine** flips between `gamdl` (default) and `amdl`. The
engine choice is stored in `config.json` (`apple_engine`), surfaces in
`/api/status`, and the **Wrapper & login** panel switches its whole UI to match.

**Why you'd use it:** syllable-level (word-by-word) lyrics incl. K-pop
romanization/translation, built-in FLAC/MP3/Opus conversion, `alac-fix` for
malformed ALAC packets, 5000×5000 cover art, and explicit Atmos/ALAC bitrate
caps (`atmos-max`, `alac-max`).

**Why only one Apple wrapper at a time:** both wrappers bind port **10020**
(decryption). amdl also needs **20020**. Starting the amdl wrapper therefore
stops `wrapper-v2` first (and vice-versa via the normal `docker compose up`);
`amdl_wrapper_status()` reports `state: conflict` when wrapper-v2 is up so the
UI can explain instead of silently failing.

**Login flow differs from wrapper-v2:**

1. `wrapperctl.amdl_login()` runs the wrapper image with `-L user:pass`
   (`WRAPPER_USERNAME`/`WRAPPER_PASSWORD` from `wrapper-amdl/.env`, written
   `0600`) and mounts the data dir at **both** `/app/rootfs/data` and `/data`.
2. When 2FA is needed the wrapper polls a code file — the same data dir is
   mounted at `/data`, and `amdl_submit_2fa()` writes the code to
   `wrapper-amdl/rootfs/data/2fa.txt`, which the wrapper picks up. No HTTP API
   is involved (unlike wrapper-v2's `/login/2fa`).
3. Session files accumulate in the same data dir; `amdl_wrapper_status()`
   treats anything except `2fa.txt` as a saved session (⇒ Start works with no
   2FA). A *login* container (`amdl-login`) and a *run* container
   (`amdl-wrapper`) are kept separate; `amdl_restart_login()` re-runs the login
   with the stored `.env` to get a fresh code.

**Download side:** `downloader.py::build_amdl_command` writes a generated
`config.yaml` (port 10020/20020, save folders mapped to the configured output,
folder/file templates, `atmos-max`, `alac-max`, syllable-lyrics toggle, embed
cover, `exit-on-error: true`) into `wrapper-amdl/config.yaml`, then runs
`docker run --rm --network host -v <out>:/downloads -v <config>:/config.yaml
ghcr.io/zhaarey/apple-music-downloader --config /config.yaml [--atmos|--aac] <url>`.
Jobs keep their engine tag (`amdl`), pre-flight fails fast if the image is
missing (`amdl_image_present`) or Docker is down, and the silent-failure guard
(snapshot before → verify new files after) applies exactly as for votify/gytmdl.

`setup_amdl_wrapper.sh` pulls both images and writes the initial
`wrapper-amdl/.env` from the environment (or leaves it for the in-app login).

---

## 2.6 The SQLite ledger (data/library.sqlite)

Every file a job actually produces is written into a local **SQLite database**
(engine-agnostic — covers gamdl, amdl, votify, gytmdl, and FLAC conversions):

- **Schema** — a single `tracks` table keyed by absolute path: `url`, `engine`,
  `title`, `artist`, `album`, `codec`, `size`, `mtime`, `downloaded_at`,
  `job_id`. WAL journal mode, guarded by `_LEDGER_LOCK` for concurrent Flask
  threads. `data/` is gitignored.
- **Recording** — `run_job` snapshots all audio files before launching the
  engine, and on success diffs the folder again; the new files are upserted by
  `ledger_record()` (tags via mutagen, codec via the quality cache — no extra
  ffprobe subprocesses for `.m4a`). Hidden dirs (`.trash`) and `Playlists/`
  folder copies are excluded so playlist duplicates never inflate the index.
  The job log notes how many files were indexed.
- **Ownership** — `ledger_owned_count()` is the authoritative fast path behind
  `owned_info()` (see §2.0): exact per-track rows instead of folder-name
  guessing. Files that were deleted or moved to `.trash` are counted as not
  owned.
- **Stats & rebuild** — `ledger_stats()` returns totals, an engine/codec
  split, and files **missing on disk** (recorded but gone — deleted or in
  `.trash`). `ledger_rebuild()` wipes and re-indexes the whole library folder
  in one pass (for libraries that predate the ledger or after big manual
  changes). `ledger_album_added()`/`ledger_path_dates()` feed the
  **downloaded-at** date shown on Library album rows and in the tag editor
  (the ledger's `downloaded_at` per file, earliest in an album folder).
- **Delta sync** — `ledger_track_owned()` answers "do we already own this
  resolved Spotify/YouTube track?" (exact tag match via `ledger_owned_count`,
  plus a filename-stem fallback so tag-less files still match). With
  **"Skip owned tracks (delta)"** enabled, `delta_filter_urls()` resolves each
  non-Apple link, drops the owned tracks, and returns only the missing ones as
  individual track URLs — re-running a playlist/album link becomes a true
  incremental sync. Apple links pass through untouched.
- **Engine ledger** — the optional **"Use engine ledger"** toggle (Settings,
  `engine_ledger`) passes `--database-path` to the gamdl and votify builders
  so each engine keeps its own SQLite download ledger alongside the
  app-level one.
- **API & CLI** — `GET /api/library/ledger` (stats) and
  `POST /api/library/ledger/rebuild` (re-index) back the Library's 📒 Ledger
  button; `cli.py --ledger` / `cli.py --ledger-rebuild` expose the same
  stats/reindex from the terminal.

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

Card "7 · Migrate" in the UI turns a foreign-service link into Apple Music
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
- **"0 · Getting started" checklist** — `GET /api/onboarding` returns the
  readiness of Python, gamdl, ffmpeg, Apple cookies, Docker, wrapper,
  Spotify/YT cookies, and the output folder (each with ok/missing + a hint);
  the UI renders it as a card under the header and re-checks on settings save
  and wrapper-state changes. `all_required_ready` drives a ✓/⚠ badge.
- Pills show live status: gamdl, gytmdl, votify, FFmpeg, Cookies, Wrapper.
  When **Apple engine = amdl** the gamdl pill reads "amdl" and the Wrapper
  panel polls `/api/wrapper` for amdl state (`mode`, `state`, `needs_2fa`,
  `hint`) instead of wrapper-v2's auth fields; the ⚙ Setup wizard in that panel
  becomes the amdl image setup, and Start/Stop call
  `/api/wrapper/amdl/start|stop`.
- **In-app player** — Library albums have ▶ buttons that stream files via
  `GET /api/audio?path=` (path-locked to the output folder, Range/seek enabled)
  and queue next/prev within the album. The engine is codec-aware:
  `list_album_files` now reports each track's `codec` (+ `duration`, cached,
  for ALAC), and the frontend probes `audio.canPlayType()` — when the browser
  can't decode the source (ALAC in Chrome/Firefox/Edge), it requests
  `GET /api/audio?path=…&transcode=1` and `downloader.transcode_audio`
  streams an **ADTS AAC 320k** transcode via ffmpeg (fragmented mp4 was
  rejected because this ffmpeg build buffers mp4 to a pipe until close —
  verified empirically; ADTS streams incrementally so playback starts in ~1s).
  `?t=seconds` restarts a transcode at an offset for seeking (Range doesn't
  map to time on a live transcode). Duration comes from ffprobe metadata and
  the UI auto-advances near the end since ADTS streams may not fire `ended`.
  `GET /api/art?path=` serves the embedded cover (mutagen, in-memory
  path+mtime cache) for the bar thumbnail + Media Session artwork. The bar
  also persists volume to localStorage, shows buffering/error states, supports
  keyboard shortcuts (Space/arrows/M), and registers Media Session handlers
  (play/pause/prev/next/seekto). Waitress runs with `threads=16` so a
  streaming track can't starve the UI polls.
- **Tag editor** — `GET /api/tags?path=` reads mutagen tags; `POST /api/tags`
  writes title/artist/album/albumartist/track/date. Album rows' ✎ button opens
  a modal.
- **Smart duplicates** — `GET /api/library/smart-duplicates` fingerprints the
  first 15s of up to 300 files (ffmpeg → mono 16kHz PCM → sha256, in-memory
  cache keyed by path+mtime) and groups same-sound files.
- **Format cleanup + recoverable trash** — `GET /api/library/format-duplicates`
  groups same-track files inside an album folder that exist in more than one
  format (FLAC + ALAC after auto-conversion): `_track_key` uses the tagged
  track number, else the filename minus extension; codecs come from extensions
  (only `.m4a` is probed via the quality cache), and `_format_rank` keeps
  lossless > flac > alac > aac. The UI renders a 🧹 Cleanup panel with per-file
  Delete / "Delete all but best" and a KEEP badge, plus a 🗑 **Universal
  cleanup** row that acts on the whole library in one click — delete all FLAC,
  delete all ALAC, or delete all but best (every non-keep copy of every
  duplicate pair). `GET /api/library/cleanup` returns per-action file counts +
  bytes (labels the buttons, disabled when 0); `POST /api/library/cleanup
  {action: flac|alac|best}` runs it. `POST /api/library/delete {path|paths}`
  moves files into `<output>/.trash/` (never a permanent delete)
  with `manifest.json` recording the original relative path; `GET
  /api/library/trash`, `POST /api/library/trash/restore {name}` and `POST
  /api/library/trash/empty` manage it — Empty is the only irreversible action
  and the UI double-confirms. All paths are `resolve()`-validated against the
  output folder (symlink-safe), and `scan_library`/`find_duplicates`/
  `find_smart_duplicates` skip dot-directories so `.trash` never appears as an
  artist or duplicate.
- **Stats** — `GET /api/stats` aggregates `scan_library` + cached quality
  probes into totals, codec split and top artists.
- **library.xml import** — `POST /api/library/import {path, playlist?}` parses
  an iTunes/Apple Music XML plist (stdlib `plistlib`), matches tracks via the
  iTunes Search API and returns the same shape as the Migrate panel.
- **Notifications** — `POST /api/notify/releases` POSTs the cached releases
  list to `notify_url` (ntfy/Pushover/any JSON webhook), best-effort.
- **Watch folder** — `WatchFolder` in downloader.py polls `watch_folder` every
  3s; new `.txt/.m3u/.url` files containing `music.apple.com` links are
  enqueued and moved to `.done/`. Restarted on config save.
- **Queue persistence** — `JobManager.save_pending()` writes queued/running
  jobs to `pending_jobs.json`; `restore_pending()` (called at startup)
  re-queues them. Files are consumed on restore, so a restart can't loop them.
- The **Wrapper & login** panel polls `/api/wrapper` every 5s:
  auth state, playback readiness, 2FA code box, restart button, live logs.
- Downloads render as cards with expandable live logs, progress bars (determinate
  for FLAC conversions, shimmer for downloads) and a live elapsed timer; active
  jobs are polled every 1.5s; finished jobs can be cleared.
- **Section 8 · System logs** tails `logs/app.log` and `logs/launcher.log` via
  `GET /api/logs?file=app|launcher` (whitelisted names only), auto-refreshing
  every 5s — no Terminal needed to diagnose problems.
- **Section 2 · Library** — the in-app library view. `GET /api/library?q=`
  scans the output folder (read-only) and returns artists → albums with track
  counts + byte sizes + **quality badges** (`album_quality`/`format_quality`,
  ffprobe results cached in `quality_cache.json`), plus playlists; the `q`
  param filters by substring. `POST /api/library/open` reveals any path under
  the output folder in Finder; `POST /api/library/rename` renames an
  artist/album folder (path-validated); `GET /api/library/duplicates` lists
  same-name+size files (Playlists excluded); `GET /api/library/export`
  downloads a JSON backup of config + library. The card auto-refreshes when a
  download batch finishes.
- **New-release tracker** — `GET /api/new-releases` (cached 6h) uses the
  iTunes Search API to find albums from your Library's artists released in the
  last 90 days; the UI's ✨ Releases button renders them with one-click
  download.
- **30s previews** — `GET /api/preview-url?url=` resolves a track to the free
  iTunes `previewUrl` via `migrate.apple_preview_url`; chips show a ▶ button.
- **Job orchestration** — `JobManager` now dispatches through a semaphore
  (`max_concurrent`), waits for a schedule window (`schedule_window`, parsed by
  `_parse_window`/`_seconds_until_window`, wraps midnight), and `run_job`
  retries failures with 1m → 5m → 15m backoff (`auto_retry`). When a batch goes
  idle, `on_batch_idle` fires — app.py uses it to POST the configured
  `scan_hook_url` (music-server rescan).
- **Quality verification** — after a successful download, `verify_new_quality`
  probes the new files and logs the real codec/bit-depth, warning on silent
  ALAC→AAC downgrades. Playlist folders can be **hardlinks** (`os.link`, APFS,
  falls back to copy) instead of copies when `playlist_hardlink` is on.
- **gamdl update pill** — `/api/status` now includes `gamdl_latest` (GitHub
  releases API, 6h cache); the pill warns with a tooltip when a newer version
  exists.
- **Storefront** — the Migrate matcher and previews use `config.storefront`
  (default US).
- **Playlist organization** — two Settings toggles (both on by default):
  *Save playlist files (.m3u)* adds `--save-playlist` to the gamdl command so
  each playlist writes `Playlists/{artist}/{title}.m3u` (tracks in order, no
  duplication); *Copy playlist tracks into a folder* makes `downloader.py`
  read each freshly-created `.m3u` and copy the referenced track files into
  `Playlists/{artist}/{title}/` (`copy_playlist_folders`), so a playlist is one
  browsable folder. The copies duplicate files on disk — disable if storage is
  tight. Settings now open expanded by default (the ⚙ button in the download
  card, or the Hide/Show button in the Settings header, toggles them).
- **Download-end notifications**: when the last active job finishes, the UI
  toasts a summary, plays a small chime (WebAudio), and — if permission was
  granted on the first download — shows a system Notification.
- Config is editable in Settings and saved to `config.json`.

---

## 5.5 Codec reference (audio formats the app produces/plays)

| Codec | Bitrate / depth | Source | File | Needs | Notes |
|---|---|---|---|---|---|
| **ALAC** | up to 24-bit / 192 kHz | Apple Music | `.m4a` | wrapper + active sub | gamdl's lossless codec; bit-depth/rate set by the label's master |
| **AAC-LC** ("web") | 256 kbps | Apple Music | `.m4a` | cookies only | the cookies-mode fallback |
| **Dolby Atmos** | object audio, up to 2768 kbps cap (amdl) | Apple Music | `.m4a` (E-AC-3 JOC) | wrapper | `atmos` codec; amdl caps bitrate (`amdl_atmos_max`) |
| **FLAC** | same PCM as source (≤24-bit/192 kHz) | local conversion | `.flac` | ffmpeg | lossless→lossless; only ALAC is converted, never AAC |
| **AAC** | 128 kbps (itag 140) | YouTube Music | `.m4a` | nothing | free tier, anonymous `tv` client |
| **AAC** | 256 kbps (itag 141) | YouTube Music | `.m4a` | YouTube cookies + Premium | `web_music` client — more reliable |
| **Opus** | 256 kbps (itag 774) | YouTube Music | `.opus` | YouTube cookies + Premium | best size/quality on YT |
| **OGG Vorbis** | 160 kbps (free) / 320 kbps (Premium) | Spotify | `.ogg` | Spotify cookies (+Premium for 320) | votify; account-suspension risk |
| **AAC ADTS stream** | 320 kbps | in-app player only | stream | ffmpeg | on-the-fly transcode for browsers that can't decode ALAC; never saved |

**Codec priority settings** — `song_codec_priority` (`alac,aac-web` default;
`alac` / `aac-web` / `atmos`), `spotify_audio_quality` (`160` / `320,160`),
`ytm_itag` (`140` / `141` / `774`), `convert_to_flac` (ALAC→FLAC auto).
Quality probing (`probe_audio_quality`) reports `{codec, bits, rate}` from
ffprobe, cached in `quality_cache.json` keyed by path+mtime.

## 5.6 Settings reference (config.json)

Every key, its default, and its meaning — the authoritative list is
`DEFAULT_CONFIG` in `downloader.py`; `FEATURES.md §5` documents them in
table form. Highlights of the non-obvious ones:

- `use_wrapper` + `wrapper_url` — wrapper mode for ALAC/Atmos; cookies not needed.
- `apple_engine` — `gamdl` (default) or `amdl`; controls the Wrapper panel UI
  and the download command builder.
- `amdl_*` — amdl-only knobs (Atmos cap, ALAC max sample rate, lyrics type,
  cover size) — ignored while `apple_engine == gamdl`.
- `schedule_window` / `max_concurrent` / `auto_retry` — job orchestration.
- `watch_folder` — auto-download dropped `.txt`/`.m3u`/`.url` link files.
- `skip_owned` — drop fully-owned links (preview + ownership check).
- `scan_hook_url` / `notify_url` — webhooks for batch-end rescan and releases.
- `storefront` — iTunes Search country used by Migrate/Import matching.
- `save_playlist` / `copy_playlist_folders` / `playlist_hardlink` — playlist
  `.m3u` + folder organization (hardlinks = zero extra disk on APFS).

## 6. One-click boot

The single source of truth is **`start.sh`** (macOS + Linux) and its Windows
twin **`start.ps1`**. Every other launcher is a thin wrapper around one of them:

- **`start.sh`** — sets a sane PATH (Homebrew + `~/.local/bin`), runs
  `setup.sh` on first launch (**venv health check**: a half-created `.venv`
  from an interrupted `pip install` fails the `import flask` probe and
  re-runs setup instead of crashing with ModuleNotFoundError), prints a
  prerequisite checklist (gamdl, ffmpeg, gytmdl, votify, cookies, Docker),
  starts Docker Desktop on macOS (bounded readiness check — never hangs),
  boots wrapper-v2 when present and waits for `auth.state == "authenticated"`,
  starts (or reuses) the app server, then opens the browser. On macOS it also
  builds `Music High Res.app` once and symlinks it onto the Desktop
  (`~/Desktop/Music High Res.app`, skip with `MHR_NO_DESKTOP=1`). Flags:
  `--min` (AAC-only: skip Docker + wrapper), `--no-docker`, `--no-browser`,
  `--app-style` (standalone window). Output is teed to `logs/launcher.log`.
- **`start.ps1`** — the Windows launcher. Same responsibilities as
  `start.sh`: runs `setup.ps1` on first launch (same `import flask` venv
  health check), prints the prerequisite checklist, launches Docker Desktop
  when installed but stopped (bounded probe via a background job — never
  hangs), boots wrapper-v2 when present, starts (or reuses) the app server,
  and opens the browser. Flags mirror the bash version: `-Min`, `-NoDocker`,
  `-NoBrowser`. Output is teed to `logs\launcher.log` via `Start-Transcript`.
  Note: `start.sh`'s `--app-style` and the Desktop-shortcut step are macOS
  only — on Windows the `.bat` double-click IS the app.
- **`Start Music High Res.bat`** — Windows double-click convenience: just
  `powershell -NoProfile -ExecutionPolicy Bypass -File start.ps1 %*`.
- **`Start Music High Res.command`** — macOS double-click convenience: just
  `exec bash start.sh "$@"`.
- **`Music High Res.app`** (built by `./make_app.sh`) — a real macOS app bundle
  with a Dock icon. Its `Contents/MacOS/MusicHighRes` launcher sets the PATH,
  then `exec bash "$PROJECT/start.sh" --app-style` — which boots the stack and
  opens the UI in a standalone app-style window via
  `open -na "<browser>" --args --app=http://127.0.0.1:8741` (Brave, then
  Chrome/Edge/Arc, then Safari). start.sh waits on the server PID, so the app
  stays in the Dock until Quit. If it **reused** a server that was already
  running (e.g. started first by `start.sh`), the app exits but the
  pre-existing server keeps running.

The **"0 · Getting started"** card in the UI mirrors this checklist live
(via `GET /api/onboarding`): Python, gamdl, ffmpeg, Apple cookies, Docker,
wrapper, Spotify/YT cookies, output folder — each with ok/missing state and a
hint for the missing ones. `start.sh --min` exists so people who only want
AAC 256kbps never have to deal with Docker.

After a reboot, one double-click:
1. **Docker Desktop** — if the daemon isn't up, launches the app and polls up to
   4 minutes (first boot is slow).
2. **Wrapper** — `docker compose up -d` in `wrapper-v2/`, then waits for
   `auth.state == "authenticated"` (session restores from disk — no 2FA).
3. **App** — starts `app.py` and opens the browser once the API responds.
   Both launchers now **reuse an already-running server** instead of failing
   with "Address already in use", and redirect their boot output to
   `logs/launcher.log`. In the reuse case `Start Music High Res.command` opens
   the browser and exits right away (the Terminal window closes) — the server it
   found is already being kept alive by whoever started it.

When you **close the app** (Ctrl+C, closing the launcher window, or Quit on the
`.app` bundle), the launcher's cleanup trap stops the ALAC wrapper it started
(`docker compose stop` in `wrapper-v2/`) — the wrapper only runs while the app
is open, and **Docker Desktop itself is left running**. If the launcher reused
an already-running server (and thus didn't start a new app session), it leaves
the wrapper alone too.

Docker Desktop also **auto-starts at login** via the LaunchAgent
`~/Library/LaunchAgents/com.musichighres.docker.plist`
(`RunAtLoad` → `open -a Docker`). Remove it with
`launchctl bootout gui/$(id -u)/com.musichighres.docker`.

By contrast the **wrapper never auto-starts** — `setup_wrapper.sh` pins the
compose restart policy to `no`, so it only runs when the launcher (or the
in-app wizard) starts it while the app is open.

---

## 7. Operations reference

### Logs (where to look when something breaks)

- **`logs/app.log`** — structured server log written by `app.py` (rotating:
  1 MB per file, 3 backups, so it never grows unbounded). It records the server
  starting, and catches **any unhandled exception** from the main thread or a
  worker thread — the first place to look when the app won't start or a
  download dies unexpectedly.
- **`logs/launcher.log`** — raw boot output from any launcher (`start.sh`,
  `Start Music High Res.command`, or the `Music High Res.app` bundle): Docker
  startup, wrapper bring-up, and any server crash output.

Both live under the project folder (gitignored via `*.log`) and are created
automatically on first run. On Windows the launcher writes them via
`Start-Transcript` to the same `logs\` folder.

### Windows specifics

- **Python environment** — the venv lives at `.venv\Scripts\` (not
  `.venv/bin`); `downloader.venv_bin()` and `gamdl_binary()` already probe
  both layouts, so pip-installed CLIs (gytmdl, votify, gamdl) resolve on
  Windows.
- **gamdl** — on Windows it's installed with `pip install gamdl` (there's no
  Homebrew). The setup/install scripts do this automatically; `install.ps1`
  also adds pip's `Scripts` dir to the user PATH when needed.
- **"Open in Finder" buttons** — the UI labels them **Explorer** on Windows
  (`/api/status.platform` drives the label), and `/api/library/open` uses
  `explorer /select,` instead of macOS `open -R`.
- **ALAC wrapper setup** — `setup_wrapper.sh` is a bash script; on Windows the
  in-app wizard (and `setup_wrapper.sh`) needs **Git for Windows (Git Bash)**
  or WSL. `wrapperctl.SetupManager` checks for `bash` and raises a clear error
  naming Git Bash when it's missing. Docker Desktop for Windows (WSL2 backend)
  is required for the wrapper.
- **Launcher autostart** — the macOS LaunchAgent / `open -a Docker` bits don't
  exist on Windows; `start.ps1` just launches "Docker Desktop" when Docker is
  installed but the daemon is down.

```bash
# App logs
tail -f logs/app.log              # watch the server live
tail -f logs/launcher.log         # startup/boot output

# Status
curl http://127.0.0.1/health            # wrapper alive?
curl http://127.0.0.1/me                # auth state + account
docker ps                               # is wrapper-v2 up?

# Wrapper logs
docker logs --tail 50 wrapper-v2        # (grep -v dlsym to silence noise)

# Restart wrapper cleanly
cd wrapper-v2 && docker compose up -d --force-recreate

# amdl wrapper (when Apple engine = amdl)
docker ps --filter name=amdl-                     # amdl-login / amdl-wrapper
docker logs --tail 50 amdl-wrapper               # run container logs
docker logs --tail 50 amdl-login                 # login container (2FA prompts)
docker rm -f amdl-login amdl-wrapper             # clean slate (session survives in wrapper-amdl/)

# Rebuild after the lib fix
./fix_wrapper_libs.sh

# Stop everything
docker compose -f wrapper-v2/compose.yaml down   # wrapper (also frees port 10020 for amdl)
pkill -f 'app.py'                                # app
```

---

## 8. Security notes


- **`cookies.txt` is a live Apple session** — never commit it, never share it.
  It is in `.gitignore`. Same for a **Spotify** cookies file (it's a live
  Spotify session) and **YouTube** cookies (only needed for premium itags).
  gytmdl/votify install into the venv via `requirements.txt`, so a fresh
  `./setup.sh` pulls them automatically.
- **`wrapper-v2/.env` holds your Apple ID password** — also gitignored
  (`wrapper-v2/` is entirely excluded from the repo). Same for
  `wrapper-amdl/.env` and the amdl session files under `wrapper-amdl/rootfs/`.
- The wrapper's cached session (`wrapper-v2/data/`) is equally sensitive.
- The app binds to **127.0.0.1 only** — it is not reachable from other devices.

## 9. Compliance

This project exists for **personal, experimental interoperability** with content
you already pay to stream. It deliberately carries a non-commercial license (see
LICENSE). Use it only for your own library; comply with the Apple Media Services
Terms and your local laws. If Apple or any rightsholder objects, this project
will be withdrawn or modified accordingly.
