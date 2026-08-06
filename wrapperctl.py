"""Music High Res — wrapper-v2 controller.

Small helpers that talk to the local glomatico/wrapper-v2 server (and its
Docker container) so the web UI can show login state, submit the 2FA code,
and restart the login flow without needing a terminal.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
WRAPPER_DIR = PROJECT_DIR / "wrapper-v2"
WRAPPER_BASE = "http://127.0.0.1"

_LOG_FILTER = ("dlsym",)  # harmless loader noise we don't want in the UI


def _get(path: str, timeout: float = 4.0):
    try:
        with urllib.request.urlopen(WRAPPER_BASE + path, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception:
        return None, {}


def _post(path: str, body: dict, timeout: float = 30.0):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        WRAPPER_BASE + path, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception:
        return None, {}


def wrapper_status() -> dict:
    """Current wrapper + auth state for the UI."""
    status, me = _get("/me")
    if status is None:
        return {"reachable": False, "error": "Wrapper not reachable. Is Docker running?"}
    auth = me.get("auth", {}) if isinstance(me, dict) else {}
    runtime = me.get("runtime", {}) if isinstance(me, dict) else {}
    state = auth.get("state", "unknown")
    if state == "awaiting_2fa":
        hint = (
            "Apple requested a verification code. Check: trusted Apple devices, "
            "SMS, and Gmail (including Spam / Promotions). Codes expire quickly — "
            "if none arrived, restart the login for a fresh one."
        )
    elif state == "authenticated":
        hint = "Logged in. The wrapper is ready for ALAC / Atmos downloads."
    elif state == "failed":
        hint = (
            "The last code was rejected (expired or mistyped). Restart the login "
            "to get a fresh code, then submit it right away."
        )
    else:
        hint = "Waiting for the wrapper to reach the login step…"
    return {
        "reachable": True,
        "auth_state": state,
        "apple_id": auth.get("apple_id") or auth.get("username"),
        "error_code": auth.get("error_code"),
        "error": auth.get("error"),
        "playback_ready": bool(runtime.get("playback_ready")),
        "loader_ok": bool(runtime.get("loader_ok")),
        "version": me.get("version") if isinstance(me, dict) else None,
        "hint": hint,
    }


def wrapper_logs(lines: int = 40) -> list[str]:
    docker = shutil.which("docker")
    if not docker:
        return []
    try:
        out = subprocess.run(
            [docker, "logs", "--tail", str(lines), "wrapper-v2"],
            capture_output=True, text=True, timeout=10,
        )
        raw = (out.stdout or out.stderr or "").splitlines()
        return [
            ln for ln in raw
            if not any(f in ln for f in _LOG_FILTER)
        ][-lines:]
    except (OSError, subprocess.SubprocessError):
        return []


def submit_2fa(code: str) -> dict:
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return {"ok": False, "error": "Enter the 6-digit code."}
    status, body = _post("/login/2fa", {"code": code})
    ok = body.get("state") in ("authenticated",) or (status is not None and status < 300 and "error" not in body)
    return {"ok": ok, "status": status, "response": body}


def restart_login() -> dict:
    """Restart the wrapper container so it starts a brand-new login."""
    if not WRAPPER_DIR.exists():
        return {"ok": False, "error": "wrapper-v2 folder not found"}
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "error": "docker not found"}
    try:
        out = subprocess.run(
            [docker, "compose", "up", "-d", "--force-recreate"],
            cwd=str(WRAPPER_DIR),
            capture_output=True, text=True, timeout=180,
        )
        if out.returncode != 0:
            return {"ok": False, "error": (out.stderr or out.stdout or "docker compose failed").strip()[-400:]}
        return {"ok": True}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
