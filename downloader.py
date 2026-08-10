"""Music High Res — download & convert manager.

Loads app config, runs the installed `gamdl` binary as a subprocess for
Apple Music downloads, and drives ffmpeg for ALAC→FLAC conversion. Each
download or conversion is tracked as a Job with a live log for the web UI / CLI.
"""

from __future__ import annotations

import hashlib
import logging
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path


def _frozen_app_dir() -> Path:
    """Writable home for a PyInstaller binary (logs, data, config.json).

    In source mode this is the repo folder. In a binary it's the folder next
    to the executable — except macOS, where the binary sits inside a read-only
    .app bundle (or anywhere in /Applications), so ~/Music High Res is used
    instead, and Windows, where %LOCALAPPDATA% is always writable (unlike
    Program Files). The _MEIPASS temp dir only holds bundled read-only
    resources (static/, the default config) — never anything we write.
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            d = Path.home() / "Music High Res"
        elif sys.platform == "win32":
            d = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Music High Res"
        else:
            d = Path(sys.executable).resolve().parent
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(__file__).resolve().parent


def _frozen_res_dir() -> Path:
    """Where bundled read-only resources live in a PyInstaller binary.

    Onefile/onedir binaries extract their data files into sys._MEIPASS; in
    source mode that's just the repo folder."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return Path(__file__).resolve().parent


PROJECT_DIR = _frozen_app_dir()
RES_DIR = _frozen_res_dir()
CONFIG_PATH = PROJECT_DIR / "config.json"

# A binary bundles a default config.json; seed the writable home with it on
# first run so Config() finds the defaults (source mode: already in place).
if getattr(sys, "frozen", False) and not CONFIG_PATH.exists():
    bundled = RES_DIR / "config.json"
    if bundled.exists():
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled, CONFIG_PATH)
        except OSError:
            pass

DEFAULT_CONFIG = {
    # Where downloaded music folders land (Artist/Album/Track.m4a)
    "output_path": "~/Music/Apple Music",
    # Netscape-format cookies exported from music.apple.com
    "cookies_path": "cookies.txt",
    # Lossless ALAC requires the wrapper-v2 server (see README / setup_wrapper.sh)
    "use_wrapper": False,
    "wrapper_url": "http://127.0.0.1",
    # Comma-separated codec priority. alac = lossless up to 24-bit/192kHz.
    # "alac,aac-web" falls back to AAC 256kbps when lossless is unavailable.
    "song_codec_priority": "alac,aac-web",
    "synced_lyrics": True,
    "synced_lyrics_format": "lrc",
    "cover_size": 1200,
    "save_cover": False,
    "artist_auto_select": "all-albums",
    "overwrite": False,
    # After a successful download, convert any new ALAC files to FLAC with ffmpeg
    # (lossless-to-lossless, both files kept). AAC downloads are left untouched.
    "convert_to_flac": False,
    # Playlist organization (both on by default):
    #   save_playlist          — write Playlists/{playlist_artist}/{playlist_title}.m3u
    #                            pointing at the downloaded tracks (no duplication)
    #   copy_playlist_folders  — additionally COPY each playlist's tracks into its own
    #                            folder so a playlist is browsable as one directory
    #                            (duplicates the files on disk)
    "save_playlist": True,
    "copy_playlist_folders": True,
    # Exposed gamdl options (music videos, cover art, folder layout). Values are
    # passed straight through to the CLI; see gamdl --help for each flag.
    "music_video_resolution": "1080p",      # 240p … 2160p
    "music_video_codec_priority": "h264,h265",  # h264 / h265 / ask
    "cover_format": "jpg",                 # jpg / png / raw
    "album_folder_template": "{album_artist}/{album}",
    "playlist_folder_template": "Playlists/{playlist_artist}",
    "use_album_date": False,
    # When on, the Download button drops links that are already fully downloaded
    # (checked via the same preview endpoint the chips use). Safe by default off.
    "skip_owned": False,
    # When on, pass gamdl/votify their own --database-path SQLite ledger so the
    # engines themselves register downloaded media and skip re-downloads.
    # (gytmdl has no such flag.) Default off — the app's own ledger already
    # powers exact ownership checks.
    "engine_ledger": False,
    # Ledger-driven delta sync: when on, Spotify/YouTube album + playlist links
    # are resolved up front and only the tracks the SQLite ledger doesn't own
    # are queued (as individual track URLs). Apple links pass through — gamdl
    # already skips existing files, so Apple library re-runs are delta anyway.
    "delta_sync": False,
    # Job orchestration:
    #   schedule_window  — "HH:MM-HH:MM" (24h, may wrap midnight); empty = run now
    #   max_concurrent   — how many gamdl jobs run at the same time
    #   auto_retry       — extra attempts after a failure (1m → 5m → 15m backoff)
    "schedule_window": "",
    "max_concurrent": 2,
    "auto_retry": 2,
    # Watch folder: drop a .txt/.m3u/.url containing Apple Music links here
    # and it gets downloaded automatically (moved to .done/ after).
    "watch_folder": "",
    # Notify a webhook (ntfy/Pushover/generic) when new releases are found.
    "notify_url": "",
    # Playlist folder: copy (duplicates files) or hardlink (zero extra disk on
    # APFS, falls back to copy on other filesystems).
    "playlist_hardlink": False,
    # After a successful download, probe the new files and report the actual
    # codec/bit-depth so silent ALAC→AAC downgrades are visible.
    "verify_quality": True,
    # iTunes Search storefront used by the Migrate matcher (US, GB, IN, …).
    "storefront": "US",
    # Optional: POST a JSON ping here after a batch finishes (Navidrome/Plex/
    # Jellyfin rescan webhook, or anything). Empty = disabled.
    "scan_hook_url": "",
    # YouTube Music downloads (gytmdl). itag: 140 = AAC 128k (free), 141 = AAC
    # 256k, 774 = Opus 256k (both Premium-only — need ytm_cookies_path). Empty
    # ytm_cookies_path falls back to the main cookies_path setting.
    "ytm_itag": "140",
    "ytm_cookies_path": "",
    # Spotify downloads (votify). audio_quality is a comma-separated priority
    # list ("320,160" — 320kbps is Premium-only, free accounts get 160). Needs
    # a Spotify cookies file (spotify_cookies_path, else the main one).
    "spotify_audio_quality": "vorbis-medium",
    "spotify_cookies_path": "",
    # Apple Music engine: "gamdl" (glomatico + wrapper-v2) or "amdl"
    # (zhaarey/apple-music-downloader + itouakirai wrapper). Both wrappers use
    # port 10020, so only one can be running at a time — switching engines
    # stops the other wrapper (see setup_amdl_wrapper.sh / the Wrapper panel).
    "apple_engine": "gamdl",
    # amdl knobs (used only when apple_engine == "amdl"):
    "amdl_atmos_max": "2768",        # Dolby Atmos bitrate cap (kbps): 2768 | 2448
    "amdl_alac_max": "192000",       # ALAC max sample rate: 192000 | 96000 | 48000 | 44100
    "amdl_lrc_type": "lyrics",       # lyrics | syllable-lyrics (word-by-word)
    "amdl_cover_size": "5000x5000",  # embedded cover resolution
    # amdl built-in conversion (convert-after-download in the generated
    # config.yaml): off | flac | mp3 | opus. When on, amdl converts every
    # downloaded track and optionally keeps the original too.
    "amdl_convert": "off",
    "amdl_convert_keep_original": False,
    # ── Gamdl file/folder naming (empty = engine default) ──────────────
    # Single-disc file template, e.g. "{track:02d} {title}" or
    # "{track:02d} - {artist} - {title}". Empty lets gamdl use its default.
    "file_name_template": "",
    # Multi-disc file template — override for multi-disc releases (e.g.
    # "{disc:02d}-{track:02d} {title}"). Falls back to file_name_template.
    "multi_disc_file_template": "",
    # Compilation albums (various artists) go to Compilations/{album} when
    # set; empty uses the regular album folder template.
    "compilation_folder_template": "",
    # Comma-separated gamdl tags to strip (sortArtist, sortTitle, comments,
    # copyright, lyrics). Empty = keep everything.
    "exclude_tags": "",
    # strftime-style date tag template (gamdl --date-tag-template). Empty =
    # default "%Y-%m-%d".
    "date_tag_template": "",
    # Music video remux container: "" (off) | m4v | mp4
    "mv_remux_format": "",
    # ── gytmdl (YouTube Music) extras ──────────────────────────────────
    # Proof-of-Origin token: fixes bot-blocks on bulk runs (see gytmdl docs).
    "ytm_po_token": "",
    # Optional song-file template (gytmdl --template-file). Empty = default.
    "ytm_file_template": "",
    # ── votify (Spotify) extras ────────────────────────────────────────
    # Path to a .wvd Widevine device file — required for the FLAC quality
    # tiers (lossless) on Spotify. Empty = OGG tiers only.
    "spotify_wvd_path": "",
    # Native desktop notification when a download batch finishes (macOS
    # Notification Center / Windows toast / Linux notify-send). Independent of
    # the in-browser Notification API.
    "desktop_notify": False,
    # Saved smart-playlist filters: [{"name", "artist", "album", "quality",
    # "years" ("2015-2020" | "2020"), "recent_days" (0 = any), "min_tracks"}]
    "smart_playlists": [],
    # Wishlist of links saved for later: [{"url", "title", "added"}]
    "wishlist": [],
    # Named settings presets: {name: {setting: value, ...}}
    "settings_presets": {},
    # ── Media-server scan presets (Navidrome / Plex / Jellyfin) ──────
    # "server_type": "" (off) | navidrome | plex | jellyfin. When set, the
    # batch-finished hook and the manual "Scan now" button call the real API
    # instead of the generic scan_hook_url webhook.
    "server_type": "",
    "server_url": "",           # e.g. http://127.0.0.1:4533 (no trailing /)
    "server_token": "",         # API key / access token
    "server_section": "1",      # Plex library section id
    # ── Remote access (LAN) ───────────────────────────────────────────
    # remote_bind: listen on 0.0.0.0 so the PWA works from a phone on the
    # same Wi-Fi. remote_token: when non-empty, every /api/* call must send it
    # (?token= or X-MHR-Token header). Keep the token secret — the API can
    # download files to the library and run arbitrary engine commands.
    "remote_bind": False,
    "remote_token": "",
    # Auto-delete empty folders after a download batch finishes.
    "auto_clean_empty": False,
    # Watch folder: comma-separated filename substrings to ignore (e.g.
    # ".DS_Store", ".part"). Matched case-insensitively against the file name.
    "watch_ignore": "",
    # Play a short in-browser chime when a download batch finishes.
    "chime_on_done": True,
    # Artists hidden from the Releases panel (persisted ignore list).
    "releases_ignored": [],
}

CODEC_LABELS = {
    "alac": "ALAC Lossless (24-bit/192kHz)",
    "alac,aac-web": "ALAC with AAC fallback",
    "aac-web": "AAC 256kbps",
    "atmos": "Dolby Atmos",
    "flac": "FLAC conversion",
}

_LOG_LINE_RE = re.compile(
    r"^(?:\[)?(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)[:\s\]]+(?P<msg>.*)$"
)
# Matches both gamdl's "INFO: …" and gytmdl/votify's "[INFO     18:40:15] …"
# (the latter is how glomatico's CLIs log — level in brackets first).


class Config:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text())
                if isinstance(stored, dict):
                    self.data.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")

    def get(self, key: str):
        return self.data.get(key, DEFAULT_CONFIG.get(key))

    def set(self, key: str, value) -> None:
        if key in DEFAULT_CONFIG:
            self.data[key] = value
            self.save()

    def update(self, mapping: dict) -> dict:
        """Set several keys at once and write the config file a single time."""
        changes = {}
        for key, value in mapping.items():
            if key in DEFAULT_CONFIG:
                self.data[key] = value
                changes[key] = value
        if changes:
            self.save()
        return changes


def expand_path(value: str) -> str:
    """Expand ~ and resolve to an absolute path string."""
    return str(Path(os.path.expanduser(value)).expanduser().resolve())


def resolve_cookies_path(config: Config) -> str:
    """Resolve the cookies file, falling back to the domain-prefixed name that
    the 'Get cookies.txt LOCALLY' extension sometimes saves."""
    configured = expand_path(config.get("cookies_path"))
    if Path(configured).exists():
        return configured
    alt = PROJECT_DIR / "music.apple.com_cookies.txt"
    return str(alt) if alt.exists() else configured


# The gamdl release this project is known to work with (README + setup pin it).
# gamdl is a brew-installed binary, so requirements.txt can't pin it — brew can:
#   brew pin gamdl        # stop brew upgrade from touching it
#   brew unpin gamdl      # allow upgrades again (after testing a newer version)
# The app shows a warning pill when the installed version drifts from this.
PINNED_GAMDL = "3.8.5"


# Homebrew installs to /usr/local/bin (Intel) or /opt/homebrew/bin (Apple
# Silicon). GUI-launched processes (Finder double-click, launchd agents, .app
# bundles) often inherit a minimal PATH without those dirs — so a PATH-only
# lookup would report gamdl/ffmpeg "missing" even when they're installed.
# Check the well-known Homebrew dirs explicitly as a fallback.
def _homebrew_bin(name: str) -> str | None:
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        p = Path(d) / name
        if p.is_file():
            return str(p)
    return None


def gamdl_binary() -> str | None:
    """gamdl binary: PATH, the venv's Scripts/bin dir (Windows pip installs),
    or the Homebrew dirs directly (GUI-launched processes with a minimal PATH).
    venv_bin already falls back to shutil.which, so this is venv_bin + the
    Homebrew fallback."""
    return venv_bin("gamdl") or _homebrew_bin("gamdl")


# Cache the version so /api/status doesn't spawn a subprocess on every poll.
_VERSION_CACHE: dict = {"value": None, "at": 0.0}


