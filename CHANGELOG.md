# Changelog

All notable changes to **Music High Res (AppleMusic Song Downloader)** are
documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions loosely follow [Semantic Versioning](https://semver.org/).
- **[Unreleased]** — changes in the working tree not yet released.

## [1.3.0] - 2026-08-10

### Added — 30 small/medium features
- **⬆ In-app updater** — the header shows a ⬆ pill when a newer Music High Res
  release exists; one click downloads it and updates **in place**, preserving
  your settings, library ledger, cookies and download history, then restarts
  the app automatically (`/api/update/check`, `/api/update/apply`). Disable
  with Settings → **Check for app updates**. Packaged binaries link to the
  release page instead (`check_updates`).
- **Queue reorder** — queued jobs get ▲/▼ buttons; the backend keeps a queue
  order that controls *actual execution order* (a concurrency gate only lets
  the front-most queued job take a slot, so ▲/▼ changes who runs next, not
  just the list view). Retry-backoff jobs are excluded from the gate so a
  failed job's 1m/5m/15m wait never freezes the queue behind it. (`/api/jobs/reorder`).
- **Album durations** — album rows show total play time (lazy, cached via
  `/api/library/album/duration`).
- **🔥 Most played** — ledger-driven top-played tracks panel + **reset play
  counts** (`/api/library/most-played`, `/api/library/plays/clear`).
- **Save cover to file** — the cover viewer writes the embedded art to
  `cover.jpg/png` next to the album (`/api/library/album/cover/save`).
- **Genre + Disc tags** — tag editor and bulk editor gain Genre and Disc #
  fields (read/write via mutagen).
- **Batch-complete chime** — a two-note Web Audio chime when a download batch
  finishes; toggle in Settings (`chime_on_done`).
- **Job log pin** — 📌 button freezes a running job's auto-scroll while you
  read the tail.
- **Open output folder on done jobs** — finished download cards get a Finder/
  Explorer button.
- **Settings test buttons** — "Test" next to the scan-hook and notify URLs
  sends a sample ping (`/api/hooks/test`).
- **Reset to defaults** — one-click settings reset keeps output folder +
  cookies, restores every other setting (`/api/config/reset`).
- **Accent colour picker** — preset swatches + custom colour wheel; applies
  the CSS gradient immediately and saves to config (`accent_color`).
- **Watch-folder ignore patterns** — comma-separated substrings to skip
  (`.DS_Store`, `.part`, …) via `watch_ignore`.
- **Header uptime pill** — 🕐 shows how long the server has been up.
- **Log download** — the logs panel's ⬇ button downloads the full file
  (`/api/logs/download`).
- **Ledger filter** — the missing-files list is now filterable as you type.
- **Library sort** — albums sort by name / newest added / biggest.
- **Bulk convert** — multi-select albums → "🔄 Convert to FLAC" in the bulk bar.
- **Releases: ignore + open** — 🙈 hides an artist's releases; 🔗 opens the
  release on Apple Music (`/api/releases/ignore`).
- **Wishlist ownership** — items show ✓ owned / new badges; "Download all"
  skips what the ledger already owns.
- **Player: playback rate** (0.5×–2×, remembered), **sleep timer** (15–60 min),
  **play queue** panel (☰), **copy lyrics** button.
- **Onboarding: open output folder** button in the Getting-started drawer.
- **Keyboard shortcut** — ⌘/Ctrl+Enter in the URL box starts the download.

### Fixed
- **Dolby Atmos / pure-ALAC downloads now fail with a clear message instead of a
  cryptic gamdl license error.** The wrapper is mandatory for Atmos (and pure
  ALAC) — there is no AAC fallback gamdl can use — but the app previously only
  routed through the wrapper if the user had separately enabled "Use wrapper"
  in Settings. Now: the download button always sends `use_wrapper` for Atmos /
  pure-ALAC codecs (backend also forces it), and the job preflights the wrapper
  before launching gamdl: wrapper setting off → "enable Use wrapper in
  Settings"; server unreachable → "is Docker running?"; up but not logged in →
  "log in from the 5 · Wrapper & login panel". When the wrapper genuinely is
  ready, an ✓ confirmation shows in the codec hint.
- `/api/status` now reports `wrapper_ok` (reachability + auth) so the UI can
  tell "wrapper off" from "wrapper on but broken".
- Dated sections describe what is actually **on GitHub** (`main`, tagged
  releases).

Legend: **Added** — new features · **Changed** — changes to existing behavior ·
**Fixed** — bug fixes · **Security** — hardening.

---

### Added — v1.2-era library features (included in 1.3.0)

- **🌓 Light / dark theme** — one-click toggle in the header (or press **t**);
  the whole palette is CSS-variable driven, and your choice is remembered
  across sessions. Keyboard shortcuts for the player area are extended with
  **l** to toggle lyrics.
- **🔍 Track-level Library search** — the Library search box now also matches
  individual track titles/file names (not just artists and albums) and lists
  the hits above the artist tree, each one playable in one click.
- **🔄 Album format conversion** — every album row gets a convert button with
  a target picker: **FLAC / MP3 320k / Opus 192k / AAC 256k / OGG q6**.
  Originals are always kept; runs as a background task with a toast summary.
- **💬 In-player lyrics viewer** — a lyrics panel (button in the player bar or
  press **l**) shows the track's `.lrc` sidecar with the current line
  highlighted and auto-scrolled; it follows track changes and seeking.
- **🖼 Cover viewer + replace** — every album row gets a cover button that
  shows the current embedded art and lets you replace it from a file; the
  image is written into every track in the folder.
- **🕘 Play tracking** — pressing play records `play_count` + `last_played`
  in the SQLite ledger (schema migrates in place); a **Recent** button lists
  your most-played/last-played tracks with one-click replay.
- **⤓ Library index export** — a full track listing (artist / album / track /
  title / codec / size / path) as CSV or a self-contained HTML page.
- **🎵 .m3u playlist import** — the Import panel accepts an .m3u file, matches
  its entries against files already in your library, and saves a playlist
  under `Playlists/{artist}/{name}.m3u` (missing entries are reported).
- **⬇ Per-track catalog download** — song results in the catalog Search get a
  one-click **Download** button that queues that song immediately.

### Added

- **🧠 MusicBrainz auto-tagging** — per-album and whole-library background
  task that matches every track against the MusicBrainz database (by title +
  primary artist + duration) and writes canonical title/artist/year tags
  (respecting MusicBrainz's 1 req/s rate limit). Buttons on each album row
  and in the Library toolbar.
- **📡 Media-server scan presets** — Settings now has a real **Navidrome /
  Plex / Jellyfin** picker (URL + token + Plex section). The batch-finished
  hook and a new 📡 Scan now button call the server's actual scan API
  (`/api/scan`, `/library/sections/<id>/refresh`, `/Library/Refresh`) instead
  of a raw webhook (which stays as the fallback).
- **✓ owned catalog badges + ⬇ Download missing** — 🔍 Search results and
  artist discographies now show green "✓ owned n" badges straight from the
  ledger, and each discography gets a one-click **⬇ Download n missing**
  button that queues every album you don't own.
- **🌐 Remote access mode** — Settings toggle to bind on all interfaces
  (phone/PWA control from the same Wi-Fi) plus an optional **access token**
  that gates every `/api/*` call (`?token=` or `X-MHR-Token`); the UI
  prompts for it once and remembers it.
- **Queue controls** — the Downloads card gets ⏸ **Pause** (holds queued
  jobs, running ones finish), ▶ **Resume** and ✕ **Cancel all**.
- **⤓ .m3u exports** — whole-library playlist plus a per-album ⤓ button on
  every album row (absolute paths, #EXTINF headers).
- **📼 CUE sheets** — per-album button that writes a standard `.cue`
  (PERFORMER/TITLE/FILE/TRACK/INDEX with cumulative offsets) from embedded
  tags + probed durations.
- **📜 In-app Logs viewer** — tail `app.log` and `launcher.log` right in the
  UI (the endpoint existed; now there's a button + modal).
- **♻ Restore settings from backup** — the Import panel accepts the exported
  `music-high-res-backup.json` and reapplies its config (known keys only;
  the token is never restored).
- **🗑 Empty-folder cleanup** — Library button that lists folders with no
  files at all and deletes them, plus an optional **auto-clean after
  batches** setting.

### Added

- **Artist discography in 🔍 Search** — the catalog search now has an
  **Artists** entity; clicking 📀 Discography lists every album by that artist
  (iTunes Lookup) and adds any of them to the download list directly.
- **⬆ Lossy → Lossless upgrade** — the Library toolbar's ⬆ Upgrade button
  lists every album whose best file is lossy (AAC/MP3/OGG) with its ledger
  source link, and re-queues each at **ALAC with overwrite** in one click.
- **Per-job overwrite** — an "Overwrite existing" toggle next to Download
  forces a re-download of existing files for that batch only (and is
  preserved on ↻ Retry). Overwrite jobs skip the ledger delta filter.
- **Multi-disc file name template** (gamdl `--multi-disc-file-template`) —
  separate `{disc:02d}-{track:02d} …` layout for multi-disc releases,
  falling back to the single-disc template.
- **Votify AAC tiers** — Spotify quality now exposes `aac-medium`/`aac-high`
  plus the FLAC-in-MP4 tiers; legacy `160`/`320` labels are mapped to
  `vorbis-medium`/`vorbis-high` automatically.

### Fixed

- **Every Spotify download crashed** — the app shipped kbps labels (`160`,
  `320,160`) that votify's CLI rejects with a usage error. Values are now
  sanitized against votify's real enum (with legacy-label mapping) and the
  default is `vorbis-medium`.
- **ReplayGain + cover upgrade skipped tagless MP3s** — both writers now
  create an ID3 container when the file has none, so TXXX gain tags and APIC
  art land on any MP3.
- **Smart-playlist quality filter mismatches** — codec names are normalized
  (`m4a`/`mp4` → `aac`, `vorbis`/`opus` → `ogg`, `pcm_*` → `wav`) so "AAC"
  and "OGG" filters actually match their files; WAV now counts as lossless
  in the quality histogram and quality badges render `WAV` instead of
  `PCM_S16LE`.
- **Linux "Open in file manager" failed** — the route shelled out to macOS's
  `open` on every non-Windows platform. It now uses `xdg-open` on Linux
  (with proper macOS `open`/`open -R` and Windows `explorer` branches).
- **Restore could point the app at a dead output folder** — restoring a
  backup with a stale/bogus `output_path` (e.g. from another machine) is
  now rejected: the path must already exist or have a creatable parent. The
  restore response also lists skipped keys.
- **Whole-library MusicBrainz runs had no time expectation** — the button
  now fetches the track count, estimates the runtime (~1 track/sec rate
  limit) and confirms before starting a long scan.
- **Artist quality badge showed stale data under quality/recent filters** —
  it's recomputed from the filtered album list, and WAV albums display
  correctly.
- **Lossy→lossless upgrade silently no-oped with delta-sync on** — overwrite
  jobs now bypass the ledger delta filter.

### Added

- **ReplayGain scan** — the Library's 🎚 ReplayGain button measures every
  track (ffmpeg EBU R128) and writes track + album `REPLAYGAIN_*` tags into
  FLAC/ALAC/AAC/MP3/OGG — normalizes playback volume across Plex/Jellyfin/
  Navidrome. Runs as a background task with a live status toast.
- **LRCLIB lyrics backfill** — the Library's 💬 Lyrics button fetches free
  synced lyrics from lrclib.net for tracks missing a `.lrc` sidecar.
- **Quality histogram** — the 📊 Stats panel now shows per-file codec,
  bit-depth and sample-rate distributions plus the lossless/lossy split.
- **Download history** — Stats adds a by-month breakdown of everything the
  SQLite ledger recorded, and the 📒 Ledger panel can export the whole ledger
  as CSV or JSON.
- **Apple Music catalog search** — 🔍 Search queries the iTunes Search API
  for albums/songs and adds links straight to the download list (no link
  hunting) — plus ⭐ one-click save to the new wishlist.
- **Wishlist** — ⭐ panel to save links for later and queue them all with one
  click (from catalog search, album rows and more).
- **Smart playlists** — ▶ panel with saved filters (artist / album / year /
  quality / recently added / min tracks), live preview counts, and export to
  `Playlists/Smart/{name}.m3u`.
- **Bulk tag editor** — check albums in the Library, then ✎ Edit tags applies
  any filled field to every track in the selection at once.
- **Hi-res cover upgrade** — the 📷 button on any album re-fetches its cover
  at 1200px+ from the iTunes catalog and re-embeds it into every track.
- **Album source links** — 🔗 on an album row opens the original Apple Music
  page (from the ledger's recorded URL).
- **Engine surface** (Settings): gamdl file-name template, compilation
  folder template, exclude-tags, date-tag template, music-video remux
  (m4v/mp4), lyrics format picker (lrc/srt/vtt/ttml), gytmdl PO-token +
  file template, full gytmdl itag list (48k–256k + Opus), Spotify FLAC
  lossless tiers + `.wvd` path, and amdl convert-after-download
  (FLAC/MP3/Opus + keep-original).
- **Desktop notifications** — optional native OS notification when a
  download batch finishes (macOS / Windows toast / Linux notify-send), even
  with the browser closed.
- **Settings presets** — save named bundles of the download settings and
  apply them in one click (e.g. “home server” vs “portable”).
- **PWA shell** — installable app: manifest, service worker, generated
  icons, and a 📲 Install pill.
- **Library filters** — quality (lossless/lossy/codec) and recently-added
  (7/30/90 days) dropdowns, plus a NEW badge on albums added in the last
  14 days.
- **Releases** — release workflows now ship a `SHA256SUMS.txt` per asset.

### Fixed

- **APK setup failure hint** — `setup_wrapper.sh` now explains *why* Apple
  library extraction failed (`extract-libs: 0 ok 18 failed`) and prints the
  exact known-good APK (Apple Music 3.6.0-beta build 1109, `arm64-v8a +
  x86_64` variant), the APKMirror link, and a `unzip -l … | grep x86_64`
  verify command. README Step 3 corrected: the `arm64-v8a + x86_64` variant
  is required on Apple Silicon too, not just Intel.
- **`jq` prerequisite check** — wrapper-v2's new `extract-libs.sh` needs
  `jq`, which macOS doesn't ship; setup now fails fast with install
  instructions instead of a cryptic `jq is required`. The one-command
  installers (`install.sh`, `install_linux.sh`, and Windows `install.ps1` via
  `winget install jqlang.jq`) install `jq` too.

### Changed

- **Cross-platform line endings** — new `.gitattributes` enforces LF for
  shell/Python/docs and CRLF for `.bat` files, so a Windows-side edit can
  never silently break the macOS/Linux scripts (bash chokes on CRLF). Batch
  files now ship as CRLF.

---

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
