"""Music High Res — download & convert manager.

Loads app config, runs the installed `gamdl` binary as a subprocess for
Apple Music downloads, and drives ffmpeg for ALAC→FLAC conversion. Each
download or conversion is tracked as a Job with a live log for the web UI / CLI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"

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
}

CODEC_LABELS = {
    "alac": "ALAC Lossless (24-bit/192kHz)",
    "alac,aac-web": "ALAC with AAC fallback",
    "aac-web": "AAC 256kbps",
    "atmos": "Dolby Atmos",
    "flac": "FLAC conversion",
}

_LOG_LINE_RE = re.compile(
    r"^(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)[:\s]+(?P<msg>.*)$"
)


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


def gamdl_binary() -> str | None:
    return shutil.which("gamdl")


# Cache the version so /api/status doesn't spawn a subprocess on every poll.
_VERSION_CACHE: dict = {"value": None, "at": 0.0}


def gamdl_version() -> str | None:
    now = time.time()
    if _VERSION_CACHE["value"] is not None and now - _VERSION_CACHE["at"] < 30:
        return _VERSION_CACHE["value"]
    try:
        out = subprocess.run(
            ["gamdl", "--version"],
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


def ffmpeg_binary() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_binary() -> str | None:
    return shutil.which("ffprobe")


_FFMPEG_VERSION_CACHE: dict = {"value": None, "at": 0.0}


def ffmpeg_version() -> str | None:
    now = time.time()
    if _FFMPEG_VERSION_CACHE["value"] is not None and now - _FFMPEG_VERSION_CACHE["at"] < 30:
        return _FFMPEG_VERSION_CACHE["value"]
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"],
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
    cmd = ["gamdl", "-n"]  # -n: ignore ~/.gamdl/config.ini, use explicit flags

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
    if config.get("overwrite"):
        cmd += ["--overwrite"]
    if config.get("artist_auto_select"):
        cmd += ["--artist-auto-select", config.get("artist_auto_select")]
    if use_wrapper:
        cmd += ["--use-wrapper", "--wrapper-url", wrapper_url]

    cmd += urls
    return cmd


def run_job(job: Job, env: dict | None = None) -> None:
    """Run the gamdl subprocess for a job (called from a worker thread)."""
    cmd = build_gamdl_command(job.config, job.options, job.urls)
    job.set_status("running")

    # Snapshot the .m4a files that already exist BEFORE gamdl starts, so the
    # auto FLAC conversion only touches what this job actually downloads.
    before_m4a: set[str] = set()
    if job.config.get("convert_to_flac"):
        try:
            before_m4a = {str(p) for p in Path(job.output_path).rglob("*.m4a")}
        except OSError:
            before_m4a = set()

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
        job.add_line(f"ERROR: could not launch gamdl: {e}")
        job.set_status("failed")
        return

    assert job.proc.stdout is not None
    for line in job.proc.stdout:
        job.add_line(line)

    job.exit_code = job.proc.wait()
    if job.exit_code == 0:
        job.add_line("Done — download finished successfully.")
        if job.config.get("convert_to_flac"):
            auto_convert_new_files(job, output_dir=job.output_path, before=before_m4a)
        job.set_status("done")
    elif job.status == "running":
        job.add_line(f"gamdl exited with code {job.exit_code}.")
        job.set_status("failed")


def run_convert_job(job: Job) -> None:
    """Convert every ALAC (.m4a) file under the source dir to FLAC with ffmpeg.

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


class JobManager:
    def __init__(self, config: Config):
        self.config = config
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._latest_id = 0

    def start(self, urls: list[str], options: dict | None = None) -> Job:
        options = options or {}
        job = Job(urls, options, self.config)
        with self._lock:
            self.jobs[job.id] = job
            self._latest_id += 1
        t = threading.Thread(target=run_job, args=(job,), daemon=True)
        t.start()
        return job

    def start_convert(self, source_dir: str, overwrite: bool = False) -> Job:
        options = {"source_dir": source_dir, "overwrite": overwrite, "codec": "flac"}
        job = Job([], options, self.config, kind="convert")
        with self._lock:
            self.jobs[job.id] = job
            self._latest_id += 1
        t = threading.Thread(target=run_convert_job, args=(job,), daemon=True)
        t.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def list(self, limit: int = 30) -> list[dict]:
        with self._lock:
            jobs = sorted(
                self.jobs.values(),
                key=lambda j: (j.created_at, j.id),
                reverse=True,
            )[:limit]
            return [j.summary() for j in jobs]

    def clear_finished(self) -> None:
        """Remove finished jobs from the list (thread-safe)."""
        with self._lock:
            for job in list(self.jobs.values()):
                if job.status in ("done", "failed", "cancelled"):
                    self.jobs.pop(job.id, None)

    def any_active(self) -> bool:
        with self._lock:
            return any(j.status in ("queued", "running") for j in self.jobs.values())
