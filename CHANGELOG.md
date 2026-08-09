# Changelog

All notable changes to **Music High Res (AppleMusic Song Downloader)** are
documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions loosely follow [Semantic Versioning](https://semver.org/).
- **[Unreleased]** — changes in the working tree not yet released.
- Dated sections describe what is actually **on GitHub** (`main`, tagged
  releases).

Legend: **Added** — new features · **Changed** — changes to existing behavior ·
**Fixed** — bug fixes · **Security** — hardening.

---

## [Unreleased]

(nothing yet)

---

## [1.0.0] - 2026-08-09

The first stable release. Everything built so far: multi-service downloads
(Apple Music via gamdl or amdl, Spotify via votify, YouTube Music via
gytmdl), a real in-app Library, automatic FLAC conversion, wrapper
automation, Windows support, and a pile of power-user tooling.

### Added

- **Windows support** — the app and launchers now run on Windows:
  `start.ps1` (PowerShell twin of `start.sh`) + `Start Music High Res.bat`
  double-click launcher, `setup.ps1`/`setup.bat`, and a one-command
  `install.ps1` (winget python/ffmpeg/git, gamdl via pip). The app is
  Windows-aware end-to-end: `.venv\Scripts` layouts (`venv_bin`,
  `gamdl_binary`), `explorer /select,` for the open-in-file-manager buttons
  (UI labels them **Explorer** on Windows via `/api/status.platform`), and a
  clear Git-Bash hint when the bash-based wrapper setup wizard runs without a
  shell.
- **Spotify downloads** (via votify) — OGG Vorbis 160/320 kbps; needs a cookies
  file exported from `open.spotify.com`. ⚠️ Spotify has suspended accounts caught
  using third-party downloaders — at your own risk.
- **YouTube Music downloads** (via gytmdl) — AAC 128 kbps (free, no cookies) or
  AAC 256 kbps / Opus 256 kbps (Premium, needs YouTube cookies).
- **Auto-routing** — paste Apple Music, Spotify, or YouTube Music links into one
  box; each is routed to the right engine (one source per batch), and
  pre-download chips preview kind + track count.
- **SQLite ledger** (`data/library.sqlite`) — records every download (path, URL,
  engine, tags, codec, size, when); powers the green **"✓ owned n/n"** chips,
  the 📒 Ledger panel (totals, engine/codec split, files missing on disk, 🔄
  Rebuild), and **delta sync** — "skip owned tracks" re-runs only fetch what the
  ledger doesn't already own.
- **In-app Library panel** — browse artists/albums with sizes and quality
  badges, search, **Open in Finder**, rename folders, JSON **backup export**,
  and a 📊 Stats dashboard.
- **In-app player** — seek, volume, next/previous, Media Session (macOS now
  playing), keyboard shortcuts, and live ALAC→AAC transcode for browsers that
  can't decode ALAC (Chrome/Firefox/Edge).
- **30-second preview playback** for Apple Music links, before you download.
- **Tag editor** — per-track title/artist/album/album-artist/track/year written
  with mutagen, plus **downloaded-at** dates from the ledger.
- **Duplicate tooling** — a same-name+size duplicate finder and a **smart
  audio-fingerprint dupe finder** (first 15 s decoded to PCM).
- **FLAC/ALAC cleanup** — per-album "delete all but best" plus a universal
  cleanup (delete all FLAC / all ALAC / all-but-best) with file+size previews;
  all deletes are **recoverable** via `.trash/` (per-file Restore + Empty).
- **amdl Apple engine** — optional second Apple Music downloader (syllable
  lyrics, Atmos/ALAC bitrate caps, built-in conversions). Both wrappers share
  port 10020, so the app manages the switch.
- **In-app wrapper setup wizard** (`wrapperctl.py`) — no-Terminal wrapper setup:
  APK path or URL, streamed build log, Intel-Mac library fix, Apple ID login and
  2FA submission right in the browser.
- **One-click launcher** `start.sh` (macOS + Linux) — first-run setup with a
  venv health check, readiness checklist, Docker boot, wrapper start, app-server
  reuse, and browser open; logs to `logs/launcher.log`.
- **Server logging** — rotating `logs/app.log` (1 MB × 3) plus
  unhandled-exception capture for main and worker threads.
- **Watch folder** — drop `.txt` / `.m3u` / `.url` link files into a folder and
  they download automatically (moved to `.done/`).
- **Queue persistence** — queued/running downloads survive an app restart.
- **Retry, scheduler, backoff** — ↻ Retry on failed job cards, a download
  window (`02:00-06:00`), auto-retry with 1m→5m→15m backoff, and a concurrency
  cap.
- **Quality verification** — new files are probed with ffprobe; the real
  codec/bit-depth shows in the log and as Library badges (probes cached in
  `quality_cache.json`).
- **Exposed gamdl knobs** — music-video resolution/codec priority, cover-art
  format, custom album/playlist folder templates, and "use album release date".
- **Playlist organization** — `.m3u` playlist files plus optional copied folders
  (or APFS hardlinks, zero extra disk).
- **New-release tracker** — ✨ Releases lists recent releases from your artists,
  with a 🔔 webhook/ntfy notify button.
- **Apple Music library import** — match an exported `library.xml` against the
  catalog and queue the matches as one batch.
- **Music-server rescan hook** — optional POST webhook when a batch finishes
  (Navidrome/Plex/Jellyfin scan endpoints).
- **CLI additions** — `--check` (readiness checklist), `--ledger`, and
  `--ledger-rebuild`.
- **macOS app bundle polish** — refreshed icon (halo + spotlight), a Desktop
  shortcut on first run, and `start.sh --app-style` window mode.
- **Repo rename** — remote moved from `gavinraspberrypi/AppleMusic-Song-Downloader`
  to `ZeroIndents/AppleMusic-Song-Downloader`; all installer `REPO_URL`s and
  README links updated to the canonical location.
- **Pinned dependencies** in `requirements.txt`, a GitHub Actions CI workflow,
  and `install_linux.sh` for Linux setup.

### Changed

- Apple Music cookies are no longer required when the ALAC wrapper is
  authenticated (wrapper mode logs in with your Apple ID instead).
- `Start Music High Res.command` and the `.app` bundle now delegate to
  `start.sh` — one boot sequence everywhere.
- Album/playlist folder templates are now configurable in Settings.

### Fixed

- First-run setup could be re-triggered by a half-created `.venv` — the
  launcher now health-checks the venv instead of trusting the folder exists.
- The in-app wrapper setup could stall forever on a hung `setup_wrapper.sh` or
  APK download — added a 45-minute wall-clock timeout that fails the state.
- Credentials containing `#`, newlines (and for amdl, `:`/spaces) could corrupt
  the wrapper's `.env` files — now validated before writing.
- Wrapper `_get`/`_post` swallowed the underlying error text — the real reason
  is now surfaced in the UI.
- `install.sh` printed nothing for "Creating Python environment" (a `say` line
  had been merged into a comment) — fixed.

### Security

- **Localhost-CSRF guard** — mutating endpoints (download, delete, rename,
  cleanup, trash, tags, wrapper login/2FA, config) now reject requests whose
  `Origin`/`Referer` isn't this app, so a malicious webpage can't fire form
  POSTs at `127.0.0.1:8741`.
- **`/api/tags` path containment** — the read/write tag endpoints now refuse
  paths outside the output folder (every other file endpoint already did).

### Changed

- **Wrapper lifecycle** — the ALAC wrapper now runs **only while the app is
  open**: the launchers (`start.sh` / `start.ps1`) start it when you launch
  the app and stop it again when you close it (`docker compose stop`), so it
  no longer auto-starts with Docker Desktop. **Docker Desktop itself is left
  running.** `setup_wrapper.sh` now pins the wrapper's compose restart policy
  to `no` for future setups.

---

## [0.1.0] - 2026-08-07

First release pushed to GitHub — the original Apple Music downloader: web app
+ CLI on top of gamdl, FLAC conversion, and wrapper automation.

### Added

- **Web app** (`app.py`, `static/index.html`) — paste Apple Music links, pick
  quality (ALAC / AAC / Atmos), watch live download logs; binds to
  `127.0.0.1:8741`.
- **CLI** (`cli.py`) — the same downloads from the Terminal, with `--codec`,
  `--output`, `--no-wrapper` / `--wrapper`.
- **gamdl engine** — lossless ALAC (up to 24-bit/192 kHz), AAC 256 kbps, and
  Dolby Atmos downloads from an Apple Music subscription.
- **Automatic ALAC → FLAC conversion** (ffmpeg, lossless→lossless) with
  `cli.py --to-flac` support.
- **Wrapper automation** — `setup_wrapper.sh` (APK-based wrapper-v2 setup),
  `login_wrapper.sh`, `fix_wrapper_libs.sh` (Intel-Mac `-42812` fix), and the
  `Start Music High Res.command` one-click launcher.
- **Spotify / YouTube Music migration** — match tracks from a Spotify or
  YouTube Music link onto the Apple Music catalog.
- **macOS app bundle** — `make_app.sh` builds `Music High Res.app` with a Dock
  icon and app-style window.
- **One-command macOS installer** — `install.sh` (Homebrew + deps + repo +
  setup) and the README quick-start.
