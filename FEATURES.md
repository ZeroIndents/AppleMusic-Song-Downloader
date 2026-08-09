# 🎧 Music High Res — Complete Feature Reference (FEATURES.md)

> The exhaustive, plain-English catalog of everything this app can do: every
> source, every audio codec, every setting, every CLI flag, every API
> endpoint, every script. If you're looking for "does it do X?", it's here.
> For the *how-it-works* internals see `docs/DOCUMENTATION.md`; for the
> 5-minute getting-started see `README.md`.

---

## 1. What it is, in one paragraph

Music High Res is a local web app (plus CLI) that downloads music **you
already pay to stream** into an organized, portable local library:

- **Apple Music** → lossless **ALAC** (up to 24-bit/192 kHz), **AAC 256 kbps**, or
  **Dolby Atmos** — via two swappable engines (**gamdl** default, **amdl** optional)
- **Spotify** → **OGG Vorbis 160/320 kbps** via **votify**
- **YouTube Music** → **AAC 128/256 kbps** or **Opus 256 kbps** via **gytmdl**
- Automatic **ALAC → FLAC** conversion (lossless-to-lossless, zero quality loss)

Everything lands in `Artist/Album/Track` folders with embedded cover art and
synced lyrics — ready for Plex, Jellyfin, Navidrome, or any music server.
The app binds to **127.0.0.1 only** (your machine, not your LAN).

---

## 2. Audio codecs — the complete table

| Codec | Bitrate / depth | Source | File | Needs | Notes |
|---|---|---|---|---|---|
| **ALAC** (Apple Lossless) | up to 24-bit / 192 kHz | Apple Music | `.m4a` | wrapper (wrapper-v2 or amdl) + active Apple Music sub | gamdl's lossless codec. Bit depth/sample rate are set by the label's master. |
| **AAC-LC** ("web") | 256 kbps | Apple Music | `.m4a` | cookies only (no wrapper) | Fallback when ALAC is unavailable ("ALAC · AAC fallback"). |
| **Dolby Atmos** | lossy object audio (up to 2768 kbps cap on amdl) | Apple Music | `.m4a` (E-AC-3 JOC) | wrapper | Spatial audio. gamdl calls it `atmos`; amdl caps the bitrate. |
| **FLAC** | up to 24-bit / 192 kHz (same PCM as source) | local conversion | `.flac` | ffmpeg | Converted from ALAC — **lossless → lossless**, no quality lost. AAC sources are never converted. |
| **AAC** | 128 kbps (itag 140) | YouTube Music | `.m4a` | nothing (free) | Anonymous `tv` client; YouTube increasingly restricts it. |
| **AAC** | 256 kbps (itag 141) | YouTube Music | `.m4a` | YouTube cookies + Premium | Uses the `web_music` client — more reliable than free itag. |
| **Opus** | 256 kbps (itag 774) | YouTube Music | `.opus` | YouTube cookies + Premium | Best size/quality trade-off on YouTube. |
| **OGG Vorbis** | 160 kbps (free) | Spotify | `.ogg` | Spotify cookies | votify's free-tier quality. |
| **OGG Vorbis** | 320 kbps (Premium) | Spotify | `.ogg` | Spotify cookies + Premium | ⚠️ Spotify has suspended accounts caught using third-party downloaders — at your own risk. |
| **AAC** (transcoded stream) | 320 kbps ADTS | in-app player only | stream | ffmpeg | ALAC is transcoded on the fly for browsers that can't decode it (Chrome/Firefox/Edge). Not saved to disk. |

### Quality definitions in the app's quality picker

| Picker option | Actual behavior |
|---|---|
| **ALAC · AAC fallback** | `alac,aac-web` — lossless when possible, else AAC 256. Default, safest. |
| **ALAC only** | `alac` — requires the wrapper. Fails if lossless isn't available. |
| **AAC 256** | `aac-web` — works with just cookies, always. |
| **Dolby Atmos** | `atmos` — requires the wrapper. |

### Quality verification