def gamdl_version() -> str | None:
    now = time.time()
    if _VERSION_CACHE["value"] is not None and now - _VERSION_CACHE["at"] < 30:
        return _VERSION_CACHE["value"]
    binary = gamdl_binary() or "gamdl"
    try:
        out = subprocess.run(
            [binary, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            v = (out.stdout or out.stderr).strip() or None
            _VERSION_CACHE["value"] = v
            _VERSION_CACHE["at"] = now
            return v
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def venv_bin(name: str) -> str | None:
    """Resolve a pip-installed CLI in this project's venv (fallback: PATH).

    gytmdl/votify install into .venv/bin (macOS/Linux) or .venv/Scripts
    (Windows) via requirements.txt; the app server is launched with
    .venv/bin/python (or .venv/Scripts/python.exe), which does NOT put the
    venv's bin dir on PATH for child processes — so commands must use the
    resolved absolute path.
    """
    bin_dir = PROJECT_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    exe = bin_dir / (name + (".exe" if os.name == "nt" else ""))
    if exe.exists():
        return str(exe)
    bare = bin_dir / name
    if bare.exists():
        return str(bare)
    return shutil.which(name)


def ytm_binary() -> str | None:
    """gytmdl (YouTube Music downloader) binary, or None."""
    return venv_bin("gytmdl")


def spotify_binary() -> str | None:
    """votify (Spotify downloader) binary, or None."""
    return venv_bin("votify")


# Small per-tool version cache so /api/status polls don't spawn subprocesses.
_TOOL_VERSIONS: dict = {}  # name -> (version, cached_at)


def _cli_version(name: str, binary: str | None) -> str | None:
    if not binary:
        return None
    now = time.time()
    cached = _TOOL_VERSIONS.get(name)
    if cached and now - cached[1] < 30:
        return cached[0]
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            lines = [l for l in (out.stdout or out.stderr).splitlines() if l.strip()]
            v = lines[0].strip() if lines else None
            _TOOL_VERSIONS[name] = (v, now)
            return v
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def ytm_version() -> str | None:
    return _cli_version("gytmdl", ytm_binary())


def spotify_version() -> str | None:
    return _cli_version("votify", spotify_binary())


def url_engine(url: str) -> str:
    """Which downloader handles a link: 'apple' (gamdl), 'spotify' (votify)
    or 'youtube' (gytmdl). Anything that isn't Spotify/YouTube is assumed to
    be an Apple Music link (gamdl is the default engine)."""
    u = (url or "").strip().lower()
    if "open.spotify.com" in u or "spotify.link" in u or "play.spotify.com" in u:
        return "spotify"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "apple"


def gamdl_pinned_ok() -> bool | None:
    """Does the installed gamdl match the pinned release?

    True = matches, False = drift (worth a warning), None = gamdl missing.
    Compares the first three version components so a "3.8.50" can't pass as
    "3.8.5", while "3.8.5" and "3.8.5.0" both match.
    """
    version = gamdl_version()
    if not version:
        return None
    pinned = [int(p) for p in PINNED_GAMDL.split(".")]
    for token in version.replace(",", " ").split():
        try:
            parts = [int(p) for p in token.split(".")[: len(pinned)]]
        except ValueError:
            continue
        if parts == pinned:
            return True
    return False


def ffmpeg_binary() -> str | None:
    return shutil.which("ffmpeg") or _homebrew_bin("ffmpeg")


def ffprobe_binary() -> str | None:
    return shutil.which("ffprobe") or _homebrew_bin("ffprobe")


_FFMPEG_VERSION_CACHE: dict = {"value": None, "at": 0.0}


def ffmpeg_version() -> str | None:
    now = time.time()
    if _FFMPEG_VERSION_CACHE["value"] is not None and now - _FFMPEG_VERSION_CACHE["at"] < 30:
        return _FFMPEG_VERSION_CACHE["value"]
    binary = ffmpeg_binary() or "ffmpeg"
    try:
        out = subprocess.run(
            [binary, "-version"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            first = (out.stdout or "").splitlines()[:1]
            v = first[0].strip() if first else None
            _FFMPEG_VERSION_CACHE["value"] = v
            _FFMPEG_VERSION_CACHE["at"] = now
            return v
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def is_alac_file(path: Path) -> bool:
    """True if the first audio stream of the file is Apple Lossless (ALAC)."""
    ffprobe = ffprobe_binary()
    if not ffprobe:
        return False
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return out.returncode == 0 and out.stdout.strip().lower() == "alac"
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# Audio quality probing (ffprobe) — cached so repeated Library scans are cheap
# ---------------------------------------------------------------------------
# The cache is a plain dict path → {codec, bits, rate} persisted to disk under
# the project dir. Keyed by (path, mtime) so edits invalidate entries.
_QUALITY_LOCK = threading.Lock()
QUALITY_CACHE_PATH = PROJECT_DIR / "quality_cache.json"


def _load_quality_cache() -> dict:
    try:
        return json.loads(QUALITY_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_quality_cache(cache: dict) -> None:
    try:
        QUALITY_CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass


def probe_audio_quality(path: Path) -> dict | None:
    """Probe the first audio stream: {codec, bits, rate} or None.

    Result is cached on disk keyed by path+mtime, so a full-library quality
    scan is fast after the first run.
    """
    ffprobe = ffprobe_binary()
    if not ffprobe:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    cache = _load_quality_cache()
    hit = cache.get(key)
    if hit and hit.get("mtime") == st.st_mtime:
        return hit.get("q")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,bits_per_raw_sample,sample_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        s = streams[0]
        codec = (s.get("codec_name") or "").lower()
        bits = s.get("bits_per_raw_sample") or s.get("bits_per_sample")
        rate = s.get("sample_rate")
        q = {
            "codec": codec,
            "bits": int(bits) if bits and str(bits).isdigit() else None,
            "rate": int(rate) if rate and str(rate).isdigit() else None,
        }
        cache[key] = {"mtime": st.st_mtime, "q": q}
        if len(cache) > 5000:  # bounded
            cache = dict(list(cache.items())[-4000:])
        with _QUALITY_LOCK:
            _save_quality_cache(cache)
        return q
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def album_quality(album_dir: Path) -> dict | None:
    """Best audio quality found in an album folder (probes up to 3 files).

    Returns {codec, bits, rate} of the "best" (highest bit-depth/rate) file, or
    None when nothing could be probed. Used for the Library's quality badges.
    """
    try:
        candidates = [
            p for p in sorted(album_dir.iterdir())
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS
        ][:3]
    except OSError:
        return None
    best = None
    for p in candidates:
        q = probe_audio_quality(p)
        if not q or not q.get("codec"):
            continue
        if best is None:
            best = q
            continue
        # rank: lossless > lossy, then bit-depth, then sample rate
        def _rank(qq):
            lossless = qq["codec"] in ("alac", "flac")
            return (1 if lossless else 0, qq.get("bits") or 0, qq.get("rate") or 0)
        if _rank(q) > _rank(best):
            best = q
    return best


def format_quality(q: dict | None) -> str:
    """Human label for a quality dict: 'ALAC 24/96' or 'AAC 256'."""
    if not q or not q.get("codec"):
        return ""
    # Canonical codec name (pcm_* → wav, m4a → aac …) so badges match the
    # histogram labels.
    codec = _quality_alias(q["codec"]).upper()
    if codec == "M4A":
        codec = "AAC"
    parts = [codec]
    if q.get("bits"):
        parts.append(str(q["bits"]))
    if q.get("rate"):
        parts.append(str(round(q["rate"] / 1000)))
    return "/".join(parts) if len(parts) > 1 else codec


# ---------------------------------------------------------------------------
# SQLite ledger — authoritative index of what's been downloaded
# ---------------------------------------------------------------------------
# Every file a job actually produces is recorded here (path, source URL,
# engine, tags, codec, size, mtime, when, which job). It's the app's own
# answer to "do we already own this?" — exact per-track rows instead of
# folder-name guessing — and doubles as a manifest of what came from where.
# gamdl/votify also maintain their own DBs when passed --database-path; this
# ledger is engine-agnostic (covers gamdl, amdl, votify, gytmdl, FLAC
# conversions) and lives in data/library.sqlite (gitignored).
LEDGER_PATH = PROJECT_DIR / "data" / "library.sqlite"
# RLock (not Lock): _ledger_query re-enters _ledger_ensure_schema, which takes
# the same lock — callers hold it around the whole read/write.
_LEDGER_LOCK = threading.RLock()
_LEDGER_READY = False  # schema created once per process


def _ledger_ensure_schema() -> None:
    """Create the schema once (cheap no-op after the first call)."""
    global _LEDGER_READY
    if _LEDGER_READY:
        return
    with _LEDGER_LOCK:
        if _LEDGER_READY:
            return
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(LEDGER_PATH, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS tracks (
                path          TEXT PRIMARY KEY,
                url           TEXT,
                engine        TEXT,
                title         TEXT,
                artist        TEXT,
                album         TEXT,
                codec         TEXT,
                size          INTEGER,
                mtime         REAL,
                downloaded_at REAL,
                job_id        TEXT
            )""")
            # v1.2: play tracking columns (play_count, last_played) — added
            # via ALTER for databases created before this release.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
            for col, decl in (("play_count", "INTEGER DEFAULT 0"),
                              ("last_played", "REAL")):
                if col not in cols:
                    conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {decl}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album, artist)")
            conn.commit()
            _LEDGER_READY = True
        finally:
            conn.close()


def _ledger_query(fn):
    """Run fn(conn) on a fresh connection and always close it.

    sqlite3's `with conn` context manager commits but does NOT close the
    connection — for a long-running server that would leak one connection per
    ledger write until GC. Explicit close on every path avoids that.
    """
    _ledger_ensure_schema()
    conn = sqlite3.connect(LEDGER_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return fn(conn)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def ledger_record(job: Job, new_files: set[str], engine: str | None = None) -> int:
    """Upsert the files a finished job produced into the SQLite ledger.

    Rows are keyed by absolute path; re-downloading a file (overwrite) simply
    refreshes its row. Only audio files inside the output folder are recorded
    (hidden dirs like .trash are skipped). Returns how many rows were written.
    """
    if not new_files:
        return 0
    root = Path(job.output_path).resolve()
    url = job.urls[0] if len(job.urls) == 1 else ""
    # The `engine` param lets run_job pass the actual tool name (gamdl/amdl),
    # so the by-engine split is honest; default to the job's source engine.
    engine_col = engine or (job.engine or "apple")
    now = time.time()
    rows = []
    for s in sorted(new_files):
        p = Path(s)
        try:
            rel = p.resolve().relative_to(root)
        except ValueError:
            continue  # outside the output folder
        if any(part.startswith(".") for part in rel.parts) or rel.parts[0] == "Playlists":
            continue  # hidden dirs (.trash) + playlist folder copies
        tags = read_audio_tags(p)
        try:
            st = p.stat()
        except OSError:
            continue
        rows.append((
            str(p.resolve()), url, engine_col,
            tags.get("title") or "", tags.get("artist") or "",
            tags.get("album") or "", file_codec(p),
            st.st_size, st.st_mtime, now, job.id,
        ))
    if not rows:
        return 0

    def _write(conn):
        conn.executemany(
            """INSERT INTO tracks (path, url, engine, title, artist, album, codec,
                                   size, mtime, downloaded_at, job_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                 url=excluded.url, engine=excluded.engine, title=excluded.title,
                 artist=excluded.artist, album=excluded.album, codec=excluded.codec,
                 size=excluded.size, mtime=excluded.mtime,
                 downloaded_at=excluded.downloaded_at, job_id=excluded.job_id""",
            rows,
        )
        conn.commit()

    with _LEDGER_LOCK:
        try:
            _ledger_query(_write)
        except sqlite3.Error:
            return 0
    return len(rows)


def ledger_owned_count(output_dir: str, kind: str, title: str, artist: str) -> int | None:
    """Exact ownership from the SQLite ledger, or None when it has no record.

    Albums match on (album title + artist), songs on (track title + artist).
    Returns how many of the recorded files still exist on disk. None means the
    ledger knows nothing about this item, so callers fall back to the folder
    scan."""
    if not title:
        return None

    def _lookup(conn):
        if kind == "song":
            if artist:
                return conn.execute(
                    "SELECT path FROM tracks WHERE title=? AND artist=?",
                    (title, artist),
                ).fetchall()
            return conn.execute(
                "SELECT path FROM tracks WHERE title=?", (title,)
            ).fetchall()
        if artist:
            return conn.execute(
                "SELECT path FROM tracks WHERE album=? AND artist=?",
                (title, artist),
            ).fetchall()
        return conn.execute(
            "SELECT path FROM tracks WHERE album=?", (title,)
        ).fetchall()

    try:
        rows = _ledger_query(_lookup)
    except sqlite3.Error:
        return None
    if not rows:
        # No record at all → the ledger can't answer; let the caller fall back
        # to the folder scan (pre-ledger library or never downloaded).
        return None
    # NOTE: rows exist but ALL files are missing ⇒ return 0 (not None): the
    # ledger is authoritative — a recorded-but-deleted track is not owned, and
    # re-downloading is the safe direction (never a missed download).
    owned = 0
    for (path,) in rows:
        try:
            if Path(path).is_file():
                owned += 1
        except OSError:
            pass
    return owned


def ledger_stats(output_dir: str) -> dict:
    """Summary of the SQLite ledger: totals, engine/codec split, and files
    recorded but no longer on disk (deleted, or sitting in .trash)."""
    out = {
        "path": str(LEDGER_PATH),
        "tracks": 0,
        "bytes": 0,
        "by_engine": {},
        "by_codec": {},
        "missing": [],
        "missing_count": 0,
    }

    def _summarize(conn):
        row = conn.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM tracks").fetchone()
        result = {"tracks": row[0] or 0, "bytes": row[1] or 0, "paths": []}
        for r in conn.execute(
            "SELECT engine, COUNT(*) c FROM tracks GROUP BY engine ORDER BY c DESC"
        ):
            out["by_engine"][r["engine"] or "unknown"] = r["c"]
        for r in conn.execute(
            "SELECT codec, COUNT(*) c FROM tracks GROUP BY codec ORDER BY c DESC"
        ):
            out["by_codec"][r["codec"] or "unknown"] = r["c"]
        result["paths"] = [r[0] for r in conn.execute("SELECT path FROM tracks ORDER BY path")]
        return result

    try:
        summary = _ledger_query(_summarize)
    except sqlite3.Error:
        return out
    out["tracks"], out["bytes"] = summary["tracks"], summary["bytes"]
    rows = summary["paths"]
    for path in rows:
        try:
            if not Path(path).is_file():
                out["missing"].append(path)
        except OSError:
            pass
    out["missing_count"] = len(out["missing"])
    out["missing"] = out["missing"][:100]  # cap the payload
    return out


def ledger_rebuild(output_dir: str) -> dict:
    """Wipe and re-index the ledger from the library folder on disk.

    For libraries that predate the ledger (or after big manual changes): walks
    every audio file, skipping Playlists/ copies and hidden dirs, then returns
    fresh stats."""
    root = Path(output_dir)
    rows = []
    if root.is_dir():
        now = time.time()
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
                    continue
                rel = p.relative_to(root)
                if any(part.startswith(".") for part in rel.parts) or rel.parts[0] == "Playlists":
                    continue
                tags = read_audio_tags(p)
                try:
                    st = p.stat()
                except OSError:
                    continue
                rows.append((
                    str(p.resolve()), "", "",
                    tags.get("title") or "", tags.get("artist") or "",
                    tags.get("album") or "", file_codec(p),
                    st.st_size, st.st_mtime, now, "rebuild",
                ))
        except OSError:
            pass
    def _rebuild(conn):
        conn.execute("DELETE FROM tracks")
        conn.executemany(
            """INSERT OR REPLACE INTO tracks
               (path, url, engine, title, artist, album, codec,
                size, mtime, downloaded_at, job_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()

    with _LEDGER_LOCK:
        try:
            _ledger_query(_rebuild)
        except sqlite3.Error:
            pass
    return ledger_stats(output_dir)


def ledger_track_owned(output_dir: str, title: str, artist: str) -> bool:
    """Does the ledger say we already own this exact track (title + artist)?

    Delta-sync building block: a resolved Spotify/YouTube playlist is filtered
    to the tracks the ledger doesn't know, so re-running a playlist only
    fetches what's missing. Checks the file actually exists on disk.
    """
    title = (title or "").strip()
    artist = (artist or "").strip()
    if not title:
        return False
    owned = ledger_owned_count(output_dir, "song", title, artist)
    if owned:
        return True
    # Tag-less fallback: gamdl/votify/gytmdl name files "{track} {title}.ext"
    # under {artist}/{album}/. If the resolved artist + title both appear in a
    # recorded path (in that order, like the folder layout), treat it as owned.
    # Requiring the artist AND a minimum title length keeps short/common titles
    # ("I", "The", "Lover") from matching unrelated files — a false "owned"
    # would wrongly skip a track, so err conservative.
    needle = title.lower().strip()
    artist_key = artist.lower().strip()
    if len(needle) < 3 or not artist_key:
        return False
    root = Path(output_dir)
    # Escape LIKE wildcards so a title with % or _ can't over-match.
    esc = lambda s: s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{esc(artist_key)}%{esc(needle)}%"

    def _lookup(conn):
        return conn.execute(
            "SELECT path FROM tracks WHERE lower(path) LIKE ? ESCAPE '\\'",
            (pattern,),
        ).fetchall()

    try:
        rows = _ledger_query(_lookup)
    except sqlite3.Error:
        return False
    for (path,) in rows:
        try:
            p = Path(path)
            p.relative_to(root)
        except ValueError:
            continue
        if p.is_file():
            return True
    return False


def ledger_album_added(output_dir: str, album_path: str) -> float | None:
    """Earliest downloaded_at among the ledger rows inside one album folder.

    Powers the Library's "added" date per album. Returns None when the ledger
    has no rows under that folder (pre-ledger library).
    """
    album = Path(album_path).resolve()
    root = Path(output_dir).resolve()
    try:
        album.relative_to(root)
    except ValueError:
        return None
    prefix = str(album) + os.sep

    def _lookup(conn):
        rows = conn.execute(
            "SELECT downloaded_at FROM tracks WHERE path LIKE ?", (prefix + "%",)
        ).fetchall()
        vals = [r[0] for r in rows if r[0]]
        return min(vals) if vals else None

    try:
        return _ledger_query(_lookup)
    except sqlite3.Error:
        return None


def ledger_path_dates(output_dir: str, paths: set[str]) -> dict[str, float]:
    """Map absolute file paths → downloaded_at for the given paths (subset of
    the ledger). Used by list_album_files so the track list can show when each
    file was added."""
    if not paths:
        return {}
    # The ledger stores resolve()d paths (macOS: /var → /private/var). Resolve
    # the caller's paths for the lookup, then return keys in the caller's own
    # form so str(p) lookups keep working.
    resolved = {str(Path(p).resolve()): p for p in paths}
    ph = ",".join("?" for _ in resolved)

    def _lookup(conn):
        rows = conn.execute(
            f"SELECT path, downloaded_at FROM tracks WHERE path IN ({ph})", list(resolved)
        ).fetchall()
        out = {}
        for r in rows:
            if r["path"] in resolved:
                out[resolved[r["path"]]] = r["downloaded_at"]
        return out

    try:
        return _ledger_query(_lookup)
    except sqlite3.Error:
        return {}


def delta_filter_urls(config: Config, urls: list[str]) -> tuple[list[str], int]:
    """Ledger-driven delta sync for Spotify/YouTube album + playlist links.

    Resolves the link to its track list, drops every track the SQLite ledger
    already owns (title+artist match, file still on disk), and returns the
    remaining tracks as individual URLs. When nothing is owned the original
    link is kept (votify/gytmdl download the whole thing in one pass); when
    everything is owned the link is dropped entirely. Apple links pass through
    unchanged — gamdl already skips existing files, so Apple library re-runs
    are delta natively. Returns (filtered_urls, skipped_track_count).
    """
    import migrate  # lazy: migrate doesn't import downloader, but keep it tidy

    out: list[str] = []
    skipped = 0
    for u in urls:
        engine = url_engine(u)
        if engine == "apple":
            out.append(u)
            continue
        try:
            if engine == "spotify":
                parsed = migrate.parse_url(u)
                if not parsed or parsed.get("kind") not in ("album", "playlist"):
                    out.append(u)
                    continue
                _, tracks = migrate.resolve_spotify(parsed["kind"], parsed["id"])
                base = "https://open.spotify.com/track/"
            else:  # youtube
                parsed = migrate.parse_url(u)
                if parsed and parsed.get("kind") == "watch":
                    out.append(u)  # single video — nothing to delta
                    continue
                _, tracks = migrate.resolve_youtube(u)
                base = "https://music.youtube.com/watch?v="
        except Exception:
            out.append(u)  # resolution hiccup — keep the original link
            continue
        output_dir = expand_path(config.get("output_path"))
        keep = [
            t for t in tracks
            if not ledger_track_owned(output_dir, t.get("title", ""), t.get("artist", ""))
        ]
        skipped += len(tracks) - len(keep)
        if not keep:
            continue  # whole link already owned → drop it
        if len(keep) == len(tracks):
            out.append(u)  # nothing owned → keep the album/playlist link
        else:
            out.extend(f"{base}{t['source_id']}" for t in keep if t.get("source_id"))
    return out, skipped


# ---------------------------------------------------------------------------
# gamdl update check (GitHub releases API, cached 6h)
# ---------------------------------------------------------------------------
_LATEST_CACHE: dict = {"value": None, "at": 0.0}
_LATEST_TTL = 6 * 3600.0


def gamdl_latest_version() -> str | None:
    """Newest gamdl release tag from GitHub, or None when offline/error."""
    now = time.time()
    if _LATEST_CACHE["value"] is not None and now - _LATEST_CACHE["at"] < _LATEST_TTL:
        return _LATEST_CACHE["value"]
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/glomatico/gamdl/releases/latest",
            headers={"User-Agent": "music-high-res", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        tag = (data.get("tag_name") or "").lstrip("v")
        if tag:
            _LATEST_CACHE["value"] = tag
            _LATEST_CACHE["at"] = now
            return tag
    except (OSError, ValueError):
        pass
    return None


class Job:
    def __init__(self, urls: list[str], options: dict, config: Config, kind: str = "download"):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind  # "download" | "convert"
        self.urls = list(urls)
        self.options = options
        self.config = config
        self.status = "queued"  # queued | running | done | failed | cancelled
        self.codec = options.get("codec", config.get("song_codec_priority"))
        self.output_path = expand_path(options.get("output_path") or config.get("output_path"))
        self.source_dir = expand_path(options.get("source_dir")) if options.get("source_dir") else None
        self.created_at = time.time()
        self.updated_at = time.time()
        self.log: list[dict] = []
        self.exit_code: int | None = None
        self.proc: subprocess.Popen | None = None
        self.manager = None
        self.engine = url_engine(urls[0]) if urls else "apple"  # apple|spotify|youtube
        self.attempts = 0  # attempts so far (auto-retry accounting)
        self._retry_delay: float | None = None  # seconds before the next attempt
        # True while the job is sleeping between auto-retry attempts. Backoff
        # sleeps OUTSIDE the concurrency slot, so the queue gate must not let
        # a backoff job block the jobs behind it.
        self._in_backoff = False
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._done = threading.Event()

    # ---- log helpers -------------------------------------------------
    def add_line(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line:
            return
        ts = time.strftime("%H:%M:%S")
        m = _LOG_LINE_RE.match(line)
        level = m.group("level") if m else ("ERROR" if "error" in line.lower() else "INFO")
        entry = {"ts": ts, "level": level, "text": line}
        with self._lock:
            self.log.append(entry)
            if len(self.log) > 600:
                self.log = self.log[-600:]
            self.updated_at = time.time()

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status
            self.updated_at = time.time()
        if status in ("done", "failed", "cancelled"):
            self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def cancel(self) -> None:
        self._cancel_event.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def summary(self) -> dict:
        with self._lock:
            last = self.log[-1]["text"] if self.log else ""
            return {
                "id": self.id,
                "kind": self.kind,
                "engine": self.engine,
                "status": self.status,
                "codec": self.codec,
                "codec_label": CODEC_LABELS.get(self.codec, self.codec),
                "urls": list(self.urls),
                "url_count": len(self.urls),
                "output_path": self.output_path,
                "source_dir": self.source_dir,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "log_count": len(self.log),
                "last_message": last,
                "exit_code": self.exit_code,
            }

    def detail(self, tail: int = 200) -> dict:
        d = self.summary()
        with self._lock:
            d["log"] = self.log[-tail:]
        return d


def build_gamdl_command(config: Config, options: dict, urls: list[str]) -> list[str]:
    """Construct the gamdl CLI command from app settings + per-job options."""
    # Resolve the real binary (PATH may lack Homebrew's dir in GUI launches) —
    # same pattern as build_gytmdl_command / build_votify_command.
    binary = gamdl_binary() or "gamdl"
    cmd = [binary, "-n"]  # -n: ignore ~/.gamdl/config.ini, use explicit flags

    if options.get("cookies_path"):
        cookies = expand_path(options.get("cookies_path"))
    else:
        cookies = resolve_cookies_path(config)
    output = expand_path(options.get("output_path") or config.get("output_path"))
    codec = options.get("codec") or config.get("song_codec_priority")
    use_wrapper = bool(options.get("use_wrapper", config.get("use_wrapper")))
    wrapper_url = options.get("wrapper_url") or config.get("wrapper_url")

    cmd += ["-c", cookies]
    cmd += ["-o", output]
    cmd += ["--song-codec-priority", codec]
    cmd += ["--synced-lyrics-format", config.get("synced_lyrics_format")]
    cmd += ["--cover-size", str(config.get("cover_size"))]
    cmd += ["--log-level", "INFO"]

    if config.get("synced_lyrics") is False:
        cmd += ["--no-synced-lyrics"]
    if config.get("save_cover"):
        cmd += ["-s"]
    if config.get("save_playlist"):
        cmd += ["--save-playlist"]
    if config.get("music_video_resolution"):
        cmd += ["--music-video-resolution", str(config.get("music_video_resolution"))]
    if config.get("music_video_codec_priority"):
        cmd += ["--music-video-codec-priority", str(config.get("music_video_codec_priority"))]
    if config.get("cover_format"):
        cmd += ["--cover-format", str(config.get("cover_format"))]
    if config.get("album_folder_template"):
        cmd += ["--album-folder-template", str(config.get("album_folder_template"))]
    if config.get("playlist_folder_template"):
        cmd += ["--playlist-folder-template", str(config.get("playlist_folder_template"))]
    # Naming/layout extras (empty settings = engine defaults).
    if config.get("compilation_folder_template"):
        cmd += ["--compilation-folder-template", str(config.get("compilation_folder_template"))]
    if config.get("file_name_template"):
        cmd += ["--single-disc-file-template", str(config.get("file_name_template"))]
    if config.get("multi_disc_file_template") or config.get("file_name_template"):
        cmd += ["--multi-disc-file-template",
                str(config.get("multi_disc_file_template") or config.get("file_name_template"))]
    if options.get("overwrite", config.get("overwrite")):
        cmd += ["--overwrite"]
    if config.get("artist_auto_select"):
        cmd += ["--artist-auto-select", config.get("artist_auto_select")]
    if use_wrapper:
        cmd += ["--use-wrapper", "--wrapper-url", wrapper_url]
    if config.get("engine_ledger"):
        cmd += ["--database-path", str(PROJECT_DIR / "data" / "gamdl.db")]

    cmd += urls
    return cmd


def build_gytmdl_command(config: Config, options: dict, urls: list[str]) -> list[str]:
    """Construct the gytmdl (YouTube Music) CLI command from app settings.

    gytmdl writes {artist}/{album}/{track} {title}.m4a by default — same shape
    as gamdl, so everything lands in the same library folder. `ytm_itag` picks
    the codec: 140 = AAC 128k (free), 141 = AAC 256k, 774 = Opus 256k (both
    Premium, need cookies). Cookies are only added when the file exists — free
    itags work without them.
    """
    binary = ytm_binary() or "gytmdl"
    cmd = [binary, "-n"]  # -n: ignore ~/.gytmdl/config.yaml, use explicit flags
    # Cookies are ONLY passed when a YouTube-specific path is configured —
    # never fall back to the main cookies.txt, which is an Apple Music export.
    # (gytmdl switches to the web_music client whenever ANY cookies file is
    # given, so a wrong-domain file actively hurts free itags.)
    ytm_cookies = (options.get("ytm_cookies_path") or config.get("ytm_cookies_path") or "").strip()
    cookies = expand_path(ytm_cookies) if ytm_cookies else ""
    output = expand_path(options.get("output_path") or config.get("output_path"))
    itag = str(options.get("ytm_itag") or config.get("ytm_itag") or "140")

    cmd += ["-o", output]
    cmd += ["-i", itag]
    if cookies and Path(cookies).exists():
        cmd += ["-c", cookies]
    if config.get("cover_size"):
        cmd += ["--cover-size", str(config.get("cover_size"))]
    if config.get("cover_format"):
        cmd += ["--cover-format", str(config.get("cover_format"))]
    if config.get("save_cover"):
        cmd += ["-s"]
    if config.get("synced_lyrics") is False:
        cmd += ["--no-synced-lyrics"]
    if config.get("ytm_po_token"):
        cmd += ["--po-token", str(config.get("ytm_po_token"))]
    if config.get("ytm_file_template"):
        cmd += ["--template-file", str(config.get("ytm_file_template"))]
    if options.get("overwrite", config.get("overwrite")):
        cmd += ["--overwrite"]
    cmd += ["--log-level", "INFO"]
    cmd += urls
    return cmd


def build_votify_command(config: Config, options: dict, urls: list[str]) -> list[str]:
    """Construct the votify (Spotify) CLI command from app settings.

    votify needs a Spotify cookies file (Netscape format, exported from
    open.spotify.com with the 'Get cookies.txt LOCALLY' extension) — without it
    there's no session to download from. `spotify_audio_quality` is a
    comma-separated priority list of votify's enum values (vorbis-low/medium/
    high, aac-medium/high, flac-flac / flac-mp4 / flac-flac-24 / flac-mp4-24);
    320k (vorbis-high) requires a Premium account, the FLAC tiers need a .wvd.
    Invalid values are dropped (votify's CLI rejects them with a crash) and the
    list falls back to vorbis-medium when nothing valid remains.
    Outputs OGG Vorbis / AAC / FLAC into {artist}/{album}/ — same layout as gamdl.
    """
    binary = spotify_binary() or "votify"
    cmd = [binary, "-n"]  # -n: don't use a config file
    cookies = expand_path(
        options.get("spotify_cookies_path") or config.get("spotify_cookies_path") or config.get("cookies_path")
    )
    output = expand_path(options.get("output_path") or config.get("output_path"))
    quality = _sanitize_votify_quality(str(
        options.get("spotify_audio_quality") or config.get("spotify_audio_quality") or "vorbis-medium"
    ))

    cmd += ["-o", output]
    cmd += ["--audio-quality", quality]
    if Path(cookies).exists():
        cmd += ["-c", cookies]
    if config.get("cover_size"):
        cmd += ["--cover-size", str(config.get("cover_size"))]
    if config.get("save_cover"):
        cmd += ["--save-cover-file"]
    if config.get("synced_lyrics") is False:
        cmd += ["--no-synced-lyrics-file"]
    if config.get("album_folder_template") and "{album_artist}" not in config.get("album_folder_template"):
        cmd += ["--album-folder-template", str(config.get("album_folder_template"))]
    if config.get("spotify_wvd_path"):
        wvd = expand_path(str(config.get("spotify_wvd_path")))
        if Path(wvd).exists():
            cmd += ["--wvd-path", wvd]
    if options.get("overwrite", config.get("overwrite")):
        cmd += ["--overwrite"]
    if config.get("engine_ledger"):
        cmd += ["--database-path", str(PROJECT_DIR / "data" / "votify.db")]
    cmd += ["--log-level", "INFO"]
    cmd += urls
    return cmd


_VOTIFY_QUALITIES = frozenset({
    "vorbis-low", "vorbis-medium", "vorbis-high",
    "aac-medium", "aac-high",
    "flac-flac", "flac-mp4", "flac-flac-24", "flac-mp4-24",
})


def _sanitize_votify_quality(value: str) -> str:
    """Keep only votify enum values; never pass garbage that crashes the CLI.

    The app once shipped kbps labels ("160", "320,160") which votify's
    Csv(AudioQuality) type rejects with a usage error on EVERY Spotify job.
    Map the legacy labels to their modern equivalents and drop anything that
    isn't a real enum value."""
    legacy = {"96": "vorbis-low", "160": "vorbis-medium", "320": "vorbis-high"}
    parts = []
    for raw in str(value).split(","):
        item = raw.strip()
        if not item:
            continue
        if item in legacy:
            item = legacy[item]
        if item in _VOTIFY_QUALITIES:
            parts.append(item)
    return ",".join(parts) if parts else "vorbis-medium"


ENGINE_TOOL = {"apple": "gamdl", "spotify": "votify", "youtube": "gytmdl"}

# ---------------------------------------------------------------------------
# amdl (zhaarey/apple-music-downloader) — optional Apple Music engine
# ---------------------------------------------------------------------------
# amdl is a Go Apple Music downloader (ALAC / Atmos / AAC / MV) that runs as a
# Docker image with --network host (Docker Desktop ≥4.34). It talks to the
# itouakirai/wrapper on ports 10020 (decrypt) + 20020 (m3u8) — a DIFFERENT
# wrapper than glomatico/wrapper-v2, which also uses port 10020. So only one
# Apple wrapper can run at a time.
AMDL_IMAGE = "ghcr.io/zhaarey/apple-music-downloader"
AMDL_CONFIG_DIR = PROJECT_DIR / "data" / "amdl"


def amdl_available() -> bool:
    """amdl runs via Docker (image auto-pulled on first run if missing)."""
    return shutil.which("docker") is not None


def amdl_image_present() -> bool:
    """True once the amdl image is pulled locally.

    `docker image inspect` takes ~4s when Docker is running — /api/status
    polls every few seconds, so the answer is cached for 60s."""
    now = time.time()
    if now - _AMDL_IMAGE_CACHE["at"] < 60:
        return _AMDL_IMAGE_CACHE["value"]
    docker = shutil.which("docker")
    if not docker:
        _AMDL_IMAGE_CACHE["value"] = False
        _AMDL_IMAGE_CACHE["at"] = now
        return False
    try:
        out = subprocess.run(
            [docker, "image", "inspect", AMDL_IMAGE],
            capture_output=True, timeout=10,
        )
        present = out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        present = False
    _AMDL_IMAGE_CACHE["value"] = present
    _AMDL_IMAGE_CACHE["at"] = now
    return present


_AMDL_IMAGE_CACHE: dict = {"value": None, "at": 0.0}


def amdl_wrapper_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Quick TCP probe — is the amdl wrapper listening on this port?"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def wrapper_state(url: str | None = None, timeout: float = 4.0) -> dict:
    """Reachability + auth state of the gamdl wrapper-v2 server.

    GETs {url}/me (the same endpoint wrapperctl.wrapper_status uses) and
    returns {"reachable": bool, "auth_state": str, "playback_ready": bool}.
    Used by the ALAC/Atmos preflight so a missing/unlogged wrapper fails
    with a clear message instead of a cryptic gamdl license error.
    """
    base = (url or "http://127.0.0.1").rstrip("/")
    try:
        req = urllib.request.Request(
            base + "/me", headers={"User-Agent": "MusicHighRes/1.2"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            me = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return {"reachable": False, "auth_state": "unknown", "playback_ready": False}
    auth = me.get("auth", {}) if isinstance(me, dict) else {}
    runtime = me.get("runtime", {}) if isinstance(me, dict) else {}
    return {
        "reachable": True,
        "auth_state": auth.get("state", "unknown"),
        "playback_ready": bool(runtime.get("playback_ready")),
    }


def _amdl_config_text(config: Config, options: dict) -> str:
    """Generate the amdl config.yaml, mounted into the container at
    /app/config.yaml (the image's baked-in config fails to parse). All save
    folders point at /downloads — the mounted output folder — so files land in
    the same Artist/Album layout as gamdl ({UrlArtistName}/{AlbumName}).
    exit-on-error is forced on: amdl then exits non-zero on failure instead of
    waiting interactively, which the job system needs for retries."""
    lrc = str(config.get("amdl_lrc_type") or "lyrics")
    convert = str(config.get("amdl_convert") or "off").strip().lower()
    convert_on = convert in ("flac", "mp3", "opus")
    keep_original = bool(config.get("amdl_convert_keep_original"))
    return f'''media-user-token: ""
authorization-token: ""
language: ""
lrc-type: "{lrc}"
lrc-format: "lrc"
embed-lrc: true
save-lrc-file: false
save-artist-cover: false
save-animated-artwork: false
emby-animated-artwork: false
embed-cover: true
cover-size: {config.get("amdl_cover_size")}
cover-format: jpg
tag-sort-order: true
tag-itunes-id: true
alac-save-folder: /downloads
atmos-save-folder: /downloads
aac-save-folder: /downloads
mv-save-folder: /downloads
max-memory-limit: 256
decrypt-m3u8-port: "127.0.0.1:10020"
get-m3u8-port: "127.0.0.1:20020"
get-m3u8-from-device: true
exit-on-error: true
get-m3u8-mode: hires
aac-type: aac-lc
alac-max: {config.get("amdl_alac_max")}
atmos-max: {config.get("amdl_atmos_max")}
limit-max: 200
album-folder-format: "{{AlbumName}}"
playlist-folder-format: "{{PlaylistName}}"
song-file-format: "{{SongNumer}}. {{SongName}}"
artist-folder-format: "{{UrlArtistName}}"
explicit-choice: "[E]"
clean-choice: "[C]"
apple-master-choice: "[M]"
use-songinfo-for-playlist: false
dl-albumcover-for-playlist: false
mv-audio-type: atmos
mv-max: 2160
storefront: "us"
alac-fix: false
convert-after-download: {"true" if convert_on else "false"}
convert-format: "{convert if convert_on else 'flac'}"
convert-keep-original: {"true" if keep_original else "false"}
convert-skip-if-source-matches: true
ffmpeg-path: "ffmpeg"
convert-extra-args: ""
convert-with-metadata: true
convert-warn-lossy-to-lossless: true
convert-skip-lossy-to-lossless: true
convert-check-bad-alac: false
convert-delete-bad-alac: false
proxy: ""
'''


def build_amdl_command(config: Config, options: dict, urls: list[str]) -> list[str]:
    """Construct the amdl download command (docker run --network host).

    The itouakirai wrapper must be running on 10020/20020 — see
    setup_amdl_wrapper.sh. Codec mapping: atmos → --atmos, aac → --aac
    (aac-lc), anything else (alac) → no flag (amdl's default). Single-song
    links (…?i=… or /song/) get --song."""
    docker = shutil.which("docker") or "docker"
    output = expand_path(options.get("output_path") or config.get("output_path"))
    codec = options.get("codec") or config.get("song_codec_priority")
    AMDL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_path = AMDL_CONFIG_DIR / "config.yaml"
    try:
        cfg_path.write_text(_amdl_config_text(config, options), encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"Could not write amdl config: {e}")

    cmd = [
        docker, "run", "--rm", "--network", "host",
        "-v", f"{output}:/downloads",
        "-v", f"{cfg_path}:/app/config.yaml",
    ]
    if "atmos" in (codec or ""):
        cmd += ["--atmos"]
    elif "aac" in (codec or ""):
        cmd += ["--aac"]
    if any(("?i=" in u) or ("/song/" in u) for u in urls):
        cmd += ["--song"]
    cmd += [AMDL_IMAGE] + urls
    return cmd


def run_job(job: Job, env: dict | None = None) -> None:
    """Run the download subprocess — one attempt (called from
    JobManager._dispatch, which owns the schedule window, the concurrency slot
    and the retry loop).

    The engine is chosen per-URL: Apple Music → gamdl, Spotify → votify,
    YouTube Music → gytmdl. Mixing sources in one batch is rejected with a
    clear error. On success the gamdl-only extras (auto FLAC conversion,
    playlist-folder copies, quality verification) run for Apple jobs; votify/
    gytmdl outputs (OGG/Opus/AAC) skip them. On failure it sets
    job._retry_delay to ask the dispatcher for another attempt (1m → 5m → 15m
    backoff, config `auto_retry`), or marks the job failed for good.
    """
    # Validate the batch: one engine per job.
    engines = {url_engine(u) for u in job.urls}
    if len(engines) > 1:
        job.add_line("ERROR: this batch mixes Apple Music, Spotify and/or YouTube links — split them into separate downloads (one source per batch).")
        job.exit_code = 2
        job.set_status("failed")
        if job.manager:
            job.manager._finish(job)
        return
    engine = next(iter(engines)) if engines else "apple"
    job.engine = engine
    tool = ENGINE_TOOL[engine]

    # Pre-flight checks that fail fast with a useful message instead of a
    # cryptic CLI error several lines down.
    if engine == "spotify" and not spotify_binary():
        job.add_line("ERROR: votify is not installed — run:  .venv/bin/pip install 'votify[librespot]'")
        job.set_status("failed")
        if job.manager:
            job.manager._finish(job)
        return
    if engine == "youtube" and not ytm_binary():
        job.add_line("ERROR: gytmdl is not installed — run:  .venv/bin/pip install gytmdl")
        job.set_status("failed")
        if job.manager:
            job.manager._finish(job)
        return
    if engine == "spotify":
        # Prefer the Spotify-specific cookies setting; only fall back to the
        # main cookies file with a heads-up (it's usually an Apple export).
        spot_cookies = (job.options.get("spotify_cookies_path") or job.config.get("spotify_cookies_path") or "").strip()
        if spot_cookies:
            cookies = expand_path(spot_cookies)
        else:
            cookies = expand_path(job.config.get("cookies_path"))
            job.add_line(f"Note: using the main Cookies file for Spotify ({cookies}) — make sure it's a Spotify export, not the Apple Music one.")
        if not Path(cookies).exists():
            job.add_line(f"ERROR: Spotify downloads need a cookies file — not found at {cookies}. Export your Spotify cookies with the 'Get cookies.txt LOCALLY' browser extension and set the path in Settings.")
            job.set_status("failed")
            if job.manager:
                job.manager._finish(job)
            return

    # Snapshot ALL audio files before the engine starts — used to (a) verify
    # votify/gytmdl success (they exit 0 even when every track fails) and
    # (b) record this job's output in the SQLite ledger.
    before_all: set[str] = set()
    try:
        before_all = {
            str(p) for p in Path(job.output_path).rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS
        }
    except OSError:
        before_all = set()

    # Snapshot the .m4a files that already exist BEFORE gamdl starts, so the
    # auto FLAC conversion only touches what this job actually downloads. The
    # snapshot is taken once per job (not per attempt), so files a failed
    # attempt managed to write are still treated as "new" by the next attempt.
    before_m4a: set[str] = set()
    if job.config.get("convert_to_flac"):
        try:
            before_m4a = {str(p) for p in Path(job.output_path).rglob("*.m4a")}
        except OSError:
            before_m4a = set()
    # Same trick for .m3u playlist files: the playlist-folder copy step only
    # processes playlists this job actually created.
    before_m3u: set[str] = set()
    if job.config.get("copy_playlist_folders"):
        try:
            before_m3u = {str(p) for p in Path(job.output_path).rglob("*.m3u")}
        except OSError:
            before_m3u = set()

    if engine == "apple":
        if str(job.config.get("apple_engine") or "gamdl") == "amdl":
            if not amdl_available():
                job.add_line("ERROR: amdl needs Docker — install/start Docker Desktop first.")
                job.set_status("failed")
                if job.manager:
                    job.manager._finish(job)
                return
            if not (amdl_wrapper_port_open(10020) and amdl_wrapper_port_open(20020)):
                job.add_line("ERROR: the amdl wrapper isn't listening on ports 10020/20020 — start it from the Wrapper panel (or ./setup_amdl_wrapper.sh).")
                job.set_status("failed")
                if job.manager:
                    job.manager._finish(job)
                return
            tool = "amdl"
            cmd = build_amdl_command(job.config, job.options, job.urls)
        else:
            # gamdl ALAC/Atmos preflight: the wrapper is mandatory for the
            # "atmos" and pure "alac" codecs (they have no AAC fallback) —
            # fail fast with a clear message instead of letting gamdl die on
            # a cryptic license error (-1002 / "could not find requested
            # codec") several lines down.
            codec = job.codec or ""
            use_wrapper = bool(job.options.get("use_wrapper", job.config.get("use_wrapper")))
            mandatory = codec == "atmos" or codec == "alac"
            if mandatory:
                if not use_wrapper:
                    job.add_line("ERROR: " + ("Dolby Atmos" if codec == "atmos" else "ALAC") + " downloads need the wrapper server — enable 'Use wrapper' in Settings (or run setup_wrapper.sh), then start it from the '5 · Wrapper & login' panel.")
                    job.set_status("failed")
                    if job.manager:
                        job.manager._finish(job)
                    return
                wurl = job.options.get("wrapper_url") or job.config.get("wrapper_url") or "http://127.0.0.1"
                ws = wrapper_state(wurl)
                if not ws["reachable"]:
                    job.add_line(f"ERROR: the wrapper server isn't reachable at {wurl} — is Docker running and the wrapper started? See the '5 · Wrapper & login' panel.")
                    job.set_status("failed")
                    if job.manager:
                        job.manager._finish(job)
                    return
                if ws["auth_state"] != "authenticated":
                    job.add_line(f"ERROR: the wrapper is up but not logged in (state: {ws['auth_state']}) — log in from the '5 · Wrapper & login' panel first.")
                    job.set_status("failed")
                    if job.manager:
                        job.manager._finish(job)
                    return
                if not ws["playback_ready"]:
                    job.add_line("WARNING: wrapper reports playback not ready yet — if downloads fail, check the Wrapper panel.")
            elif "alac" in codec and not use_wrapper:
                job.add_line("Note: wrapper is off — lossless ALAC won't be available; tracks fall back to AAC (enable 'Use wrapper' in Settings for lossless).")
            elif use_wrapper:
                ws = wrapper_state(job.options.get("wrapper_url") or job.config.get("wrapper_url") or "http://127.0.0.1")
                if not ws["reachable"]:
                    job.add_line("WARNING: the wrapper isn't reachable — lossless will be skipped; tracks fall back to AAC.")
            cmd = build_gamdl_command(job.config, job.options, job.urls)
    elif engine == "spotify":
        cmd = build_votify_command(job.config, job.options, job.urls)
    else:
        cmd = build_gytmdl_command(job.config, job.options, job.urls)
    job.add_line(f"Engine: {tool} ({engine})")
    job.set_status("running")
    try:
        job.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except OSError as e:
        job.add_line(f"ERROR: could not launch {tool}: {e}")
        job.set_status("failed")
        if job.manager:
            job.manager._finish(job)
        return

    assert job.proc.stdout is not None
    for line in job.proc.stdout:
        job.add_line(line)

    job.exit_code = job.proc.wait()
    if job.exit_code == 0:
        if engine == "apple":
            job.add_line("Done — download finished successfully.")
            # gamdl-only post-processing: the FLAC/playlist/quality steps all
            # assume gamdl's .m4a/.m3u output layout.
            if job.config.get("convert_to_flac"):
                auto_convert_new_files(job, output_dir=job.output_path, before=before_m4a)
            if job.config.get("copy_playlist_folders"):
                copy_playlist_folders(job, output_dir=job.output_path, before=before_m3u)
            if job.config.get("verify_quality"):
                verify_new_quality(job, output_dir=job.output_path, before=before_m4a)
            # Record this job's new files in the SQLite ledger (authoritative
            # "what we own" index used by the ✓ owned chips and Ledger panel).
            try:
                after_all = {
                    str(p) for p in Path(job.output_path).rglob("*")
                    if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                }
            except OSError:
                after_all = before_all
            written = ledger_record(job, after_all - before_all, engine=tool)
            if written:
                job.add_line(f"Ledger: indexed {written} file(s) in library.sqlite")
            job.set_status("done")
            if job.manager:
                job.manager._finish(job)
            return
        # votify/gytmdl exit 0 even when nothing downloaded — verify files
        # actually appeared, so silent failures become visible (and retryable).
        try:
            after_all = {
                str(p) for p in Path(job.output_path).rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS
            }
        except OSError:
            after_all = before_all
        new_files = after_all - before_all
        if new_files:
            job.add_line(f"Done — download finished successfully ({len(new_files)} new file(s)).")
            written = ledger_record(job, new_files, engine=tool)
            if written:
                job.add_line(f"Ledger: indexed {written} file(s) in library.sqlite")
            job.set_status("done")
            if job.manager:
                job.manager._finish(job)
            return
        has_error = any(
            e.get("level") == "ERROR"
            or ("error" in e["text"].lower() and "0 error" not in e["text"].lower())
            for e in job.log
        )
        if has_error:
            # Fall through to the retry/backoff logic below (same as a gamdl
            # non-zero exit): a transient engine failure respects auto_retry.
            job.add_line("WARNING: no new files appeared — the engine reported errors above, so nothing was downloaded.")
            job.exit_code = 1
        else:
            job.add_line("Done — no new files (nothing to download, or already present).")
            job.set_status("done")
            if job.manager:
                job.manager._finish(job)
            return
    if job.status != "running":  # cancelled by the user via the API
        job.add_line(f"Cancelled (exit {job.exit_code}).")
        if job.manager:
            job.manager._finish(job)
        return
    # Failed: ask the dispatcher to retry with backoff, or fail for good.
    job.attempts += 1
    max_retries = int(job.config.get("auto_retry") or 0)
    if job.attempts <= max_retries:
        delay = [60, 300, 900][min(job.attempts - 1, 2)]
        job.add_line(f"Attempt {job.attempts} failed (exit {job.exit_code}) — retrying in {delay // 60} min…")
        job._retry_delay = delay
        return  # slot released; _dispatch sleeps the backoff, then re-runs us
    job.add_line(f"{tool} exited with code {job.exit_code} (after {job.attempts} attempts).")
    job.set_status("failed")
    if job.manager:
        job.manager._finish(job)


def verify_new_quality(job: Job, output_dir: str, before: set[str]) -> None:
    """Probe the files this job downloaded and log the real codec/bit-depth.

    Makes silent ALAC→AAC downgrades visible: when ALAC was requested but a
    track came back AAC (e.g. the label only offers lossy), the log warns so
    the user knows it wasn't their settings.
    """
    root = Path(output_dir)
    try:
        new_files = [
            p for p in sorted(root.rglob("*.m4a"))
            if str(p) not in before
            and not any(part.startswith(".") for part in p.relative_to(root).parts)
        ]
    except OSError:
        return
    if not new_files:
        return
    quals = {}
    for p in new_files[:40]:
        q = probe_audio_quality(p)
        if not q:
            continue
        key = (q.get("codec"), q.get("bits"), q.get("rate"))
        quals[key] = quals.get(key, 0) + 1
    if not quals:
        return
    summary = ", ".join(
        f"{format_quality({'codec': c, 'bits': b, 'rate': r})}×{n}"
        for (c, b, r), n in sorted(quals.items(), key=lambda kv: -kv[1])
    )
    job.add_line(f"Quality check: {summary}")
    if "alac" in (job.codec or "") or "atmos" in (job.codec or ""):
        aac_count = sum(n for (c, b, r), n in quals.items() if c in ("aac", "m4a", "aac_he"))
        total_probed = sum(quals.values())
        if aac_count and total_probed and aac_count == total_probed:
            job.add_line("WARNING: all tracks came back AAC — lossless wasn't available (label/region limit).")
        elif aac_count:
            job.add_line(f"WARNING: {aac_count}/{total_probed} track(s) came back AAC instead of lossless.")


def run_convert_job(job: Job) -> None:
    """Convert every ALAC (.m4a) file under the source dir to FLAC with ffmpeg.

    Thin wrapper that guarantees the batch-idle callback (music-server rescan
    hook) fires even on the early-return paths — missing folder, missing
    ffmpeg/ffprobe, cancellation.
    """
    try:
        _run_convert_job(job)
    finally:
        if job.manager:
            job.manager._finish(job)


def _run_convert_job(job: Job) -> None:
    """Actual conversion body.

    Only true ALAC files are converted (detected via ffprobe) — AAC downloads
    are skipped automatically since converting lossy audio to FLAC wastes space.
    Original .m4a files are kept; existing .flac files are left untouched
    unless the "overwrite" option is set.
    """
    job.set_status("running")
    src = Path(job.source_dir) if job.source_dir else None
    if not src or not src.is_dir():
        job.add_line(f"ERROR: folder not found: {src}")
        job.set_status("failed")
        return
    if not ffmpeg_binary():
        job.add_line("ERROR: ffmpeg not found. Install it with:  brew install ffmpeg")
        job.set_status("failed")
        return
    if not ffprobe_binary():
        job.add_line("ERROR: ffprobe not found (it ships with ffmpeg). Without it ALAC files can't be detected safely — refusing to convert blindly.")
        job.set_status("failed")
        return

    overwrite = bool(job.options.get("overwrite"))
    m4a_files = sorted(
        p for p in src.rglob("*.m4a")
        if not any(part.startswith(".") for part in p.relative_to(src).parts)
    )
    job.add_line(f"Scanning {src} — found {len(m4a_files)} .m4a file(s).")
    if not m4a_files:
        job.add_line("No .m4a files found — nothing to convert.")
        job.exit_code = 0
        job.set_status("done")
        return

    converted = skipped = errors = 0
    for i, m4a in enumerate(m4a_files, 1):
        # Cancellation is honored between tracks; the in-flight ffmpeg (if any)
        # finishes first, then the loop stops.
        if job._cancel_event.is_set():
            job.add_line("Conversion cancelled by user.")
            job.exit_code = 130
            job.set_status("cancelled")
            return
        result = _convert_one(job, m4a, overwrite, i, len(m4a_files))
        if result == "ok":
            converted += 1
        elif result == "skip":
            skipped += 1
        else:
            errors += 1

    job.exit_code = 1 if errors else 0
    job.add_line(
        f"Done — {converted} converted, {skipped} skipped, {errors} error(s). "
        "Original .m4a files were kept."
    )
    job.set_status("failed" if errors else "done")


def _convert_one(job: Job, m4a: Path, overwrite: bool, i: int, total: int) -> str:
    """Convert a single .m4a to .flac with ffmpeg.

    Returns "ok", "skip" (not ALAC / already has .flac) or "error". Logs
    progress into the job. Only true ALAC files are converted — AAC downloads
    are left alone so we never bloat a library with lossy-to-lossless copies.
    """
    if not ffmpeg_binary():
        job.add_line("ERROR: ffmpeg not found. Install it with:  brew install ffmpeg")
        return "error"
    if not ffprobe_binary():
        job.add_line("ERROR: ffprobe not found (it ships with ffmpeg) — can't detect ALAC safely, skipping.")
        return "error"

    flac = m4a.with_suffix(".flac")
    if flac.exists() and not overwrite:
        job.add_line(f"[{i}/{total}] skip (already has .flac): {m4a.name}")
        return "skip"
    if not is_alac_file(m4a):
        job.add_line(f"[{i}/{total}] skip (not ALAC): {m4a.name}")
        return "skip"
    job.add_line(f"[{i}/{total}] converting: {m4a.name}")
    cmd = [
        ffmpeg_binary(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(m4a),
        "-map", "0:a", "-map", "0:v?",   # audio + optional embedded cover
        "-c:a", "flac", "-c:v", "copy",  # lossless audio, cover copied as-is
        "-map_metadata", "0",              # carry over all tags
        "-map_chapters", "-1",             # drop chapters (FLAC has no ffmpeg writer)
        str(flac),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        if out.returncode == 0 and flac.exists() and flac.stat().st_size > 0:
            size = flac.stat().st_size
            size_s = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"
            job.add_line(f"      → {flac.name} ({size_s})")
            return "ok"
        msg = (out.stderr or out.stdout or "ffmpeg failed").strip().splitlines()
        job.add_line(f"ERROR converting {m4a.name}: {msg[-1] if msg else 'unknown'}")
    except (OSError, subprocess.SubprocessError) as e:
        job.add_line(f"ERROR converting {m4a.name}: {e}")
    return "error"


def auto_convert_new_files(job: Job, output_dir: str, before: set[str]) -> None:
    """Convert only the .m4a files created since the download started.

    Runs after a successful download when "convert_to_flac" is enabled. Only
    files that didn't exist before the job are scanned, so an existing library
    is never re-scanned — just the tracks this download added.
    """
    root = Path(output_dir)
    try:
        new_files = [
            p for p in sorted(root.rglob("*.m4a"))
            if str(p) not in before
            and not any(part.startswith(".") for part in p.relative_to(root).parts)
        ]
    except OSError as e:
        job.add_line(f"ERROR scanning for new files: {e}")
        return
    if not new_files:
        job.add_line("Auto-FLAC: no new .m4a files found to convert.")
        return
    job.add_line(f"Auto-FLAC: converting {len(new_files)} new file(s) to FLAC…")
    converted = skipped = errors = 0
    for i, m4a in enumerate(new_files, 1):
        result = _convert_one(job, m4a, overwrite=False, i=i, total=len(new_files))
        if result == "ok":
            converted += 1
        elif result == "skip":
            skipped += 1
        else:
            errors += 1
    job.add_line(
        f"Auto-FLAC: {converted} converted, {skipped} skipped, {errors} error(s). "
        "Original .m4a files were kept."
    )


def copy_playlist_folders(job: Job, output_dir: str, before: set[str]) -> None:
    """Copy each playlist's tracks into a per-playlist folder.

    gamdl scatters playlist tracks into their normal Artist/Album folders (and,
    with --save-playlist, writes Playlists/{artist}/{title}.m3u listing the
    tracks in playlist order). This step reads every freshly-created .m3u, and
    copies the referenced track files into Playlists/{artist}/{title}/ so the
    playlist is browsable as one folder. Files are COPIED (originals stay in
    their album folders) — enable only if you want the duplication.

    The .m3u lines are paths relative to the .m3u's own directory, so they
    resolve correctly no matter how deep the album folders are.
    """
    root = Path(output_dir)
    try:
        new_m3u = [
            p for p in sorted(root.rglob("*.m3u"))
            if str(p) not in before
            and not any(part.startswith(".") for part in p.relative_to(root).parts)
        ]
    except OSError as e:
        job.add_line(f"ERROR scanning playlists: {e}")
        return
    if not new_m3u:
        return
    for m3u in new_m3u:
        try:
            lines = [
                ln.strip() for ln in m3u.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
        except OSError:
            continue
        if not lines:
            continue
        dest = m3u.parent / m3u.stem  # Playlists/{playlist_artist}/{playlist_title}/
        copied = 0
        mode = "hardlinked" if job.config.get("playlist_hardlink") else "copied"
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for i, rel in enumerate(lines, 1):
                src = (m3u.parent / rel).resolve()
                if not src.is_file():
                    continue
                # Name the copy by playlist position. gamdl's originals are
                # "{album track} {title}.m4a" — drop that number so the copy is
                # just "{playlist position} {title}.m4a" (ordered, no dupes).
                name = src.name
                m = re.match(r"^\d{1,3} ", name)
                if m:
                    name = name[m.end():]
                target = dest / f"{i:02d} {name}"
                try:
                    if target.exists():
                        continue
                    if mode == "hardlinked":
                        try:
                            os.link(src, target)  # APFS: no extra disk used
                        except OSError:
                            shutil.copy2(src, target)  # cross-volume fallback
                    else:
                        shutil.copy2(src, target)
                    copied += 1
                except OSError:
                    continue
        except OSError as e:
            job.add_line(f"ERROR creating playlist folder {dest}: {e}")
            continue
        rel_dest = dest.relative_to(root) if dest.is_relative_to(root) else dest
        job.add_line(f"Playlist folder: {rel_dest}/ ({copied} tracks {mode})")


AUDIO_EXTS = {".m4a", ".flac", ".aac", ".mp3", ".wav", ".alac", ".ogg", ".opus"}
# .ogg/.opus: votify (Spotify) writes OGG Vorbis — without these the
# new-files guard, Library scan and in-app player would miss Spotify content.

# Codec ranking for the format-cleanup tool (lossless > flac > alac > aac).
_CODEC_PREF = {"flac": 3, "wav": 3, "alac": 2, "aac": 1, "aac_he": 1, "m4a": 1, "mp3": 0}


# Codec aliases: ffprobe names and legacy labels → one canonical name each.
# .m4a probes to "alac"/"aac"; OGG Vorbis probes to "vorbis"; a Wave
# container probes to its PCM codec. Normalizing keeps the smart-playlist
# quality filter and the quality histogram consistent with what users pick.
_QUALITY_ALIASES = {
    "m4a": "aac", "mp4": "aac", "aac_he": "aac",
    "vorbis": "ogg", "opus": "ogg",
    "pcm_s8": "wav", "pcm_u8": "wav", "pcm_s16le": "wav",
    "pcm_s24le": "wav", "pcm_s32le": "wav", "pcm_f32le": "wav",
    "pcm_f64le": "wav",
}


def _quality_alias(codec: str) -> str:
    """Canonical codec name for filters/aggregation (aac, alac, flac, mp3,
    ogg, wav, …)."""
    codec = (codec or "").lower()
    return _QUALITY_ALIASES.get(codec, codec)


def file_codec(path: Path) -> str:
    """Best-effort codec name. Instant for non-m4a extensions; only .m4a is
    probed (ALAC vs AAC matters) via the disk quality cache."""
    suffix = path.suffix.lower()
    if suffix == ".alac":
        return "alac"
    if suffix == "":
        return path.name.lower() or "audio"
    if suffix == ".flac":
        return "flac"
    if suffix == ".aac":
        return "aac"
    if suffix == ".mp3":
        return "mp3"
    if suffix == ".wav":
        return "wav"
    if suffix == ".m4a":
        q = probe_audio_quality(path) or {}
        codec = (q.get("codec") or "m4a").lower()
        return codec if codec in _CODEC_PREF else "m4a"
    return suffix.lstrip(".") or "audio"


def _dir_size(directory: Path) -> int:
    """Total size in bytes of all files under a directory."""
    total = 0
    try:
        for p in directory.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _count_audio_files(directory: Path) -> int:
    """Count audio files under a directory (cheap suffix check, no ffprobe)."""
    try:
        return sum(
            1 for p in directory.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS
        )
    except OSError:
        return 0


def _folders_named(root: Path, name: str) -> list[Path]:
    """Find subfolders of root whose name matches (case-insensitive)."""
    try:
        return [p for p in root.iterdir() if p.is_dir() and p.name.lower() == name.lower()]
    except OSError:
        return []


def owned_info(output_dir: str, preview: dict) -> dict:
    """Best-effort check: does the output folder already contain this item?

    `preview` is an /api/url-preview result ({kind, title, track_count, artist}).
    Returns {"owned": n, "total": t} — how many of the item's tracks already
    exist. Exact-name folder matching only (gamdl writes {album_artist}/{album}
    and Playlists/{artist}/{title}.m3u), so it's a hint, not gospel — re-running
    a download is always safe (gamdl skips existing files unless Overwrite).
    """
    root = Path(output_dir)
    kind = preview.get("kind")
    title = (preview.get("title") or "").strip()
    artist = (preview.get("artist") or "").strip()
    total = preview.get("track_count")
    if not root.is_dir() or not title:
        return {"owned": 0, "total": total}

    # Authoritative first: if the SQLite ledger has a record of this item,
    # its per-track rows (checked against disk) are exact. Only when the
    # ledger knows nothing (pre-ledger libraries) fall back to folder scanning.
    if kind in ("song", "album"):
        ledger_owned = ledger_owned_count(output_dir, kind, title, artist)
        if ledger_owned is not None:
            return {"owned": ledger_owned, "total": total}

    if kind == "playlist":
        playlists_root = root / "Playlists"
        if playlists_root.is_dir():
            # Copied playlist folder (Playlists/{artist}/{title}/)
            if artist:
                for artist_dir in _folders_named(playlists_root, artist):
                    for folder in _folders_named(artist_dir, title):
                        return {"owned": _count_audio_files(folder), "total": total}
            for m3u in sorted(playlists_root.rglob(f"{title}.m3u")):
                return {"owned": _count_m3u_entries(m3u), "total": total}
        return {"owned": 0, "total": total}

    if kind == "song":
        # Album folder exists with any audio → assume the track is there.
        if artist:
            for artist_dir in _folders_named(root, artist):
                if _count_audio_files(artist_dir):
                    return {"owned": 1, "total": total}
        return {"owned": 0, "total": total}

    # album (and anything else with a title)
    search_dirs: list[Path] = [root]
    if artist:
        for artist_dir in _folders_named(root, artist):
            search_dirs.append(artist_dir)
        search_dirs.append(root / "Compilations")
    for base in search_dirs:
        for album_dir in _folders_named(base, title):
            return {"owned": _count_audio_files(album_dir), "total": total}
    return {"owned": 0, "total": total}


def scan_library(output_dir: str, max_artists: int = 300, query: str = "") -> dict:
    """Walk the output folder and summarize it for the Library view.

    Returns artists (with their albums + track counts + sizes) and playlists
    (.m3u + copied playlist folders). Everything is read-only. Track counts are
    file counts by extension, sizes are raw byte sums — no metadata parsing, so
    it's fast even on big libraries. `query` filters artists/albums/playlists by
    case-insensitive substring. Capped at `max_artists` entries to stay snappy.
    """
    root = Path(output_dir)
    q = (query or "").strip().lower()
    result = {
        "path": str(root),
        "total_tracks": 0,
        "total_bytes": 0,
        "artists": [],
        "playlists": [],
        "truncated": False,
    }
    if not root.is_dir():
        return result

    def _match(name: str) -> bool:
        return not q or q in name.lower()

    # --- playlists (Playlists/{artist}/{title}.m3u and/or copied folders) ---
    playlists_root = root / "Playlists"
    if playlists_root.is_dir():
        try:
            for m3u in sorted(playlists_root.rglob("*.m3u")):
                if any(part.startswith(".") for part in m3u.relative_to(playlists_root).parts):
                    continue
                title = m3u.stem
                artist = m3u.parent.name if m3u.parent != playlists_root else ""
                if not (_match(title) or _match(artist)):
                    continue
                # A copied playlist folder sits next to the .m3u — count its
                # files, otherwise count the .m3u entries as a hint of size.
                copied_dir = m3u.parent / title
                count = _count_audio_files(copied_dir) if copied_dir.is_dir() else _count_m3u_entries(m3u)
                result["playlists"].append({
                    "name": title,
                    "artist": artist,
                    "path": str(m3u.parent / title if copied_dir.is_dir() else m3u),
                    "track_count": count,
                    "size_bytes": _dir_size(copied_dir) if copied_dir.is_dir() else 0,
                    "copied": copied_dir.is_dir(),
                })
        except OSError:
            pass

    # --- artists ---
    try:
        entries = sorted(
            (p for p in root.iterdir()
             if p.is_dir() and p.name != "Playlists" and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        )
    except OSError:
        entries = []
    for artist_dir in entries:
        if len(result["artists"]) >= max_artists:
            result["truncated"] = True
            break
        albums = []
        try:
            album_dirs = sorted(
                (p for p in artist_dir.iterdir() if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            album_dirs = []
        has_album_dirs = bool(album_dirs)
        # Artist counts as matching when its own name matches the query; in that
        # case ALL its albums are shown (not just name-matching ones).
        match_artist = _match(artist_dir.name)
        artist_tracks = 0
        artist_bytes = 0
        for album_dir in album_dirs:
            n = _count_audio_files(album_dir)
            if not n:
                continue
            sz = _dir_size(album_dir)
            if _match(album_dir.name) or match_artist:
                albums.append({"name": album_dir.name, "path": str(album_dir), "track_count": n, "size_bytes": sz})
                artist_tracks += n
                artist_bytes += sz
        if not has_album_dirs and match_artist:
            # Artist folder with loose files (no album subfolders)
            artist_tracks = _count_audio_files(artist_dir)
            artist_bytes = _dir_size(artist_dir)
        if artist_tracks:
            result["artists"].append({
                "name": artist_dir.name,
                "path": str(artist_dir),
                "track_count": artist_tracks,
                "size_bytes": artist_bytes,
                "albums": albums,
            })
            result["total_tracks"] += artist_tracks
            result["total_bytes"] += artist_bytes
    return result


# ---------------------------------------------------------------------------
# Tag reading/editing (mutagen) + album file listing (for the in-app player)
# ---------------------------------------------------------------------------
def _tag_field(file, key: str) -> str:
    """Read one tag from a mutagen EasyID3/MP4/FLAC object, best-effort."""
    try:
        vals = file.get(key)
        if not vals:
            return ""
        if isinstance(vals, (list, tuple)):
            return str(vals[0])
        return str(vals)
    except Exception:
        return ""


def read_audio_tags(path: Path) -> dict:
    """Read title/artist/album/track tags with mutagen (handles m4a, flac, mp3).
    Returns a dict with the fields that exist; never raises."""
    try:
        from mutagen import File as MFile
    except ImportError:
        return {}
    try:
        f = MFile(path, easy=True)
        if f is None:
            return {}
        return {
            "title": _tag_field(f, "title"),
            "artist": _tag_field(f, "artist"),
            "album": _tag_field(f, "album"),
            "albumartist": _tag_field(f, "albumartist"),
            "track": _tag_field(f, "tracknumber"),
            "date": _tag_field(f, "date")[:4],
            "genre": _tag_field(f, "genre"),
            "disc": _tag_field(f, "discnumber"),
        }
    except Exception:
        return {}


def write_audio_tags(path: Path, fields: dict) -> tuple[bool, str]:
    """Update text tags on one audio file. Returns (ok, message).
    `fields` may include: title, artist, album, albumartist, track, date.
    Empty string or None deletes the tag (so a cleared field can be removed);
    a missing key leaves it unchanged."""
    try:
        from mutagen import File as MFile
    except ImportError:
        return False, "mutagen not installed — run: pip install mutagen"
    try:
        f = MFile(path, easy=True)
        if f is None:
            return False, "Unsupported or corrupt audio file."
        mapping = {"title": "title", "artist": "artist", "album": "album",
                   "albumartist": "albumartist", "track": "tracknumber", "date": "date",
                   "genre": "genre", "disc": "discnumber"}
        changed = []
        for field, tag in mapping.items():
            if field not in fields:
                continue
            val = fields[field]
            if val is None or (isinstance(val, str) and val.strip() == ""):
                # Empty/None = delete this tag (lets the UI clear a wrong value).
                if tag in f:
                    del f[tag]
                    changed.append(field)
                continue
            val = str(val).strip()
            if _tag_field(f, tag) == val:
                continue
            f[tag] = val
            changed.append(field)
        if changed:
            f.save()
        return True, f"Updated: {', '.join(changed) or 'nothing to change'}"
    except Exception as e:
        return False, f"Could not update tags: {e}"


def list_album_files(album_dir: Path) -> list[dict]:
    """List the audio files of one album folder with their tags (for the
    in-app player + tag editor). Sorted by track number when available.
    Each entry carries `codec` (cheap: extension + quality-cache probe for
    .m4a) and, for ALAC files only, `duration` in seconds — the player needs
    both to decide whether the browser can decode the file natively or must
    transcode it (Chrome/Firefox/Edge can't decode ALAC)."""
    try:
        files = [p for p in album_dir.iterdir()
                 if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    except OSError:
        return []
    # Attach the SQLite ledger's downloaded_at per file ("added" date) in one
    # query — the Library's track lists and tag editor can show it.
    dates = ledger_path_dates(str(album_dir.resolve().parent), {str(p) for p in files})
    out = []
    for p in files:
        tags = read_audio_tags(p)
        codec = file_codec(p)
        entry = {
            "path": str(p),
            "name": p.name,
            "title": tags.get("title") or p.stem,
            "artist": tags.get("artist") or "",
            "album": tags.get("album") or "",
            "track": tags.get("track") or "",
            "codec": codec,
            "size": p.stat().st_size if p.exists() else 0,
        }
        if str(p) in dates:
            entry["added"] = dates[str(p)]
        if codec == "alac":  # only ALAC ever needs transcoding — probe its length once
            entry["duration"] = _probe_duration(p)
        out.append(entry)
    def _key(f):
        try:
            return (int(f["track"]), f["name"].lower())
        except (TypeError, ValueError):
            return (9999, f["name"].lower())
    out.sort(key=_key)
    return out


_DUR_CACHE: dict = {}
_DUR_LOCK = threading.Lock()


def _probe_duration(path: Path) -> float | None:
    """Audio length in seconds via ffprobe (format.duration), or None.
    Cached in memory by path+mtime so album opens (player + tag editor) don't
    re-spawn an ffprobe per ALAC file every time."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _DUR_CACHE.get(key)
    if cached and cached[0] == st.st_mtime:
        return cached[1]
    ffprobe = ffprobe_binary()
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        dur = (data.get("format") or {}).get("duration")
        result = round(float(dur), 2) if dur else None
        with _DUR_LOCK:
            _DUR_CACHE[key] = (st.st_mtime, result)
            if len(_DUR_CACHE) > 2000:
                _DUR_CACHE.clear()
        return result
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------
# Embedded cover art + on-the-fly transcoding (in-app player)
# ---------------------------------------------------------------------------
# The player streams files via /api/audio. Chrome/Firefox/Edge cannot decode
# ALAC, so ALAC tracks are transcoded to AAC on the fly with ffmpeg (fragmented
# mp4 = playback starts in ~1s; seeking re-streams from a t= seconds offset).
# Covers are read with mutagen and cached in memory (path+mtime keyed).
_ART_CACHE: dict = {}
_ART_LOCK = threading.Lock()


def read_cover_art(path: Path) -> tuple[bytes, str] | None:
    """Embedded cover art of an audio file: (bytes, mime) or None. Handles
    FLAC pictures, MP4 covr, and ID3 APIC. Cached in memory by path+mtime."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _ART_CACHE.get(key)
    if cached and cached[0] == st.st_mtime:
        return cached[1]
    try:
        from mutagen import File as MFile
    except ImportError:
        return None
    result = None
    try:
        f = MFile(path)
        if f is None:
            return None
        # FLAC / Ogg: pictures list
        pics = getattr(f, "pictures", None)
        if pics:
            p = pics[0]
            result = (bytes(p.data), p.mime or "image/jpeg")
        # MP4: covr atom (imageformat 13 = jpeg, 14 = png)
        elif getattr(f, "tags", None) is not None and "covr" in f.tags:
            data = f.tags["covr"][0]
            mime = "image/png" if getattr(data, "imageformat", 0) == 14 else "image/jpeg"
            result = (bytes(data), mime)
        # ID3: APIC frame
        elif getattr(f, "tags", None) is not None and "APIC:" in f.tags:
            apic = f.tags["APIC:"]
            result = (bytes(apic.data), apic.mime or "image/jpeg")
    except Exception:
        result = None
    if result:
        with _ART_LOCK:
            _ART_CACHE[key] = (st.st_mtime, result)
            if len(_ART_CACHE) > 400:
                _ART_CACHE.clear()
    return result


def transcode_audio(path: Path, seek_seconds: float = 0.0):
    """Generator streaming a file transcoded to AAC (ADTS) via ffmpeg, for
    browsers that can't decode the source codec (ALAC in Chrome/Firefox/Edge).
    `seek_seconds` restarts from that offset (fast input seek). ADTS is used
    instead of fragmented mp4 because this ffmpeg build buffers mp4 output to
    a pipe until the very end — ADTS writes incrementally, so playback starts
    in about a second. The subprocess is terminated when the generator closes
    (client disconnect or stream end). Returns None if ffmpeg is unavailable."""
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        return None
    cmd = [ffmpeg, "-v", "error", "-y"]
    if seek_seconds and seek_seconds > 0:
        cmd += ["-ss", f"{seek_seconds:.3f}"]  # before -i = fast seek
    cmd += [
        "-i", str(path),
        "-map", "0:a:0",
        "-c:a", "aac", "-b:a", "320k",
        "-f", "adts", "-",
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=65536
        )
    except OSError:
        return None

    def _gen():
        produced = False
        try:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                produced = True
                yield chunk
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.SubprocessError:
                        proc.kill()
                # ffmpeg errors are quiet (stderr not read until here) — log them
                # so a transcode that silently yields nothing is diagnosable.
                if proc.stderr:
                    err = proc.stderr.read().decode("utf-8", "replace")[-500:].strip()
                    proc.stderr.close()
                    if not produced and err:
                        logging.getLogger("app").warning(
                            "Transcode failed for %s: %s", path, err
                        )
            except OSError:
                pass

    return _gen()


# ---------------------------------------------------------------------------
# Smart duplicate finder (audio fingerprinting)
# ---------------------------------------------------------------------------
# Matches by *sound*, not by filename: decode the first 15s of each candidate
# to mono PCM via ffmpeg and hash it. Same track with different filenames or
# containers (ALAC vs FLAC of the same master) share a fingerprint. Lossy AAC
# won't match lossless of the same song (different data) — that's fine, the
# name+size finder already handles exact-file copies.
_FP_CACHE: dict = {}  # path -> (mtime, fingerprint)
_FP_LOCK = threading.Lock()


def _audio_fingerprint(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    cached = _FP_CACHE.get(str(path))
    if cached and cached[0] == st.st_mtime:
        return cached[1]
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        return None
    try:
        # 15 seconds, mono, 16 kHz, raw s16le → hash the PCM. Different codecs
        # of the same master decode to near-identical PCM (lossless ones at least).
        out = subprocess.run(
            [ffmpeg, "-v", "error", "-t", "15", "-i", str(path),
             "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
            capture_output=True, timeout=60,
        )
        if out.returncode != 0 or not out.stdout:
            return None
        fp = hashlib.sha256(out.stdout).hexdigest()
        with _FP_LOCK:
            _FP_CACHE[str(path)] = (st.st_mtime, fp)
            if len(_FP_CACHE) > 2000:
                _FP_CACHE.clear()
        return fp
    except (OSError, subprocess.SubprocessError):
        return None


def find_smart_duplicates(output_dir: str, limit: int = 300) -> list[dict]:
    """Group audio files that sound the same (fingerprint match), excluding
    Playlists/ copies. `limit` caps how many files get fingerprinted per call
    (each is a short ffmpeg decode) — results are cached in memory."""
    root = Path(output_dir)
    if not root.is_dir():
        return []
    # Walk with an early stop so the 300-file cap actually bounds the work
    # (rglob-then-slice would scan a huge library before taking anything).
    files: list[Path] = []
    try:
        for p in root.rglob("*"):
            if len(files) >= limit:
                break
            if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
                continue
            rel = p.relative_to(root)
            if rel.parts and (rel.parts[0].lower() == "playlists" or rel.parts[0].startswith(".")):
                continue
            files.append(p)
    except OSError:
        pass
    groups: dict[str, list[Path]] = {}
    for p in files:
        fp = _audio_fingerprint(p)
        if fp:
            groups.setdefault(fp, []).append(p)
    out = []
    for fp, paths in groups.items():
        if len(paths) < 2:
            continue
        entries = []
        for p in paths:
            q = probe_audio_quality(p)
            entries.append({
                "path": str(p),
                "name": p.name,
                "quality": format_quality(q) or "",
                "size": p.stat().st_size if p.exists() else 0,
                "title": (read_audio_tags(p).get("title") or ""),
            })
        entries.sort(key=lambda e: (e["quality"] or "").lower(), reverse=True)
        out.append({"name": entries[0]["title"] or entries[0]["name"], "files": entries})
    out.sort(key=lambda g: -g["files"][0]["size"])
    return out


# ---------------------------------------------------------------------------
# Watch folder (drop a URL file → it downloads)
# ---------------------------------------------------------------------------
def _read_urls_from_file(path: Path) -> list[str]:
    """Pull download links (Apple Music / Spotify / YouTube) out of a
    text/.m3u/.url file."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    urls = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "http://" in line or "https://" in line:
            # .url files: [InternetShortcut]\nURL=https://…
            if line.lower().startswith("url="):
                line = line[4:].strip()
            urls.append(line)
    return urls


class WatchFolder:
    """Polls a configured folder; when a new file containing Apple Music links
    appears, it enqueues a download job and moves the file to a done/ folder.
    Start with start(), stop with stop()."""

    def __init__(self, manager, config: Config):
        self.manager = manager
        self.config = config
        self._stop = threading.Event()
        self._thread = None
        self._empty_polls: dict[str, int] = {}  # path → consecutive URL-less polls

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watch-folder")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(3.0):
            try:
                self._poll()
            except Exception:
                pass  # never kill the watcher

    def _poll(self) -> None:
        folder = str(self.config.get("watch_folder") or "").strip()
        if not folder:
            return
        root = Path(expand_path(folder))
        if not root.is_dir():
            return
        done_dir = root / ".done"
        ignore = [s.strip().lower() for s in str(self.config.get("watch_ignore") or "").split(",") if s.strip()]
        try:
            candidates = [p for p in root.iterdir()
                          if p.is_file() and p.suffix.lower() in (".txt", ".m3u", ".url", ".list", "")
                          and not any(ig and ig in p.name.lower() for ig in ignore)]
        except OSError:
            return
        for p in sorted(candidates):
            if p.name.startswith("."):
                continue
            urls = _read_urls_from_file(p)
            if not urls:
                # No URLs (yet) — the file may still be being written. Give it a
                # couple of polls, then park it in .done/ so it stops polling.
                empty = self._empty_polls.get(str(p), 0)
                self._empty_polls[str(p)] = empty + 1
                if empty + 1 >= 3:
                    try:
                        done_dir.mkdir(exist_ok=True)
                        p.rename(done_dir / p.name)
                    except OSError:
                        pass
                    self._empty_polls.pop(str(p), None)
                continue
            self._empty_polls.pop(str(p), None)
            try:
                job = self.manager.start(urls[:200], {})
                job.add_line(f"Watch folder: {p.name} → {len(urls[:200])} URL(s).")
                try:
                    done_dir.mkdir(exist_ok=True)
                    p.rename(done_dir / p.name)
                except OSError:
                    pass
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Library stats (for the dashboard)
# ---------------------------------------------------------------------------
def library_stats(output_dir: str) -> dict:
    """Aggregate stats from a library scan: totals, codec split, top artists.
    Uses the cached quality probes so repeated calls are cheap."""
    lib = scan_library(output_dir)
    stats = {
        "path": lib.get("path"),
        "artists": len(lib.get("artists", [])),
        "albums": sum(len(a.get("albums", [])) for a in lib.get("artists", [])),
        "playlists": len(lib.get("playlists", [])),
        "tracks": lib.get("total_tracks", 0),
        "bytes": lib.get("total_bytes", 0),
        "codec_split": {},
        "top_artists": [],
    }
    # Codec split by probing each album once (cached).
    codecs: dict[str, int] = {}
    for artist in lib.get("artists", []):
        for album in artist.get("albums", []):
            q = album_quality(Path(album["path"]))
            if q and q.get("codec"):
                codecs[q["codec"]] = codecs.get(q["codec"], 0) + 1
    stats["codec_split"] = dict(sorted(codecs.items(), key=lambda kv: -kv[1]))
    top = sorted(lib.get("artists", []), key=lambda a: -a.get("track_count", 0))[:10]
    stats["top_artists"] = [{"name": a["name"], "tracks": a.get("track_count", 0), "bytes": a.get("size_bytes", 0)} for a in top]
    return stats


def _count_m3u_entries(m3u: Path) -> int:
    """Count non-comment, non-empty lines of an .m3u file."""
    try:
        lines = m3u.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    return sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#"))


# ---------------------------------------------------------------------------
# Duplicate finder + library renames
# ---------------------------------------------------------------------------
def find_duplicates(output_dir: str) -> list[dict]:
    """Find audio files duplicated across the library.

    Groups by (filename, byte size) — same name and same size is almost always
    the same track. Playlists/ copies are intentionally excluded (they're
    hardlinks/copies of the originals). Each group lists every path with its
    size and (best-effort) audio quality so the user can keep the best one.
    """
    root = Path(output_dir)
    seen: dict[tuple[str, int], list[Path]] = {}
    try:
        files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    except OSError:
        return []
    for p in files:
        rel = p.relative_to(root)
        if rel.parts and (rel.parts[0].lower() == "playlists" or rel.parts[0].startswith(".")):
            continue
        try:
            key = (p.name.lower(), p.stat().st_size)
        except OSError:
            continue
        seen.setdefault(key, []).append(p)
    groups = []
    for (name, size), paths in seen.items():
        if len(paths) < 2:
            continue
        entries = []
        for p in paths:
            q = probe_audio_quality(p)
            entries.append({
                "path": str(p),
                "size": size,
                "quality": format_quality(q) or "",
                "mtime": p.stat().st_mtime if p.exists() else 0,
            })
        entries.sort(key=lambda e: (e["quality"] or "").lower(), reverse=True)
        groups.append({"name": name, "size": size, "files": entries})
    groups.sort(key=lambda g: -g["size"])
    return groups


def rename_library_path(output_dir: str, old_path: str, new_name: str) -> tuple[bool, str]:
    """Rename an artist or album folder inside the library. Returns (ok, msg)."""
    root = Path(output_dir).resolve()
    old = Path(os.path.expanduser(old_path)).resolve()
    new_name = (new_name or "").strip()
    try:
        old.relative_to(root)
    except ValueError:
        return False, "Path is outside the output folder."
    if not old.is_dir():
        return False, "That folder no longer exists."
    if not new_name or new_name in (".", "..") or "/" in new_name or "\\" in new_name:
        return False, "Enter a plain folder name (no slashes)."
    if old.name == new_name:
        return True, "No change."
    target = old.parent / new_name
    if target.exists():
        return False, f"{new_name} already exists there."
    try:
        old.rename(target)
    except OSError as e:
        return False, f"Could not rename: {e}"
    return True, str(target)


# ---------------------------------------------------------------------------
# Format duplicate cleanup (FLAC vs ALAC in the same album) + recoverable trash
# ---------------------------------------------------------------------------
# With "Auto-convert ALAC → FLAC" on, every album folder ends up with BOTH a
# .m4a (ALAC) and a .flac of the same track. This tool groups those by track
# (tagged track number, else normalized title) and flags which file is worth
# keeping, so the UI can offer to delete the rest. Deletions are never
# permanent: files move into <output>/.trash/ with a manifest recording their
# original location, so they can be restored.
# Guards the .trash manifest read-modify-write so two rapid deletes can't
# clobber each other's entries (single-user loopback app, but cheap insurance).
_TRASH_LOCK = threading.Lock()


def _track_key(track: str, title: str, name: str) -> str:
    """Group key for the tracks of one album: track number when tagged, else
    a normalized title. '01 Song.flac' and '01 Song.m4a' land on the same key
    even when tags are missing."""
    m = re.match(r"\s*(\d+)", track or "")
    if m:
        return "t" + str(int(m.group(1))).zfill(3)
    base = re.sub(r"[^a-z0-9]+", "", (title or "").lower())
    if not base:
        # Fall back to the filename WITHOUT its extension, so '01 Airbag.flac'
        # and '01 Airbag.m4a' still land on the same key when tags are missing.
        stem = re.sub(r"\.[^.]+$", "", name or "")
        base = re.sub(r"[^a-z0-9]+", "", stem.lower())
        base = re.sub(r"^\d+", "", base)
    return "n" + base


def _format_rank(f: dict) -> tuple:
    """Sort key for a group of same-track files: lossless first, then codec
    preference (flac > alac > aac), then size as a bitrate proxy."""
    codec = (f.get("codec") or "").lower()
    lossless = codec in ("flac", "alac", "wav")
    return (1 if lossless else 0, _CODEC_PREF.get(codec, 0), f.get("size") or 0)


def find_format_duplicates(output_dir: str, max_artists: int = 600) -> list[dict]:
    """Tracks that exist in the same album folder in MORE THAN ONE format
    (e.g. ALAC .m4a + FLAC .flac after auto-conversion). Each group lists its
    files with codec/quality and marks the one worth keeping (`keep: true`).
    Playlists/ copies are excluded (only Artist/Album folders are scanned)."""
    root = Path(output_dir)
    if not root.is_dir():
        return []
    lib = scan_library(output_dir, max_artists=max_artists)
    groups: list[dict] = []
    for artist in lib.get("artists", []):
        for album in artist.get("albums", []):
            files = list_album_files(Path(album["path"]))
            if len(files) < 2:
                continue
            by_key: dict[str, list[dict]] = {}
            for f in files:
                key = _track_key(f.get("track") or "", f.get("title") or "", f.get("name") or "")
                by_key.setdefault(key, []).append(f)
            for members in by_key.values():
                if len(members) < 2:
                    continue
                for m in members:
                    m["codec"] = file_codec(Path(m["path"]))
                if len({m["codec"] for m in members}) < 2:
                    continue  # same format twice — not a format duplicate
                members.sort(key=_format_rank, reverse=True)
                for i, m in enumerate(members):
                    m["keep"] = i == 0
                groups.append({
                    "artist": artist["name"],
                    "album": album["name"],
                    "title": members[0].get("title") or members[0].get("name"),
                    "files": members,
                })
    groups.sort(key=lambda g: -(g["files"][0].get("size") or 0))
    return groups


def _library_codec_files(output_dir: str, max_artists: int = 600) -> dict[str, list[dict]]:
    """One pass over the library: map codec → files ({path, name, codec, size}).

    Walks the same Artist/Album folders as find_format_duplicates, so
    Playlists/ copies and hidden folders (.trash) are excluded. Only FLAC and
    ALAC files are collected — the codecs the universal cleanup cares about.
    """
    root = Path(output_dir)
    by_codec: dict[str, list[dict]] = {}
    if not root.is_dir():
        return by_codec
    lib = scan_library(output_dir, max_artists=max_artists)
    for artist in lib.get("artists", []):
        for album in artist.get("albums", []):
            for f in list_album_files(Path(album["path"])):
                codec = (f.get("codec") or "").lower()
                if codec not in ("flac", "alac"):
                    continue
                by_codec.setdefault(codec, []).append({
                    "path": f["path"],
                    "name": f["name"],
                    "codec": codec,
                    "size": f.get("size") or 0,
                })
    return by_codec


def _format_duplicate_deletables(output_dir: str) -> list[dict]:
    """Every non-keep copy across the whole library's format-duplicate groups
    (the "delete all but best" target list)."""
    out: list[dict] = []
    for g in find_format_duplicates(output_dir):
        for f in g["files"]:
            if f.get("keep"):
                continue
            out.append({
                "path": f["path"],
                "name": f["name"],
                "codec": (f.get("codec") or "").lower(),
                "size": f.get("size") or 0,
            })
    return out


def cleanup_preview(output_dir: str) -> dict:
    """Counts + sizes for each universal cleanup action, in one library scan:
    {'flac': {count, bytes}, 'alac': {count, bytes}, 'best': {count, bytes}}.
    Used to label the Cleanup panel's universal buttons before any delete."""
    by_codec = _library_codec_files(output_dir)
    best = _format_duplicate_deletables(output_dir)

    def _stat(files: list[dict]) -> dict:
        return {"count": len(files), "bytes": sum(f["size"] for f in files)}

    return {
        "flac": _stat(by_codec.get("flac", [])),
        "alac": _stat(by_codec.get("alac", [])),
        "best": _stat(best),
    }


def delete_library_files(output_dir: str, paths: list[str], limit: int = 2000) -> dict:
    """Move several library files into .trash in one batch (recoverable).

    Faster than calling delete_library_file per file on big cleanups: every
    rename happens first, then the manifest is written once (a crash mid-batch
    could leave a file in .trash without a manifest entry — the trash strip
    still lists it, but restore would need the original path looked up by
    hand; that window is one write, not one per file). Returns {deleted,
    results, truncated} where each result is {path, ok, message}."""
    root = Path(output_dir).resolve()
    trash = root / ".trash"
    results: list[dict] = []
    try:
        trash.mkdir(exist_ok=True)
    except OSError as e:
        return {"deleted": 0, "truncated": 0, "results": [
            {"path": p, "ok": False, "message": f"Could not create .trash: {e}"}
            for p in paths[:limit]
        ]}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    moved: list[tuple[Path, str]] = []  # (trash path, original rel path)
    handled = 0
    for raw in paths[:limit]:
        handled += 1
        target = Path(os.path.expanduser(raw)).resolve()
    for raw in paths[:limit]:
        target = Path(os.path.expanduser(raw)).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            results.append({"path": raw, "ok": False, "message": "Path is outside the output folder."})
            continue
        if not target.is_file():
            results.append({"path": raw, "ok": False, "message": "That file no longer exists."})
            continue
        dest = trash / f"{stamp}__{target.name}"
        n = 1
        while dest.exists():
            dest = trash / f"{stamp}__{n}__{target.name}"
            n += 1
        try:
            target.rename(dest)
        except OSError as e:
            results.append({"path": raw, "ok": False, "message": f"Could not move to trash: {e}"})
            continue
        moved.append((dest, str(target.relative_to(root))))
        results.append({"path": raw, "ok": True, "message": f"Moved to trash: {dest.relative_to(root)}"})
    if moved:
        with _TRASH_LOCK:
            manifest = _load_trash_manifest(trash)
            for dest, rel in moved:
                manifest[dest.name] = rel
            _save_trash_manifest(trash, manifest)
    return {
        "deleted": len(moved),
        "results": results,
        "truncated": max(0, len(paths) - handled),
    }


def cleanup_library_files(output_dir: str, action: str) -> dict:
    """Run a universal cleanup. action: 'flac' | 'alac' | 'best'.

    - 'flac' — every FLAC file in the library
    - 'alac' — every ALAC file in the library
    - 'best' — every non-best copy in each format-duplicate pair
    All deletes are recoverable (.trash). Returns {ok, action, found, deleted,
    bytes_freed, results}."""
    if action == "best":
        targets = _format_duplicate_deletables(output_dir)
    elif action in ("flac", "alac"):
        targets = _library_codec_files(output_dir).get(action, [])
    else:
        return {"ok": False, "error": f"Unknown cleanup action: {action!r}"}
    if not targets:
        return {"ok": False, "error": "Nothing matched — nothing to delete."}
    batch = delete_library_files(output_dir, [t["path"] for t in targets])
    sizes = {t["path"]: t["size"] for t in targets}
    bytes_freed = sum(sizes[r["path"]] for r in batch.get("results", []) if r.get("ok"))
    return {
        "ok": True,
        "action": action,
        "found": len(targets),
        "deleted": batch.get("deleted", 0),
        "bytes_freed": bytes_freed,
        "truncated": batch.get("truncated", 0),
        "results": batch.get("results", []),
    }


def _load_trash_manifest(trash: Path) -> dict:
    manifest_path = trash / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_trash_manifest(trash: Path, manifest: dict) -> None:
    try:
        (trash / "manifest.json").write_text(json.dumps(manifest, indent=2))
    except OSError:
        pass


def delete_library_file(output_dir: str, path: str) -> tuple[bool, str]:
    """Move one library file into <output>/.trash/ (recoverable). The manifest
    records where it came from so restore_trash_file can put it back."""
    root = Path(output_dir).resolve()
    target = Path(os.path.expanduser(path)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return False, "Path is outside the output folder."
    if not target.is_file():
        return False, "That file no longer exists."
    trash = root / ".trash"
    try:
        trash.mkdir(exist_ok=True)
        rel = str(target.relative_to(root))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = trash / f"{stamp}__{target.name}"
        n = 1
        while dest.exists():
            dest = trash / f"{stamp}__{n}__{target.name}"
            n += 1
        target.rename(dest)
        with _TRASH_LOCK:
            manifest = _load_trash_manifest(trash)
            manifest[dest.name] = rel
            _save_trash_manifest(trash, manifest)
    except OSError as e:
        return False, f"Could not move to trash: {e}"
    return True, f"Moved to trash: {dest.relative_to(root)}"


def trash_info(output_dir: str) -> dict:
    """Everything sitting in the output folder's .trash (recoverable)."""
    root = Path(output_dir)
    trash = root / ".trash"
    out = {"path": str(trash), "files": 0, "bytes": 0, "items": []}
    if not trash.is_dir():
        return out
    try:
        for p in sorted(trash.iterdir()):
            if not p.is_file() or p.name == "manifest.json":
                continue
            out["files"] += 1
            out["bytes"] += p.stat().st_size
            out["items"].append({"name": p.name, "size": p.stat().st_size, "path": str(p)})
    except OSError:
        pass
    return out


def restore_trash_file(output_dir: str, name: str) -> tuple[bool, str]:
    """Move a file back out of .trash to its original location (per the
    manifest). Returns (ok, message)."""
    root = Path(output_dir).resolve()
    trash = root / ".trash"
    if not trash.is_dir():
        return False, "Trash is empty."
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return False, "Invalid file name."
    src = (trash / name).resolve()
    try:
        src.relative_to(trash)
    except ValueError:
        return False, "Invalid trash path."
    if not src.is_file():
        return False, "That file is no longer in trash."
    rel = _load_trash_manifest(trash).get(name)
    if not rel:
        return False, "Original location unknown — drag it back manually from .trash."
    dest = (root / rel).resolve()
    try:
        dest.relative_to(root)
    except ValueError:
        return False, "Invalid original location."
    if dest.exists():
        return False, f"A file already exists at its old spot ({rel}) — drag it back manually."
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
        with _TRASH_LOCK:
            manifest = _load_trash_manifest(trash)
            manifest.pop(name, None)
            _save_trash_manifest(trash, manifest)
    except OSError as e:
        return False, f"Could not restore: {e}"
    return True, f"Restored to {rel}"


def empty_trash(output_dir: str) -> tuple[bool, str]:
    """Permanently delete everything in .trash. This is the ONLY irreversible
    action in the whole cleanup tool — the UI warns before calling it."""
    root = Path(output_dir).resolve()
    trash = root / ".trash"
    if not trash.is_dir():
        return True, "Trash is empty."
    removed = 0
    try:
        for p in list(trash.iterdir()):
            if p.name == "manifest.json":
                continue
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)
            removed += 1
    except OSError as e:
        return False, f"Could not empty trash: {e}"
    try:
        (trash / "manifest.json").unlink(missing_ok=True)
    except OSError:
        pass
    return True, f"Permanently deleted {removed} file(s)."


def _parse_window(window: str) -> tuple[int, int] | None:
    """Parse 'HH:MM-HH:MM' (24h) into (start_min, end_min)."""
    m = re.match(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$", (window or "").strip())
    if not m:
        return None
    h1, m1, h2, m2 = map(int, m.groups())
    if not (0 <= h1 < 24 and 0 <= m1 < 60 and 0 <= h2 < 24 and 0 <= m2 < 60):
        return None
    return (h1 * 60 + m1, h2 * 60 + m2)


def _seconds_until_window(window: str, now: float | None = None) -> int:
    """Seconds until the schedule window opens; 0 if we're inside it now.
    Handles overnight windows (e.g. 22:00-06:00)."""
    parsed = _parse_window(window)
    if parsed is None:
        return 0
    start_min, end_min = parsed
    now = time.localtime(now if now is not None else time.time())
    now_min = now.tm_hour * 60 + now.tm_min
    if start_min == end_min:  # degenerate → treat as no window
        return 0
    if start_min < end_min:  # same-day window
        if start_min <= now_min < end_min:
            return 0
        if now_min < start_min:
            return (start_min - now_min) * 60
        return (24 * 60 - now_min + start_min) * 60
    # overnight window (end wraps past midnight)
    if now_min >= start_min or now_min < end_min:
        return 0
    return (start_min - now_min) * 60


PENDING_JOBS_PATH = PROJECT_DIR / "pending_jobs.json"


class JobManager:
    def __init__(self, config: Config):
        self.config = config
        self.jobs: dict[str, Job] = {}
        self._queue_order: list[str] = []  # queued job ids, in queue order (reorderable)
        self._lock = threading.Lock()
        self._latest_id = 0
        self._sem = threading.Semaphore(max(1, int(config.get("max_concurrent") or 2)))
        # Queue pause: when True, queued jobs hold at the gate (running
        # subprocesses finish naturally). Set/cleared via /api/jobs/pause|resume.
        self._paused = False
        # Called (in a thread) when a batch finishes: all jobs idle. Used to
        # ping a music-server rescan hook. app.py sets this.
        self.on_batch_idle = None
        # True once the current batch has already triggered the callback — the
        # last job in a batch fires it, a later finished job must not re-fire.
        self._batch_fired = False

    def _any_active_locked(self) -> bool:
        return any(j.status in ("queued", "running") for j in self.jobs.values())

    def any_active(self) -> bool:
        with self._lock:
            return self._any_active_locked()

    def pause(self) -> None:
        """Hold queued jobs; running ones finish naturally."""
        self._paused = True

    def resume(self) -> None:
        """Let queued jobs proceed."""
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def cancel_all(self) -> int:
        """Cancel every queued/running job. Returns the number cancelled."""
        n = 0
        with self._lock:
            jobs = list(self.jobs.values())
        for j in jobs:
            if j.status in ("queued", "running"):
                j.add_line("Cancelled by 'Cancel all'.")
                j.cancel()
                j.set_status("cancelled")
                n += 1
        return n

    def _maybe_fire_batch_idle(self) -> None:
        # Check-then-set under the lock: two jobs finishing near-simultaneously
        # (a max_concurrent batch) must not both pass the guard and double-fire.
        with self._lock:
            if self._any_active_locked():
                return
            if self._batch_fired:
                return
            self._batch_fired = True
        cb = self.on_batch_idle
        if cb:
            t = threading.Thread(target=cb, daemon=True)
            t.start()

    def _dispatch(self, job: Job, runner) -> None:
        """Worker thread: wait for the schedule window, then loop over the
        job's attempts. Each attempt takes a concurrency slot, runs, and
        releases it again — so a job sleeping in backoff does NOT hold a slot
        and can't block the rest of the queue."""
        window = str(self.config.get("schedule_window") or "")
        if window and _parse_window(window) is not None:
            delay = _seconds_until_window(window)
            if delay > 0:
                job.add_line(f"Scheduled — waiting for download window ({window}, starts in {delay // 60} min).")
                while delay > 0:
                    if job._cancel_event.is_set():
                        job.set_status("cancelled")
                        if job.manager:
                            job.manager._finish(job)
                        return
                    # Pause holds scheduled jobs too.
                    while self._paused and not job._cancel_event.is_set():
                        time.sleep(0.5)
                    time.sleep(min(15, delay))
                    delay = _seconds_until_window(window)
        while True:
            # Pause gate: queued jobs wait here while the queue is paused.
            while self._paused and not job._cancel_event.is_set():
                time.sleep(0.5)
            if job._cancel_event.is_set():
                job.set_status("cancelled")
                if job.manager:
                    job.manager._finish(job)
                return
            # Queue-order gate + slot acquire. A ▲/▼ reorder must change
            # *execution* order, not just the list view — so a job only tries
            # for a slot while it is the front-most still-queued job. When a
            # slot frees up, the current front job wins it, never a later one
            # that happened to be awake. The acquire is non-blocking so a
            # cancelled job doesn't wait out a long batch before its status
            # flips (and its _done event sets).
            while True:
                if job._cancel_event.is_set():
                    job.set_status("cancelled")
                    if job.manager:
                        job.manager._finish(job)
                    return
                if not self._is_front_of_queue(job.id):
                    time.sleep(0.25)
                    continue
                if self._sem.acquire(blocking=False):
                    break
                time.sleep(0.25)
            # Flip to "running" the moment the slot is held — before the
            # runner's (possibly slow) preflight — so the queue gate can let
            # the next job in instead of waiting on a job that's mid-setup.
            job.set_status("running")
            try:
                if job._cancel_event.is_set():
                    job.set_status("cancelled")
                    if job.manager:
                        job.manager._finish(job)
                    return
                runner(job)
            except Exception as e:  # never strand a job in "running"
                job.add_line(f"ERROR: job thread crashed: {e}")
                job.set_status("failed")
                if job.manager:
                    job.manager._finish(job)
                return
            finally:
                self._sem.release()
            delay = job._retry_delay
            if delay is None:
                return
            # Backoff sleep happens OUTSIDE the semaphore: the slot is free for
            # other jobs while this one waits to retry. _in_backoff makes the
            # queue gate skip this job so it can't hold up the jobs behind it.
            job._retry_delay = None
            job._in_backoff = True
            job.set_status("queued")
            slept = 0
            while slept < delay:
                if job._cancel_event.is_set():
                    job.set_status("cancelled")
                    if job.manager:
                        job.manager._finish(job)
                    return
                # A paused queue holds retries too, not just fresh queued jobs.
                while self._paused and not job._cancel_event.is_set():
                    time.sleep(0.5)
                time.sleep(min(5, delay - slept))
                slept += 5
            job._in_backoff = False

    def start(self, urls: list[str], options: dict | None = None) -> Job:
        options = options or {}
        job = Job(urls, options, self.config)
        job.manager = self
        self._batch_fired = False  # new batch → the idle callback can fire again
        with self._lock:
            self.jobs[job.id] = job
            self._queue_order.append(job.id)
            self._latest_id += 1
        t = threading.Thread(target=self._dispatch, args=(job, run_job), daemon=True)
        t.start()
        self.save_pending()
        return job

    def start_convert(self, source_dir: str, overwrite: bool = False) -> Job:
        options = {"source_dir": source_dir, "overwrite": overwrite, "codec": "flac"}
        job = Job([], options, self.config, kind="convert")
        job.manager = self
        self._batch_fired = False  # new batch → the idle callback can fire again
        with self._lock:
            self.jobs[job.id] = job
            self._queue_order.append(job.id)
            self._latest_id += 1
        t = threading.Thread(target=self._dispatch, args=(job, run_convert_job), daemon=True)
        t.start()
        self.save_pending()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def list(self, limit: int = 30) -> list[dict]:
        """Active jobs first (running, then queued in user queue order), then
        finished jobs newest-first. Queued order is what the ▲/▼ reorder
        buttons change."""
        with self._lock:
            order_idx = {jid: i for i, jid in enumerate(self._queue_order)}
            def _key(j: Job):
                if j.status == "queued":
                    return (0, order_idx.get(j.id, 10 ** 9), -j.created_at)
                if j.status == "running":
                    return (1, 0, -j.created_at)
                return (2, 0, -j.created_at)
            jobs = sorted(self.jobs.values(), key=_key)[:limit]
            return [j.summary() for j in jobs]

    def _is_front_of_queue(self, job_id: str) -> bool:
        """True when `job_id` may take a concurrency slot now: it must be the
        earliest still-queued job per _queue_order. Jobs with no recorded
        order (restored from disk before any reorder) are never blocked."""
        with self._lock:
            others = [
                jid for jid, j in self.jobs.items()
                if jid != job_id and j.status == "queued" and not j._in_backoff
            ]
            if not others:
                return True
            order = {jid: i for i, jid in enumerate(self._queue_order)}
            mine = order.get(job_id)
            if mine is None:
                return True
            for oid in others:
                oi = order.get(oid)
                if oi is not None and oi < mine:
                    return False
            return True

    def reorder(self, job_id: str, delta: int) -> tuple[bool, str]:
        """Move a queued job up/down in the queue (delta ±1). Returns
        (ok, message). Running jobs are unaffected — they already started."""
        with self._lock:
            q = [jid for jid, j in self.jobs.items() if j.status == "queued"]
            if job_id not in q:
                return False, "That job isn't in the queue anymore."
            i = q.index(job_id)
            j = i + delta
            if not (0 <= j < len(q)):
                return False, "Already at the edge of the queue."
            q[i], q[j] = q[j], q[i]
            self._queue_order = q
        self.save_pending()
        return True, "Queue reordered."

    def clear_finished(self) -> None:
        """Remove finished jobs from the list (thread-safe)."""
        with self._lock:
            for job in list(self.jobs.values()):
                if job.status in ("done", "failed", "cancelled"):
                    self.jobs.pop(job.id, None)

    def any_active(self) -> bool:
        with self._lock:
            return any(j.status in ("queued", "running") for j in self.jobs.values())

    def _finish(self, job: Job) -> None:
        """Called when a job reaches a terminal state; fires the batch-idle
        callback if nothing else is still running."""
        self.save_pending()
        self._maybe_fire_batch_idle()

    # ---- queue persistence (survives restarts) -------------------------
    def save_pending(self) -> None:
        """Persist queued/running jobs to pending_jobs.json so they survive a
        server restart. Called on every terminal state and after new starts."""
        with self._lock:
            items = []
            for job in self.jobs.values():
                if job.status not in ("queued", "running"):
                    continue
                items.append({
                    "kind": job.kind,
                    "urls": list(job.urls),
                    "options": {k: v for k, v in job.options.items() if isinstance(v, (str, int, bool))},
                    "source_dir": job.source_dir,
                })
        try:
            if items:
                PENDING_JOBS_PATH.write_text(json.dumps(items))
            elif PENDING_JOBS_PATH.exists():
                PENDING_JOBS_PATH.unlink()
        except OSError:
            pass

    def restore_pending(self) -> int:
        """Re-queue jobs saved by a previous run. Returns how many were restored.
        Safe to call once at startup; jobs re-enter the normal queue."""
        if not PENDING_JOBS_PATH.exists():
            return 0
        try:
            items = json.loads(PENDING_JOBS_PATH.read_text(encoding="utf-8"))
            PENDING_JOBS_PATH.unlink()
        except (OSError, ValueError):
            return 0
        if not isinstance(items, list):
            return 0
        restored = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                if item.get("kind") == "convert":
                    src = item.get("source_dir")
                    if src:
                        job = self.start_convert(str(src))
                        job.add_line("Restored from a previous session.")
                        restored += 1
                else:
                    urls = [u for u in item.get("urls") or [] if isinstance(u, str) and u.strip()]
                    if urls:
                        job = self.start(urls, dict(item.get("options") or {}))
                        job.add_line("Restored from a previous session.")
                        restored += 1
            except Exception:
                continue
        return restored


# ---------------------------------------------------------------------------
# Library power features (v1.1): desktop notifications, ReplayGain, LRCLIB
# lyrics backfill, quality histogram, ledger export, download history,
# Apple Music catalog search, hi-res cover upgrades, smart playlists and the
# wishlist. All pure functions on the library folder / ledger — no app wiring.
# ---------------------------------------------------------------------------


def notify_desktop(title: str, message: str) -> bool:
    """Fire a native OS notification (macOS Notification Center, Windows toast,
    Linux notify-send). Best-effort: never raises, returns whether it tried."""
    try:
        if sys.platform == "darwin":
            msg = message.replace("\\", "\\\\").replace('"', '\\"')
            ttl = title.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e",
                              f'display notification "{msg}" with title "{ttl}"'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sys.platform == "win32":
            ps = (
                "$ErrorActionPreference='SilentlyContinue'\n"
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
                "$t=[Windows.UI.Notifications.ToastTemplateType]::ToastText02\n"
                "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t)\n"
                "$text=$x.GetElementsByTagName('text')\n"
                "$text.Item(0).AppendChild($x.CreateTextNode($env:MHR_TITLE)) | Out-Null\n"
                "$text.Item(1).AppendChild($x.CreateTextNode($env:MHR_MSG)) | Out-Null\n"
                "$toast=[Windows.UI.Notifications.ToastNotification]::new($x)\n"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Music High Res').Show($toast)\n"
            )
            env = dict(os.environ)
            env["MHR_TITLE"] = title
            env["MHR_MSG"] = message
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, message],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# ReplayGain (EBU R128 loudness normalization tags)
# ---------------------------------------------------------------------------
# Each track is measured with ffmpeg's ebur128 filter; track gain is relative
# to a -18 LUFS reference (the de-facto RG standard). Album gain is the
# energy average of the album's tracks, album peak the loudest true peak.
RG_REFERENCE = -18.0


def _ffmpeg_ebur128(path: Path) -> tuple[float | None, float | None]:
    """Measure one audio file: (integrated loudness LUFS, true peak dBFS)."""
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        return None, None
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
             "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True, timeout=900,
        )
        txt = out.stderr or ""
    except (OSError, subprocess.SubprocessError):
        return None, None
    # The ebur128 summary layout varies between ffmpeg releases: 8.x prints
    # "Integrated loudness: I: -9.2 LUFS" on one line, older versions print
    # "I:" on its own line behind a [Parsed_ebur128_0 @ …] prefix. Try the
    # strict form first, then a loose fallback so a match failure never
    # silently skips a file.
    i = re.search(r"Integrated loudness:\s*I:\s*(-?[\d.]+)\s*LUFS", txt)
    p = re.search(r"Peak:\s*(-?[\d.]+)\s*dBFS", txt)
    if not i:
        i = re.search(r"\bI:\s*(-?[\d.]+)\s*LUFS", txt)
    if not p:
        p = re.search(r"\bPeak:\s*(-?[\d.]+)\s*dBFS", txt)
    loudness = float(i.group(1)) if i else None
    peak = float(p.group(1)) if p else None
    return loudness, peak


def _mp3_id3(path: Path, f):
    """Return an ID3 tag object for an MP3, creating it when missing.

    mutagen.File() returns EasyID3 for tagged MP3s and an object with
    tags=None for untagged ones — both leave raw ID3 writes broken. Opening
    mutagen.id3.ID3(path) explicitly reads existing tags or creates an empty
    container (which .save(path) then writes) for tagless files."""
    try:
        import mutagen
    except ImportError:
        return None
    if not getattr(mutagen, "id3", None):
        return None
    tags = getattr(f, "tags", None)
    if isinstance(tags, mutagen.id3.ID3):
        return tags
    try:
        from mutagen.mp3 import MP3
        if isinstance(f, MP3) or str(path).lower().endswith(".mp3"):
            return mutagen.id3.ID3(path)
    except Exception:
        pass
    return None


def _write_replaygain_tags(path: Path, track_gain: float, track_peak: float,
                           album_gain: float, album_peak: float) -> bool:
    """Write REPLAYGAIN_* tags into any container mutagen understands."""
    try:
        from mutagen import File as MFile
        import mutagen
    except ImportError:
        return False
    try:
        f = MFile(path)
        if f is None:
            return False
        tg = f"{track_gain:+.2f} dB"
        tp = f"{track_peak:.6f}"
        ag = f"{album_gain:+.2f} dB"
        ap = f"{album_peak:.6f}"
        vc_types = (
            getattr(mutagen, "flac", None) and mutagen.flac.FLAC,
            getattr(mutagen, "oggvorbis", None) and mutagen.oggvorbis.OggVorbis,
            getattr(getattr(mutagen, "oggopus", None), "OggOpus", None),
        )
        if any(t is not None and isinstance(f, t) for t in vc_types):
            for key, val in [("replaygain_track_gain", tg), ("replaygain_track_peak", tp),
                             ("replaygain_album_gain", ag), ("replaygain_album_peak", ap)]:
                f[key] = [val]
            f.save()
            return True
        if getattr(getattr(mutagen, "mp4", None), "MP4", None) and isinstance(f, mutagen.mp4.MP4):
            from mutagen.mp4 import MP4FreeForm
            for k, v in [("----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN", tg),
                         ("----:com.apple.iTunes:REPLAYGAIN_TRACK_PEAK", tp),
                         ("----:com.apple.iTunes:REPLAYGAIN_ALBUM_GAIN", ag),
                         ("----:com.apple.iTunes:REPLAYGAIN_ALBUM_PEAK", ap)]:
                f[k] = [MP4FreeForm(v.encode("utf-8"))]
            f.save()
            return True
        id3 = _mp3_id3(path, f)
        if id3:
            from mutagen.id3 import TXXX
            for desc, val in [("REPLAYGAIN_TRACK_GAIN", tg), ("REPLAYGAIN_TRACK_PEAK", tp),
                              ("REPLAYGAIN_ALBUM_GAIN", ag), ("REPLAYGAIN_ALBUM_PEAK", ap)]:
                id3.delall("TXXX:" + desc)
                id3.add(TXXX(encoding=3, desc=desc, text=[val]))
            id3.save(path)
            return True
        return False
    except Exception:
        return False


def _album_audio_files(album_dir: Path, limit: int = 300) -> list[Path]:
    try:
        return [p for p in sorted(album_dir.iterdir())
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS][:limit]
    except OSError:
        return []


def replaygain_album(album_dir: Path) -> dict:
    """Measure every track in an album and return per-track loudness + the
    album aggregate. Never writes tags — that's replaygain_scan's job."""
    results: dict[str, dict] = {}
    for p in _album_audio_files(album_dir):
        lufs, peak = _ffmpeg_ebur128(p)
        if lufs is None:
            continue
        results[str(p)] = {"loudness": lufs, "peak": peak}
    if not results:
        return {"tracks": {}, "album_gain": None, "album_peak": None}
    import math
    energy = sum(10 ** (v["loudness"] / 10.0) for v in results.values())
    album_loudness = 10 * math.log10(energy / len(results))
    album_peak = max((v.get("peak") or 0.0) for v in results.values())
    return {
        "tracks": results,
        "album_gain": RG_REFERENCE - album_loudness,
        "album_peak": album_peak,
    }


def replaygain_scan(output_dir: str, album_paths: list[str] | None = None,
                    max_albums: int = 400) -> dict:
    """Scan (a subset of) the library, measure each album, and write RG tags.
    Returns {ok, albums, tracks, failed, errors}. Long-running — callers run
    it in a background task thread."""
    if album_paths:
        albums = [Path(p) for p in album_paths]
    else:
        lib = scan_library(output_dir)
        albums = [Path(al["path"]) for a in lib.get("artists", []) for al in a.get("albums", [])]
    albums = albums[:max_albums]
    updated = 0
    failed = 0
    errors: list[str] = []
    for album in albums:
        try:
            agg = replaygain_album(album)
            if not agg["tracks"]:
                continue
            album_gain = agg["album_gain"]
            album_peak = agg["album_peak"]
            for path_str, v in agg["tracks"].items():
                if _write_replaygain_tags(Path(path_str), RG_REFERENCE - v["loudness"],
                                          v.get("peak") or 0.0, album_gain or 0.0,
                                          album_peak or 0.0):
                    updated += 1
                else:
                    failed += 1
        except Exception as e:
            failed += 1
            errors.append(f"{album.name}: {e}")
    return {"ok": True, "albums": len(albums), "tracks": updated, "failed": failed,
            "errors": errors[:20]}


# ---------------------------------------------------------------------------
# LRCLIB lyrics backfill (free synced-lyrics API, lrclib.net)
# ---------------------------------------------------------------------------
def _fetch_lrclib(artist: str, title: str, album: str, duration: float | None) -> str | None:
    import urllib.parse
    params = {"artist_name": artist, "track_name": title, "album_name": album}
    if duration:
        params["duration"] = str(int(duration))
    url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MusicHighRes/1.1 (lyrics backfill)"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if not isinstance(data, dict):
            return None
        text = data.get("syncedLyrics") or data.get("plainLyrics") or ""
        return text.strip() or None
    except (OSError, ValueError):
        return None


def lrclib_backfill(output_dir: str, max_files: int = 1500) -> dict:
    """Fetch synced (or plain) lyrics from lrclib.net for library tracks that
    don't already have a .lrc sidecar. Writes {stem}.lrc next to each file.
    Returns {ok, fetched, already, no_match, errors}."""
    root = Path(output_dir)
    fetched = 0
    already = 0
    no_match = 0
    errors: list[str] = []
    count = 0
    try:
        audio = [p for p in root.rglob("*")
                 if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                 and not any(part.startswith(".") for part in p.relative_to(root).parts)]
    except OSError:
        audio = []
    for p in audio:
        if count >= max_files:
            break
        count += 1
        lrc = p.with_suffix(".lrc")
        if lrc.exists():
            already += 1
            continue
        tags = read_audio_tags(p)
        title = tags.get("title") or p.stem
        artist = tags.get("artist") or ""
        if not artist or not title:
            continue
        text = _fetch_lrclib(artist, title, tags.get("album") or "", _probe_duration(p))
        if not text:
            no_match += 1
            continue
        try:
            lrc.write_text(text, encoding="utf-8")
            fetched += 1
        except OSError as e:
            errors.append(f"{p.name}: {e}")
    return {"ok": True, "fetched": fetched, "already": already,
            "no_match": no_match, "errors": errors[:20]}


# ---------------------------------------------------------------------------
# Quality histogram + download history (from the cached probes / ledger)
# ---------------------------------------------------------------------------
def quality_histogram(output_dir: str, max_files: int = 20000) -> dict:
    """Aggregate probed quality across the whole library: codec, bit-depth and
    sample-rate distributions plus the lossless/lossy split."""
    root = Path(output_dir)
    codecs: dict[str, int] = {}
    bits: dict[str, int] = {}
    rates: dict[str, int] = {}
    lossless = lossy = total = 0
    try:
        audio = [p for p in root.rglob("*")
                 if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                 and not any(part.startswith(".") for part in p.relative_to(root).parts)]
    except OSError:
        audio = []
    for p in audio[:max_files]:
        q = probe_audio_quality(p)
        if not q or not q.get("codec"):
            continue
        total += 1
        codec = _quality_alias(q["codec"])
        codecs[codec] = codecs.get(codec, 0) + 1
        if codec in ("alac", "flac", "wav"):
            lossless += 1
        else:
            lossy += 1
        if q.get("bits"):
            b = str(q["bits"])
            bits[b] = bits.get(b, 0) + 1
        if q.get("rate"):
            r = str(round(q["rate"] / 1000))
            rates[r] = rates.get(r, 0) + 1
    return {
        "tracks": total, "lossless": lossless, "lossy": lossy,
        "by_codec": dict(sorted(codecs.items(), key=lambda kv: -kv[1])),
        "by_bits": dict(sorted(bits.items(), key=lambda kv: -int(kv[0]))),
        "by_rate": dict(sorted(rates.items(), key=lambda kv: -int(kv[0]))),
    }


def ledger_export(output_dir: str, fmt: str = "csv") -> tuple[str, str]:
    """Dump the whole SQLite ledger. Returns (content, suggested_filename)."""
    def _dump(conn):
        rows = conn.execute(
            "SELECT path, url, engine, title, artist, album, codec, size, "
            "downloaded_at, job_id FROM tracks ORDER BY downloaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    try:
        rows = _ledger_query(_dump)
    except sqlite3.Error:
        rows = []
    if fmt == "json":
        return json.dumps(rows, indent=2, ensure_ascii=False), "ledger.json"
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    if rows:
        writer.writerow(list(rows[0].keys()))
        for r in rows:
            writer.writerow(list(r.values()))
    return buf.getvalue(), "ledger.csv"


def library_history(output_dir: str) -> dict:
    """Download history from the ledger: counts per month/year, top artists,
    total jobs recorded."""
    def _dump(conn):
        return conn.execute("SELECT downloaded_at, artist, size FROM tracks").fetchall()

    try:
        rows = _ledger_query(_dump)
    except sqlite3.Error:
        rows = []
    by_month: dict[str, int] = {}
    by_year: dict[str, int] = {}
    artists: dict[str, int] = {}
    for r in rows:
        ts = r["downloaded_at"]
        if not ts:
            continue
        lt = time.localtime(ts)
        ym = f"{lt.tm_year:04d}-{lt.tm_mon:02d}"
        by_month[ym] = by_month.get(ym, 0) + 1
        by_year[str(lt.tm_year)] = by_year.get(str(lt.tm_year), 0) + 1
        name = r["artist"] or "Unknown"
        artists[name] = artists.get(name, 0) + 1
    return {
        "total": len(rows),
        "by_month": dict(sorted(by_month.items())),
        "by_year": dict(sorted(by_year.items())),
        "top_artists": [{"name": k, "tracks": v}
                        for k, v in sorted(artists.items(), key=lambda kv: -kv[1])[:10]],
    }


# ---------------------------------------------------------------------------
# Apple Music catalog search (iTunes Search API) — find + queue without
# leaving the app. Also powers the hi-res cover upgrade.
# ---------------------------------------------------------------------------
def catalog_search(query: str, entity: str = "album", country: str = "US",
                   limit: int = 24) -> list[dict]:
    import urllib.parse
    itunes_entity = {"song": "song", "artist": "musicArtist"}.get(entity, "album")
    url = ("https://itunes.apple.com/search?term=" + urllib.parse.quote(query) +
           f"&entity={itunes_entity}&country={urllib.parse.quote(str(country))}&limit={limit}&media=music")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MusicHighRes/1.1"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return []
    results = []
    for item in (data.get("results") or []):
        art = item.get("artworkUrl100") or ""
        if itunes_entity == "musicArtist":
            results.append({
                "kind": "artist",
                "name": item.get("artistName") or "",
                "artist": item.get("artistName") or "",
                "artist_id": item.get("artistId"),
                "url": item.get("artistLinkUrl") or "",
                "art": art.replace("100x100bb", "400x400bb") if art else "",
                "track_count": 0,
                "year": "",
            })
            continue
        url_v = item.get("collectionViewUrl") or item.get("trackViewUrl") or ""
        if not url_v:
            continue
        results.append({
            "kind": entity,
            "name": item.get("collectionName") or item.get("trackName") or "",
            "artist": item.get("artistName") or "",
            "album": item.get("collectionName") or "",
            "url": url_v,
            "art": art.replace("100x100bb", "400x400bb") if art else "",
            "track_count": item.get("trackCount") or 0,
            "year": (item.get("releaseDate") or "")[:4],
        })
    return results


def artist_discography(artist_id: str, country: str = "US", limit: int = 200) -> list[dict]:
    """Albums by an Apple Music artist (iTunes Lookup API, entity=album)."""
    import urllib.parse
    url = ("https://itunes.apple.com/lookup?id=" + urllib.parse.quote(str(artist_id)) +
           f"&entity=album&country={urllib.parse.quote(str(country))}&limit={limit}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MusicHighRes/1.1"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return []
    results = []
    for item in (data.get("results") or []):
        if item.get("wrapperType") != "collection":
            continue
        art = item.get("artworkUrl100") or ""
        url_v = item.get("collectionViewUrl") or ""
        if not url_v:
            continue
        results.append({
            "kind": "album",
            "name": item.get("collectionName") or "",
            "artist": item.get("artistName") or "",
            "album": item.get("collectionName") or "",
            "url": url_v,
            "art": art.replace("100x100bb", "400x400bb") if art else "",
            "track_count": item.get("trackCount") or 0,
            "year": (item.get("releaseDate") or "")[:4],
        })
    return results


def lossy_albums(output_dir: str) -> list[dict]:
    """Albums whose best file is lossy (AAC/MP3/OGG/Opus) — candidates for a
    lossless re-download. Joins the library scan with the ledger's source URL
    so the UI can re-queue each album's Apple Music link at ALAC (+ overwrite)."""
    lossless = {"alac", "flac", "wav"}
    out: list[dict] = []
    try:
        scanned = scan_library(output_dir)
    except Exception:
        scanned = None
    if scanned and scanned.get("artists"):
        for artist in scanned["artists"]:
            for album in artist.get("albums", []):
                q = album_quality(Path(album["path"]))
                codec = _quality_alias((q or {}).get("codec") or "")
                if codec in lossless:
                    continue
                out.append({
                    "artist": artist.get("name") or "",
                    "album": album.get("name") or "",
                    "path": album["path"],
                    "track_count": album.get("track_count") or 0,
                    "quality": format_quality(q),
                    "codec": codec,
                    "url": album_source_url(album["path"]),
                })
    out.sort(key=lambda a: (a["artist"].lower(), a["album"].lower()))
    return out


def album_source_url(album_path: str) -> str:
    """First non-empty source URL the ledger recorded under an album folder
    (used for 'Open on Apple Music' album rows)."""
    album = Path(album_path).resolve()
    prefix = str(album) + os.sep

    def _lookup(conn):
        row = conn.execute(
            "SELECT url FROM tracks WHERE path LIKE ? AND url != '' LIMIT 1",
            (prefix + "%",),
        ).fetchone()
        return row[0] if row else ""

    try:
        return _ledger_query(_lookup) or ""
    except sqlite3.Error:
        return ""


def upgrade_album_cover(output_dir: str, album_path: str,
                        size: int = 1200, country: str = "US") -> dict:
    """Re-fetch the album's cover at high resolution from the iTunes Search
    API and re-embed it into every track in the folder."""
    album = Path(album_path)
    files = _album_audio_files(album)
    if not files:
        return {"ok": False, "error": "No audio files in that album folder."}
    tags = read_audio_tags(files[0])
    artist = tags.get("artist") or ""
    title = tags.get("album") or album.name
    if not title:
        return {"ok": False, "error": "Album has no tags to search with."}
    import urllib.parse
    term = urllib.parse.quote(f"{artist} {title}")
    url = (f"https://itunes.apple.com/search?term={term}&entity=album&media=music&limit=8&country={urllib.parse.quote(str(country))}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MusicHighRes/1.1"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return {"ok": False, "error": "Could not reach the iTunes Search API."}
    best = None
    art_low = artist.lower()
    for item in (data.get("results") or []):
        name = (item.get("collectionName") or "").lower()
        if title.lower() in name or name in title.lower():
            art = item.get("artworkUrl100") or ""
            if art:
                best = art
                break
        elif not best and art_low and art_low in (item.get("artistName") or "").lower():
            best = item.get("artworkUrl100") or ""
    if not best:
        return {"ok": False, "error": "No artwork found for that album on the iTunes catalog."}
    hi = best.replace("100x100bb", f"{size}x{size}bb")
    try:
        req = urllib.request.Request(hi, headers={"User-Agent": "MusicHighRes/1.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            img = resp.read()
        mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    except OSError:
        return {"ok": False, "error": "Could not download the artwork."}
    updated = 0
    failed = 0
    for p in files:
        if _embed_cover(p, img, mime):
            updated += 1
        else:
            failed += 1
    return {"ok": True, "tracks": updated, "failed": failed, "bytes": len(img),
            "url": hi}


def _embed_cover(path: Path, data: bytes, mime: str) -> bool:
    """Embed cover art into any mutagen-supported audio file."""
    try:
        from mutagen import File as MFile
        import mutagen
    except ImportError:
        return False
    is_jpg = "jpeg" in mime or "jpg" in mime
    try:
        f = MFile(path)
        if f is None:
            return False
        flac = getattr(mutagen, "flac", None)
        if flac and isinstance(f, flac.FLAC):
            from mutagen.flac import Picture
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = data
            f.clear_pictures()
            f.add_picture(pic)
            f.save()
            return True
        mp4 = getattr(mutagen, "mp4", None)
        if mp4 and isinstance(f, mp4.MP4):
            from mutagen.mp4 import MP4Cover
            fmt = MP4Cover.FORMAT_JPEG if is_jpg else MP4Cover.FORMAT_PNG
            f["covr"] = [MP4Cover(data, imageformat=fmt)]
            f.save()
            return True
        id3 = _mp3_id3(path, f)
        if id3:
            from mutagen.id3 import APIC
            id3.delall("APIC")
            id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
            id3.save(path)
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Smart playlists (saved filters) — match against the scanned library and
# export a real .m3u
# ---------------------------------------------------------------------------
def smart_playlist_matches(output_dir: str, pl: dict) -> dict:
    """Evaluate a saved filter against the library.

    Filter keys (all optional): artist (substring), album (substring),
    quality (codec name from the quality probe: flac/alac/aac/mp3/ogg/opus…),
    years ("2020" or "2015-2020"), recent_days (added within N days; 0 = any),
    min_tracks. Returns matched albums + total track count."""
    artist_q = (str(pl.get("artist") or "")).strip().lower()
    album_q = (str(pl.get("album") or "")).strip().lower()
    quality_q = (str(pl.get("quality") or "")).strip().lower()
    years = str(pl.get("years") or "").strip()
    year_range = None
    if years:
        parts = [p.strip() for p in years.replace("–", "-").split("-")]
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            year_range = (int(parts[0]), int(parts[1]))
        elif len(parts) == 1 and parts[0].isdigit():
            year_range = (int(parts[0]), int(parts[0]))
    recent_days = 0
    try:
        recent_days = max(0, int(pl.get("recent_days") or 0))
    except (TypeError, ValueError):
        recent_days = 0
    min_tracks = 0
    try:
        min_tracks = max(0, int(pl.get("min_tracks") or 0))
    except (TypeError, ValueError):
        min_tracks = 0

    lib = scan_library(output_dir)
    now = time.time()
    matched = []
    for artist in lib.get("artists", []):
        if artist_q and artist_q not in (artist.get("name") or "").lower():
            continue
        for album in artist.get("albums", []):
            if album_q and album_q not in (album.get("name") or "").lower():
                continue
            if min_tracks and (album.get("track_count") or 0) < min_tracks:
                continue
            q = album_quality(Path(album["path"]))
            codec = _quality_alias((q or {}).get("codec") or "")
            if quality_q and codec != _quality_alias(quality_q):
                continue
            added = ledger_album_added(output_dir, album["path"])
            if recent_days and (not added or added < now - recent_days * 86400):
                continue
            year = None
            files = _album_audio_files(Path(album["path"]), limit=1)
            if files:
                year = (read_audio_tags(files[0]).get("date") or "")[:4] or None
            if year_range:
                try:
                    y = int(year)
                except (TypeError, ValueError):
                    continue
                if not (year_range[0] <= y <= year_range[1]):
                    continue
            matched.append({
                "artist": artist.get("name") or "",
                "album": album.get("name") or "",
                "path": album["path"],
                "track_count": album.get("track_count") or 0,
                "quality": format_quality(q),
                "year": year,
            })
    return {"matched": matched, "count": len(matched),
            "tracks": sum(m.get("track_count") or 0 for m in matched)}


def export_smart_playlist(output_dir: str, pl: dict) -> tuple[bool, str, str]:
    """Write a smart playlist's matched tracks to
    Playlists/Smart/{name}.m3u (absolute paths, #EXTINF headers). Returns
    (ok, message, written_path)."""
    name = (str(pl.get("name") or "")).strip() or "Smart playlist"
    safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "Smart playlist"
    result = smart_playlist_matches(output_dir, pl)
    files: list[Path] = []
    for m in result.get("matched", []):
        files.extend(_album_audio_files(Path(m["path"])))
    if not files:
        return False, "No tracks match that filter.", ""
    lines = ["#EXTM3U"]
    for p in files:
        tags = read_audio_tags(p)
        lines.append(f"#EXTINF:{int(_probe_duration(p) or 0)},{tags.get('artist') or ''} - {tags.get('title') or p.stem}")
        lines.append(str(p))
    dest = Path(output_dir) / "Playlists" / "Smart"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{safe}.m3u"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        return False, f"Could not write the playlist: {e}", ""
    return True, f"Wrote {len(files)} track(s) to {out}", str(out)


# ===========================================================================
# v1.1+ must-have batch: media-server scan presets, MusicBrainz tag fixing,
# .m3u + CUE exports, empty-folder housekeeping, catalog ownership enrichment
# ===========================================================================


def server_scan_request(config: Config) -> tuple[str, str, dict, dict] | None:
    """Build the scan request for the configured media server.

    Returns (method, url, headers, json_body) or None when no preset is
    configured. Called by the batch-finished hook and the manual "Scan now"
    button — replaces the raw webhook for the three supported servers:

    * Navidrome: POST /api/scan  (header X-ND-Authorization: Bearer <token>)
    * Plex:      GET  /library/sections/<id>/refresh?X-Plex-Token=<token>
    * Jellyfin:  POST /Library/Refresh?api_key=<token>
    """
    stype = str(config.get("server_type") or "").strip().lower()
    base = str(config.get("server_url") or "").strip().rstrip("/")
    token = str(config.get("server_token") or "").strip()
    if not stype or not base:
        return None
    if stype == "navidrome":
        return ("POST", f"{base}/api/scan",
                {"Content-Type": "application/json",
                 "X-ND-Authorization": f"Bearer {token}" if token else "",
                 "User-Agent": "music-high-res"},
                {"refresh": True})
    if stype == "plex":
        q = f"?X-Plex-Token={token}" if token else ""
        section = str(config.get("server_section") or "1").strip() or "1"
        return ("GET", f"{base}/library/sections/{section}/refresh{q}",
                {"Accept": "application/json", "User-Agent": "music-high-res"}, {})
    if stype == "jellyfin":
        q = f"?api_key={token}" if token else ""
        return ("POST", f"{base}/Library/Refresh{q}",
                {"Content-Type": "application/json", "User-Agent": "music-high-res"}, {})
    return None


# ---------------------------------------------------------------------------
# MusicBrainz tag fixing
# ---------------------------------------------------------------------------
_MB_LAST_CALL = [0.0]  # module-level clock for MusicBrainz's 1 req/s policy


def _mb_search(query: str, limit: int = 5) -> list[dict]:
    """One MusicBrainz recording search with the required rate limit."""
    import urllib.parse
    global _MB_LAST_CALL
    elapsed = time.time() - _MB_LAST_CALL[0]
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)
    url = ("https://musicbrainz.org/ws/2/recording/?query=" +
           urllib.parse.quote(query) +
           f"&fmt=json&limit={limit}")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "MusicHighRes/1.1 (local music manager)",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        _MB_LAST_CALL[0] = time.time()
        return data.get("recordings") or []
    except (OSError, ValueError):
        return []


def _mb_pick(recordings: list[dict], want_ms: float | None) -> dict | None:
    """Best recording match by duration proximity (MusicBrainz durations are
    in ms). Prefers a hit within ±3s; otherwise the smallest difference."""
    if not recordings:
        return None
    if want_ms is None:
        return recordings[0]
    scored = []
    for r in recordings:
        d = r.get("length")
        if not d:
            continue
        diff = abs(float(d) - want_ms)
        scored.append((diff, r))
    scored.sort(key=lambda t: t[0])
    return (scored[0][1] if scored else recordings[0])


def musicbrainz_fix_album(album_path: str | Path) -> dict:
    """Auto-fix track tags from MusicBrainz for one album folder.

    Matches each track's (artist, title, duration) against the MusicBrainz
    recording database and writes canonical title/artist/year (and album tags
    when the file has none). Never deletes existing tags it can't confirm.
    Returns {total, fixed, skipped, notes} for the task log.
    """
    album_dir = Path(album_path)
    files = _album_audio_files(album_dir)
    fixed = skipped = 0
    notes: list[str] = []
    for p in files:
        tags = read_audio_tags(p)
        title = (tags.get("title") or p.stem).strip()
        artist = (tags.get("artist") or "").strip()
        if not title:
            skipped += 1
            continue
        # Escape embedded double quotes — a title like "Say \"Hello\"" would
        # otherwise produce a malformed Lucene query and silently miss.
        def _q(s: str) -> str:
            return s.replace('"', '\\"')
        query = f'recording:"{_q(title)}"'
        primary = artist
        if artist:
            # Multi-artist credits ("A, B & C", "A feat. B") rarely match the
            # full phrase — search the primary name only, then fall back.
            primary = re.split(r"[,;&]|feat\.|ft\.", artist)[0].strip()
            query += f' AND artist:"{_q(primary)}"'
        recs = _mb_search(query)
        if not recs and primary and primary != artist:
            recs = _mb_search(f'recording:"{_q(title)}" AND artist:"{_q(artist)}"')
        if not recs:
            recs = _mb_search(f'recording:"{_q(title)}"')
        want_ms = None
        dur = _probe_duration(p)
        if dur:
            want_ms = dur * 1000.0
        best = _mb_pick(recs, want_ms)
        if not best:
            skipped += 1
            continue
        fields: dict = {"title": best.get("title") or title}
        if artist:
            fields["artist"] = artist
        rels = best.get("releases") or []
        if rels:
            rel = rels[0]
            if not (tags.get("album") or "").strip():
                fields["album"] = rel.get("title") or tags.get("album") or ""
                cc = rel.get("artist-credit") or []
                if cc and not (tags.get("albumartist") or "").strip():
                    names = [c.get("name") for c in cc if isinstance(c, dict) and c.get("name")]
                    if names:
                        fields["albumartist"] = ", ".join(names)
            date = rel.get("date") or ""
            if date and not (tags.get("date") or "").strip():
                fields["date"] = date[:4]
        # Only count as "fixed" when something actually changed — a track with
        # already-canonical tags counts as skipped, not fixed.
        changed = any(
            str(tags.get(k) or "").strip() != str(v or "").strip()
            for k, v in fields.items()
        )
        ok, msg = write_audio_tags(p, fields)
        if ok and changed:
            fixed += 1
        else:
            skipped += 1
            if not ok:
                notes.append(f"{p.name}: {msg}")
    return {"total": len(files), "fixed": fixed, "skipped": skipped, "notes": notes}


def musicbrainz_scan(output_dir: str) -> dict:
    """Run musicbrainz_fix_album over every album in the library."""
    albums_fixed = total_fixed = total = 0
    notes: list[str] = []
    try:
        scanned = scan_library(output_dir)
    except Exception:
        scanned = None
    if scanned and scanned.get("artists"):
        for artist in scanned["artists"]:
            for album in artist.get("albums", []):
                total += 1
                r = musicbrainz_fix_album(album["path"])
                total_fixed += r.get("fixed", 0)
                if r.get("fixed"):
                    albums_fixed += 1
                for n in r.get("notes") or []:
                    notes.append(n)
    return {"albums": total, "albums_fixed": albums_fixed,
            "tracks_fixed": total_fixed, "notes": notes}


# ---------------------------------------------------------------------------
# .m3u exports + CUE sheets + empty-dir housekeeping
# ---------------------------------------------------------------------------
def _m3u_lines(files: list[Path]) -> list[str]:
    lines = ["#EXTM3U"]
    for p in files:
        tags = read_audio_tags(p)
        lines.append(f"#EXTINF:{int(_probe_duration(p) or 0)},{tags.get('artist') or ''} - {tags.get('title') or p.stem}")
        lines.append(str(p))
    return lines


def export_library_m3u(output_dir: str) -> str:
    """Full-library playlist (absolute paths)."""
    root = Path(output_dir)
    files = []
    try:
        files = [p for p in root.rglob("*")
                 if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                 and not any(part.startswith(".") for part in p.relative_to(root).parts)
                 and "Playlists" not in p.relative_to(root).parts[:1]]
    except OSError:
        pass
    return "\n".join(_m3u_lines(files)) + "\n"


def album_m3u(album_path: str | Path) -> str:
    """One album's tracks as an .m3u."""
    return "\n".join(_m3u_lines(_album_audio_files(Path(album_path)))) + "\n"


def _cue_index(seconds: float) -> str:
    """Seconds → CUE INDEX 'mm:ss:ff' (75 frames per second). Pure frame math
    so rounding at the top of a second can never emit 'ss:60'."""
    total = max(0, int(round((seconds if seconds > 0 else 0.0) * 75)))
    mm, rem = divmod(total, 4500)  # 4500 frames = 60s
    ss, ff = divmod(rem, 75)
    return f"{mm:02d}:{ss:02d}:{ff:02d}"


def generate_cue_sheet(album_path: str | Path) -> tuple[bool, str, str]:
    """Write a .cue for one album folder (from embedded tags + durations).

    Standard CUE with a FILE per track and cumulative INDEX offsets — the
    format CD players, foobar2000 and XLD all accept. Returns (ok, msg, path).
    """
    album_dir = Path(album_path)
    files = _album_audio_files(album_dir)
    if not files:
        return False, "No audio files in that folder.", ""
    tags = read_audio_tags(files[0])
    album_title = (tags.get("album") or album_dir.name).strip()
    album_artist = (tags.get("albumartist") or tags.get("artist") or "Unknown Artist").strip()
    genre = (tags.get("genre") or "").strip()
    date = (tags.get("date") or "").strip()

    def _q(s: str) -> str:
        return '"' + (s or "").replace('"', "'") + '"'

    lines = []
    if genre:
        lines.append(f'REM GENRE {_q(genre)}')
    if date:
        lines.append(f'REM DATE {_q(date)}')
    lines.append(f'PERFORMER {_q(album_artist)}')
    lines.append(f'TITLE {_q(album_title)}')
    track_no = 0
    offset = 0.0
    for p in files:
        track_no += 1
        t = read_audio_tags(p)
        track_title = (t.get("title") or p.stem).strip()
        track_artist = (t.get("artist") or album_artist).strip()
        dur = _probe_duration(p) or 0.0
        suffix = p.suffix.lower()
        # Technically-correct FILE type per container (most players ignore it,
        # but MP4/FLAC are the right markers for those files).
        marker = "MP4" if suffix in (".m4a", ".mp4", ".aac") else "FLAC" if suffix == ".flac" else "WAVE"
        lines.append(f'FILE {_q(p.name)} {marker}')
        lines.append(f'  TRACK {track_no:02d} AUDIO')
        lines.append(f'    TITLE {_q(track_title)}')
        lines.append(f'    PERFORMER {_q(track_artist)}')
        lines.append(f'    INDEX 01 {_cue_index(offset)}')
        offset += dur
    # Sanitize the filename — album titles can contain '/' etc.
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", album_title).strip() or "Album"
    try:
        out = album_dir / f"{safe_title}.cue"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        return False, f"Could not write the cue sheet: {e}", ""
    return True, f"Wrote {len(files)} track(s) to {out.name}", str(out)


def find_empty_dirs(output_dir: str) -> list[str]:
    """Directories with no files at all (stale after renames/cleanups),
    excluding hidden folders and the trash."""
    root = Path(output_dir)
    empty = []
    if not root.is_dir():
        return empty
    try:
        for d in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
            if not d.is_dir():
                continue
            rel = d.relative_to(root).parts
            if any(part.startswith(".") for part in rel):
                continue
            if rel and rel[0] == ".trash":
                continue
            try:
                if not any(d.iterdir()):
                    empty.append(str(d))
            except OSError:
                continue
    except OSError:
        pass
    return empty


def delete_empty_dirs(output_dir: str) -> int:
    """Remove empty directories, looping until stable so nested empty chains
    fully collapse (a parent only becomes empty after its child is removed).
    Returns how many were removed."""
    removed = 0
    while True:
        batch = find_empty_dirs(output_dir)
        if not batch:
            break
        n = 0
        for d in batch:
            try:
                Path(d).rmdir()
                n += 1
            except OSError:
                continue
        removed += n
        if n == 0:
            break
    return removed

# ---------------------------------------------------------------------------
# v1.2 feature batch (10 must-haves): track search, album format conversion,
# lyrics sidecar reading, cover replace, play-count ledger, index export,
# .m3u import.
# ---------------------------------------------------------------------------


def search_tracks(output_dir: str, query: str, limit: int = 60) -> list[dict]:
    """Search the library by track title / file name, not just artist/album.

    Returns flat matches: {path, name, title, artist, album, album_path,
    artist_path, codec}. Reads tags only for files whose file name matches
    (cheap — no full-library tag pass)."""
    root = Path(output_dir)
    q = (query or "").strip().lower()
    if not q or not root.is_dir():
        return []
    hits: list[dict] = []
    try:
        for artist_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "Playlists" and not p.name.startswith(".")):
            album_dirs = [p for p in artist_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
            # Loose files directly under the artist folder count as a
            # pseudo-album so they're searchable too — even when the artist
            # also has real album subfolders.
            loose = [p for p in artist_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
            folders = album_dirs + ([artist_dir] if loose else [])
            for folder in folders:
                files = loose if folder is artist_dir else sorted(folder.iterdir())
                for p in files:
                    if p.suffix.lower() not in AUDIO_EXTS or not p.is_file():
                        continue
                    if q not in p.stem.lower():
                        continue
                    tags = read_audio_tags(p)
                    album_name = artist_dir.name if folder is artist_dir else folder.name
                    hits.append({
                        "path": str(p),
                        "name": p.name,
                        "title": tags.get("title") or p.stem,
                        "artist": tags.get("artist") or artist_dir.name,
                        "album": tags.get("album") or album_name,
                        "album_path": str(folder),
                        "artist_path": str(artist_dir),
                        "codec": file_codec(p),
                    })
                    if len(hits) >= limit:
                        return hits
    except OSError:
        pass
    return hits


def convert_album_to_format(album_path: str, codec: str, overwrite: bool = False) -> dict:
    """Convert every audio file in one album folder to the given codec.

    codec: 'flac' | 'mp3' | 'opus' | 'aac' | 'ogg'. Original files are always
    kept; a target with the same stem + new extension is written next to them.
    Lossy sources are converted too (user asked for it) — unlike the ALAC→FLAC
    auto-convert, this is an explicit action. Returns {ok, converted, skipped,
    errors, codec, notes}.
    """
    mapping = {
        "flac": (".flac", ["-c:a", "flac"]),
        "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "320k"]),
        "opus": (".opus", ["-c:a", "libopus", "-b:a", "192k"]),
        "aac": (".m4a", ["-c:a", "aac", "-b:a", "256k"]),
        "ogg": (".ogg", ["-c:a", "libvorbis", "-q:a", "6"]),
    }
    if codec not in mapping:
        return {"ok": False, "error": f"Unknown codec '{codec}' — use flac, mp3, opus, aac or ogg."}
    if not ffmpeg_binary():
        return {"ok": False, "error": "ffmpeg isn't installed — needed for conversion."}
    ext, codec_args = mapping[codec]
    album_dir = Path(album_path)
    files = [p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    if not files:
        return {"ok": False, "error": "No audio files in that folder."}
    converted = skipped = errors = 0
    notes: list[str] = []
    for p in sorted(files):
        if p.suffix.lower() == ext:
            skipped += 1
            notes.append(f"skip (already {codec}): {p.name}")
            continue
        target = p.with_suffix(ext)
        if target.exists() and not overwrite:
            skipped += 1
            notes.append(f"skip (already has {target.name}): {p.name}")
            continue
        cmd = [
            ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(p),
            "-map", "0:a", "-map", "0:v?",
            *codec_args, "-c:v", "copy",
            "-map_metadata", "0",
            str(target),
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if out.returncode == 0 and target.exists() and target.stat().st_size > 0:
                converted += 1
                notes.append(f"→ {target.name}")
            else:
                msg = (out.stderr or out.stdout or "ffmpeg failed").strip().splitlines()
                errors += 1
                notes.append(f"ERROR {p.name}: {msg[-1] if msg else 'unknown'}")
        except (OSError, subprocess.SubprocessError) as e:
            errors += 1
            notes.append(f"ERROR {p.name}: {e}")
    return {"ok": True, "codec": codec, "converted": converted,
            "skipped": skipped, "errors": errors, "notes": notes}


def read_lyrics_sidecar(audio_path: str | Path) -> str | None:
    """Read the .lrc sidecar next to an audio file, if present (synced or
    plain). Returns the text or None."""
    p = Path(audio_path)
    for sidecar in (p.with_suffix(".lrc"),):
        if sidecar.is_file():
            try:
                return sidecar.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
    return None


def write_album_cover(album_dir: str | Path, data: bytes, mime: str) -> dict:
    """Embed the given cover image into every audio file in an album folder.

    Returns {ok, updated, errors, message}."""
    album = Path(album_dir)
    files = _album_audio_files(album)
    if not files:
        return {"ok": False, "error": "No audio files in that folder."}
    updated = errors = 0
    for p in files:
        try:
            if _embed_cover(p, data, mime):
                updated += 1
            else:
                errors += 1
        except Exception:
            errors += 1
    return {"ok": True, "updated": updated, "errors": errors,
            "message": f"Cover written into {updated} file(s)" + (f", {errors} failed" if errors else "")}


def ledger_play(output_dir: str, path: str) -> bool:
    """Record a play in the ledger (play_count + last_played) for one file."""
    output = Path(output_dir).resolve()
    target = Path(os.path.expanduser(path)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return False
    if not target.is_file():
        return False
    now = time.time()
    try:
        def _up(conn):
            # Files never recorded before get mtime as their (approximate)
            # downloaded_at so the Library "added" date isn't the play time.
            conn.execute(
                "INSERT INTO tracks (path, mtime, size, codec, downloaded_at, play_count, last_played) "
                "VALUES (?, ?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(path) DO UPDATE SET "
                "play_count = play_count + 1, last_played = ?",
                (str(target), target.stat().st_mtime, target.stat().st_size, file_codec(target),
                 target.stat().st_mtime, now, now),
            )
            conn.commit()
        _ledger_query(_up)
        return True
    except sqlite3.Error:
        return False


def ledger_most_played(output_dir: str, limit: int = 12) -> list[dict]:
    """Most-played tracks from the ledger (play_count DESC, then most recent).
    Rows that no longer exist on disk are dropped. Same shape as
    ledger_recent_plays so the UI can reuse its row renderer."""
    output = Path(output_dir).resolve()
    try:
        def _q(conn):
            rows = conn.execute(
                "SELECT path, play_count, last_played, codec FROM tracks "
                "WHERE play_count > 0 "
                "ORDER BY play_count DESC, last_played DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        rows = _ledger_query(_q)
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        p = Path(r["path"])
        if not p.is_file():
            continue
        try:
            rel = str(p.relative_to(output))
        except ValueError:
            rel = p.name
        out.append({
            "path": str(p),
            "rel": rel,
            "name": p.stem,
            "play_count": r["play_count"] or 0,
            "last_played": r["last_played"],
            "codec": r["codec"] or "",
        })
    return out


def ledger_clear_plays() -> int:
    """Reset every play count in the ledger. Returns how many tracks had
    plays before the reset (0 = nothing to clear)."""
    try:
        def _w(conn):
            n = conn.execute("SELECT COUNT(*) FROM tracks WHERE play_count > 0").fetchone()[0]
            conn.execute("UPDATE tracks SET play_count = 0, last_played = NULL")
            conn.commit()
            return n
        return _ledger_query(_w)
    except sqlite3.Error:
        return 0


def album_duration(album_dir: Path) -> float:
    """Total play time (seconds) of every audio file in an album folder,
    summed from cached ffprobe durations. Returns 0 when nothing is probeable."""
    total = 0.0
    try:
        files = [p for p in album_dir.iterdir()
                 if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    except OSError:
        return 0.0
    for f in files:
        d = _probe_duration(f)
        if d:
            total += d
    return round(total, 1)


def save_album_cover(album_dir: Path) -> tuple[bool, str]:
    """Write the album's embedded cover art to cover.jpg / cover.png in the
    album folder (next to the tracks). Returns (ok, message)."""
    try:
        files = sorted(p for p in album_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    except OSError:
        return False, "Could not read the album folder."
    for f in files:
        art = read_cover_art(f)
        if not art:
            continue
        data, mime = art
        ext = ".png" if "png" in (mime or "") else ".jpg"
        target = album_dir / f"cover{ext}"
        try:
            target.write_bytes(data)
            return True, str(target)
        except OSError as e:
            return False, f"Could not write cover: {e}"
    return False, "No embedded cover art found in that album."


def ledger_recent_plays(output_dir: str, limit: int = 12) -> list[dict]:
    """Most recently played tracks from the ledger (path, title-ish name,
    last_played, play_count)."""
    output = Path(output_dir).resolve()
    try:
        def _q(conn):
            rows = conn.execute(
                "SELECT path, play_count, last_played, codec FROM tracks "
                "WHERE last_played IS NOT NULL "
                "ORDER BY last_played DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        rows = _ledger_query(_q)
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        p = Path(r["path"])
        rel = ""
        try:
            rel = str(p.relative_to(output))
        except ValueError:
            rel = p.name
        out.append({
            "path": r["path"],
            "name": p.name,
            "rel": rel,
            "play_count": r["play_count"],
            "last_played": r["last_played"],
            # Codec so the player knows whether to transcode (ALAC can't be
            # decoded by Chrome/Firefox/Edge) — matches list_album_files.
            "codec": r["codec"] or (file_codec(p) if p.is_file() else ""),
        })
    return out


def export_library_index(output_dir: str, fmt: str = "csv") -> tuple[str, str]:
    """Export a full library track index as CSV or a self-contained HTML page.

    Returns (content, filename). fmt: 'csv' | 'html'."""
    root = Path(output_dir)
    rows: list[dict] = []
    try:
        for artist_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "Playlists" and not p.name.startswith(".")):
            for album_dir in sorted(p for p in artist_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
                for p in sorted(album_dir.iterdir()):
                    if p.suffix.lower() not in AUDIO_EXTS or not p.is_file():
                        continue
                    tags = read_audio_tags(p)
                    rows.append({
                        "artist": artist_dir.name,
                        "album": album_dir.name,
                        "track": tags.get("track") or "",
                        "title": tags.get("title") or p.stem,
                        "codec": file_codec(p) or p.suffix.lstrip(".").upper(),
                        "size": p.stat().st_size if p.is_file() else 0,
                        "path": str(p),
                    })
    except OSError:
        pass
    import csv as _csv
    import io as _io
    if fmt == "html":
        from urllib.parse import quote as _quote
        items = "".join(
            f"<tr><td>{_esc(r['artist'])}</td><td>{_esc(r['album'])}</td>"
            f"<td>{_esc(r['track'])}</td><td>{_esc(r['title'])}</td>"
            f"<td>{_esc(r['codec'])}</td><td>{r['size']:,}</td></tr>"
            for r in rows
        )
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Music High Res — Library index ({len(rows)} tracks)</title>
<style>body{{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;margin:32px;background:#0d0d14;color:#f2f2f7}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #2a2a3a;padding:6px 10px;font-size:13px;text-align:left}}
th{{background:#14141f;position:sticky;top:0}}tr:nth-child(even){{background:#14141f}}
h1{{font-size:18px}}code{{color:#af52de}}</style></head><body>
<h1>🎧 Music High Res — Library index · {len(rows)} tracks</h1>
<p>Exported from <code>{_esc(str(root))}</code></p>
<table><thead><tr><th>Artist</th><th>Album</th><th>#</th><th>Title</th><th>Codec</th><th>Size</th></tr></thead>
<tbody>{items}</tbody></table></body></html>"""
        return html, "music-high-res-library-index.html"
    buf = _io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(["artist", "album", "track", "title", "codec", "size_bytes", "path"])
    for r in rows:
        writer.writerow([r["artist"], r["album"], r["track"], r["title"], r["codec"], r["size"], r["path"]])
    return buf.getvalue(), "music-high-res-library-index.csv"


def import_m3u_playlist(output_dir: str, name: str, content: str, artist: str = "Imported") -> dict:
    """Import an .m3u playlist: parse entries, resolve paths against the
    output folder, and write a Playlists/{artist}/{name}.m3u with only the
    entries that exist. Returns {ok, matched, missing, written}."""
    import re as _re
    name = (name or "Playlist").strip().replace("/", "-") or "Playlist"
    artist = (artist or "Imported").strip().replace("/", "-") or "Imported"
    root = Path(output_dir)
    entries: list[str] = []
    for line in (content or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line)
        if not p.is_absolute():
            p = root / line
        p = Path(os.path.expanduser(str(p))).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            continue
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            entries.append(str(p))
    matched = len(entries)
    # Count entries that point at files which don't exist (informational).
    missing = 0
    for line in (content or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line)
        if not p.is_absolute():
            p = root / line
        p = Path(os.path.expanduser(str(p))).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            continue
        if not (p.is_file() and p.suffix.lower() in AUDIO_EXTS):
            missing += 1
    if not entries:
        return {"ok": False, "error": "No existing library tracks matched in that .m3u.",
                "matched": 0, "missing": missing}
    dest_dir = root / "Playlists" / artist
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.m3u"
    body = "#EXTM3U\n"
    for e in entries:
        body += f"#EXTINF:0,{Path(e).stem}\n{e}\n"
    try:
        dest.write_text(body, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"Could not write playlist: {exc}", "matched": matched, "missing": missing}
    return {"ok": True, "matched": matched, "missing": missing,
            "written": str(dest), "name": name}


def _esc(s: str) -> str:
    """HTML-escape helper for the HTML library index export."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
