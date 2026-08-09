# 🎧 AppleMusic Song Downloader

Download **lossless ALAC (up to 24-bit/192kHz)**, **AAC 256kbps**, or **Dolby Atmos**
music from your Apple Music subscription — plus **Spotify** and **YouTube Music**
— into an organized local library you own, ready for Plex, Jellyfin,
Navidrome, or any home music server.

- 🌐 **Local web app** — paste links from Apple Music, Spotify, or YouTube Music, pick quality, watch downloads live
- ⌨️ **CLI** — the same thing from the Terminal
- 🔁 **Automatic ALAC → FLAC conversion** — for servers that prefer FLAC
- ⚙️ **One-click boot** — `./start.sh` (macOS + Linux), double-click `Start Music High Res.command`, or `Music High Res.app` — Docker + wrapper + app all start from one action

> 📖 **Looking for everything it can do?** The complete catalog — every
> feature, every audio codec (ALAC / AAC / Atmos / FLAC / Opus / OGG Vorbis),
> every setting, every CLI flag, every API endpoint — lives in
> **[FEATURES.md](FEATURES.md)**. The internals are in
> [docs/DOCUMENTATION.md](docs/DOCUMENTATION.md). What changed, release by
> release: **[CHANGELOG.md](CHANGELOG.md)**.