After a batch finishes, new files are probed with **ffprobe** and the log
shows the *real* codec/bit-depth (e.g. `ALAC 24/96`) — with a warning when
ALAC was requested but tracks came back AAC (that happens when a label
doesn't offer lossless). The Library shows quality badges per album/artist.
Probes are cached in `quality_cache.json` (keyed by path + mtime), so
re-scanning is fast.

---

## 3. Download sources & engines

| Source | Engine | Installed by | Command built by |
|---|---|---|---|
| Apple Music (default) | **gamdl** v3.8.5 (pinned) | Homebrew (`brew install gamdl`) or pip | `build_gamdl_command` |
| Apple Music (alternate) | **amdl** (Go, Docker) | `setup_amdl_wrapper.sh` | `build_amdl_command` |
| Spotify | **votify** 1.9.9 | pip (venv, pinned in requirements.txt) | `build_votify_command` |
| YouTube Music | **gytmdl** 2.1.6 | pip (venv, pinned) | `build_gytmdl_command` |

- **Auto-routing** — the app classifies each pasted URL: `open.spotify.com` /
  `spotify.link` → votify; `youtube.com` / `youtu.be` → gytmdl; anything else →
  gamdl. Job cards tag their engine (gamdl / votify / gytmdl / amdl).
- **One source per batch** — mixing Apple + Spotify + YouTube links in one
  Download click is rejected with a clear error.
- **Apple engine toggle** — Settings → *Apple engine* flips between gamdl and
  amdl. Both Apple wrappers share port **10020**, so only one runs at a time
  (starting the amdl wrapper stops wrapper-v2 automatically, and vice versa).

### What URL types are supported per source

**Apple Music (gamdl/amdl):** songs, albums, playlists, artists (auto-selects
"All albums" by default — change in Settings), music videos, and **Your
Library URLs** (`music.apple.com/{cc}/library/{songs|albums|playlist}/{id}` —
a built-in **delta sync**, since re-running skips already-downloaded files).

**Spotify (votify):** songs, albums, playlists, artists.

**YouTube Music (gytmdl):** songs, albums, playlists.

---

## 4. Feature catalog (the whole list)

### Downloading
- Multi-engine auto-routing (Apple / Spotify / YouTube Music) — §3
- Per-batch quality picker (ALAC·fallback / ALAC / AAC / Atmos) — §2
- **Live logs** — every job streams its output into an expandable card
- **Progress bars** — shimmer for downloads, determinate for FLAC conversion
- **Cancel** any running job; **↻ Retry** any failed/cancelled job (same URLs + options)
- **Queue persistence** — queued/running jobs survive an app restart and re-queue on launch
- **Concurrency limit** — cap simultaneous jobs (default 2; Settings → Max concurrent downloads)
- **Download scheduler** — set a window (`02:00-06:00`, may wrap midnight); queued jobs wait for it
- **Auto-retry** — failed jobs retry with 1m → 5m → 15m backoff (0–3 attempts; default 2)
- **Skip already-downloaded** — Settings toggle; links whose tracks are all on
  disk are dropped automatically (the chips show green **✓ owned n/n**)
- **Overwrite** — re-download over existing files (off by default)
- **Library URLs** — download your whole Apple Music library, or use as delta sync — §3
- **Watch folder** — drop a `.txt` / `.m3u` / `.url` containing links into a
  folder and it downloads automatically (moved to `.done/` after)
- **Clipboard suggest** — a paste pill appears when an Apple Music link is on
  your clipboard and the URL box is empty
- **URL preview chips** — before you commit, chips show kind + track count
  (Apple Music JSON-LD, Spotify embed page, YouTube via yt-dlp flat metadata)
- **30-second previews** — ▶ on any song/album chip plays the free Apple
  snippet before you download (Apple links only)

### Library management (in-app "Library" card)
- Browse artists → albums with **track counts, disk sizes, quality badges**
- **Search box** filters artists/albums/playlists as you type
- **Open in Finder** on every album/artist
- **Rename folders** in place (✎ button)
- **Tag editor** (✎) — per-track title / artist / album / album artist / track /
  year, written with mutagen
- **Duplicate finder** — files with the same name + size (playlist copies excluded)
- **Smart dupes** (🎧) — same *song* by **audio fingerprint** (first 15s decoded
  to PCM, sha256), catching renames + container swaps (ALAC vs FLAC)
- **Format cleanup** (🧹) — tracks that exist in an album in more than one
  format (e.g. ALAC + FLAC after auto-conversion); marks **KEEP** on the best
  copy and deletes the rest — one at a time or "delete all but best"
- **Universal cleanup** — a 🗑 row on top of the Cleanup panel acts on the
  **whole library** in one click: **delete all FLAC**, **delete all ALAC**, or
  **delete all but best** (every non-keep copy of every duplicate pair). Each
  button shows its live file count + size (via `GET /api/library/cleanup`) and
  is disabled when there's nothing to delete; the confirm dialog shows the
  count before anything moves (`POST /api/library/cleanup {action}`)
- **Recoverable trash** — deletes move to `.trash/` (per-file Restore + Empty;
  Empty is the only irreversible action and double-confirms)
- **SQLite ledger** (📒) — every completed download is recorded in
  `data/library.sqlite` (path, source URL, engine, tags, codec, size, mtime,
  when, which job). The **✓ owned** chips read it for **exact** "do I already
  have this?" answers (per-track rows checked against disk) instead of
  folder-name guessing; pre-ledger libraries fall back to the folder scan.
  The Ledger panel shows totals, an engine/codec split, files recorded but
  **missing on disk** (deleted or in `.trash`), and a **🔄 Rebuild from disk**
  button that re-indexes the whole library in one pass. Library album rows +
  the tag editor show the **downloaded-at** date from the ledger
- **Delta sync (ledger-driven)** — with **"Skip owned tracks (delta)"** on in
  Settings, re-running a Spotify/YouTube album or playlist link resolves it,
  filters out every track the ledger already owns (tag *and* filename match,
  checked against disk), and only downloads what's missing. Fully-owned links
  are dropped without a job; the download card shows how many were skipped
- **Engine ledger** — optional **"Use engine ledger"** toggle (Settings) passes
  `--database-path` to gamdl and votify so each engine keeps its own SQLite
  download ledger next to the app-level one
- **Stats dashboard** (📊) — totals, size, codec split, top artists
- **New-release tracker** (✨ Releases) — albums from your artists in the last
  90 days (iTunes Search API, 6h cache), one-click download
- **Import library** (📥) — read an Apple Music `library.xml` (File → Library →
  Export Library), match tracks on the catalog, queue them as one batch
- **Backup export** (⬇) — JSON manifest of config + full library index
- **In-app player** — play albums in the browser: seek, volume, prev/next,
  real album cover, **macOS now-playing widget** (Media Session), keyboard
  shortcuts (Space / ←→ / ↑↓ / M), volume remembered, buffering + error toasts.
  ALAC is transcoded to AAC on the fly for browsers that can't decode it.
- **Auto-refresh** — the Library re-scans when a download batch finishes

### Playlists
- **Save playlist files (.m3u)** — `Playlists/{artist}/{title}.m3u` listing
  tracks in order (no duplication). On by default.
- **Copy playlist tracks into a folder** — each playlist's tracks **copied**
  into `Playlists/{artist}/{title}/` so a playlist is one browsable folder.
  On by default; duplicates files on disk (disable if storage is tight).
- **Hardlink playlist folders** — same feature with APFS hardlinks = **zero
  extra disk** (falls back to copy automatically).

### Conversion & organization
- **Auto-convert ALAC → FLAC** — after each download, new ALAC tracks get a
  `.flac` sibling (AAC skipped automatically; both files kept)
- **Manual conversion** — "Convert ALAC → FLAC" card converts any folder; CLI `--to-flac`
- **Folder templates** — custom album (`{album_artist}/{album}`) and playlist
  (`Playlists/{playlist_artist}`) templates
- **Use album release date** for tags (instead of track date)
- **Embedded cover art** + optional **save cover as file**
- **Synced lyrics (.lrc)** on by default; **amdl syllable lyrics** (word-by-word,
  incl. K-pop translation) when using the amdl engine

### Automation & notifications
- **Music-server rescan hook** — POST a webhook when a batch finishes
  (Navidrome/Plex/Jellyfin scan endpoints)
- **New-release notify** — the Releases panel's 🔔 POSTs the list to
  ntfy.sh / Pushover / any JSON webhook
- **gamdl update pill** — header pill turns yellow with a tooltip when a newer
  gamdl release exists; also warns on version drift from the pinned release

### Wrapper & login (lossless ALAC / Atmos)
- **Guided setup wizard** in the app — "5 · Wrapper & login" → **⚙ Setup the
  wrapper**: give it the Apple Music Android APK (path or URL) + optional Apple
  ID, it streams the build log into the page
- **Login from the browser** — Apple ID + password; 2FA code box auto-focuses,
  auto-submits on 6 digits, shakes on rejection, "resend" cools down 30s
- **Intel-Mac fix** — the wizard applies `fix_wrapper_libs.sh` when needed
  (the `-42812` FairPlay symbol bug)
- **amdl mode** — full panel for the alternate engine (start/stop/logs, code
  written straight to the wrapper's code file, port-conflict warnings)

### System
- **System logs card** — tails `logs/app.log` + `logs/launcher.log` in the UI
  (auto-refresh 5s), no Terminal needed
- **Rotating server log** — 1 MB × 3 backups, captures unhandled exceptions
  from main + worker threads
- **Getting-started checklist** — the "0 · Getting started" card shows what's
  ready (gamdl, ffmpeg, cookies, Docker, wrapper, Spotify/YT cookies, output
  folder) with a hint for each missing item
- **One-click launcher** — `./start.sh` (macOS + Linux), double-click
  `Start Music High Res.command`, or `Music High Res.app` (built with
  `./make_app.sh`), or **`Start Music High Res.bat` / `start.ps1` (Windows)**
  — boots Docker → wrapper → app → browser automatically.
  On macOS the first launch builds the `.app` bundle and drops a **Desktop
  shortcut** to it, so future launches are a single double-click.

---

## 4.5 gamdl options deep-dive (Settings → Apple engine = gamdl)

gamdl is the default Apple Music engine. The app builds the gamdl command
from the config **with `-n`** (ignore `~/.gamdl/config.ini`, use explicit
flags) so every run is reproducible from `config.json`. Here's exactly which
gamdl flag each setting maps to (see `build_gamdl_command` in
`downloader.py`), with examples of what the generated command looks like:

```
gamdl -n \
  -c cookies.txt -o ~/Music/Apple\ Music \
  --song-codec-priority alac,aac-web \
  --synced-lyrics-format lrc --cover-size 1200 --log-level INFO \
  --save-playlist \
  --music-video-resolution 1080p --music-video-codec-priority h264,h265 \
  --cover-format jpg \
  --album-folder-template "{album_artist}/{album}" \
  --playlist-folder-template "Playlists/{playlist_artist}" \
  [--use-wrapper --wrapper-url http://127.0.0.1] \
  <url...>
```

| Setting | gamdl flag | What it does |
|---|---|---|
| `song_codec_priority` | `--song-codec-priority` | `alac,aac-web` (lossless→AAC fallback), `alac`, `aac-web`, `atmos`. **The quality picker in the UI sets this per-download.** |
| `synced_lyrics` (on) | *(default)* / `--no-synced-lyrics` | Embed synced lyrics. Off adds `--no-synced-lyrics`. |
| `synced_lyrics_format` | `--synced-lyrics-format` | `lrc` (or gamdl's other formats: `srt`, `vtt`, …). |
| `cover_size` | `--cover-size` | Embedded cover size in px (default 1200). |
| `save_cover` | `-s` | Also save the cover as a standalone file. |
| `save_playlist` | `--save-playlist` | Write `Playlists/{artist}/{title}.m3u` listing tracks in order. |
| `music_video_resolution` | `--music-video-resolution` | Music-video quality: `240p` … `2160p` (default `1080p`). |
| `music_video_codec_priority` | `--music-video-codec-priority` | Comma list: `h264`, `h265`, `ask`. H.265 = smaller files, needs player support. |
| `cover_format` | `--cover-format` | `jpg` / `png` / `raw`. |
| `album_folder_template` | `--album-folder-template` | Layout template, e.g. `{album_artist}/{album}`. Placeholders: `{album_artist}`, `{album}`, `{year}`, `{track_number}`, … |
| `playlist_folder_template` | `--playlist-folder-template` | e.g. `Playlists/{playlist_artist}`. |
| `use_album_date` | `--use-album-date` | Tag tracks with the album release date instead of the track date. |
| `overwrite` | `--overwrite` | Re-download over existing files. |
| `artist_auto_select` | `--artist-auto-select` | What an artist link downloads: `all-albums` (default), `main-albums`, `compilation-albums`, `live-albums`, `singles-eps`, `top-songs`. |
| `use_wrapper` + `wrapper_url` | `--use-wrapper --wrapper-url` | Route through wrapper-v2 for ALAC/Atmos (FairPlay decryption). |
| `cookies_path` | `-c` | Netscape cookies file (the app resolves `music.apple.com_cookies.txt` as a fallback name). |
| `output_path` | `-o` | Where `Artist/Album/Track.m4a` lands. |

**Cookies mode vs wrapper mode:** with `use_wrapper` off, gamdl downloads
AAC 256 kbps using your `media-user-token` cookie. With the wrapper on, the
command gains `--use-wrapper --wrapper-url http://127.0.0.1` and gamdl calls
the wrapper for FairPlay licenses + decryption — which is what unlocks ALAC
and Atmos, and **no cookies are needed**.

**Library URLs work natively:** `music.apple.com/{cc}/library/{songs|albums|
playlist}/{id}` links are passed straight to gamdl. Because gamdl skips files
that already exist, re-running a library URL only grabs new additions — a
built-in delta sync.

## 4.6 amdl options deep-dive (Settings → Apple engine = amdl)

**amdl** (`ghcr.io/zhaarey/apple-music-downloader`) is the optional Go-based
Apple engine. It runs as a Docker container with `--network host` and decrypts
through the **itouakirai wrapper** (ports 10020 + 20020) instead of wrapper-v2.
The app generates a full `config.yaml` (in `data/amdl/config.yaml`, mounted
into the container at `/app/config.yaml`) and then runs:

```
docker run --rm --network host \
  -v <output>:/downloads -v <config.yaml>:/app/config.yaml \
  ghcr.io/zhaarey/apple-music-downloader [--atmos | --aac | --song] <url...>
```

### The amdl knobs (Settings)

| Setting | config.yaml key | What it does |
|---|---|---|
| `amdl_atmos_max` | `atmos-max` | Dolby Atmos bitrate cap in kbps: `2768` (max) or `2448`. |
| `amdl_alac_max` | `alac-max` | ALAC max sample rate: `192000` / `96000` / `48000` / `44100`. |
| `amdl_lrc_type` | `lrc-type` | `lyrics` (standard synced) or `syllable-lyrics` (word-by-word, incl. K-pop romanization/translation). |
| `amdl_cover_size` | `cover-size` | Embedded cover resolution, e.g. `5000x5000`. |

### How the codec picker maps to amdl flags

- `alac` (or `alac,aac-web`) → no flag — ALAC is amdl's default download mode.
- `atmos` → `--atmos`.
- `aac-web` → `--aac` (aac-lc).
- Single-song links (`…?i=…` or `/song/`) additionally get `--song`.

### amdl's built-in extras the config turns on

- `embed-cover: true`, `cover-size` from Settings — 5000×5000 covers supported.
- `embed-lrc: true` — lyrics embedded; `lrc-type` switches to syllable lyrics.
- `alac-save-folder` / `atmos-save-folder` / `aac-save-folder` / `mv-save-folder`
  all point at `/downloads` (the mounted output folder), so files land in the
  same `{UrlArtistName}/{AlbumName}/{TrackNumber}. {TrackName}` layout as gamdl.
- `exit-on-error: true` is forced — amdl exits non-zero on failure instead of
  waiting interactively, which the retry/silent-failure machinery needs.
- `aac-type: aac-lc`, `get-m3u8-mode: hires`, `storefront: us`, `max-memory-limit: 256`.
- Conversion options (`convert-after-download`, `convert-format: flac`, …) ship
  in the config with sane defaults, ready to enable via `amdl`'s own docs.
- `alac-fix: false` — flip on for malformed ALAC packets (see amdl README).

### amdl gotchas

- **Port clash:** the itouakirai wrapper uses port **10020**, the same as
  wrapper-v2. Only one Apple wrapper can run at a time — starting the amdl
  wrapper stops wrapper-v2 automatically (the UI warns on `state: conflict`).
- **Image pull:** the first amdl download pulls the image if missing
  (`amdl_image_present` pre-flights this and fails fast with a clear message).
- **Engine switching** is just Settings → Apple engine. Both engines write to
  the same `Artist/Album` folders and share the wrapper login flow
  (Apple ID + 2FA) — gamdl remains the default; amdl is opt-in.

---

## 5. Settings — every key, default, and what it does

Stored in `config.json` next to the app. All editable in the app's Settings.

| Key | Default | What it does |
|---|---|---|
| `output_path` | `~/Music/Apple Music` | Where `Artist/Album/Track` folders land |
| `cookies_path` | `cookies.txt` | Apple Music cookies (Netscape format) |
| `use_wrapper` | `false` | Enable wrapper mode (ALAC/Atmos) |
| `wrapper_url` | `http://127.0.0.1` | wrapper-v2 base URL |
| `song_codec_priority` | `alac,aac-web` | Codec priority list for gamdl |
| `synced_lyrics` | `true` | Save synced lyrics (.lrc) |
| `synced_lyrics_format` | `lrc` | Lyrics format (gamdl option) |
| `cover_size` | `1200` | Embedded cover size (px) |
| `save_cover` | `false` | Also save cover art as a file |
| `artist_auto_select` | `all-albums` | What to download for artist links: `all-albums`, `main-albums`, `compilation-albums`, `live-albums`, `singles-eps`, `top-songs` |
| `overwrite` | `false` | Re-download over existing files |
| `convert_to_flac` | `false` | Auto-convert new ALAC files to FLAC |
| `save_playlist` | `true` | Write `Playlists/{artist}/{title}.m3u` |
| `copy_playlist_folders` | `true` | Copy playlist tracks into a browsable folder |
| `music_video_resolution` | `1080p` | 240p … 2160p |
| `music_video_codec_priority` | `h264,h265` | h264 / h265 / ask |
| `cover_format` | `jpg` | jpg / png / raw |
| `album_folder_template` | `{album_artist}/{album}` | Album folder layout |
| `playlist_folder_template` | `Playlists/{playlist_artist}` | Playlist folder layout |
| `use_album_date` | `false` | Use album release date for tags |
| `skip_owned` | `false` | Drop fully-owned links before downloading |
| `schedule_window` | `""` | `HH:MM-HH:MM` (24h, may wrap midnight); empty = run now |
| `max_concurrent` | `2` | Simultaneous jobs |
| `auto_retry` | `2` | Extra attempts after failure (1m→5m→15m backoff) |
| `watch_folder` | `""` | Auto-download links dropped here (`.txt`/`.m3u`/`.url`) |
| `notify_url` | `""` | Webhook for new-release notifications |
| `playlist_hardlink` | `false` | Playlist copies as APFS hardlinks (zero extra disk) |
| `verify_quality` | `true` | Probe new files + warn on ALAC→AAC downgrade |
| `storefront` | `US` | iTunes Search storefront for Migrate/Import matching |
| `scan_hook_url` | `""` | POSTed when a batch finishes (server rescan) |
| `ytm_itag` | `140` | YouTube Music quality: `140` AAC 128k free · `141` AAC 256k · `774` Opus 256k (Premium) |
| `ytm_cookies_path` | `""` | YouTube cookies (only for Premium itags; falls back to `cookies_path`) |
| `spotify_audio_quality` | `160` | `160` free · `320,160` Premium |
| `spotify_cookies_path` | `""` | Spotify cookies (required for Spotify; falls back to `cookies_path`) |
| `apple_engine` | `gamdl` | `gamdl` (default) or `amdl` |
| `amdl_atmos_max` | `2768` | amdl Atmos bitrate cap (kbps): `2768` / `2448` |
| `amdl_alac_max` | `192000` | amdl ALAC max sample rate: `192000` / `96000` / `48000` / `44100` |
| `amdl_lrc_type` | `lyrics` | `lyrics` (synced) or `syllable-lyrics` (word-by-word) |
| `amdl_cover_size` | `5000x5000` | amdl embedded cover resolution |

---

## 6. CLI (`cli.py`)

```
.venv/bin/python cli.py [URLS...] [options]
```

With no URLs you're prompted to paste them (one per line, blank line to start).

| Flag | Description |
|---|---|
| `--check` | Print a **readiness checklist** (gamdl, ffmpeg, gytmdl, votify, cookies, Docker, wrapper, output folder) and exit. Exit code 0 = all required ready, 1 = something missing — the CLI twin of the app's "0 · Getting started" card. |
| `--codec CODEC` | `alac` \| `alac,aac-web` \| `aac-web` \| `atmos` (overrides config) |
| `--output`, `-o DIR` | Output folder (overrides config) |
| `--wrapper` / `--no-wrapper` | Force wrapper on/off (overrides config) |
| `--to-flac [DIR]` | Convert ALAC files in DIR (default: config output) to FLAC |
| `--overwrite-flac` | Re-convert files that already have a `.flac` |
| `--ledger` | Print ledger stats: indexed tracks, bytes, engine/codec split, files recorded but missing on disk |
| `--ledger-rebuild` | Wipe and re-index the ledger from the whole output folder (pre-ledger libraries) |

The CLI drives the **same** `JobManager` as the web app (same config, same
engines, same retry/logging), so `cli.py` and the UI are interchangeable.

---

## 7. HTTP API (internal, loopback only)

Base: `http://127.0.0.1:8741`. All JSON.

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | The web UI |
| `/api/status` | GET | Pill data: versions, cookies, wrapper, codec, python |
| `/api/onboarding` | GET | Getting-started checklist (what's ready/missing + hints) |
| `/api/config` | GET/POST | Read/save settings |
| `/api/download` | POST | Start a download job `{urls, codec?, output_path?, use_wrapper?, …}` |
| `/api/convert` | POST | Start an ALAC→FLAC job `{source_dir?, overwrite?}` |
| `/api/jobs` | GET/DELETE | List jobs / clear finished |
| `/api/jobs/<id>` | GET | Job detail (live log) |
| `/api/jobs/<id>/retry` | POST | Re-queue a finished job |
| `/api/jobs/<id>/cancel` | POST | Cancel a job |
| `/api/url-preview?url=` | GET | Link kind + track count + ownership |
| `/api/preview-url?url=` | GET | 30s preview URL for an Apple link |
| `/api/library?q=` | GET | Library scan (artists → albums → tracks, sizes, quality) |
| `/api/library/album?path=` | GET | Tracks of one album (tags, codec, duration) |
| `/api/library/open` | POST | Reveal a path in Finder |
| `/api/library/rename` | POST | Rename an artist/album folder |
| `/api/library/duplicates` | GET | Same name+size duplicates |
| `/api/library/smart-duplicates` | GET | Same-audio-fingerprint duplicates |
| `/api/library/format-duplicates` | GET | Multi-format duplicates (FLAC+ALAC) |
| `/api/library/delete` | POST | Move file(s) to `.trash` (recoverable) |
| `/api/library/trash` | GET | List trash |
| `/api/library/trash/restore` | POST | Restore a trashed file |
| `/api/library/trash/empty` | POST | Permanently empty trash |
| `/api/library/export` | GET | JSON backup (config + library) |
| `/api/library/import` | POST | Import `library.xml` `{path, playlist?, preview_only?}` |
| `/api/library/ledger` | GET | SQLite ledger stats (tracks, bytes, engine/codec split, missing files) |
| `/api/library/ledger/rebuild` | POST | Wipe + re-index the ledger from disk |
| `/api/audio?path=` | GET | Stream a file (Range/seek); `&transcode=1` for ALAC→AAC; `&t=` seek offset |
| `/api/art?path=` | GET | Embedded cover art (player thumbnail) |
| `/api/tags` | GET/POST | Read/write file tags |
| `/api/stats` | GET | Library stats dashboard |
| `/api/new-releases` | GET | New releases from your artists (90 days, 6h cache) |
| `/api/notify/releases` | POST | Send releases to the notify webhook |
| `/api/scan-hook` | POST | Ping the rescan webhook manually |
| `/api/wrapper` | GET | Wrapper status (mode-aware: wrapper-v2 or amdl) |
| `/api/wrapper/2fa` | POST | Submit 2FA code |
| `/api/wrapper/restart` | POST | Restart wrapper login |
| `/api/wrapper/login` | POST | Save Apple ID + restart login |
| `/api/wrapper/setup` | GET/POST | Guided setup state / start setup |
| `/api/wrapper/amdl/start` \| `/stop` | POST | Start/stop the amdl wrapper |
| `/api/migrate/preview` | POST | Resolve Spotify/YT link + match tracks on Apple Music |
| `/api/logs?file=app\|launcher` | GET | Tail a project log |

---

## 8. Scripts & files

| File | Purpose |
|---|---|
| `start.sh` | **Universal one-click launcher** (macOS + Linux): first-run setup (with a real venv-health check — a half-created `.venv` re-runs setup instead of crashing), prerequisite checklist, Docker → wrapper → app → browser. On macOS it also builds `Music High Res.app` once and puts a **Desktop shortcut** pointing at it (skip with `MHR_NO_DESKTOP=1`). |
| `start.ps1` | **Windows launcher** (PowerShell twin of `start.sh`): first-run setup + checklist + Docker → wrapper → app → browser; flags `-Min` / `-NoDocker` / `-NoBrowser`; logs to `logs\launcher.log`. |
| `Start Music High Res.command` | macOS double-click launcher (thin wrapper around `start.sh`) |
| `Start Music High Res.bat` | Windows double-click launcher (thin wrapper around `start.ps1`) |
| `make_app.sh` | Builds `Music High Res.app` (Dock icon, standalone window) |
| `install.sh` | One-command macOS installer (Homebrew, gamdl, ffmpeg, repo, venv) |
| `install_linux.sh` | One-command Linux installer (apt/dnf/pacman, gamdl via pip) |
| `install.ps1` | One-command Windows installer (winget python/ffmpeg/git, gamdl via pip, repo, venv) |
| `setup.ps1` / `setup.bat` | First-run setup for Windows (venv + pinned dependencies) |
| `setup.sh` | First-run setup: venv + pinned dependencies |
| `setup_wrapper.sh` | wrapper-v2 setup: clone, extract APK libs, stage runtime, build |
| `setup_amdl_wrapper.sh` | amdl engine setup: pull amdl + itouakirai wrapper images |
| `login_wrapper.sh` | Terminal Apple ID login for wrapper-v2 |
| `login2fa.sh` | Fresh login + submit the new 2FA code in one go |
| `fix_wrapper_libs.sh` | Intel-Mac FairPlay fix (swaps 7 `.so` files, rebuilds) |
| `app.py` | Flask server + API (waitress, `127.0.0.1:8741`) |
| `downloader.py` | Config, Job manager, engine command builders, library tools |
| `wrapperctl.py` | Wrapper status/2FA/login/setup (wrapper-v2 **and** amdl) |
| `migrate.py` | Spotify/YouTube resolution + iTunes matching + library.xml import |
| `cli.py` | Terminal interface (same core as the app) |
| `static/index.html` | The whole UI (no build step) |
| `config.json` | User settings |
| `quality_cache.json` | ffprobe quality cache (path+mtime keyed) |
| `data/library.sqlite` | SQLite ledger — authoritative index of every download (runtime file) |
| `logs/` | `app.log` (rotating server log) + `launcher.log` (startup) |
| `pending_jobs.json` | Queued/running jobs saved across restarts (runtime file) |

---

## 9. Legal & safety notes

- Downloads are for **personal, non-commercial, experimental** use of content
  you already pay to stream (see `LICENSE`).
- **Cookies are live sessions** — never share/commit `cookies.txt`, Spotify
  cookies, or YouTube cookies (all gitignored).
- `wrapper-v2/.env` and `wrapper-amdl/.env` hold your Apple ID password
  (written `0600`, kept local).
- The app binds to **127.0.0.1 only** — not reachable from your LAN.
- ⚠️ Spotify has suspended accounts caught using third-party downloaders.
- All downloaded media is subject to the Apple Media Services Terms and local
  law.
