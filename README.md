# 🎧 AppleMusic Song Downloader

Download **lossless ALAC (up to 24-bit/192kHz)**, **AAC 256kbps**, or **Dolby Atmos**
music from your Apple Music subscription into an organized local library you
own — ready for Plex, Jellyfin, Navidrome, or any home music server.

- 🌐 **Local web app** — paste Apple Music links, pick quality, watch downloads live
- ⌨️ **CLI** — the same thing from the Terminal
- 🔁 **Automatic ALAC → FLAC conversion** — for servers that prefer FLAC
- ⚙️ **One-click boot** — Docker + wrapper + app all start from a single double-click

> **Core engine:** this project wraps **[gamdl](https://github.com/glomatico/gamdl)**
> by **glomatico** — all credit for the actual Apple Music download/decode
> machinery goes to him. This repo adds the friendly UI, CLI, FLAC conversion,
> wrapper automation, and setup tooling on top. See **Credits** below.

---

## ⚡ One-command install (macOS only)

> **Platform support:** this installer is **macOS only** for now (Intel & Apple
> Silicon). **Linux support is coming soon.** The app itself is plain Python and
> portable; the macOS-specific bits are the launchers and the wrapper setup.

**New machine?** One command downloads everything, installs Homebrew + gamdl +
ffmpeg, clones the repo, creates the Python environment and prints your next
steps:

```bash
curl -fsSL https://raw.githubusercontent.com/gavinraspberrypi/AppleMusic-Song-Downloader/main/install.sh | bash
```

Already cloned it? Just run it from inside the repo:

```bash
./install.sh
```

What it does (and what it deliberately does **not**):

- ✅ Checks you're on macOS (with a clear message otherwise)
- ✅ Installs Homebrew, git, python3, ffmpeg, and **gamdl** (and pins gamdl so
  `brew upgrade` can't silently break downloads)
- ✅ Clones the repo (bootstrap mode) and installs the Python dependencies
- ❌ Does **not** ask for your Apple ID, cookies, or APK — those stay manual
  (export cookies → Step 1, optional lossless wrapper → Step 3)

---

## What you need

| Requirement | Status | Notes |
|---|---|---|
| macOS + Python 3.10+ | ✅ 3.14.6 | Intel & Apple Silicon; Linux coming soon |
| gamdl | ✅ v3.8.5 | `brew install gamdl` |
| ffmpeg | ✅ v8.1.2 | `brew install ffmpeg` (needed for FLAC) |
| **Active Apple Music subscription** | ✅ | required for every download |
| Apple Music cookies | ✅ | Step 1 (not needed if using the wrapper) |
| Docker Desktop + wrapper | ✅ | Step 3 — required for **ALAC / Atmos** |

---

## Quick start (new machine, 15 minutes)

```bash
# 1. Install prerequisites
brew install gamdl ffmpeg

# 2. Set up the app
./setup.sh

# 3. Export cookies from a browser signed into music.apple.com → save as cookies.txt
#    (browser extension: "Get cookies.txt LOCALLY")

# 4. Start the app
./Start\ Music\ High\ Res.command

# 5. (Optional, for lossless ALAC) Docker + wrapper — see Step 3
./setup_wrapper.sh ~/Downloads/apple-music.apk
```

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

Two ways to launch — pick your favourite:

- **Like a normal Mac app** (recommended): run `./make_app.sh` once, then
  double-click **`Music High Res.app`** in Finder. It gets a Dock icon and opens
  the UI in a standalone app-style window. *(The app stays in the Dock while it
  runs; right-click → Quit to stop it.)*
- **Classic**: double-click `Start Music High Res.command`, or run
  `./setup.sh` then `.venv/bin/python app.py` in a Terminal.

Both boot Docker + the ALAC wrapper automatically when needed.

→ UI: **http://127.0.0.1:8741**

### Using the web app
1. Paste Apple Music links — songs, albums, playlists, artists, music videos — one per line.
2. Pick quality:
   - **ALAC · AAC fallback** — lossless when possible, otherwise AAC 256 (default, safest)
   - **ALAC only** — requires the wrapper
   - **AAC 256** — works with just cookies, always
   - **Dolby Atmos** — requires the wrapper
3. Hit **Download** and watch the live log.

### Using the CLI
```bash
.venv/bin/python cli.py "https://music.apple.com/us/album/in-rainbows/1109714933"
.venv/bin/python cli.py --codec aac-web "https://music.apple.com/..."
.venv/bin/python cli.py --to-flac                    # convert ALAC → FLAC
.venv/bin/python cli.py --to-flac "/path/to/Albums" --overwrite-flac
```

### Where files land
Default output: **`~/Music/Apple Music`**, organized as
`{Album Artist}/{Album}/{Track Number} {Title}.m4a` (+ `.flac` when conversion is on)
with embedded cover art and synced lyrics (.lrc). Change the folder in Settings —
point it straight at your music server's library if you like.

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
5. In the app's Settings, enable **"Use wrapper"** and pick **ALAC** or **Atmos**.

> **Intel-Mac FairPlay fix (important):** wrapper-v2's pinned libraries are missing
> a symbol, so ALAC fails with `KDProcessResponseCKC status: -42812`. This repo
> ships the fix as **`./fix_wrapper_libs.sh`** (swaps 7 `.so` files with working
> builds and rebuilds). Apply it after `setup_wrapper.sh` if ALAC downloads fail.

---

## Step 3.5 — Migrate albums/playlists from Spotify or YouTube Music

Have an album on Spotify or a playlist on YouTube Music you want in Apple
Music? No need to re-find every track by hand:

1. In the app, open **"5 · Migrate from Spotify / YouTube Music"**.
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
(LaunchAgent installed during setup), and `Start Music High Res.command` boots
the wrapper and the app for you:

```
✓ Docker is already running            (or: launching Docker Desktop…)
✓ Wrapper up — Apple session restored  (no 2FA, ever)
✓ App running → http://127.0.0.1:8741
```

**1. Double-click `Start Music High Res.command`** — done.

### Manual version (if you prefer Terminal)
```bash
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
| Wrapper login stuck / no 2FA code | Check Gmail (incl. spam), SMS, trusted devices. App panel "3 · Wrapper & login" shows live state. Apple rate-limits rapid retries — wait ~10 min between attempts |
| Downloads slow after reboot | Docker Desktop needs ~1 min to boot the first time; the start script waits for it |

---

## Credits

- **gamdl** — the core Apple Music download engine, by
  **[glomatico](https://github.com/glomatico/gamdl)** (MIT). All respect for the
  hard reverse-engineering work. This project is a friendly wrapper around it.
- **wrapper-v2** — the FairPlay decryption server, by
  **[glomatico](https://github.com/glomatico/wrapper-v2)**.
- **WorldObservationLog/wrapper** — the library build used by `fix_wrapper_libs.sh`.
- **This project (AppleMusic Song Downloader)** — the web app, CLI, FLAC
  conversion, wrapper automation, setup scripts, and documentation by
  **gavinjoseph**.

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
