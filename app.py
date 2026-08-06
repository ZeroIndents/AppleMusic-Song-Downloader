"""Music High Res — local web app.

Serves a small single-page UI on http://127.0.0.1:8741 where you paste Apple
Music URLs, pick quality, and watch gamdl download them into your library
folder. The CLI (cli.py) uses the same download manager.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
import wrapperctl
from downloader import (
    PROJECT_DIR,
    Config,
    JobManager,
    expand_path,
    ffmpeg_binary,
    ffmpeg_version,
    ffprobe_binary,
    gamdl_binary,
    gamdl_version,
    resolve_cookies_path,
)

PORT = int(os.environ.get("MHR_PORT", "8741"))
HOST = "127.0.0.1"

config = Config()
manager = JobManager(config)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 1_000_000  # 1 MB request cap


# ----------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(PROJECT_DIR / "static", "index.html")


# ----------------------------------------------------------------------
# Status + config
# ----------------------------------------------------------------------
@app.get("/api/status")
def api_status():
    cookies_path = resolve_cookies_path(config)
    return jsonify({
        "gamdl": gamdl_version(),
        "gamdl_found": gamdl_binary() is not None,
        "ffmpeg": ffmpeg_version(),
        "ffmpeg_found": ffmpeg_binary() is not None and ffprobe_binary() is not None,
        "cookies_exists": Path(cookies_path).exists(),
        "cookies_path": cookies_path,
        "output_path": expand_path(config.get("output_path")),
        "wrapper_enabled": bool(config.get("use_wrapper")),
        "wrapper_url": config.get("wrapper_url"),
        "codec": config.get("song_codec_priority"),
        "convert_to_flac": bool(config.get("convert_to_flac")),
        "python": os.sys.version.split()[0],
        "any_active": manager.any_active(),
    })


@app.get("/api/config")
def api_get_config():
    return jsonify(config.data)


@app.post("/api/config")
def api_save_config():
    body = request.get_json(silent=True) or {}
    normalized = {}
    for key, value in body.items():
        if key not in config.data:
            continue
        if key in ("output_path", "cookies_path", "wrapper_url"):
            value = str(value).strip()
            if not value:
                continue
        elif key in ("cover_size",):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif key in ("use_wrapper", "synced_lyrics", "save_cover", "overwrite", "convert_to_flac"):
            value = bool(value)
        normalized[key] = value
    changes = config.update(normalized)
    return jsonify({"ok": True, "changes": changes, "config": config.data})


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
        return jsonify({"ok": False, "error": "Paste at least one Apple Music URL."}), 400

    if len(urls) > 200:
        return jsonify({"ok": False, "error": "Too many URLs (max 200 per batch)."}), 400

    options = {
        k: body[k]
        for k in ("codec", "output_path", "cookies_path", "use_wrapper", "wrapper_url")
        if k in body
    }
    job = manager.start(urls, options)
    return jsonify({"ok": True, "job": job.summary()})


# ----------------------------------------------------------------------
# Wrapper (gamdl wrapper-v2) status + login
# ----------------------------------------------------------------------
@app.get("/api/wrapper")
def api_wrapper():
    data = wrapperctl.wrapper_status()
    data["logs"] = wrapperctl.wrapper_logs()
    return jsonify(data)


@app.post("/api/wrapper/2fa")
def api_wrapper_2fa():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip()
    result = wrapperctl.submit_2fa(code)
    return jsonify(result)


@app.post("/api/wrapper/restart")
def api_wrapper_restart():
    return jsonify(wrapperctl.restart_login())


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
    return jsonify({"jobs": manager.list()})


@app.get("/api/jobs/<job_id>")
def api_job_detail(job_id: str):
    job = manager.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Job not found."}), 404
    return jsonify(job.detail())


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


def main():
    print(f"\n  Music High Res is running →  http://{HOST}:{PORT}", flush=True)
    print("  Press Ctrl+C to stop.\n", flush=True)
    try:
        from waitress import serve

        serve(app, host=HOST, port=PORT, threads=8)
    except ImportError:
        app.run(host=HOST, port=PORT, threaded=True)


if __name__ == "__main__":
    main()
