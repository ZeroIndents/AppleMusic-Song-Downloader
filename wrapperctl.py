"""Music High Res — wrapper-v2 controller.

Small helpers that talk to the local glomatico/wrapper-v2 server (and its
Docker container) so the web UI can show login state, submit the 2FA code,
and restart the login flow without needing a terminal.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
WRAPPER_DIR = PROJECT_DIR / "wrapper-v2"
WRAPPER_BASE = "http://127.0.0.1"
APK_DOWNLOAD_DIR = PROJECT_DIR / "data" / "apk"

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
    except Exception as e:
        return None, {"error": str(e)}


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
    except Exception as e:
        return None, {"error": str(e)}


def _check_creds(email: str, password: str, amdl: bool = False) -> str | None:
    """Reject credentials that would corrupt the .env / login.env files.

    Dotenv lines break on newlines and treat '#' as a comment; the amdl
    wrapper additionally parses `-L email:password` out of a single arg, so
    ':' or a space would split the pair. Returns an error string or None.
    """
    if any(c in email + password for c in "\n\r#"):
        return "Email/password can't contain newlines or '#' (they would corrupt the wrapper's .env file)."
    if amdl and any(c in email + password for c in ": "):
        return "For the amdl engine, email/password can't contain ':' or spaces."
    return None


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


# ----------------------------------------------------------------------
# Guided setup + credential helpers (used by the in-app setup wizard)
# ----------------------------------------------------------------------

_DOCKER_CACHE: dict = {"value": None, "at": 0.0}


def docker_status() -> dict:
    """Is Docker installed, and is the daemon running?

    Cached for a few seconds — the wizard polls this endpoint.
    """
    now = time.time()
    if _DOCKER_CACHE["value"] is not None and now - _DOCKER_CACHE["at"] < 5:
        return _DOCKER_CACHE["value"]
    docker = shutil.which("docker")
    if not docker:
        val = {"installed": False, "running": False}
        _DOCKER_CACHE.update(value=val, at=now)
        return val
    try:
        out = subprocess.run([docker, "info"], capture_output=True, text=True, timeout=10)
        val = {"installed": True, "running": out.returncode == 0}
    except (OSError, subprocess.SubprocessError):
        val = {"installed": True, "running": False}
    _DOCKER_CACHE.update(value=val, at=now)
    return val


def machine_info() -> dict:
    """Which Mac are we on? (drives the Intel-Mac library-fix suggestion)."""
    arch = platform.machine().lower()
    return {"arch": arch, "apple_silicon": arch in ("arm64", "aarch64")}


def wrapper_present() -> bool:
    """Has wrapper-v2 been cloned/set up at least once?"""
    if not WRAPPER_DIR.exists():
        return False
    return (WRAPPER_DIR / "compose.yaml").exists() or (WRAPPER_DIR / "docker-compose.yml").exists()


def save_credentials(email: str, password: str) -> dict:
    """Write the Apple ID credentials to wrapper-v2/.env and restart the login.

    This is the web-app version of login_wrapper.sh — no Terminal needed.
    The .env is written with 0600 perms and never leaves this machine.
    """
    if not wrapper_present():
        return {"ok": False, "error": "wrapper-v2 isn't set up yet — run the Setup wizard first."}
    err = _check_creds(email, password)
    if err:
        return {"ok": False, "error": err}
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "error": "docker not found"}
    try:
        env_file = WRAPPER_DIR / ".env"
        env_file.write_text(f"WRAPPER_USERNAME={email}\nWRAPPER_PASSWORD={password}\n", encoding="utf-8")
        os.chmod(env_file, 0o600)
    except OSError as e:
        return {"ok": False, "error": f"Could not write wrapper-v2/.env: {e}"}
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


# ----------------------------------------------------------------------
# amdl wrapper (itouakirai/wrapper) — the other Apple wrapper
# ----------------------------------------------------------------------
# When Settings → Apple engine = "amdl", downloads run through
# zhaarey/apple-music-downloader (a Docker image), which decrypts via the
# itouakirai wrapper on ports 10020 (decrypt) + 20020 (m3u8). BOTH Apple
# wrappers use port 10020, so only one can run at a time — starting the amdl
# wrapper stops wrapper-v2 first. The login container uses -F (code-from-file):
# it polls /data/2fa.txt, and the data dir is bind-mounted at BOTH
# /app/rootfs/data (session persistence) and /data, so submitting the code is
# just writing wrapper-amdl/rootfs/data/2fa.txt.
AMDL_IMAGE = "ghcr.io/itouakirai/wrapper:x86"
AMDL_DIR = PROJECT_DIR / "wrapper-amdl"
AMDL_DATA_DIR = AMDL_DIR / "rootfs" / "data"
AMDL_LOGIN_NAME = "amdl-login"
AMDL_RUN_NAME = "amdl-wrapper"


def _docker_running(name: str) -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        out = subprocess.run(
            [docker, "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        return name in (out.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False


def _docker_rm(name: str) -> None:
    docker = shutil.which("docker")
    if not docker:
        return
    try:
        subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _stop_wrapper_v2() -> None:
    """Free port 10020 by stopping the glomatico wrapper-v2 container."""
    if not _docker_running("wrapper-v2"):
        return
    if WRAPPER_DIR.exists():
        try:
            subprocess.run(
                ["docker", "compose", "down"],
                cwd=str(WRAPPER_DIR), capture_output=True, text=True, timeout=120,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    _docker_rm("wrapper-v2")


def _amdl_prepare() -> str | None:
    """Make sure the amdl data dir + 2fa.txt file exist. Returns an error or None."""
    try:
        AMDL_DATA_DIR.mkdir(parents=True, exist_ok=True)
        # The /data bind must mount a FILE (docker turns a missing host path
        # into a directory, which the wrapper can't read as a code file).
        (AMDL_DATA_DIR / "2fa.txt").touch()
    except OSError as e:
        return f"Could not prepare wrapper-amdl: {e}"
    return None


def amdl_login(email: str, password: str) -> dict:
    """Start the one-shot login container (-L user:pass -F)."""
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "error": "docker not found"}
    if not email or not password:
        return {"ok": False, "error": "Enter your Apple ID email and password."}
    err = _check_creds(email, password, amdl=True)
    if err:
        return {"ok": False, "error": err}
    err = _amdl_prepare()
    if err:
        return {"ok": False, "error": err}
    try:
        (AMDL_DIR / ".env").write_text(
            f"WRAPPER_USERNAME={email}\nWRAPPER_PASSWORD={password}\n", encoding="utf-8"
        )
        os.chmod(AMDL_DIR / ".env", 0o600)
    except OSError as e:
        return {"ok": False, "error": f"Could not write wrapper-amdl/.env: {e}"}
    _stop_wrapper_v2()
    _docker_rm(AMDL_LOGIN_NAME)
    # Fresh login ⇒ clear any stale code from a previous attempt (the wrapper
    # polls 2fa.txt and would otherwise auto-submit the old code).
    try:
        (AMDL_DATA_DIR / "2fa.txt").write_text("", encoding="utf-8")
    except OSError:
        pass
    # Credentials go through --env-file (0600) instead of -e args=… on the
    # command line, so they never appear in `ps` / `docker inspect`.
    try:
        login_env = AMDL_DIR / "login.env"
        login_env.write_text(f"args=-L {email}:{password} -F -H 0.0.0.0\n", encoding="utf-8")
        os.chmod(login_env, 0o600)
    except OSError as e:
        return {"ok": False, "error": f"Could not write wrapper-amdl/login.env: {e}"}
    try:
        out = subprocess.run(
            [docker, "run", "-d", "--name", AMDL_LOGIN_NAME,
             "--env-file", str(AMDL_DIR / "login.env"),
             "-v", f"{AMDL_DATA_DIR}:/app/rootfs/data",
             "-v", f"{AMDL_DATA_DIR}:/data",
             AMDL_IMAGE],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            return {"ok": False, "error": (out.stderr or out.stdout or "docker run failed").strip()[-400:]}
        return {"ok": True, "note": "Login started — if Apple asks for a code, enter it below."}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}


def amdl_submit_2fa(code: str) -> dict:
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return {"ok": False, "error": "Enter the 6-digit code."}
    try:
        AMDL_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (AMDL_DATA_DIR / "2fa.txt").write_text(code, encoding="utf-8")
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": f"Could not write the code: {e}"}


def amdl_restart_login() -> dict:
    """Re-run the login with the saved credentials (fresh code)."""
    try:
        env_file = AMDL_DIR / ".env"
        if not env_file.exists():
            return {"ok": False, "error": "No saved credentials — log in from the panel first."}
        creds = {}
        for ln in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                creds[k.strip()] = v.strip()
        return amdl_login(creds.get("WRAPPER_USERNAME", ""), creds.get("WRAPPER_PASSWORD", ""))
    except OSError as e:
        return {"ok": False, "error": str(e)}


def amdl_wrapper_start() -> dict:
    """Start the persistent wrapper (serves 10020 + 20020). Stops wrapper-v2 first."""
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "error": "docker not found"}
    err = _amdl_prepare()
    if err:
        return {"ok": False, "error": err}
    _stop_wrapper_v2()
    _docker_rm(AMDL_RUN_NAME)
    _docker_rm(AMDL_LOGIN_NAME)
    try:
        out = subprocess.run(
            [docker, "run", "-d", "--name", AMDL_RUN_NAME,
             "-v", f"{AMDL_DATA_DIR}:/app/rootfs/data",
             "-v", f"{AMDL_DATA_DIR}:/data",
             "-p", "10020:10020", "-p", "20020:20020",
             "-e", "args=-M 20020 -H 0.0.0.0",
             AMDL_IMAGE],
            capture_output=True, text=True, timeout=180,
        )
        if out.returncode != 0:
            return {"ok": False, "error": (out.stderr or out.stdout or "docker run failed").strip()[-400:]}
        return {"ok": True}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}


def amdl_wrapper_stop() -> dict:
    _docker_rm(AMDL_RUN_NAME)
    _docker_rm(AMDL_LOGIN_NAME)
    return {"ok": True}


def amdl_wrapper_logs(lines: int = 40) -> list[str]:
    docker = shutil.which("docker")
    if not docker:
        return []
    out: list[str] = []
    for name in (AMDL_RUN_NAME, AMDL_LOGIN_NAME):
        try:
            r = subprocess.run(
                [docker, "logs", "--tail", str(lines), name],
                capture_output=True, text=True, timeout=10,
            )
            if r.stdout or r.stderr:
                out += (r.stdout or r.stderr).splitlines()
        except (OSError, subprocess.SubprocessError):
            pass
    return [ln for ln in out if "dlsym" not in ln][-lines:]


def amdl_wrapper_status() -> dict:
    """State for the UI when apple_engine == amdl."""
    if not shutil.which("docker"):
        return {"mode": "amdl", "reachable": True, "docker": False,
                "state": "no_docker", "hint": "Docker Desktop isn't installed — amdl needs it."}
    run = _docker_running(AMDL_RUN_NAME)
    login = _docker_running(AMDL_LOGIN_NAME)
    v2 = _docker_running("wrapper-v2")
    session = False
    if AMDL_DATA_DIR.exists():
        try:
            session = any(p.name not in ("2fa.txt",) for p in AMDL_DATA_DIR.iterdir())
        except OSError:
            session = False
    needs_2fa = False
    if login:
        try:
            r = subprocess.run(
                [docker, "logs", "--tail", "30", AMDL_LOGIN_NAME],
                capture_output=True, text=True, timeout=10,
            )
            if "2FA" in (r.stdout or "") + (r.stderr or ""):
                needs_2fa = True
        except (OSError, subprocess.SubprocessError):
            pass
    if run:
        state, hint = "running", "amdl wrapper is up — Apple downloads will use amdl."
    elif v2:
        state, hint = ("conflict",
                       "wrapper-v2 is still running and holds port 10020 — Start stops it automatically.")
    elif login:
        state = "logging_in"
        hint = ("Logging in…" if not needs_2fa else
                "Apple wants a verification code — enter it below (written straight to the wrapper's code file).")
    elif session:
        state, hint = ("not_running",
                       "amdl wrapper is stopped but a saved session exists — hit Start to bring it up (no 2FA needed).")
    else:
        state, hint = ("not_running",
                       "amdl wrapper is stopped and has no session yet — log in with your Apple ID, then Start it.")
    return {
        "mode": "amdl",
        "reachable": True,
        "docker": True,
        "wrapper_running": run,
        "login_running": login,
        "needs_2fa": needs_2fa,
        "wrapper_v2_running": v2,
        "session_present": session,
        "state": state,
        "hint": hint,
        "data_dir": str(AMDL_DATA_DIR),
    }


class SetupError(Exception):
    pass


class SetupManager:
    """Runs the wrapper setup (setup_wrapper.sh) in a background thread and
    keeps a capped log buffer the web UI polls — same pattern as download jobs.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "idle"  # idle | running | done | failed
        self.step = ""
        self.error = ""
        self.started_at = 0.0
        self.log: list[str] = []

    # ---- log helpers -------------------------------------------------
    def _add(self, line: str) -> None:
        line = str(line).rstrip("\n")
        if not line:
            return
        with self._lock:
            self.log.append(line)
            if len(self.log) > 600:
                self.log = self.log[-600:]

    def tail(self, n: int = 200) -> list[str]:
        with self._lock:
            return self.log[-n:]

    def is_running(self) -> bool:
        with self._lock:
            return self.state == "running"

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "step": self.step,
                "error": self.error,
                "started_at": self.started_at,
                "log": self.log[-200:],
            }

    # ---- runner ------------------------------------------------------
    def start(self, apk: str, email: str, password: str, apply_fix: bool) -> None:
        with self._lock:
            if self.state == "running":
                raise SetupError("Setup is already running.")
            self.state = "running"
            self.step = "Preparing…"
            self.error = ""
            self.log = []
            self.started_at = time.time()
        t = threading.Thread(
            target=self._run, args=(apk, email, password, apply_fix), daemon=True
        )
        t.start()

    def _stream(self, cmd: list[str], env: dict, cwd: str, deadline: float = 0.0) -> int:
        """Run a command, feeding every output line into the log buffer.
        `deadline` (epoch seconds, 0 = none) is a wall-clock cap so a hung
        setup can't leave the wizard stuck on "running" forever."""
        self._add("$ " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env, cwd=cwd,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if deadline and time.time() > deadline:
                proc.kill()
                raise SetupError("Setup timed out — see the log above and try again.")
            self._add(line)
        return proc.wait()

    def _download(self, url: str, dest: Path, deadline: float = 0.0) -> None:
        """Stream a file download with rough progress lines. Best-effort — if
        the site blocks it, the wizard tells the user to download manually."""
        self._add(f"Downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        chunk = 1024 * 1024  # 1 MB
        got = 0
        last_pct = 0
        with urllib.request.urlopen(req, timeout=60) as src:
            total = int(src.headers.get("Content-Length") or 0)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                while True:
                    buf = src.read(chunk)
                    if not buf:
                        break
                    if deadline and time.time() > deadline:
                        raise SetupError("APK download timed out — check the URL and try again.")
                    fh.write(buf)
                    got += len(buf)
                    if total:
                        pct = got * 100 // total
                        if pct >= last_pct + 10:
                            self._add(f"  downloaded {pct}%")
                            last_pct = pct
        self._add(f"Downloaded {got / 1024 / 1024:.1f} MB")

    def _run(self, apk: str, email: str, password: str, apply_fix: bool) -> None:
        try:
            self._add("━━━ Music High Res — guided wrapper setup ━━━")
            # 45-minute wall-clock cap for the whole setup (clone + APK
            # download + extraction) so a stall can't wedge the wizard.
            deadline = time.time() + 60 * 45

            # 1. Docker
            self.step = "Checking Docker…"
            docker = docker_status()
            if not docker["installed"]:
                raise SetupError(
                    "Docker Desktop isn't installed. Download it from "
                    "https://www.docker.com/products/docker-desktop/ , start it, "
                    "then run Setup again."
                )
            if not docker["running"]:
                raise SetupError(
                    "Docker is installed but not running. Open Docker Desktop and "
                    "wait for it to be ready, then run Setup again."
                )
            self._add("✓ Docker is running")

            # 2. APK — download if a URL, otherwise validate the local path.
            apk_path = apk
            if apk.lower().startswith(("http://", "https://")):
                self.step = "Downloading the Apple Music APK…"
                apk_path = str(APK_DOWNLOAD_DIR / "apple-music.apk")
                try:
                    self._download(apk, Path(apk_path), deadline)
                except Exception as e:
                    raise SetupError(
                        f"Could not download the APK ({e}). Download the APK manually "
                        f"and give the file path instead."
                    )
            if not os.path.isfile(apk_path):
                raise SetupError(f"APK not found: {apk_path}")
            self._add(f"✓ APK: {apk_path}")

            # 3. Run the setup script (non-interactive). setup_wrapper.sh is a
            #    bash script — on Windows that needs Git Bash (or WSL). Give a
            #    clear error instead of a confusing "not found" crash.
            self.step = "Cloning wrapper + extracting libraries…"
            bash = shutil.which("bash")
            if not bash:
                if os.name == "nt":
                    raise SetupError(
                        "The wrapper setup script needs a bash shell, which Windows "
                        "doesn't ship with. Install Git for Windows (Git Bash) or "
                        "enable WSL, then run Setup again."
                    )
                raise SetupError("bash not found — the wrapper setup needs a bash shell.")
            env = dict(os.environ)
            if email and password:
                env["WRAPPER_USERNAME"] = email
                env["WRAPPER_PASSWORD"] = password
            cmd = [
                bash, str(PROJECT_DIR / "setup_wrapper.sh"), "--ui", apk_path,
            ]
            if apply_fix:
                cmd.append("--fix-libs")
            rc = self._stream(cmd, env, str(PROJECT_DIR), deadline)
            if rc != 0:
                raise SetupError(
                    "setup_wrapper.sh exited with an error — see the log above."
                )

            self.step = "Done"
            self._add("✓ Setup complete. If the wrapper needs a 2FA code, it will "
                      "appear in the login panel above.")
            with self._lock:
                self.state = "done"
        except SetupError as e:
            with self._lock:
                self.state = "failed"
                self.error = str(e)
            self._add("✗ " + str(e))
        except Exception as e:  # anything unexpected
            with self._lock:
                self.state = "failed"
                self.error = str(e)
            self._add("✗ " + str(e))