> **Core engines:** Apple Music downloads wrap **[gamdl](https://github.com/glomatico/gamdl)**,
> with **[amdl](https://github.com/zhaarey/apple-music-downloader)** available as
> a second Apple engine (Settings → Apple engine); Spotify downloads wrap
> **[votify](https://github.com/glomatico/votify)**, and YouTube Music downloads
> wrap **[gytmdl](https://github.com/glomatico/gytmdl)** — credit for the actual
> download/decode machinery goes to those projects. This repo adds the friendly
> UI, CLI, FLAC conversion, wrapper automation, and setup tooling on top. See
> **Credits** below.

---

## What you need

| Requirement | macOS | Windows | Linux | Notes |
|---|---|---|---|---|
| Python 3.10+ | ✅ 3.14.6 | ✅ 3.12+ | ✅ | macOS via brew / python.org; Windows via python.org / winget; Linux via your package manager |
| gamdl | ✅ v3.8.5 | ✅ via pip | ✅ via pip | macOS: `brew install gamdl` · Win/Linux: `pip install gamdl` |
| ffmpeg | ✅ v8.1.2 | ✅ winget | ✅ apt/dnf/pacman | needed for FLAC + in-app player |
| gytmdl + votify | ✅ via pip | ✅ via pip | ✅ via pip | installed by `setup.sh` / `setup.ps1` (pinned in requirements.txt) |
| **Active Apple Music subscription** | ✅ | ✅ | ✅ | required for every Apple Music download |
| Apple Music cookies | ✅ | ✅ | ✅ | Step 1 (not needed if using the wrapper) |
| Docker Desktop + wrapper | ✅ | ✅ (WSL2) | ✅ | Step 3 — required for **ALAC / Atmos** |
| amdl image (optional) | 🔀 | 🔀 | 🔀 | second Apple engine — see Step 3 (alternative) |

---

## Quick start (new machine, 15 minutes)

**macOS — one command does everything** (installs Homebrew + gamdl + ffmpeg,
downloads the repo, sets up the app):

```bash
curl -fsSL https://raw.githubusercontent.com/ZeroIndents/AppleMusic-Song-Downloader/main/install.sh | bash
```

**Windows — one command does everything** (installs Python + ffmpeg via winget,
gamdl via pip, downloads the repo, sets up the app):

```powershell
irm https://raw.githubusercontent.com/ZeroIndents/AppleMusic-Song-Downloader/main/install.ps1 | iex
```

Already have the folder?

```bash
# 1. Install prerequisites (macOS)
brew install gamdl ffmpeg

# 2. Set up the app (creates .venv + installs dependencies)
./setup.sh

# 3. Export cookies from a browser signed into music.apple.com → save as cookies.txt
#    (browser extension: "Get cookies.txt LOCALLY")

# 4. Start the app — one click, on macOS or Linux
./start.sh
#    …or double-click Start Music High Res.command (macOS)
#    …or run: ./make_app.sh && open "Music High Res.app" (Dock icon, macOS)

# 5. (Optional, for lossless ALAC) Docker + wrapper — see Step 3
./setup_wrapper.sh ~/Downloads/apple-music.apk
```

**Windows, already have the folder?**

```powershell
# 1. Install prerequisites (one-time)
winget install Python.Python.3.12 Gyan.FFmpeg
pip install gamdl

# 2. Set up the app (creates .venv + installs dependencies)
.\setup.bat          # or: powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1

# 3. Export cookies from a browser signed into music.apple.com → save as cookies.txt

# 4. Start the app — one click
.\"Start Music High Res.bat"
#    …or: powershell -NoProfile -ExecutionPolicy Bypass -File start.ps1
#    …or run: .venv\Scripts\python.exe app.py

# 5. (Optional, for lossless ALAC) Docker Desktop + wrapper — see Step 3
#    (the wrapper setup script is bash; on Windows install Git for Windows / Git Bash first)
```

`start.sh` (macOS/Linux) and `start.ps1` (Windows) handle everything: first-run
setup, a prerequisite checklist, Docker startup, the ALAC wrapper, the app
server, and opening your browser.

---

## Step 1 — Export your Apple Music cookies (5 minutes)

gamdl logs in as *you* using your browser's Apple Music session cookies.

1. Sign in at **https://music.apple.com** and confirm your subscription is active (play any song).
2. Install a cookie exporter (Netscape format):
   - **Chrome / Edge / Brave**: [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - **Firefox**: [Export Cookies](https://addons.mozilla.org/addon/export-cookies-txt)
3. Export **only for `music.apple.com`** and save as **`cookies.txt`** in this folder.
   - Cookies expire — if a download stops with a 401, re-export.
4. The app's header pill should turn green (**Cookies ✓**).

> **Skip this if you use the wrapper (Step 3)** — wrapper mode logs in with your
> Apple ID directly and doesn't need cookies.

---

## Step 2 — Start the app

Four ways to launch — pick your favourite:

- **Universal (macOS + Linux)**: `./start.sh` — the one-click launcher. It runs
  setup on first launch (it even re-runs setup if it detects a half-created
  `.venv`), prints a friendly checklist of what's installed (gamdl, ffmpeg,
  cookies, Docker, wrapper), boots Docker + the ALAC wrapper when present,
  starts the app, and opens the browser. The ALAC wrapper starts with the app
  and stops again when you close it — **Docker Desktop itself stays running**. On macOS the first launch also builds
  `Music High Res.app` and puts a **Desktop shortcut** to it (future launches
  are a single double-click). Flags: `--min` (AAC only, skip Docker/wrapper),
  `--no-browser`, `--no-docker`.
- **Windows**: double-click **`Start Music High Res.bat`** (or run
  `start.ps1` — the Windows twin of `start.sh`, same flags: `-Min`,
  `-NoBrowser`, `-NoDocker`). First launch runs `setup.bat` automatically, and
  Docker Desktop is launched when installed.
- **Like a normal Mac app**: run `./make_app.sh` once, then double-click
  **`Music High Res.app`** in Finder. It gets a Dock icon and opens the UI in a
  standalone app-style window. *(The app stays in the Dock while it runs;
  right-click → Quit to stop it.)*
- **Classic**: double-click `Start Music High Res.command`, or run
  `./setup.sh` then `.venv/bin/python app.py` in a Terminal.

All launchers boot Docker + the ALAC wrapper automatically when needed, and
reuse an already-running server instead of failing with "Address already in
use". The app's header shows a **"0 · Getting started"** checklist so you
always know what's left to set up.

→ UI: **http://127.0.0.1:8741**

### Using the web app
1. Paste links — Apple Music, **Spotify**, or **YouTube Music** — one per line.
   The app auto-routes each to the right engine (chips under the box preview
   kind + track count before you commit).
2. Pick quality:
   - **ALAC · AAC fallback** — lossless when possible, otherwise AAC 256 (default, safest)
   - **ALAC only** — requires the wrapper
   - **AAC 256** — works with just cookies, always
   - **Dolby Atmos** — requires the wrapper
3. Hit **Download** and watch the live log (each job tags its engine: gamdl / votify / gytmdl).

### Using the CLI
```bash
.venv/bin/python cli.py --check                    # readiness checklist (exit 0 = all ready)
.venv/bin/python cli.py "https://music.apple.com/us/album/in-rainbows/1109714933"
.venv/bin/python cli.py --codec aac-web "https://music.apple.com/..."
.venv/bin/python cli.py --to-flac                    # convert ALAC → FLAC
.venv/bin/python cli.py --to-flac "/path/to/Albums" --overwrite-flac
.venv/bin/python cli.py --ledger                  # print ledger stats (tracks, bytes, engine/codec split, missing)
.venv/bin/python cli.py --ledger-rebuild          # re-index the ledger from disk
```

### Where files land
Default output: **`~/Music/Apple Music`**, organized as
`{Album Artist}/{Album}/{Track Number} {Title}.m4a` (+ `.flac` when conversion is on)
with embedded cover art and synced lyrics (.lrc). Change the folder in Settings —
point it straight at your music server's library if you like.

### Browsing your downloads (in-app Library)
The **"2 · Library"** card shows everything you've downloaded — artists expand
into albums with track counts and disk sizes, and each has an **Open in Finder**
button. It auto-refreshes when a download batch finishes, and the **search box**
filters artists/albums/playlists as you type.

**Playlists get organized too** (both on by default in Settings):
- **Save playlist files (.m3u)** — each playlist gets a
  `Playlists/{artist}/{title}.m3u` listing its tracks in order (no duplication).
- **Copy playlist tracks into a folder** — each playlist's tracks are **copied**
  into `Playlists/{artist}/{title}/` so the playlist is one browsable folder
  (duplicates files on disk — disable if your drive is tight).

Playlist tracks still land in their normal `Artist/Album` folders (that's what
keeps your library tidy); the `.m3u` + copied folder are added organization.

### Already-downloaded detection & whole-library downloads
- Paste a link and the chip under the box turns **green "✓ owned 12/12"** when
the whole album/playlist is already on disk (partial ownership shows a yellow
count). Enable **"Skip already-downloaded links"** in Settings and the Download
button drops fully-owned links automatically.
- **Your Library URLs work natively** — paste any `music.apple.com/{cc}/library/
{songs|albums|playlist}/{id}` link and gamdl downloads it. Because gamdl skips
existing files, re-running the same library URL later only grabs the **new
additions** — a built-in delta sync.

### Retry failed downloads
Failed or cancelled download cards get a **↻ Retry** button that re-queues the
same URLs with one click.

### Exposed gamdl options (Settings)
Settings now surfaces more of gamdl's knobs: **music video resolution**
(240p–2160p) and **codec priority** (h264/h265/ask), **cover art format**
(jpg/png/raw), custom **album/playlist folder templates** (e.g.
`{album_artist}/{album}`), and **use album release date** for tags.

### Spotify & YouTube Music downloads (Settings)
- **YouTube Music** — pick the quality itag: **AAC 128k** (free, no cookies) or
  **AAC 256k / Opus 256k** (Premium — needs a YouTube cookies file). Premium
  itags also work more reliably because they use YouTube's `web_music` client
  instead of the anonymous `tv` client, which YouTube increasingly restricts.
- **Spotify** — **160kbps** (free accounts) or **320kbps** (Premium), plus a
  cookies file exported from `open.spotify.com` ("Get cookies.txt LOCALLY").
  Spotify downloads need the cookies file; without it the job fails fast with a
  clear message. ⚠️ Spotify has suspended accounts caught using third-party
  downloaders — use at your own risk.
- Everything lands in the same `Artist/Album` library folders as Apple Music
  downloads (gytmdl writes `.m4a`, votify writes `.ogg`). Mixing Apple +
  Spotify + YouTube links in one batch is rejected with a clear error — one
  source per batch.

### Power-user features (all in Settings or the UI)
- **In-app player** — the Library's albums have a ▶ button that plays them
  right in the browser: seek, volume, next/previous track, all without leaving
  the app. It plays **every format in every browser** — ALAC files are
  auto-transcoded to AAC on the fly (ffmpeg) for Chrome/Firefox/Edge, which
  can't decode ALAC natively. The bar shows the **real album cover**, feeds the
  **macOS now-playing widget** (Media Session: lock screen / Control Center
  play, pause, skip, seek), remembers your **volume** between sessions, shows a
  buffering indicator and error toasts instead of silent failures, and answers
  to **keyboard shortcuts** (Space play/pause, ←/→ seek, ↑/↓ volume, M mute).
  Also included: **30s preview playback** — every song/album chip has a ▶
  button that plays a free 30-second Apple snippet before you download.
- **Tag editor** — the ✎ button on any album row opens a per-track editor
  (title / artist / album / album artist / track / year) that writes tags
  straight into the files with mutagen.
- **Smart duplicate finder** — the Library's 🎧 Smart dupes button matches
  files by *audio fingerprint* (first 15s decoded to PCM), so the same song
  with different filenames or containers (ALAC vs FLAC) is caught — not just
  identical name+size copies.
- **FLAC/ALAC cleanup** — the Library's 🧹 Cleanup button lists every track
  that exists in an album in more than one format (e.g. ALAC + FLAC after
  auto-conversion), marks the best copy **KEEP**, and lets you delete the rest
  — one file at a time or "Delete all but best". A **Universal cleanup** row on
  top acts on the **whole library** in one click: **delete all FLAC**, **delete
  all ALAC**, or **delete all but best** (every non-keep copy of every
  duplicate pair) — each button shows its file count + size before you commit.
  Deletes are **recoverable**: files move to `.trash/` in your music folder
  (with per-file Restore + Empty buttons).
- **Stats dashboard** — 📊 Stats shows totals, size, codec split, and your top
  artists.
- **Import your Apple Music library** — 📥 Import reads an exported
  `library.xml` (File → Library → Export Library), matches every track on the
  Apple Music catalog, and queues the matched ones as one batch.
- **New-release notifications** — with a Notify URL set in Settings, the
  Releases panel's 🔔 button POSTs the list to ntfy.sh/Pushover/any webhook.
- **Watch folder** — set a folder in Settings; drop a `.txt`/`.m3u`/`.url`
  containing Apple Music, Spotify or YouTube links and it downloads
  automatically (moved to `.done/` after).
- **Queue persistence** — queued/running downloads survive an app restart and
  are re-queued automatically on the next launch.
- **30s preview playback** — every song/album chip has a ▶ button that plays a
  free 30-second snippet before you download.
- **SQLite ledger** — every download is recorded in `data/library.sqlite`
  (path, URL, engine, tags, codec, size, when, which job). The **✓ owned**
  chips read it for exact ownership answers instead of folder-name guessing;
  the Library's **📒 Ledger** button shows totals, engine/codec split, files
  recorded but **missing on disk** (deleted or in `.trash`), and a **🔄 Rebuild**
  button that re-indexes the whole library. The Library's album rows + tag
  editor show the **downloaded-at** date from the ledger. With **"Use engine
  ledger"** on (Settings), gamdl/votify also keep their own `--database-path`
  sqlite file; and with **"Skip owned tracks (delta)"** on, re-running a
  Spotify/YouTube album or playlist link only fetches the tracks the ledger
  doesn't already own — a true delta sync.
- **Quality verification** — after a batch finishes, new files are probed with
  ffprobe and the log shows the real codec/bit-depth (e.g. `ALAC/24/96`), with a
  warning when ALAC was requested but tracks came back AAC. The Library shows
  quality badges per album (and per artist).
- **Hardlink playlist folders** — the playlist-folder copy can be done with APFS
  hardlinks instead, so a playlist folder costs **zero extra disk**.
- **Download scheduler** — set a window (e.g. `02:00-06:00`, may wrap midnight)
  and queued downloads wait for it.
- **Auto-retry** — failed jobs retry themselves with 1m → 5m → 15m backoff
  (0–3 attempts).
- **Concurrency limit** — cap how many gamdl jobs run at once (default 2).
- **New-release tracker** — the Library's **✨ Releases** button lists releases
  from your artists in the last 90 days, each one click away from the download
  list.
- **Duplicate finder** — the Library's **Duplicate finder** button lists files
  with the same name + size (playlist copies excluded) so you can keep the
  best one.
- **Backup export** — the Library's **⬇ Backup** button downloads a JSON
  manifest of your config + full library index.
- **Rename folders** — each album row has a ✎ button to rename the folder in
  place.
- **Clipboard suggest** — with an empty URL box and an Apple Music link on your
  clipboard, a paste pill appears.
- **Storefront picker** — the Migrate matcher uses your country's store
  (Settings → Storefront, default US).
- **Music-server rescan hook** — optionally POST a webhook when a batch
  finishes (Navidrome/Plex/Jellyfin scan endpoints).
- **gamdl update pill** — the header pill turns yellow with a tooltip when a
  newer gamdl release exists.

---

## Step 3 — Lossless ALAC & Dolby Atmos (the wrapper)

AAC 256kbps works with just cookies. **ALAC lossless** and **Atmos** require
`wrapper-v2` — a small server that handles Apple's FairPlay decryption. It runs
in Docker and needs the Apple Music **Android** libraries (same Apple ID).

1. **Install Docker Desktop** → https://www.docker.com/products/docker-desktop/
2. **Get an Apple Music for Android APK** (v3.6.0-beta build 1109) from APKMirror.
   On an Intel Mac you need the **`arm64-v8a + x86_64`** variant.
3. Run the automated setup:
   ```bash
   ./setup_wrapper.sh ~/Downloads/apple-music.apk
   ```
4. **One-time login**: the script asks for your Apple ID email + password (stored in
   `wrapper-v2/.env`, kept local). With 2FA, submit the code Apple sends you:
   ```bash
   curl -X POST http://127.0.0.1/login/2fa -H 'Content-Type: application/json' -d '{"code":"123456"}'
   ```
   The session is cached on disk — **future restarts never ask again**.

> **No Terminal? Use the in-app wizard instead.** Open the app →
> **"5 · Wrapper & login"** → **⚙ Setup the wrapper**. It runs the same steps
> (you give it the APK as a file path or URL, optionally your Apple ID), streams
> the build log right into the page, applies the Intel-Mac library fix when
> needed, and lets you log in — including pasting the 2FA code — entirely from
> the browser. The code box auto-focuses, auto-submits on 6 digits, and the
> "resend" button cools down to avoid Apple's rate limits.
5. In the app's Settings, enable **"Use wrapper"** and pick **ALAC** or **Atmos**.

> **Intel-Mac FairPlay fix (important):** wrapper-v2's pinned libraries are missing
> a symbol, so ALAC fails with `KDProcessResponseCKC status: -42812`. This repo
> ships the fix as **`./fix_wrapper_libs.sh`** (swaps 7 `.so` files with working
> builds and rebuilds). Apply it after `setup_wrapper.sh` if ALAC downloads fail.

### Step 3 (alternative) — amdl engine (syllable lyrics, Atmos cap, conversions)

**[amdl](https://github.com/zhaarey/apple-music-downloader)** is a Go-based Apple
Music downloader with its own Docker wrapper — the other big community option
next to gamdl. Its wins: **syllable-level lyrics** (word-by-word, incl. K-pop
translation), **built-in FLAC/MP3/Opus conversion**, `alac-fix` for malformed
ALAC packets, 5000×5000 covers, and Atmos/ALAC bitrate caps. To use it:

1. Make sure Docker Desktop is running and `wrapper-v2` is **stopped** — both
   wrappers need port **10020**, so only one Apple wrapper can run at a time
   (starting amdl stops wrapper-v2 automatically; the app tells you when there's
   a conflict).
2. In the app, open **"5 · Wrapper & login"** → **⚙ Setup the wrapper** → switch
   **Apple engine** to **amdl** in Settings. The setup step pulls the amdl image
   (`ghcr.io/zhaarey/apple-music-downloader`) and the itouakirai wrapper
   (`ghcr.io/itouakirai/wrapper:x86`).
3. Log in with your Apple ID in the same panel (2FA works the same — the code
   box writes straight to the wrapper's code file).
4. In Settings pick **amdl quality knobs**: Atmos cap (2564/2768 kbps), max
   ALAC sample rate (192 kHz), syllable lyrics on/off.

> Both engines share the same Apple session credentials model and write to the
> same `Artist/Album` folders — switching engines is just flipping the toggle.
> gamdl remains the default; amdl is opt-in.

---

## Step 3.5 — Migrate albums/playlists from Spotify or YouTube Music

Have an album on Spotify or a playlist on YouTube Music you want in Apple
Music? No need to re-find every track by hand:

1. In the app, open **"7 · Migrate from Spotify / YouTube Music"**.
2. Paste a Spotify album/playlist link or a YouTube / YouTube Music
   album/playlist link.
3. Hit **Preview & match** — the app reads the track list (Spotify embed page /
   yt-dlp), then matches each track against the Apple Music catalog.
4. Ticked rows are ready — hit **⬇ Download selected** to grab them all as
   lossless ALAC (or your chosen codec).

No API keys required: Spotify's public embed page and yt-dlp both expose
metadata only; matching uses the public iTunes Search API.

---

## Step 4 — FLAC instead of ALAC (built-in, automatic)

gamdl's lossless codec is **ALAC** (perfect for Apple devices). If your server
prefers **FLAC**, this project converts automatically with ffmpeg — a
**lossless-to-lossless** conversion, so **zero audio quality is lost**.
Metadata and embedded cover art carry over.

- **In the app**: Settings → toggle **"Auto-convert ALAC → FLAC"**. After every
  download, new ALAC tracks get a `.flac` sibling automatically (AAC files are
  detected and skipped — converting lossy AAC to FLAC would just waste space).
- **Manual**: the "Convert ALAC → FLAC" section converts any folder in one click.
- **CLI**: `cli.py --to-flac` or `cli.py --to-flac "/path" --overwrite-flac`

Original `.m4a` files are always kept; a re-run skips files that already have a
`.flac` (use *Overwrite* to redo).

---

## 🔁 After a reboot — it's ONE click

Nothing needs to be re-setup. Docker Desktop **auto-starts at login**
(LaunchAgent installed during setup) and **stays running**. The wrapper runs
**only while the app is open** — the launcher starts it when you launch the
app and stops it again when you close the app:

```
✓ Docker is already running            (or: launching Docker Desktop…)
✓ Wrapper up — Apple session restored  (no 2FA, ever)
✓ App running → http://127.0.0.1:8741
```

**1. Pick one:**
- `./start.sh` — works on **macOS and Linux**
- Double-click **`Start Music High Res.bat`** (Windows)
- Double-click **`Start Music High Res.command`** (macOS)
- Double-click **`Music High Res.app`** (macOS, after `./make_app.sh`)

…and you're done.

### Manual version (if you prefer Terminal)
```bash
./start.sh                            # does all three steps below for you
# …or by hand:
open -a Docker                       # 1. start Docker, wait for the whale
cd ~/Desktop/"Music High Res"/wrapper-v2 && docker compose up -d   # 2. wrapper
cd ~/Desktop/"Music High Res" && ./Start\ Music\ High\ Res.command # 3. app
```

> The full stack survives reboots: the Docker image, your Apple session, the
> working libraries, and all config are saved on disk. Only a **brand-new
> machine** (or wiping this folder) requires repeating Steps 1–3.

---

## Step 5 — Home music server

Downloads land in a clean `Artist/Album/Track` structure, so any server can
pick them up. **Navidrome** (lightweight, music-only, great iOS apps):
```bash
docker run -d --name navidrome --restart=unless-stopped \
  --user $(id -u):$(id -g) \
  -v ~/Music/Apple\ Music:/music \
  -v ~/.navidrome:/data \
  -p 4533:4533 deluan/navidrome:latest
```
Jellyfin and Plex also work well with ALAC/FLAC libraries.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Cookies missing` pill is yellow | Export cookies from a logged-in browser → `cookies.txt` |
| `401 / authentication` errors | Cookies expired — re-export and replace `cookies.txt` |
| ALAC fails with `-1002` | Wrapper not running or not logged in — redo Step 3 |
| ALAC fails with `-42812` (Intel) | Run `./fix_wrapper_libs.sh` (the symbol fix) |
| `No active Apple Music subscription` | Confirm your plan at music.apple.com |
| Artist link downloads nothing | gamdl prompts interactively; the app auto-selects **All albums** (change in Settings) |
| Wrapper login stuck / no 2FA code | Check Gmail (incl. spam), SMS, trusted devices. App panel "5 · Wrapper & login" shows live state. Apple rate-limits rapid retries — wait ~10 min between attempts |
| Spotify job fails instantly | Set your Spotify cookies file in Settings (Settings → Spotify cookies) — votify needs it |
| YouTube Music job downloads nothing | Free itag 128k is limited by YouTube's anonymous client; add Premium itag + cookies, or use `--po-token` (see gytmdl docs) |
| Mixed Apple/Spotify/YouTube links in one batch | Split them — one source per batch (the job says so) |
| A download failed partway | Hit **↻ Retry** on the job card — it re-queues the same URLs |
| amdl panel says "port conflict" | `wrapper-v2` is still running — hit **Start** on the amdl panel (it stops wrapper-v2 first) or `cd wrapper-v2 && docker compose down` |
| amdl downloads fail / wrong quality | Check Settings → amdl **Atmos cap** / **ALAC max**; amdl needs the wrapper running (`state: running`) |
| Downloads slow after reboot | Docker Desktop needs ~1 min to boot the first time; the start script waits for it |
| Windows: wrapper setup says bash not found | Install **Git for Windows** (ships Git Bash) or enable WSL, then run the setup wizard again — `setup_wrapper.sh` is a bash script |
| Windows: app won't start, "python not found" | Install Python 3.10+ from python.org (check *Add to PATH*) or `winget install Python.Python.3.12` |
| Something broke and you need to know why | Logs live in `logs/` — `logs/app.log` (server, rotates at 1 MB × 3) and `logs/launcher.log` (startup). See `docs/DOCUMENTATION.md §7` |

---

## Credits

- **gamdl** — the core Apple Music download engine, by
  **[glomatico](https://github.com/glomatico/gamdl)** (MIT). All respect for the
  hard reverse-engineering work. This project is a friendly wrapper around it.
- **votify** — the Spotify download engine, by
  **[glomatico](https://github.com/glomatico/votify)** (MIT).
- **gytmdl** — the YouTube Music download engine, by
  **[glomatico](https://github.com/glomatico/gytmdl)** (MIT).
- **wrapper-v2** — the FairPlay decryption server, by
  **[glomatico](https://github.com/glomatico/wrapper-v2)**.
- **amdl** — the alternate Apple Music download engine, by
  **[zhaarey](https://github.com/zhaarey/apple-music-downloader)**, and its
  wrapper by **[itouakirai](https://github.com/itouakirai/wrapper)**.
- **WorldObservationLog/wrapper** — the library build used by `fix_wrapper_libs.sh`.
- **This project (AppleMusic Song Downloader)** — the web app, CLI, FLAC
  conversion, wrapper automation, setup scripts, and documentation by
  **gavinjoseph** / **ZeroIndents** (GitHub).

---

## License

**Personal, non-commercial, experimental use only.** See the full [LICENSE](LICENSE).
In short: use it for your own library, don't sell it, don't redistribute
downloaded media, and keep attribution intact. This deliberately grants **no
commercial rights** — the project lives in the personal/experimental space.

## Legal note

This downloads music **you already have the right to stream** through your paid
Apple Music subscription, for your personal library. Keep it personal. Usage must
comply with the Apple Media Services Terms and Conditions and local law.

---

*For gamdl's full option reference: `gamdl --help` or the
[gamdl README](https://github.com/glomatico/gamdl).*
