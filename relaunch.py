#!/usr/bin/env python3
"""Detached relaunch helper for the in-app self-updater.

The updater swaps the app files in place and then must restart the server. A
plain `os._exit(0)` restart would orphan the ALAC wrapper: start.sh stops the
wrapper when the app process it launched exits, so the freshly started server
would come up with the wrapper down. This script (spawned detached, 2s after
the old process exits) restores the wrapper that the old session's launcher is
about to stop, then starts the server. Best-effort: any failure here only
means the user restarts via their normal launcher — nothing is lost.

Usage: relaunch.py          (reads config.json in the current directory)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _defaults() -> dict:
    try:
        with open(ROOT / "config.json", encoding="utf-8") as f:
            stored = json.load(f)
        return stored if isinstance(stored, dict) else {}
    except Exception:
        return {}


def _start_wrapper(cfg: dict) -> None:
    """Best-effort: bring back the wrapper container the previous launcher
    session is about to stop. Mirrors start.sh's engine-aware start."""
    try:
        engine = cfg.get("apple_engine") or "gamdl"
        if engine == "amdl":
            import wrapperctl

            wrapperctl.amdl_wrapper_start()
            return
        # gamdl wrapper (wrapper-v2) — docker compose up -d
        compose = ROOT / "wrapper-v2" / "docker-compose.yml"
        if not compose.exists():
            return
        subprocess.Popen(
            ["docker", "compose", "up", "-d"],
            cwd=str(ROOT / "wrapper-v2"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # best-effort only


def main() -> None:
    time.sleep(2)  # let the old process release the port + launcher cleanup run
    cfg = _defaults()
    if cfg.get("use_wrapper"):
        _start_wrapper(cfg)
    # Start the server, detached from any terminal.
    cmd = [sys.executable, str(ROOT / "app.py")]
    env = dict(os.environ)
    kwargs = dict(cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


if __name__ == "__main__":
    main()
