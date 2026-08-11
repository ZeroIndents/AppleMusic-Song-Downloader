"""Music High Res — local web app.

Serves a small single-page UI on http://127.0.0.1:8741 where you paste Apple
Music URLs, pick quality, and watch gamdl download them into your library
folder. The CLI (cli.py) uses the same download manager.
"""

from __future__ import annotations

import json
import logging
import os
import re as _re
import sys
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
import wrapperctl
import migrate
from downloader import (
    PROJECT_DIR,
    RES_DIR,
    DEFAULT_CONFIG,
    Config,
    JobManager,
    WatchFolder,
    expand_path,
    ffmpeg_binary,
    ffmpeg_version,
    ffprobe_binary,
    PINNED_GAMDL,
    gamdl_binary,
    gamdl_pinned_ok,
    gamdl_version,
    album_quality,
    album_source_url,
    album_m3u,
    amdl_available,
    amdl_image_present,
    artist_discography,
    catalog_search,
    delete_empty_dirs,
    convert_album_to_format,
    export_library_index,
    export_library_m3u,
    export_smart_playlist,
    import_m3u_playlist,
    ledger_play,
    ledger_recent_plays,
    read_lyrics_sidecar,
    search_tracks,
    write_album_cover,
    find_empty_dirs,
    generate_cue_sheet,
    ledger_owned_count,
    lossy_albums,
    musicbrainz_fix_album,
    musicbrainz_scan,
    server_scan_request,
    ledger_export,
    library_history,
    lrclib_backfill,
    notify_desktop,
    quality_histogram,
    replaygain_scan,
    smart_playlist_matches,
    upgrade_album_cover,
    cleanup_library_files,
    cleanup_preview,
    delta_filter_urls,
    delete_library_file,
    empty_trash,
    find_duplicates,
    find_format_duplicates,
    find_smart_duplicates,
    format_quality,
    gamdl_latest_version,
    ledger_album_added,
    ledger_path_dates,
    ledger_rebuild,
    ledger_stats,
    ledger_track_owned,
    library_stats,
    list_album_files,
    owned_info,
    read_audio_tags,
    read_cover_art,
    rename_library_path,
    resolve_cookies_path,
    restore_trash_file,
    scan_library,
    spotify_binary,
    spotify_version,
    transcode_audio,
    url_engine,
    ytm_binary,
    ytm_version,
    trash_info,
    write_audio_tags,
    album_duration,
    ledger_most_played,
    ledger_clear_plays,
    save_album_cover,
)

PORT = int(os.environ.get("MHR_PORT", "8741"))
HOST = "127.0.0.1"
# Keep in lockstep with the CHANGELOG heading (e.g. "[2.1.4] - 2026-08-11")
# when cutting the next release.
VERSION = "2.1.4"

# GitHub repo used by the in-app self-updater (/api/update/*).
GITHUB_REPO = "ZeroIndents/AppleMusic-Song-Downloader"
LOG_DIR = PROJECT_DIR / "logs"

# Server boot timestamp — powers the header "up for …" pill (/api/status).
_SERVER_START = time.time()
# static/index.html is a bundled read-only resource — in a PyInstaller binary
# it lives in the _MEIPASS dir, in source mode next to the app.
STATIC_DIR = RES_DIR / "static"


def setup_logging() -> None:
    """Rotating project-local log (logs/app.log) + normal terminal output.

    Rotates at 1 MB with 3 backups, so the log never grows unbounded. Also
    captures any unhandled exception (main or worker thread) into the file —
    the main reason to have it is diagnosing a server that won't start.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    # Root stays at WARNING so third-party INFO noise (urllib3, werkzeug…) never
    # reaches the file; the "app" logger is the one INFO source we want.
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    logging.getLogger("app").setLevel(logging.INFO)
    # Not per-request noise.
    logging.getLogger("waitress").setLevel(logging.WARNING)

    def _log_exception(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.getLogger("app").critical(
            "Unhandled exception", exc_info=(exc_type, exc, tb)
        )

    sys.excepthook = _log_exception
    threading.excepthook = _log_exception

config = Config()
manager = JobManager(config)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 16_000_000  # 16 MB request cap (cover uploads are base64 — a few MB raw image becomes a few MB of JSON)


@app.before_request
def _reject_cross_origin_mutations():
    """Localhost-CSRF guard for mutating endpoints.

    The app binds to 127.0.0.1, but any webpage the user visits can still fire
    a "simple" form POST at http://127.0.0.1:8741 (browsers send no CORS
    preflight for plain forms) — e.g. to empty .trash or run a cleanup.
    Browsers attach an Origin (and usually Referer) header to those requests;
    curl and the app's own same-origin fetch either omit it or send this
    origin. If an Origin is present and it isn't this app, refuse the request.
    """
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return
    origin = (request.headers.get("Origin") or request.headers.get("Referer") or "").strip()
    if not origin:
        return  # non-browser client (curl, scripts) — nothing to be cross-origin
    if origin.startswith(("http://127.0.0.1", "http://localhost")):
        return
    return jsonify({"ok": False, "error": "Cross-origin request rejected."}), 403


@app.before_request
def _enforce_remote_token():
    """Remote-access token gate. Active only when Settings → Remote access
    token is set. Protects every /api/* call (except /api/status, so health
    checks and the UI's "enter token" detection keep working). Send the token
    as ?token= or the X-MHR-Token header."""
    token = str(config.get("remote_token") or "").strip()
    if not token:
        return
    if not request.path.startswith("/api/") or request.path == "/api/status":
        return
    given = (request.headers.get("X-MHR-Token") or request.args.get("token") or "").strip()
    try:
        import hmac
        ok = given and hmac.compare_digest(given, token)
    except Exception:
        ok = False
    if ok:
        return
    return jsonify({"ok": False, "error": "Access token required. Enter it in Settings → Remote access, or append ?token=…"}), 401


def _fire_scan_hook_async() -> None:
    """When a download batch finishes: trigger a scan on the configured media
    server (Navidrome/Plex/Jellyfin preset) or POST the raw webhook, fire a
    native desktop notification if enabled, and sweep empty folders when
    auto-clean is on."""
    preset = server_scan_request(config)
    if preset:
        method, url, headers, body = preset
        import urllib.request as _urlreq
        import json as _json
        try:
            data = _json.dumps(body).encode() if method == "POST" else None
            req = _urlreq.Request(
                url, data=data,
                headers={k: v for k, v in headers.items() if v},
                method=method,
            )
            with _urlreq.urlopen(req, timeout=8):
                pass
            logging.getLogger("app").info("Media-server scan triggered: %s", url)
        except OSError as e:
            logging.getLogger("app").warning("Media-server scan failed: %s", e)
    else:
        hook = str(config.get("scan_hook_url") or "").strip()
        if hook:
            import urllib.request as _urlreq
            import json as _json
            try:
                body = _json.dumps({"event": "rescan", "source": "music-high-res"}).encode()
                req = _urlreq.Request(
                    hook, data=body,
                    headers={"Content-Type": "application/json", "User-Agent": "music-high-res"},
                    method="POST",
                )
                with _urlreq.urlopen(req, timeout=8):
                    pass
                logging.getLogger("app").info("Scan hook fired: %s", hook)
            except OSError as e:
                logging.getLogger("app").warning("Scan hook failed: %s", e)
    if config.get("desktop_notify"):
        notify_desktop("Music High Res", "Your downloads have finished.")
    if config.get("auto_clean_empty"):
        try:
            n = delete_empty_dirs(str(expand_path(config.get("output_path"))))
            if n:
                logging.getLogger("app").info("Auto-clean removed %d empty folder(s).", n)
        except Exception as e:
            logging.getLogger("app").warning("Auto-clean failed: %s", e)


manager.on_batch_idle = _fire_scan_hook_async
WATCHER = WatchFolder(manager, config)


def _restart_watcher_if_needed() -> None:
    """Start/stop the watch folder thread to match the current config."""
    if str(config.get("watch_folder") or "").strip():
        WATCHER.start()
    else:
        WATCHER.stop()


# ----------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


# PWA shell assets (manifest, service worker, icons) — the app has no static
# folder configured, so these few files get explicit routes.
@app.get("/manifest.json")
def pwa_manifest():
    resp = send_from_directory(STATIC_DIR, "manifest.json")
    # Never let browsers cache these 24h+ — a stale manifest/SW would keep the
    # installed PWA on an old version long after updates ship.
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/sw.js")
def pwa_sw():
    resp = send_from_directory(STATIC_DIR, "sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/icon.svg")
def pwa_icon_svg():
    return send_from_directory(STATIC_DIR, "icon.svg")


@app.get("/icons/<path:name>")
def pwa_icons(name: str):
    return send_from_directory(STATIC_DIR / "icons", name)


# ----------------------------------------------------------------------
# Status + config
# ----------------------------------------------------------------------
_WRAPPER_OK_CACHE: dict = {"value": False, "at": 0.0}


def _wrapper_ok() -> bool:
    """Is the active Apple wrapper genuinely ready for ALAC/Atmos?

    Cached for 3s — /api/status is polled constantly and a wrapper HTTP call
    per poll would stall the UI when the wrapper is slow/unresponsive.
    Semantics per engine: gamdl wrapper is ready when reachable AND logged in
    (auth_state == "authenticated"); the amdl wrapper is ready when its
    container state is "running". Mirrors api_onboarding's definition.
    """
    import time as _t
    now = _t.time()
    if now - _WRAPPER_OK_CACHE["at"] < 3.0:
        return _WRAPPER_OK_CACHE["value"]
    ok = False
    if _amdl_mode():
        try:
            ok = wrapperctl.amdl_wrapper_status().get("state") == "running"
        except Exception:
            ok = False
    else:
        try:
            w = wrapperctl.wrapper_status()
            ok = bool(w.get("reachable") and w.get("auth_state") == "authenticated")
        except Exception:
            ok = False
    _WRAPPER_OK_CACHE["value"] = ok
    _WRAPPER_OK_CACHE["at"] = now
    return ok


@app.get("/api/status")
def api_status():
    cookies_path = resolve_cookies_path(config)
    wrapper_auth = _wrapper_ok()
    return jsonify({
        "gamdl": gamdl_version(),
        "gamdl_found": gamdl_binary() is not None,
        "gamdl_pinned": PINNED_GAMDL,
        "gamdl_pinned_ok": gamdl_pinned_ok(),
        "gamdl_latest": gamdl_latest_version(),
        "ytm_found": ytm_binary() is not None,
        "ytm_version": ytm_version(),
        "spotify_found": spotify_binary() is not None,
        "spotify_version": spotify_version(),
        "apple_engine": config.get("apple_engine"),
        "amdl_available": amdl_available(),
        "amdl_image_present": amdl_image_present(),
        "ffmpeg": ffmpeg_version(),
        "ffmpeg_found": ffmpeg_binary() is not None and ffprobe_binary() is not None,
        "cookies_exists": Path(cookies_path).exists(),
        "cookies_path": cookies_path,
        "output_path": expand_path(config.get("output_path")),
        "wrapper_enabled": bool(config.get("use_wrapper")),
        "wrapper_ok": wrapper_auth,
        "wrapper_url": config.get("wrapper_url"),
        "codec": config.get("song_codec_priority"),
        "convert_to_flac": bool(config.get("convert_to_flac")),
        "python": os.sys.version.split()[0],
        "platform": sys.platform,  # "darwin" | "linux" | "win32" — UI uses it for Finder/Explorer labels
        "version": VERSION,
        "any_active": manager.any_active(),
        "started_at": _SERVER_START,
        "uptime": int(time.time() - _SERVER_START),
    })


@app.get("/api/config")
def api_get_config():
    return jsonify(config.data)


def _normalize_config_value(key: str, value):
    """Coerce one config value to the right type. Shared by the Settings save
    route and backup restore — a malformed backup must never be able to write
    garbage (e.g. a string where an int is expected) into config.json, which
    would crash the app at startup (JobManager does int() on max_concurrent).
    Returns None when the key should be skipped."""
    if key in ("output_path", "cookies_path", "wrapper_url"):
        value = str(value).strip()
        if not value:
            return None
    elif key in ("cover_size",):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
    elif key in ("use_wrapper", "synced_lyrics", "save_cover", "overwrite", "convert_to_flac", "save_playlist", "copy_playlist_folders", "use_album_date", "skip_owned", "playlist_hardlink", "verify_quality", "engine_ledger", "delta_sync", "amdl_convert_keep_original", "desktop_notify", "remote_bind", "auto_clean_empty", "chime_on_done", "check_updates"):
        value = bool(value)
    elif key in ("max_concurrent", "auto_retry"):
        try:
            value = max(0, int(value))
        except (TypeError, ValueError):
            return None
    elif key in ("watch_folder", "notify_url", "spotify_wvd_path", "remote_token",
                 "server_type", "server_url", "server_token", "server_section",
                 "watch_ignore"):
        value = str(value).strip()
    elif key == "releases_ignored":
        if not isinstance(value, list):
            return None
        value = [str(v).strip() for v in value if isinstance(v, (str, int, float))]
    elif key == "accent_color":
        value = str(value).strip()
        if value and not value.startswith("#"):
            return None
    elif key == "smart_playlists":
        if not isinstance(value, list):
            return None
        clean = []
        for item in value:
            if isinstance(item, dict):
                clean.append({k: v for k, v in item.items() if isinstance(v, (str, int, float, bool))})
        value = clean
    elif key == "wishlist":
        if not isinstance(value, list):
            return None
        value = [i for i in value if isinstance(i, dict)]
    elif key == "settings_presets":
        if not isinstance(value, dict):
            return None
        value = {str(k): v for k, v in value.items() if isinstance(v, dict)}
    return value


@app.post("/api/config")
def api_save_config():
    body = request.get_json(silent=True) or {}
    normalized = {}
    for key, value in body.items():
        if key not in config.data:
            continue
        v = _normalize_config_value(key, value)
        if v is not None:
            normalized[key] = v
    changes = config.update(normalized)
    _restart_watcher_if_needed()
    return jsonify({"ok": True, "changes": changes, "config": config.data})


@app.get("/api/onboarding")
def api_onboarding():
    """A friendly 'Getting started' checklist for the UI header: what's ready,
    what's missing, and a hint for each missing item. Everything here is cheap
    (cached versions + filesystem stat calls), so it's safe to poll."""
    cookies_path = resolve_cookies_path(config)
    spotify_cookies = str(config.get("spotify_cookies_path") or "").strip()
    spotify_cookies_path = expand_path(spotify_cookies) if spotify_cookies else ""
    ytm_cookies = str(config.get("ytm_cookies_path") or "").strip()
    ytm_cookies_path = expand_path(ytm_cookies) if ytm_cookies else ""

    gamdl_found = gamdl_binary() is not None
    ffmpeg_found = ffmpeg_binary() is not None and ffprobe_binary() is not None
    cookies_ok = Path(cookies_path).exists()

    docker = wrapperctl.docker_status()
    docker_running = bool(docker.get("running"))
    wrapper_present = wrapperctl.wrapper_present()
    wrapper_auth = False
    if docker_running:
        try:
            w = wrapperctl.wrapper_status()
            wrapper_auth = bool(w.get("reachable") and w.get("auth_state") == "authenticated")
        except Exception:
            wrapper_auth = False

    output = expand_path(config.get("output_path"))
    output_writable = False
    try:
        probe = Path(output)
        if not probe.exists():
            probe.mkdir(parents=True, exist_ok=True)
        test_file = probe / ".mhr-write-test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        output_writable = True
    except OSError:
        output_writable = False

    _ffmpeg_short = "missing"
    if ffmpeg_found:
        full = ffmpeg_version() or ""
        _ffmpeg_short = full.split(",")[0] if "," in full else (full.split()[2] if len(full.split()) > 2 else full)

    items = [
        {
            "key": "python", "label": "Python environment", "ok": True,
            "required": True, "detail": f"Python {os.sys.version.split()[0]}",
            "hint": "The app is running, so this is ready.",
        },
        {
            "key": "gamdl", "label": "gamdl engine (Apple Music)",
            "ok": gamdl_found, "required": True,
            "detail": (gamdl_version() or "missing").replace("gamdl, version ", "v").strip() or "missing",
            "hint": "Install it with:  brew install gamdl  (macOS) or  pip install gamdl",
        },
        {
            "key": "ffmpeg", "label": "FFmpeg (FLAC, player, quality checks)",
            "ok": ffmpeg_found, "required": True,
            "detail": _ffmpeg_short,
            "hint": "Install it with:  brew install ffmpeg  (macOS) or your package manager",
        },
        {
            "key": "cookies", "label": "Apple Music cookies",
            "ok": cookies_ok, "required": not wrapper_auth,
            "detail": cookies_path,
            "hint": "Export cookies.txt from music.apple.com (see README Step 1). Not needed if you use the wrapper.",
        },
        {
            "key": "docker", "label": "Docker Desktop",
            "ok": docker_running, "required": False,
            "detail": "running" if docker_running else ("installed but stopped" if docker.get("installed") else "not installed"),
            "hint": "Only needed for lossless ALAC / Dolby Atmos. Start Docker Desktop, then hit Start here again.",
        },
        {
            "key": "wrapper", "label": "ALAC wrapper (wrapper-v2)",
            "ok": wrapper_auth, "required": False,
            "detail": "authenticated" if wrapper_auth else ("set up" if wrapper_present else "not set up"),
            "hint": "Only needed for lossless ALAC / Atmos — see the '5 · Wrapper & login' panel to set it up and log in.",
        },
        {
            "key": "spotify_cookies", "label": "Spotify cookies",
            "ok": bool(spotify_cookies_path) and Path(spotify_cookies_path).exists(),
            "required": False,
            "detail": spotify_cookies_path or "not configured",
            "hint": "Only needed for Spotify downloads — export from open.spotify.com and set it in Settings.",
        },
        {
            "key": "ytm_cookies", "label": "YouTube Music cookies",
            "ok": bool(ytm_cookies_path) and Path(ytm_cookies_path).exists(),
            "required": False,
            "detail": ytm_cookies_path or "not configured",
            "hint": "Only needed for Premium YouTube Music itags (AAC/Opus 256k) — set it in Settings.",
        },
        {
            "key": "output", "label": "Output folder",
            "ok": output_writable, "required": True,
            "detail": output,
            "hint": "The app can't write to the output folder — change it in Settings.",
        },
    ]
    required_ok = sum(1 for i in items if i["required"] and i["ok"])
    required_total = sum(1 for i in items if i["required"])
    return jsonify({
        "ok": True,
        "items": items,
        "required_ok": required_ok,
        "required_total": required_total,
        "all_required_ready": required_ok == required_total,
    })


# ----------------------------------------------------------------------
# Library (browse downloaded music)
# ----------------------------------------------------------------------
@app.get("/api/library")
def api_library():
    """Summarize the output folder: artists, albums, track counts, sizes,
    quality badges, playlists. Quality probing is cached, so repeated scans
    are cheap after the first."""
    query = str(request.args.get("q") or "").strip()
    output = expand_path(config.get("output_path"))
    try:
        data = scan_library(output, query=query)
        # Attach quality badges (best codec/bit-depth per album).
        for artist in data.get("artists", []):
            for album in artist.get("albums", []):
                q = album_quality(Path(album["path"]))
                album["quality"] = format_quality(q)
                # "Added" date from the SQLite ledger (earliest downloaded_at
                # in the album folder). One query per album — cheap and only
                # hits the ledger when it has rows.
                album["added"] = ledger_album_added(output, album["path"])
            artist["quality"] = next((a["quality"] for a in artist.get("albums", []) if a.get("quality")), "")
    except Exception as e:  # filesystem hiccups
        return jsonify({"ok": False, "error": f"Could not scan library: {e}"}), 500
    return jsonify({"ok": True, **data})


@app.get("/api/library/duplicates")
def api_library_duplicates():
    """Audio files duplicated across the library (same name + size)."""
    try:
        groups = find_duplicates(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "groups": groups, "count": len(groups)})


@app.post("/api/library/rename")
def api_library_rename():
    """Rename an artist or album folder in the Library."""
    body = request.get_json(silent=True) or {}
    ok, msg = rename_library_path(
        expand_path(config.get("output_path")),
        str(body.get("path") or ""),
        str(body.get("new_name") or ""),
    )
    return jsonify({"ok": ok, "message": msg, "path": msg if ok else None}), (200 if ok else 400)


@app.get("/api/library/export")
def api_library_export():
    """Download a JSON backup: config + full library index."""
    import time as _time

    try:
        data = scan_library(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    payload = {
        "exported_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": os.sys.version.split()[0],
        "config": config.data,
        "library": data,
    }
    resp = jsonify(payload)
    resp.headers["Content-Disposition"] = "attachment; filename=music-high-res-backup.json"
    return resp


@app.post("/api/hooks/test")
def api_hooks_test():
    """Send a test payload to the configured scan hook / media server or the
    new-release notify webhook — so users can verify a URL without waiting for
    a real batch. Body: {"target": "scan" | "notify"}."""
    import json as _json
    import urllib.request as _urlreq
    import urllib.error as _urlerr

    target = str((request.get_json(silent=True) or {}).get("target") or "scan")
    if target == "notify":
        url = str(config.get("notify_url") or "").strip()
        payload = {"test": True, "title": "Music High Res — test notification", "releases": [], "message": "This is a test from Music High Res ✓"}
    else:
        preset = server_scan_request(config)
        if not preset:
            url = str(config.get("scan_hook_url") or "").strip()
            payload = {"test": True, "message": "Music High Res scan-hook test ✓"}
        else:
            method, url, headers, body = preset
            try:
                data = _json.dumps(body).encode() if method == "POST" else None
                req = _urlreq.Request(url, data=data, headers={k: v for k, v in headers.items() if v}, method=method)
                with _urlreq.urlopen(req, timeout=8):
                    return jsonify({"ok": True, "target": "server", "url": url})
            except Exception as e:
                return jsonify({"ok": False, "error": f"Server preset failed: {e}", "url": url}), 502
    if not url:
        return jsonify({"ok": False, "error": "No URL configured for that target."}), 400
    try:
        req = _urlreq.Request(url, data=_json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
        with _urlreq.urlopen(req, timeout=8):
            return jsonify({"ok": True, "target": target, "url": url})
    except _urlerr.HTTPError as e:
        return jsonify({"ok": False, "error": f"HTTP {e.code}: {e.reason}", "url": url}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "url": url}), 502

    preset = server_scan_request(config)
    if preset:
        method, url, headers, body = preset
        import urllib.request as _urlreq
        import json as _json
        try:
            data = _json.dumps(body).encode() if method == "POST" else None
            req = _urlreq.Request(url, data=data, headers={k: v for k, v in headers.items() if v}, method=method)
            with _urlreq.urlopen(req, timeout=8):
                pass
        except OSError as e:
            return jsonify({"ok": False, "error": f"Scan request failed: {e}"}), 502
        return jsonify({"ok": True, "server": config.get("server_type")})
    hook = str(config.get("scan_hook_url") or "").strip()
    if not hook:
        return jsonify({"ok": False, "error": "No media server configured (Settings → Media server) and no scan-hook URL."}), 400
    import urllib.request as _urlreq
    try:
        body = json.dumps({"event": "rescan", "source": "music-high-res"}).encode()
        req = _urlreq.Request(hook, data=body, headers={"Content-Type": "application/json", "User-Agent": "music-high-res"}, method="POST")
        with _urlreq.urlopen(req, timeout=8):
            pass
    except OSError as e:
        return jsonify({"ok": False, "error": f"Hook failed: {e}"}), 502
    return jsonify({"ok": True})


@app.get("/api/library/album/source")
def api_library_album_source():
    """Source URL of an album folder (from the SQLite ledger) — powers the
    'Open on Apple Music' link on album rows. Fetched lazily on click so a
    Library scan doesn't pay for 300 ledger queries."""
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= parameter."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    return jsonify({"ok": True, "url": album_source_url(str(target))})


@app.get("/api/library/album")
def api_library_album():
    """List the tracks of one album folder (with tags) — powers the in-app
    player and the tag editor."""
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= parameter."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.is_dir():
        return jsonify({"ok": False, "error": "That album folder no longer exists."}), 404
    return jsonify({"ok": True, "tracks": list_album_files(target)})


@app.get("/api/audio")
def api_audio():
    """Stream an audio file from the library (in-app player). Supports HTTP
    Range requests so seeking works. Only serves files under the output dir.

    With ?transcode=1 the file is transcoded to AAC on the fly (ffmpeg, ADTS
    stream — this ffmpeg build buffers fragmented mp4 to a pipe until close, so
    ADTS is what actually streams incrementally) for browsers that can't decode
    ALAC (Chrome/Firefox/Edge). ?t=seconds restarts from an offset (used for
    seeking on transcoded streams, where Range doesn't map to time)."""
    from flask import Response, send_file

    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= parameter."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.is_file():
        return jsonify({"ok": False, "error": "File not found."}), 404
    if request.args.get("transcode") == "1":
        seek = 0.0
        try:
            seek = max(0.0, float(request.args.get("t") or "0"))
        except (TypeError, ValueError):
            seek = 0.0
        gen = transcode_audio(target, seek)
        if gen is None:
            return jsonify({"ok": False, "error": "ffmpeg isn't available — can't transcode this file for your browser."}), 500
        resp = Response(gen, mimetype="audio/aac", direct_passthrough=True)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Disposition"] = 'inline; filename="stream.aac"'
        return resp
    return send_file(str(target), conditional=True)  # conditional → Range/seek support


@app.get("/api/art")
def api_art():
    """Embedded cover art of a library audio file (in-app player thumbnail +
    Media Session artwork). Path-locked to the output folder."""
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= parameter."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.is_file():
        return jsonify({"ok": False, "error": "File not found."}), 404
    art = read_cover_art(target)
    if not art:
        return jsonify({"ok": False, "error": "No embedded cover art."}), 404
    data, mime = art
    from flask import Response as _Resp

    resp = _Resp(data, mimetype=mime)
    # Short browser cache: tag edits change the file (mtime) and the server
    # cache is mtime-keyed, so stale art shouldn't outlive an edit for long.
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.get("/api/tags")
def api_tags_get():
    """Read the tags of one audio file."""
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= parameter."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.is_file():
        return jsonify({"ok": False, "error": "File not found."}), 404
    return jsonify({"ok": True, "path": str(target), "tags": read_audio_tags(target)})


@app.post("/api/tags")
def api_tags_write():
    """Update text tags on one audio file (title/artist/album/albumartist/track/date)."""
    body = request.get_json(silent=True) or {}
    raw = str(body.get("path") or "").strip()
    fields = {k: body[k] for k in ("title", "artist", "album", "albumartist", "track", "date") if k in body}
    if not raw:
        return jsonify({"ok": False, "error": "No file path given."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.is_file():
        return jsonify({"ok": False, "error": "File not found."}), 404
    ok, msg = write_audio_tags(target, fields)
    return jsonify({"ok": ok, "message": msg, "tags": read_audio_tags(target)}), (200 if ok else 400)


@app.get("/api/library/smart-duplicates")
def api_library_smart_duplicates():
    """Duplicates found by audio fingerprinting (same song, different file)."""
    limit = 300
    try:
        limit = int(request.args.get("limit", "300"))
    except ValueError:
        pass
    try:
        groups = find_smart_duplicates(expand_path(config.get("output_path")), limit=limit)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "groups": groups, "count": len(groups)})


@app.get("/api/library/format-duplicates")
def api_library_format_duplicates():
    """Tracks present in one album in more than one format (FLAC + ALAC…).
    Powers the Library's 🧹 Cleanup panel — per-file delete + keep-best."""
    try:
        groups = find_format_duplicates(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "groups": groups, "count": len(groups)})


@app.get("/api/library/cleanup")
def api_library_cleanup_preview():
    """Counts for the universal Cleanup buttons (delete all FLAC / ALAC /
    all-but-best) — lets the UI label each button before anything is moved."""
    try:
        preview = cleanup_preview(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "actions": preview})


@app.post("/api/library/cleanup")
def api_library_cleanup_run():
    """Run a universal cleanup: move every FLAC / every ALAC / all non-best
    copies to .trash (recoverable). Body: {action: 'flac'|'alac'|'best'}."""
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip().lower()
    if action not in ("flac", "alac", "best"):
        return jsonify({"ok": False, "error": "action must be 'flac', 'alac' or 'best'."}), 400
    try:
        result = cleanup_library_files(expand_path(config.get("output_path")), action)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "Cleanup failed.")}), 400
    return jsonify({"ok": True, **result})


@app.post("/api/library/delete")
def api_library_delete():
    """Move one or more library files into .trash (recoverable, never a
    permanent delete). Accepts {path} or {paths: [...]}."""
    body = request.get_json(silent=True) or {}
    paths = body.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        single = str(body.get("path") or "").strip()
        paths = [single] if single else []
    paths = [p for p in paths if isinstance(p, str) and p.strip()]
    if not paths:
        return jsonify({"ok": False, "error": "No file path given."}), 400
    output = expand_path(config.get("output_path"))
    results = []
    for p in paths[:100]:
        ok, msg = delete_library_file(output, p)
        results.append({"path": p, "ok": ok, "message": msg})
    deleted = sum(1 for r in results if r["ok"])
    if deleted == 0:
        return jsonify({"ok": False, "error": results[0]["message"], "results": results}), 400
    return jsonify({"ok": True, "deleted": deleted, "results": results})


@app.get("/api/library/trash")
def api_library_trash():
    """What's sitting in the output folder's .trash (recoverable deletes)."""
    try:
        return jsonify({"ok": True, **trash_info(expand_path(config.get("output_path")))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/library/trash/restore")
def api_library_trash_restore():
    """Move a trashed file back to its original location."""
    body = request.get_json(silent=True) or {}
    ok, msg = restore_trash_file(
        expand_path(config.get("output_path")), str(body.get("name") or "")
    )
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@app.post("/api/library/trash/empty")
def api_library_trash_empty():
    """Permanently empty .trash. The only irreversible action — the UI
    double-confirms before calling this."""
    ok, msg = empty_trash(expand_path(config.get("output_path")))
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@app.get("/api/library/ledger")
def api_library_ledger():
    """SQLite ledger stats: what's been downloaded, engine/codec split, and
    files recorded but no longer on disk (deleted or sitting in .trash)."""
    try:
        return jsonify({"ok": True, **ledger_stats(expand_path(config.get("output_path")))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/library/ledger/rebuild")
def api_library_ledger_rebuild():
    """Wipe and re-index the SQLite ledger from the library folder on disk
    (for libraries that predate the ledger, or after big manual changes)."""
    try:
        return jsonify({"ok": True, **ledger_rebuild(expand_path(config.get("output_path")))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ----------------------------------------------------------------------
# Background tasks (ReplayGain scan, lyrics backfill, cover upgrades) —
# long-running library jobs run in a thread; the UI polls /api/tasks/<id>
# ----------------------------------------------------------------------
_TASK_LOCK = threading.Lock()
_TASKS: dict[str, dict] = {}


def _start_task(name: str, fn, *args) -> str:
    """Run fn(*args) in a daemon thread and return a task id to poll.

    Finished tasks are kept so the UI can poll the result, but the registry is
    capped — oldest tasks are evicted so a long-running server doesn't grow
    it unboundedly."""
    tid = uuid.uuid4().hex[:10]
    with _TASK_LOCK:
        _TASKS[tid] = {"id": tid, "name": name, "status": "running", "started": time.time()}
        if len(_TASKS) > 30:
            for old in sorted(_TASKS, key=lambda t: _TASKS[t]["started"])[: len(_TASKS) - 30]:
                _TASKS.pop(old, None)

    def _run():
        try:
            result = fn(*args) or {}
            with _TASK_LOCK:
                _TASKS[tid] = {**_TASKS[tid], "status": "done", "result": result}
        except Exception as e:  # never strand a task in "running"
            logging.getLogger("app").exception("Task %s failed", name)
            with _TASK_LOCK:
                _TASKS[tid] = {**_TASKS[tid], "status": "failed", "error": str(e)}

    threading.Thread(target=_run, daemon=True).start()
    return tid


@app.get("/api/tasks/<task_id>")
def api_task(task_id: str):
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
    if task is None:
        return jsonify({"ok": False, "error": "Task not found."}), 404
    return jsonify({"ok": True, **task})


@app.post("/api/library/replaygain")
def api_library_replaygain():
    """Start a ReplayGain scan (EBU R128) over the whole library or a subset
    of album folders. Returns a task id — poll /api/tasks/<id>."""
    body = request.get_json(silent=True) or {}
    paths = body.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    output = expand_path(config.get("output_path"))
    tid = _start_task("ReplayGain scan", replaygain_scan, output, [p for p in paths if isinstance(p, str) and p.strip()] or None)
    return jsonify({"ok": True, "task": tid})


@app.post("/api/library/lyrics")
def api_library_lyrics():
    """Start an LRCLIB lyrics backfill over the library (writes .lrc sidecars
    for tracks that don't have one). Returns a task id."""
    output = expand_path(config.get("output_path"))
    tid = _start_task("Lyrics backfill", lrclib_backfill, output)
    return jsonify({"ok": True, "task": tid})


@app.post("/api/library/cover-upgrade")
def api_library_cover_upgrade():
    """Re-fetch one album's cover at high resolution from the iTunes catalog
    and re-embed it into every track in the folder. Returns a task id."""
    body = request.get_json(silent=True) or {}
    album_path = str(body.get("album_path") or "").strip()
    if not album_path:
        return jsonify({"ok": False, "error": "No album path given."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(album_path)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.is_dir():
        return jsonify({"ok": False, "error": "That album folder no longer exists."}), 404
    tid = _start_task("Cover upgrade", upgrade_album_cover, str(output), str(target),
                      1200, str(config.get("storefront") or "US"))
    return jsonify({"ok": True, "task": tid})


@app.get("/api/library/histogram")
def api_library_histogram():
    """Quality histogram across the whole library: codec / bit-depth /
    sample-rate distributions (uses the cached ffprobe results)."""
    try:
        hist = quality_histogram(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, **hist})


@app.get("/api/library/ledger/export")
def api_library_ledger_export():
    """Download the SQLite ledger as CSV or JSON."""
    fmt = str(request.args.get("fmt") or "csv").lower()
    if fmt not in ("csv", "json"):
        return jsonify({"ok": False, "error": "fmt must be 'csv' or 'json'."}), 400
    content, filename = ledger_export(expand_path(config.get("output_path")), fmt)
    from flask import Response as _Resp
    resp = _Resp(content, mimetype="text/csv" if fmt == "csv" else "application/json")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


@app.get("/api/stats/history")
def api_stats_history():
    """Download history from the SQLite ledger: totals per month/year and top
    artists by tracks downloaded."""
    try:
        return jsonify({"ok": True, **library_history(expand_path(config.get("output_path")))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/catalog/search")
def api_catalog_search():
    """Search the Apple Music catalog (iTunes Search API) for albums or
    songs — lets you find and queue links without leaving the app."""
    q = str(request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Missing ?q= search term."}), 400
    entity = str(request.args.get("entity") or "album").strip()
    results = catalog_search(q, entity, str(config.get("storefront") or "US"))
    _enrich_catalog_owned(results)
    return jsonify({"ok": True, "results": results, "count": len(results)})


@app.get("/api/catalog/artist")
def api_catalog_artist():
    """Albums by an Apple Music artist (iTunes Lookup, entity=album) — the
    second half of the in-app catalog search's artist entity."""
    artist_id = str(request.args.get("id") or "").strip()
    if not artist_id:
        return jsonify({"ok": False, "error": "Missing ?id= artist id."}), 400
    results = artist_discography(artist_id, str(config.get("storefront") or "US"))
    _enrich_catalog_owned(results)
    return jsonify({"ok": True, "results": results, "count": len(results)})


@app.get("/api/library/lossy-albums")
def api_library_lossy_albums():
    """Albums whose best file is lossy (AAC/MP3/OGG) — candidates for a
    lossless re-download. Each entry carries its ledger source URL so the UI
    can re-queue it at ALAC with overwrite."""
    output = expand_path(config.get("output_path"))
    try:
        return jsonify({"ok": True, "albums": lossy_albums(str(output))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _enrich_catalog_owned(results: list[dict]) -> None:
    """Stamp each catalog result with how many of its tracks the ledger owns
    (exact ownership: album → album title + artist, song → track + artist).
    The UI shows ✓ owned badges and offers 'Download missing'."""
    if not results:
        return
    output = str(expand_path(config.get("output_path")))
    for r in results:
        if r.get("kind") == "artist":
            r["owned"] = 0
            continue
        owned = ledger_owned_count(
            output, "song" if r.get("kind") == "song" else "album",
            r.get("name") or "", r.get("artist") or "",
        )
        r["owned"] = owned if owned is not None else 0


def _resolve_album_path(raw: str):
    """Resolve a user-supplied album path, refusing anything outside the output
    folder (same containment guard as /api/library/album/source). Returns the
    resolved path or None."""
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return None
    return target


@app.get("/api/library/m3u")
def api_library_m3u():
    """Download a .m3u playlist — whole library (?scope=library) or one album
    (?scope=album&path=...). Absolute paths, #EXTINF headers."""
    scope = str(request.args.get("scope") or "library")
    from flask import Response as _Resp
    from urllib.parse import quote as _quote
    try:
        if scope == "album":
            raw = str(request.args.get("path") or "").strip()
            if not raw:
                return jsonify({"ok": False, "error": "Missing ?path= album folder."}), 400
            target = _resolve_album_path(raw)
            if target is None:
                return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
            content = album_m3u(target)
            fname = target.name + ".m3u"
        else:
            output = expand_path(config.get("output_path"))
            content = export_library_m3u(str(output))
            fname = "Music High Res Library.m3u"
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    resp = _Resp(content, mimetype="audio/x-mpegurl")
    resp.headers["Content-Disposition"] = f"attachment; filename={_quote(fname)}"
    return resp


@app.get("/api/library/cue")
def api_library_cue():
    """Generate a .cue sheet for one album folder (from embedded tags)."""
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= album folder."}), 400
    target = _resolve_album_path(raw)
    if target is None:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    ok, msg, written = generate_cue_sheet(target)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg, "path": written})


@app.get("/api/library/empty-dirs")
def api_library_empty_dirs():
    """List folders with no files at all (stale after renames/cleanups)."""
    output = expand_path(config.get("output_path"))
    try:
        return jsonify({"ok": True, "dirs": find_empty_dirs(str(output))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/library/empty-dirs/delete")
def api_library_empty_dirs_delete():
    """Delete all empty folders (bottom-up, files-only check)."""
    output = expand_path(config.get("output_path"))
    try:
        removed = delete_empty_dirs(str(output))
        return jsonify({"ok": True, "removed": removed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/library/restore")
def api_library_restore():
    """Restore settings from a backup JSON ({config: {...}}). Only known keys
    are applied; the library index part is informational and ignored."""
    body = request.get_json(silent=True) or {}
    incoming = body.get("config")
    if not isinstance(incoming, dict) or not incoming:
        return jsonify({"ok": False, "error": "Backup has no config object."}), 400
    # Normalize exactly like the Settings save route (same type coercion) so a
    # malformed backup can't write garbage into config.json, and batch it into
    # a single file write. The access token is never restored.
    normalized = {}
    skipped: list[str] = []
    for k, v in incoming.items():
        if k not in config.data or k == "remote_token":
            continue
        nv = _normalize_config_value(k, v)
        if nv is None:
            skipped.append(k)
            continue
        # Guard the output folder: restoring a backup made on another machine
        # (or with a deleted/stale path) would silently empty the Library view
        # and point every download at a dead folder. Accept the path only when
        # it already exists or its parent does (so it can be created).
        if k == "output_path":
            try:
                probe = Path(os.path.expanduser(str(nv))).resolve()
            except OSError:
                skipped.append(k)
                continue
            # Must already be a folder, or sit under a real folder so it can
            # be created (reject /etc/passwd/foo-style paths where the
            # "parent" is a file).
            if probe.is_dir() or probe.parent.is_dir():
                pass
            else:
                skipped.append(k)
                continue
        normalized[k] = nv
    changes = config.update(normalized)
    _restart_watcher_if_needed()
    resp = {"ok": True, "applied": list(changes.keys()), "count": len(changes)}
    if skipped:
        resp["skipped"] = skipped
    return jsonify(resp)


@app.post("/api/library/musicbrainz")
def api_library_musicbrainz():
    """Auto-fix tags from MusicBrainz as a background task — one album
    ({path}) or the whole library (scope=library). MusicBrainz enforces a
    1 req/s rate limit, handled server-side; a full-library run takes a while.
    """
    body = request.get_json(silent=True) or {}
    scope = str(body.get("scope") or "album")
    output = expand_path(config.get("output_path"))
    if scope == "library":
        tid = _start_task("musicbrainz", musicbrainz_scan, str(output))
        return jsonify({"ok": True, "task": tid})
    raw = str(body.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing path (or scope=library)."}), 400
    target = _resolve_album_path(raw)
    if target is None:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    tid = _start_task("musicbrainz", musicbrainz_fix_album, target)
    return jsonify({"ok": True, "task": tid})


@app.get("/api/library/tracks")
def api_library_tracks():
    """Track-level search: ?q= matches titles/file names (not just
    artist/album). Returns flat matches with album/artist paths."""
    q = str(request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Missing ?q= search term."}), 400
    try:
        hits = search_tracks(expand_path(config.get("output_path")), q)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "tracks": hits, "count": len(hits)})


@app.post("/api/library/convert")
def api_library_convert():
    """Convert every audio file in one album folder to flac/mp3/opus/aac/ogg
    as a background task (originals kept). Body: {path, codec, overwrite}."""
    body = request.get_json(silent=True) or {}
    raw = str(body.get("path") or "").strip()
    codec = str(body.get("codec") or "flac").strip().lower()
    if not raw:
        return jsonify({"ok": False, "error": "Missing album path."}), 400
    target = _resolve_album_path(raw)
    if target is None:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.is_dir():
        return jsonify({"ok": False, "error": "That album folder no longer exists."}), 404
    tid = _start_task("convert", convert_album_to_format, target,
                      codec, bool(body.get("overwrite")))
    return jsonify({"ok": True, "task": tid, "codec": codec})


@app.get("/api/lyrics")
def api_lyrics():
    """The .lrc lyrics sidecar of a library audio file, if present."""
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= parameter."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    text = read_lyrics_sidecar(target)
    if text is None:
        return jsonify({"ok": False, "error": "No .lrc lyrics next to that file."}), 404
    return jsonify({"ok": True, "lyrics": text})


@app.get("/api/library/album/cover")
def api_library_album_cover():
    """Current embedded cover of an album (first audio file) — for the cover
    viewer modal."""
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= parameter."}), 400
    target = _resolve_album_path(raw)
    if target is None or not target.is_dir():
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    files = list_album_files(target)
    if not files:
        return jsonify({"ok": False, "error": "No audio files in that folder."}), 404
    # Some tracks in an album may lack embedded art — try them all before
    # giving up, so a cover is shown whenever any track has one.
    art = None
    for f in files:
        art = read_cover_art(Path(f["path"]))
        if art:
            break
    if not art:
        return jsonify({"ok": False, "error": "No embedded cover art."}), 404
    data, mime = art
    from flask import Response as _Resp
    resp = _Resp(data, mimetype=mime)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.post("/api/library/album/cover")
def api_library_album_cover_set():
    """Replace the cover of an album with an uploaded image. Body: {path,
    data: base64, mime}. Written into every audio file in the folder."""
    import base64 as _b64
    body = request.get_json(silent=True) or {}
    raw = str(body.get("path") or "").strip()
    b64 = str(body.get("data") or "").strip()
    if not raw or not b64:
        return jsonify({"ok": False, "error": "Missing album path or image data."}), 400
    target = _resolve_album_path(raw)
    if target is None or not target.is_dir():
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    try:
        data = _b64.b64decode(b64, validate=True)
    except Exception:
        return jsonify({"ok": False, "error": "Image data is not valid base64."}), 400
    if len(data) > 15_000_000:
        return jsonify({"ok": False, "error": "Image too large (max 15 MB)."}), 400
    mime = str(body.get("mime") or "image/jpeg").strip()
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    result = write_album_cover(target, data, mime)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "Cover write failed.")}), 400
    return jsonify({"ok": True, **result})


@app.post("/api/ledger/play")
def api_ledger_play():
    """Record a play (play_count + last_played) for one library file."""
    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "Missing file path."}), 400
    ok = ledger_play(expand_path(config.get("output_path")), path)
    return jsonify({"ok": ok}), (200 if ok else 400)


@app.get("/api/library/recent")
def api_library_recent():
    """Most recently played tracks (from the ledger's play tracking)."""
    try:
        recent = ledger_recent_plays(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "recent": recent})


@app.get("/api/library/most-played")
def api_library_most_played():
    """Most-played tracks (play_count DESC) from the ledger."""
    try:
        most = ledger_most_played(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "most_played": most})


@app.post("/api/library/plays/clear")
def api_library_plays_clear():
    """Reset every play count in the ledger (play tracking)."""
    try:
        cleared = ledger_clear_plays()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "cleared": cleared})


@app.get("/api/library/album/duration")
def api_library_album_duration():
    """Total play time of an album (sum of cached ffprobe durations)."""
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing ?path= parameter."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.is_dir():
        return jsonify({"ok": False, "error": "That album folder no longer exists."}), 404
    return jsonify({"ok": True, "path": str(target), "duration": album_duration(target)})


@app.post("/api/library/album/cover/save")
def api_library_album_cover_save():
    """Write the album's embedded cover art to cover.jpg/png in its folder."""
    body = request.get_json(silent=True) or {}
    raw = str(body.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "Missing album path."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    ok, msg = save_album_cover(target)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "path": msg})


@app.get("/api/library/index")
def api_library_index():
    """Export a full library track index as CSV (?fmt=csv) or an HTML page
    (?fmt=html) — for spreadsheets, archiving, or sharing."""
    fmt = str(request.args.get("fmt") or "csv").lower()
    if fmt not in ("csv", "html"):
        return jsonify({"ok": False, "error": "fmt must be 'csv' or 'html'."}), 400
    from flask import Response as _Resp
    from urllib.parse import quote as _quote
    try:
        content, fname = export_library_index(expand_path(config.get("output_path")), fmt)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    resp = _Resp(content, mimetype="text/html; charset=utf-8" if fmt == "html" else "text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f"attachment; filename={_quote(fname)}"
    return resp


@app.post("/api/library/m3u-import")
def api_library_m3u_import():
    """Import an .m3u playlist file: entries are resolved against the output
    folder, only existing library tracks are kept, and the playlist is saved
    to Playlists/{artist}/{name}.m3u. Body: {name, content, artist}."""
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    content = str(body.get("content") or "")
    if not content.strip():
        return jsonify({"ok": False, "error": "Empty .m3u content."}), 400
    result = import_m3u_playlist(expand_path(config.get("output_path")),
                                 name, content, str(body.get("artist") or "Imported"))
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "Import failed.")}), 400
    return jsonify({"ok": True, **result})


@app.post("/api/jobs/pause")
def api_jobs_pause():
    manager.pause()
    return jsonify({"ok": True, "paused": True})


@app.post("/api/jobs/resume")
def api_jobs_resume():
    manager.resume()
    return jsonify({"ok": True, "paused": False})


@app.post("/api/jobs/cancel-all")
def api_jobs_cancel_all():
    n = manager.cancel_all()
    return jsonify({"ok": True, "cancelled": n})


# ----------------------------------------------------------------------
# Smart playlists (saved filters) + wishlist (saved links)
# ----------------------------------------------------------------------
@app.get("/api/smart-playlists")
def api_smart_playlists():
    return jsonify({"ok": True, "playlists": config.get("smart_playlists") or []})


@app.post("/api/smart-playlists")
def api_smart_playlist_save():
    """Save (or update by name) a smart-playlist filter."""
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Give the playlist a name."}), 400
    try:
        recent_days = max(0, int(body.get("recent_days") or 0))
    except (TypeError, ValueError):
        recent_days = 0
    try:
        min_tracks = max(0, int(body.get("min_tracks") or 0))
    except (TypeError, ValueError):
        min_tracks = 0
    pl = {
        "name": name,
        "artist": str(body.get("artist") or "").strip(),
        "album": str(body.get("album") or "").strip(),
        "quality": str(body.get("quality") or "").strip(),
        "years": str(body.get("years") or "").strip(),
        "recent_days": recent_days,
        "min_tracks": min_tracks,
    }
    playlists = [p for p in (config.get("smart_playlists") or []) if not isinstance(p, dict) or p.get("name") != name]
    playlists.append(pl)
    config.set("smart_playlists", playlists)
    return jsonify({"ok": True, "playlists": playlists})


@app.post("/api/smart-playlists/preview")
def api_smart_playlist_preview():
    """Evaluate a filter (saved or draft) and return matching albums/tracks."""
    body = request.get_json(silent=True) or {}
    pl = body.get("playlist") or body
    if not isinstance(pl, dict):
        return jsonify({"ok": False, "error": "No playlist filter given."}), 400
    result = smart_playlist_matches(expand_path(config.get("output_path")), pl)
    return jsonify({"ok": True, **result})


@app.post("/api/smart-playlists/<name>/export")
def api_smart_playlist_export(name: str):
    """Export a saved filter to Playlists/Smart/{name}.m3u."""
    playlists = config.get("smart_playlists") or []
    pl = next((p for p in playlists if isinstance(p, dict) and p.get("name") == name), None)
    if not pl:
        return jsonify({"ok": False, "error": "No such smart playlist."}), 404
    ok, msg, path = export_smart_playlist(expand_path(config.get("output_path")), pl)
    return jsonify({"ok": ok, "message": msg, "path": path}), (200 if ok else 400)


@app.delete("/api/smart-playlists/<name>")
def api_smart_playlist_delete(name: str):
    playlists = [p for p in (config.get("smart_playlists") or []) if not (isinstance(p, dict) and p.get("name") == name)]
    config.set("smart_playlists", playlists)
    return jsonify({"ok": True, "playlists": playlists})


@app.get("/api/wishlist")
def api_wishlist():
    return jsonify({"ok": True, "wishlist": config.get("wishlist") or []})


@app.post("/api/wishlist")
def api_wishlist_add():
    """Save a link to the wishlist (download later, one click)."""
    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "No URL given."}), 400
    wish = [w for w in (config.get("wishlist") or []) if isinstance(w, dict) and w.get("url") != url]
    wish.insert(0, {"url": url, "title": str(body.get("title") or url)[:200], "added": time.time()})
    config.set("wishlist", wish)
    return jsonify({"ok": True, "wishlist": wish})


@app.delete("/api/wishlist/<int:index>")
def api_wishlist_remove(index: int):
    wish = [w for w in (config.get("wishlist") or []) if isinstance(w, dict)]
    if 0 <= index < len(wish):
        wish.pop(index)
    config.set("wishlist", wish)
    return jsonify({"ok": True, "wishlist": wish})


@app.post("/api/wishlist/download")
def api_wishlist_download():
    """Queue every URL currently on the wishlist as one download job."""
    wish = [w.get("url") for w in (config.get("wishlist") or []) if isinstance(w, dict) and w.get("url")]
    if not wish:
        return jsonify({"ok": False, "error": "Your wishlist is empty."}), 400
    job = manager.start(wish, {})
    return jsonify({"ok": True, "job": job.summary(), "queued": len(wish)})


@app.post("/api/tags/bulk")
def api_tags_bulk():
    """Apply the same tag fields to many files at once (bulk tag editor)."""
    body = request.get_json(silent=True) or {}
    paths = body.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    paths = [p for p in paths if isinstance(p, str) and p.strip()][:200]
    fields = {k: body[k] for k in ("title", "artist", "album", "albumartist", "track", "date") if k in body}
    if not paths:
        return jsonify({"ok": False, "error": "No file paths given."}), 400
    if not fields:
        return jsonify({"ok": False, "error": "No tag fields to set."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    updated, failed, errors = 0, 0, []
    for p in paths:
        target = Path(os.path.expanduser(p)).resolve()
        try:
            target.relative_to(output)
        except ValueError:
            failed += 1
            errors.append(f"{target.name}: outside output folder")
            continue
        if not target.is_file():
            failed += 1
            errors.append(f"{target.name}: not found")
            continue
        ok, msg = write_audio_tags(target, fields)
        if ok:
            updated += 1
        else:
            failed += 1
            errors.append(f"{target.name}: {msg}")
    return jsonify({"ok": True, "updated": updated, "failed": failed, "errors": errors[:20]})


# ----------------------------------------------------------------------
# Settings presets — save/apply/delete named bundles of settings
# ----------------------------------------------------------------------
_PRESET_KEYS = [
    "output_path", "song_codec_priority", "synced_lyrics_format", "cover_size",
    "artist_auto_select", "convert_to_flac", "album_folder_template",
    "playlist_folder_template", "music_video_resolution", "music_video_codec_priority",
    "cover_format", "use_album_date", "ytm_itag", "spotify_audio_quality",
    "file_name_template", "compilation_folder_template", "exclude_tags",
    "date_tag_template", "mv_remux_format", "max_concurrent", "auto_retry",
    "skip_owned", "delta_sync", "desktop_notify",
]


@app.post("/api/presets")
def api_presets():
    """Manage named settings presets. Body: {action: save|apply|delete, name}."""
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip()
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Give the preset a name."}), 400
    presets = {str(k): v for k, v in (config.get("settings_presets") or {}).items() if isinstance(v, dict)}
    if action == "save":
        presets[name] = {k: config.data.get(k) for k in _PRESET_KEYS if k in config.data}
        config.set("settings_presets", presets)
        return jsonify({"ok": True, "presets": presets})
    if action == "apply":
        preset = presets.get(name)
        if not preset:
            return jsonify({"ok": False, "error": "No such preset."}), 404
        config.update({k: v for k, v in preset.items() if k in config.data})
        _restart_watcher_if_needed()
        return jsonify({"ok": True, "applied": preset, "config": config.data})
    if action == "delete":
        presets.pop(name, None)
        config.set("settings_presets", presets)
        return jsonify({"ok": True, "presets": presets})
    return jsonify({"ok": False, "error": "action must be save, apply or delete."}), 400


@app.get("/api/stats")
def api_stats():
    """Library dashboard stats: totals, codec split, top artists."""
    try:
        stats = library_stats(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, **stats})


@app.post("/api/releases/ignore")
def api_releases_ignore():
    """Hide an artist (or all releases of one name) from the Releases panel.
    Body: {"artist": "…", "action": "add" | "remove"}."""
    body = request.get_json(silent=True) or {}
    artist = str(body.get("artist") or "").strip()
    action = str(body.get("action") or "add")
    if not artist:
        return jsonify({"ok": False, "error": "No artist given."}), 400
    ignored = [str(a) for a in (config.get("releases_ignored") or []) if str(a).strip()]
    lowered = {a.lower() for a in ignored}
    if action == "remove":
        ignored = [a for a in ignored if a.lower() != artist.lower()]
    else:
        if artist.lower() not in lowered:
            ignored.append(artist)
    config.set("releases_ignored", ignored)
    return jsonify({"ok": True, "ignored": ignored})

    """Import tracks from an Apple Music/iTunes library.xml and match them on
    the Apple Music catalog (optionally restricted to one playlist)."""
    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip()
    playlist = str(body.get("playlist") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "Give the path to your library.xml."}), 400
    target = Path(os.path.expanduser(path)).resolve()
    if not target.is_file():
        return jsonify({"ok": False, "error": f"File not found: {target}"}), 404
    try:
        summary = migrate.parse_library_xml(str(target))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if body.get("preview_only"):
        return jsonify({"ok": True, **summary})
    try:
        limit = 150
        try:
            limit = min(1000, max(1, int(body.get("limit") or 150)))
        except (TypeError, ValueError):
            pass
        tracks, truncated = migrate.import_library_tracks(
            str(target), playlist=playlist,
            country=str(config.get("storefront") or "US"), limit=limit,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Import failed: {e}"}), 502
    ok = sum(1 for t in tracks if t.get("match"))
    return jsonify({
        "ok": True,
        "title": playlist or "Full library",
        "total": len(tracks),
        "matched": ok,
        "unmatched": len(tracks) - ok,
        "truncated": truncated,
        "tracks": tracks,
    })


@app.post("/api/notify/releases")
def api_notify_releases():
    """Push the current new-releases list to the configured webhook (ntfy,
    Pushover bridge, generic). Returns how many were sent."""
    hook = str(config.get("notify_url") or "").strip()
    if not hook:
        return jsonify({"ok": False, "error": "No notify URL configured (Settings → Notify URL)."}), 400
    # Reuse the same cached endpoint logic without double-fetching: hit the
    # in-process cache by calling the view function's data manually.
    import time as _time

    cache = api_new_releases.__dict__
    if not cache.get("_at"):
        # Cold cache: fetching now would run up to 12 artist lookups and hang
        # the button ~30s. Ask the user to open Releases once first.
        return jsonify({"ok": False, "error": "Releases haven't been loaded yet — open ✨ Releases once, then hit Notify again."}), 409
    data = cache["_data"]
    releases = data.get("releases", [])[:10]
    if not releases:
        return jsonify({"ok": False, "error": "No new releases found to notify about."}), 404
    lines = [f"• {r['artist']} — {r['name']} ({r.get('release_date', '')})" for r in releases]
    migrate.notify_webhook(hook, f"{len(releases)} new release(s)", "\n".join(lines))
    return jsonify({"ok": True, "sent": len(releases)})


@app.get("/api/preview-url")
def api_preview_url():
    """Resolve an Apple Music link to a 30s preview URL (in-app playback)."""
    url = str(request.args.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Missing ?url= parameter."}), 400
    try:
        result = migrate.apple_preview_url(url, country=str(config.get("storefront") or "US"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not find a preview: {e}"}), 502
    if not result:
        return jsonify({"ok": False, "error": "No 30-second preview available for that link."}), 404
    return jsonify({"ok": True, **result})


@app.get("/api/new-releases")
def api_new_releases():
    """Recent releases (≤90 days) from artists already in your Library.
    Cached 6h; capped at 12 artists to stay polite to the API."""
    import threading as _th
    import time as _time

    cache = api_new_releases.__dict__
    now = _time.time()
    if cache.get("_at") and now - cache["_at"] < 6 * 3600:
        return jsonify({"ok": True, **cache["_data"]})

    try:
        lib = scan_library(expand_path(config.get("output_path")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    ignored = {str(a).strip().lower() for a in (config.get("releases_ignored") or []) if str(a).strip()}
    artists = [a["name"] for a in lib.get("artists", [])
               if a["name"].strip().lower() not in ignored][:12]
    releases = []
    for artist in artists:
        try:
            releases.extend(migrate.recent_albums(artist, country=str(config.get("storefront") or "US")))
        except Exception:
            continue
    releases.sort(key=lambda r: r.get("release_date") or "", reverse=True)
    data = {"releases": releases[:40], "checked_at": now}
    cache["_at"] = now
    cache["_data"] = data
    return jsonify({"ok": True, **data})


@app.post("/api/library/open")
def api_library_open():
    """Reveal a library path in the OS file manager (Finder / Explorer). Path
    must live under the output folder (loopback-only app, but no reason to let
    a URL open arbitrary paths)."""
    import subprocess

    body = request.get_json(silent=True) or {}
    raw = str(body.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "No path given."}), 400
    output = Path(expand_path(config.get("output_path"))).resolve()
    target = Path(os.path.expanduser(raw)).resolve()
    try:
        target.relative_to(output)
    except ValueError:
        return jsonify({"ok": False, "error": "Path is outside the output folder."}), 403
    if not target.exists():
        return jsonify({"ok": False, "error": "That path no longer exists."}), 404
    try:
        if os.name == "nt":
            # Windows Explorer: /select, reveals the item; folders just open.
            if target.is_dir():
                subprocess.Popen(["explorer", str(target)])
            else:
                subprocess.Popen(["explorer", "/select,", str(target)])
        elif sys.platform == "darwin":
            # macOS Finder: -R reveals the item in a window; folders plain `open`.
            if target.is_dir():
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["open", "-R", str(target)])
        else:
            # Linux: xdg-open opens the folder in the default file manager.
            # (No reveal-and-select equivalent; opening the folder is enough.)
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as e:
        hint = " (install xdg-utils — e.g. apt install xdg-utils)" if sys.platform.startswith("linux") else ""
        return jsonify({"ok": False, "error": f"Could not open the file manager: {e}{hint}"}), 500
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Downloads
# ----------------------------------------------------------------------
@app.post("/api/download")
def api_download():
    body = request.get_json(silent=True) or {}
    urls = body.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
    if not urls:
        return jsonify({"ok": False, "error": "Paste at least one link (Apple Music, Spotify or YouTube Music)."}), 400

    if len(urls) > 200:
        return jsonify({"ok": False, "error": "Too many URLs (max 200 per batch)."}), 400

    options = {
        k: body[k]
        for k in ("codec", "output_path", "cookies_path", "use_wrapper", "wrapper_url", "overwrite")
        if k in body
    }
    # Dolby Atmos (and pure ALAC) can ONLY come from the wrapper — there is no
    # lossy fallback gamdl can use. Force the wrapper flag on so picking Atmos
    # always routes through it; run_job then preflights reachability/auth and
    # fails fast with a clear message instead of a cryptic gamdl license error.
    codec_choice = str(options.get("codec") or config.get("song_codec_priority") or "")
    if codec_choice == "atmos" or codec_choice == "alac":
        options["use_wrapper"] = True
    # Ledger-driven delta sync: for Spotify/YouTube album+playlist links, drop
    # the tracks the SQLite ledger already owns before anything is queued. The
    # skipped count rides along so the UI can report it. Apple links pass
    # through (gamdl skips existing files natively = Apple delta).
    delta = body.get("delta")
    if delta is None:
        delta = bool(config.get("delta_sync"))
    # Overwrite jobs mean "re-download regardless" — delta filtering would
    # drop already-owned tracks and make the lossy→lossless upgrade a silent
    # no-op (the lossy files are in the ledger, so nothing survives the filter).
    if bool(options.get("overwrite")):
        delta = False
    skipped_tracks = 0
    if delta:
        try:
            urls, skipped_tracks = delta_filter_urls(config, urls)
        except Exception as e:  # never block a download on delta hiccups
            logging.getLogger("app").warning("Delta sync failed: %s", e)
        if not urls:
            return jsonify({
                "ok": True,
                "message": "Everything on those links is already in your library — nothing to download.",
                "delta_skipped": skipped_tracks,
                "job": None,
            })
    job = manager.start(urls, options)
    return jsonify({"ok": True, "job": job.summary(), "delta_skipped": skipped_tracks})


# ----------------------------------------------------------------------
# Wrapper status + login (mode-aware: wrapper-v2 for gamdl, itouakirai for
# amdl — see Settings → Apple engine)
# ----------------------------------------------------------------------
def _amdl_mode() -> bool:
    return str(config.get("apple_engine") or "gamdl") == "amdl"


@app.get("/api/wrapper")
def api_wrapper():
    if _amdl_mode():
        data = wrapperctl.amdl_wrapper_status()
        data["logs"] = wrapperctl.amdl_wrapper_logs()
        return jsonify(data)
    data = wrapperctl.wrapper_status()
    data["logs"] = wrapperctl.wrapper_logs()
    return jsonify(data)


@app.post("/api/wrapper/2fa")
def api_wrapper_2fa():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip()
    if _amdl_mode():
        result = wrapperctl.amdl_submit_2fa(code)
    else:
        result = wrapperctl.submit_2fa(code)
    return jsonify(result)


@app.post("/api/wrapper/restart")
def api_wrapper_restart():
    if _amdl_mode():
        return jsonify(wrapperctl.amdl_restart_login())
    return jsonify(wrapperctl.restart_login())


@app.post("/api/wrapper/amdl/start")
def api_wrapper_amdl_start():
    # Starting the amdl wrapper stops wrapper-v2 (port 10020 clash) — refuse
    # if a gamdl ALAC/Atmos job is mid-flight, or we'd kill it silently.
    if manager.any_active():
        return jsonify({"ok": False, "error": "Downloads are still running — wait for them to finish before switching wrappers."}), 409
    return jsonify(wrapperctl.amdl_wrapper_start())


@app.post("/api/wrapper/amdl/stop")
def api_wrapper_amdl_stop():
    return jsonify(wrapperctl.amdl_wrapper_stop())


SETUP_MANAGER = wrapperctl.SetupManager()


@app.get("/api/wrapper/setup")
def api_wrapper_setup():
    """Setup-wizard state + environment facts (docker, arch, wrapper present)."""
    data = SETUP_MANAGER.status()
    data["docker"] = wrapperctl.docker_status()
    data["machine"] = wrapperctl.machine_info()
    data["wrapper_present"] = wrapperctl.wrapper_present()
    return jsonify({"ok": True, **data})


@app.post("/api/wrapper/setup")
def api_wrapper_setup_start():
    """Start the guided wrapper setup in a background thread."""
    body = request.get_json(silent=True) or {}
    apk = str(body.get("apk") or "").strip()
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    apply_fix = bool(body.get("apply_fix"))
    if not apk:
        return jsonify({"ok": False, "error": "Enter the APK path or URL."}), 400
    if SETUP_MANAGER.is_running():
        return jsonify({"ok": False, "error": "Setup is already running."}), 409
    try:
        SETUP_MANAGER.start(apk, email, password, apply_fix)
    except wrapperctl.SetupError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, **SETUP_MANAGER.status()})


@app.post("/api/wrapper/login")
def api_wrapper_login():
    """Save Apple ID credentials and restart the wrapper login (no Terminal)."""
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    if not email or not password:
        return jsonify({"ok": False, "error": "Enter your Apple ID email and password."}), 400
    if _amdl_mode():
        result = wrapperctl.amdl_login(email, password)
    else:
        result = wrapperctl.save_credentials(email, password)
    return jsonify(result)


@app.get("/api/url-preview")
def api_url_preview():
    """Peek a download link: what it is + track count, for the pre-download
    chips. Apple Music → SEO JSON-LD; Spotify → embed page; YouTube Music →
    yt-dlp flat metadata. Ownership hints only make sense for Apple links."""
    url = str(request.args.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Missing ?url= parameter."}), 400
    try:
        engine = url_engine(url)
        if engine == "spotify":
            parsed = migrate.parse_url(url)
            if not parsed or parsed.get("source") != "spotify":
                return jsonify({"ok": False, "error": "That doesn't look like a Spotify link."}), 400
            title, tracks = migrate.resolve_spotify(parsed["kind"], parsed["id"])
            owned = sum(1 for t in tracks
                        if ledger_track_owned(expand_path(config.get("output_path")),
                                              t.get("title", ""), t.get("artist", "")))
            return jsonify({
                "ok": True, "source": "spotify", "kind": parsed["kind"],
                "title": title, "track_count": len(tracks),
                "owned": owned,
            })
        if engine == "youtube":
            title, tracks = migrate.resolve_youtube(url)
            kind = "playlist" if len(tracks) > 1 else "song"
            owned = sum(1 for t in tracks
                        if ledger_track_owned(expand_path(config.get("output_path")),
                                              t.get("title", ""), t.get("artist", "")))
            return jsonify({
                "ok": True, "source": "youtube", "kind": kind,
                "title": title, "track_count": len(tracks),
                "owned": owned,
            })
        result = migrate.apple_preview(url)
        if result.get("ok"):
            result["owned"] = owned_info(expand_path(config.get("output_path")), result)
        return jsonify(result)
    except Exception as e:  # network / parse hiccups
        return jsonify({"ok": False, "error": f"Could not preview that link: {e}"}), 502
def api_migrate_preview():
    """Resolve a Spotify / YouTube Music link and match its tracks on Apple Music."""
    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Paste a Spotify or YouTube Music link."}), 400
    try:
        result = migrate.preview(url, country=str(config.get("storefront") or "US"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:  # network / upstream hiccups
        return jsonify({"ok": False, "error": f"Could not read that link: {e}"}), 502
    return jsonify({"ok": True, **result})


@app.post("/api/convert")
def api_convert():
    """Start an ALAC→FLAC conversion job over a folder."""
    body = request.get_json(silent=True) or {}
    source_dir = str(body.get("source_dir") or config.get("output_path")).strip()
    if not source_dir:
        return jsonify({"ok": False, "error": "No folder to convert."}), 400
    overwrite = bool(body.get("overwrite"))
    job = manager.start_convert(source_dir, overwrite=overwrite)
    return jsonify({"ok": True, "job": job.summary()})


@app.get("/api/jobs")
def api_jobs():
    return jsonify({"jobs": manager.list(), "paused": manager.paused})


@app.post("/api/jobs/reorder")
def api_jobs_reorder():
    """Move a queued job up/down in the queue (delta: -1 up, +1 down)."""
    body = request.get_json(silent=True) or {}
    job_id = str(body.get("job_id") or "").strip()
    try:
        delta = int(body.get("delta", 0))
    except (TypeError, ValueError):
        delta = 0
    if not job_id or delta not in (-1, 1):
        return jsonify({"ok": False, "error": "job_id and delta (-1|1) are required."}), 400
    ok, msg = manager.reorder(job_id, delta)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg})



@app.get("/api/jobs/<job_id>")
def api_job_detail(job_id: str):
    job = manager.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Job not found."}), 404
    return jsonify(job.detail())


@app.post("/api/jobs/<job_id>/retry")
def api_job_retry(job_id: str):
    """Re-queue a finished job with the same URLs and options."""
    job = manager.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Job not found."}), 404
    if job.status in ("running", "queued"):
        return jsonify({"ok": False, "error": "Job is still running."}), 409
    urls = [u for u in job.urls if u]
    if not urls:
        return jsonify({"ok": False, "error": "Nothing to retry (no URLs)."}), 400
    if job.kind != "download":
        return jsonify({"ok": False, "error": "Only download jobs can be retried."}), 400
    # Preserve the original job's options (wrapper mode, cookies, codec, …) so
    # an ALAC retry doesn't silently fall back to cookies/AAC.
    options = {
        k: v for k, v in job.options.items()
        if k in ("codec", "output_path", "cookies_path", "use_wrapper", "wrapper_url", "overwrite")
    }
    new_job = manager.start(urls, options)
    new_job.add_line(f"Retry of {job.id} — queued with same {len(urls)} URL(s).")
    return jsonify({"ok": True, "job": new_job.summary()})


@app.post("/api/jobs/<job_id>/cancel")
def api_job_cancel(job_id: str):
    job = manager.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Job not found."}), 404
    if job.status in ("running", "queued"):
        job.add_line("Cancelled by user.")
        job.cancel()
        job.set_status("cancelled")
    return jsonify({"ok": True, "job": job.summary()})


@app.delete("/api/jobs")
def api_clear_jobs():
    """Remove finished jobs from the list."""
    manager.clear_finished()
    return jsonify({"ok": True})


@app.get("/api/logs")
def api_logs():
    """Return the tail of a project-local log file for the in-app viewer.

    Only the two known files are served (loopback-only anyway): app.log
    (rotating server log) and launcher.log (startup output).
    """
    name = str(request.args.get("file", "app"))
    if name not in ("app", "launcher"):
        return jsonify({"ok": False, "error": "unknown log file"}), 400
    path = LOG_DIR / f"{name}.log"
    lines: list[str] = []
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            lines = raw.splitlines()[-200:]
        except OSError:
            pass
    return jsonify({"ok": True, "file": name, "lines": lines})


@app.get("/api/logs/download")
def api_logs_download():
    """Download a full project log file as an attachment (no tailing)."""
    name = str(request.args.get("file", "app"))
    if name not in ("app", "launcher"):
        return jsonify({"ok": False, "error": "unknown log file"}), 400
    path = LOG_DIR / f"{name}.log"
    if not path.exists():
        return jsonify({"ok": False, "error": "log file not found"}), 404
    return send_file(path, as_attachment=True, download_name=f"{name}.log", mimetype="text/plain")


@app.post("/api/config/reset")
def api_config_reset():
    """Reset every setting to its default (output folder + cookies preserved
    so the app stays usable; everything else is restored to stock)."""
    defaults = dict(DEFAULT_CONFIG)
    # Machine-specific setup facts survive the reset — output folder, cookie
    # files, the wrapper URL and the wrapper ON/OFF state (otherwise ALAC/
    # Atmos downloads would silently start failing after a reset), the chosen
    # Apple engine, and the persisted releases ignore list + accent colour.
    for keep in ("output_path", "cookies_path", "spotify_cookies_path",
                 "ytm_cookies_path", "wrapper_url", "use_wrapper",
                 "apple_engine", "releases_ignored", "accent_color"):
        defaults[keep] = config.get(keep)
    config.data = defaults
    config.save()
    _restart_watcher_if_needed()
    return jsonify({"ok": True, "config": config.data})



# ---------------------------------------------------------------------------
# In-app self-updater — lets existing installs move to a new release without
# re-downloading: checks GitHub for the latest release, then on Apply downloads
# the source tarball, backs up user state (config.json, data/, cookies, caches),
# swaps the app files in place, restores the backup, and restarts the server.
#
# Source installs only: packaged binaries (PyInstaller .app/.exe) can't rewrite
# themselves, so for those the UI links to the release page instead.
# ---------------------------------------------------------------------------
_UPDATE_CACHE: dict = {"at": 0.0, "data": None}  # (checked_at, release json)
_UPDATE_STATE: dict = {"running": False, "phase": "", "lines": [], "error": None, "done": False}


def _version_tuple(v: str) -> tuple:
    return (tuple(int(x) for x in _re.findall(r"\d+", v or "")[:3]) + (0, 0, 0))[:3]


def _github_latest_release(force: bool = False) -> dict | None:
    now = time.time()
    if not force and _UPDATE_CACHE["data"] and now - _UPDATE_CACHE["at"] < 1800:
        return _UPDATE_CACHE["data"]
    try:
        import urllib.request

        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": "Music-High-Res", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        _UPDATE_CACHE["data"] = data
        _UPDATE_CACHE["at"] = now
        return data
    except Exception:
        return None


@app.get("/api/update/check")
def api_update_check():
    """Is a newer Music High Res release available? Also reports a running
    update's progress lines. Cached 30 min against GitHub's API."""
    if not config.get("check_updates"):
        return jsonify({
            "ok": True, "current": VERSION, "latest": VERSION, "available": False,
            "disabled": True, "source_install": not getattr(sys, "frozen", False),
            "state": {**{k: _UPDATE_STATE[k] for k in ("running", "phase", "error", "done")}, "lines": _UPDATE_STATE["lines"][-20:]},
        })
    rel = _github_latest_release()
    if not rel:
        return jsonify({
            "ok": False, "error": "Could not reach GitHub (offline or rate-limited).",
            "current": VERSION, "available": False,
            "state": {**{k: _UPDATE_STATE[k] for k in ("running", "phase", "error", "done")}, "lines": _UPDATE_STATE["lines"][-20:]},
        })
    tag = (rel.get("tag_name") or "").lstrip("v")
    available = _version_tuple(tag) > _version_tuple(VERSION)
    return jsonify({
        "ok": True,
        "current": VERSION,
        "latest": tag,
        "available": available,
        "name": rel.get("name"),
        "url": rel.get("html_url"),
        "published": rel.get("published_at"),
        "notes": (rel.get("body") or "")[:4000],
        "source_install": not getattr(sys, "frozen", False),
        "state": {**{k: _UPDATE_STATE[k] for k in ("running", "phase", "error", "done")}, "lines": _UPDATE_STATE["lines"][-20:]},
    })


@app.post("/api/update/apply")
def api_update_apply():
    """Download the latest release and replace this install in place, preserving
    settings, the library ledger, cookies, and the output folder. Restarts the
    server automatically when finished."""
    if getattr(sys, "frozen", False):
        return jsonify({"ok": False, "error": "This is a packaged app — download the new version from the release page instead."}), 400
    if _UPDATE_STATE["running"]:
        return jsonify({"ok": False, "error": "An update is already in progress."}), 409
    rel = _github_latest_release(force=True)
    if not rel:
        return jsonify({"ok": False, "error": "Could not reach GitHub to fetch the latest release."}), 502
    tag = (rel.get("tag_name") or "").lstrip("v")
    if not (_version_tuple(tag) > _version_tuple(VERSION)):
        return jsonify({"ok": False, "error": f"Already on the latest version ({VERSION})."}), 400
    tarball = rel.get("tarball_url")
    if not tarball:
        return jsonify({"ok": False, "error": "Release has no source tarball to download."}), 400
    if manager.any_active():
        return jsonify({"ok": False, "error": "Downloads are still running — finish or cancel them first."}), 409
    _UPDATE_STATE.update(running=True, phase="starting", lines=["Preparing update…"], error=None, done=False)
    threading.Thread(target=_run_update, args=(tarball, tag), daemon=True).start()
    return jsonify({"ok": True, "started": True})


def _run_update(tarball_url: str, tag: str) -> None:
    """Background updater: backup → download → swap → restore → restart."""
    import shutil
    import subprocess
    import tarfile
    import tempfile

    def line(s: str) -> None:
        _UPDATE_STATE["lines"].append(s)
        _UPDATE_STATE["lines"] = _UPDATE_STATE["lines"][-30:]

    tmp = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="mhr-update-"))

        # 1 ── Back up user state before touching anything.
        _UPDATE_STATE["phase"] = "backup"
        line("Backing up settings, library index & cookies…")
        backup_dir = tmp / "backup"
        backup_dir.mkdir()
        for name in ("config.json", "data", "quality_cache.json", "pending_jobs.json"):
            p = PROJECT_DIR / name
            if p.exists():
                if p.is_dir():
                    shutil.copytree(p, backup_dir / name)
                else:
                    shutil.copy2(p, backup_dir / name)
        for key in ("cookies_path", "ytm_cookies_path", "spotify_cookies_path"):
            cp = config.get(key)
            if cp:
                p = Path(expand_path(cp))
                if p.exists() and p.resolve().parent == PROJECT_DIR.resolve():
                    shutil.copy2(p, backup_dir / p.name)

        # 2 ── Download the source tarball.
        _UPDATE_STATE["phase"] = "downloading"
        line(f"Downloading v{tag} from GitHub…")
        import urllib.request

        tar_path = tmp / "update.tar.gz"
        req = urllib.request.Request(tarball_url, headers={"User-Agent": "Music-High-Res"})
        with urllib.request.urlopen(req, timeout=180) as r, open(tar_path, "wb") as f:
            shutil.copyfileobj(r, f)

        # 3 ── Extract safely (GitHub tarballs: single top-level Owner-Repo-sha/ dir).
        _UPDATE_STATE["phase"] = "extracting"
        line("Extracting…")
        extract_dir = tmp / "src"
        extract_dir.mkdir()
        root_resolved = extract_dir.resolve()
        with tarfile.open(tar_path, "r:gz") as tf:
            for m in tf.getmembers():
                parts = m.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                target = (extract_dir / parts[1]).resolve()
                # Hard containment check (not a prefix string compare): the
                # resolved target must live strictly inside the extract dir.
                try:
                    target.relative_to(root_resolved)
                except ValueError:
                    raise RuntimeError("Unsafe path in release tarball") from None
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif m.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tf.extractfile(m) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
        inner = next(p for p in extract_dir.iterdir() if p.is_dir())

        # 4 ── Copy the new tree over the project dir (keep runtime-owned paths).
        _UPDATE_STATE["phase"] = "installing"
        line("Installing new app files…")
        skip = {".venv", "wrapper-v2", "wrapper-amdl", "data", "logs",
                "config.json", "quality_cache.json", "pending_jobs.json",
                "Music High Res.app", ".git", "dist", "build", "__pycache__"}
        for item in inner.iterdir():
            if item.name in skip:
                continue
            dst = PROJECT_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        for pyc in PROJECT_DIR.rglob("__pycache__"):
            shutil.rmtree(pyc, ignore_errors=True)

        # 5 ── Restore user state over the fresh files.
        _UPDATE_STATE["phase"] = "restoring"
        line("Restoring your settings & library index…")
        for name in ("config.json", "data", "quality_cache.json", "pending_jobs.json"):
            b = backup_dir / name
            if b.exists():
                dst = PROJECT_DIR / name
                if dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(dst, ignore_errors=True)
                    else:
                        dst.unlink(missing_ok=True)
                if b.is_dir():
                    shutil.copytree(b, dst)
                else:
                    shutil.copy2(b, dst)
        for f in backup_dir.iterdir():
            if f.is_file() and f.name != "config.json":
                dst = PROJECT_DIR / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)

        # 6 ── Refresh Python deps if the venv exists.
        try:
            from downloader import venv_bin
        except Exception:
            venv_bin = None
        pip = venv_bin("pip") if venv_bin else None
        if pip and (PROJECT_DIR / "requirements.txt").exists():
            _UPDATE_STATE["phase"] = "deps"
            line("Refreshing Python dependencies…")
            try:
                subprocess.run([pip, "install", "-q", "-r", str(PROJECT_DIR / "requirements.txt")],
                               cwd=str(PROJECT_DIR), timeout=600, capture_output=True)
            except Exception as e:
                line(f"Note: dependency refresh skipped ({e})")

        _UPDATE_STATE["phase"] = "restarting"
        line("Done — restarting the app…")
        _UPDATE_STATE["done"] = True
        _UPDATE_STATE["running"] = False

        # 7 ── Restart: this process must release the port BEFORE the new
        # server binds, so relaunch via the detached relaunch.py helper (waits
        # 2s, restores the ALAC wrapper that start.sh stops on app close, then
        # starts the server).
        env = dict(os.environ)
        helper_cmd = [sys.executable, str(PROJECT_DIR / "relaunch.py")]
        if os.name == "nt":
            subprocess.Popen(helper_cmd, cwd=str(PROJECT_DIR), env=env,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0))
        else:
            subprocess.Popen(helper_cmd, cwd=str(PROJECT_DIR), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1.0)
        os._exit(0)
    except Exception as e:
        _UPDATE_STATE["error"] = str(e)
        _UPDATE_STATE["phase"] = "error"
        _UPDATE_STATE["running"] = False
        line(f"Update failed: {e}")
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _serve_with_retry(serve_fn) -> None:
    """Bind the HTTP server, tolerating a briefly-occupied port.

    A stale Music High Res server (e.g. one auto-started by a LaunchAgent
    outside start.sh, or a leftover after a hard crash) can hold the port
    without answering the health probe. start.sh clears such squatters before
    starting us, but a race (the old process hasn't released the socket yet)
    can still hit "Address already in use". Retry a few times, then give up
    with a clear message instead of a raw traceback.
    """
    import time as _t

    for _attempt in range(5):
        try:
            serve_fn()
            return
        except OSError as e:
            if getattr(e, "errno", None) not in (48, 98):  # EADDRINUSE (macOS/Linux)
                raise
            if _attempt == 4:
                import shutil as _sh

                hint = ""
                if _sh.which("lsof"):
                    # Guard against an empty pid list (e.g. a TIME_WAIT socket
                    # holds the port but nothing is LISTENing).
                    pids = _sh.os.popen(f"lsof -tiTCP:{PORT} -sTCP:LISTEN 2>/dev/null").read().strip()
                    if pids:
                        hint = f"Close it, or run:  kill {pids}  then relaunch."
                print(
                    f"\n  ✗ Port {PORT} is already in use by another process.\n"
                    "    A previous Music High Res server may still be running.\n"
                    f"    {hint or f'Free the port (lsof -iTCP:{PORT}) and relaunch.'}\n",
                    flush=True,
                )
                raise SystemExit(1)
            _t.sleep(2)


def main():
    setup_logging()
    log = logging.getLogger("app")
    log.info("Music High Res server starting on http://%s:%s (log → %s)", HOST, PORT, LOG_DIR / "app.log")
    # Re-queue downloads that were interrupted by a previous shutdown.
    restored = manager.restore_pending()
    if restored:
        log.info("Restored %d pending job(s) from the previous session.", restored)
    _restart_watcher_if_needed()
    print(f"\n  Music High Res is running →  http://{HOST}:{PORT}", flush=True)
    print("  Press Ctrl+C to stop.\n", flush=True)
    # A PyInstaller binary is usually double-clicked with no terminal — open
    # the UI automatically once the server is listening.
    # Remote access: Settings → Remote access → listen on all interfaces so
    # the installed PWA works from a phone on the same network (the token
    # gate protects the API; the browser link stays loopback).
    bind_host = "0.0.0.0" if config.get("remote_bind") else HOST
    if getattr(sys, "frozen", False):
        try:
            import webbrowser

            threading.Timer(1.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
        except Exception:
            pass
    try:
        from waitress import serve

        # A generous thread pool keeps the 1.5s job poll / 5s wrapper poll
        # responsive while one or two transcoded audio streams are streaming
        # (each holds a request thread for the whole track).
        _serve_with_retry(lambda: serve(app, host=bind_host, port=PORT, threads=16))
    except ImportError:
        _serve_with_retry(lambda: app.run(host=bind_host, port=PORT, threaded=True))


if __name__ == "__main__":
    main()
