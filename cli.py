#!/usr/bin/env python3
"""Music High Res — CLI downloader.

Terminal fallback for the web app. Uses the same config.json settings.

Usage:
    python3 cli.py "https://music.apple.com/us/album/..." [more urls...]
    python3 cli.py --codec alac "https://..."
    python3 cli.py --output "~/Music/Apple Music" "https://..."
    python3 cli.py --check            # print a readiness checklist and exit

With no URLs, you'll be prompted to paste them (one per line, blank line to finish).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from downloader import (
    LEDGER_PATH,
    Config,
    JobManager,
    expand_path,
    ffmpeg_binary,
    ffprobe_binary,
    ffmpeg_version,
    gamdl_binary,
    gamdl_version,
    ledger_rebuild,
    ledger_stats,
    resolve_cookies_path,
    spotify_binary,
    spotify_version,
    ytm_binary,
    ytm_version,
)
import wrapperctl

LEVEL_COLORS = {
    "ERROR": "\033[91m",
    "CRITICAL": "\033[91m",
    "WARNING": "\033[93m",
    "INFO": "\033[90m",
}
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="music-high-res",
        description="Download Apple Music tracks as lossless ALAC (or AAC 256kbps).",
        add_help=True,
    )
    parser.add_argument("urls", nargs="*", help="Apple Music URLs (song/album/playlist/artist)")
    parser.add_argument("--codec", default=None,
                        help="Codec priority: alac | alac,aac-web | aac-web | atmos")
    parser.add_argument("--output", "-o", default=None, help="Output folder")
    parser.add_argument("--no-wrapper", action="store_true", help="Disable wrapper (overrides config)")
    parser.add_argument("--wrapper", action="store_true", help="Enable wrapper (overrides config)")
    parser.add_argument("--to-flac", nargs="?", const="", metavar="DIR",
                        help="Convert ALAC files in DIR (default: config output path) to FLAC")
    parser.add_argument("--overwrite-flac", action="store_true",
                        help="Overwrite existing .flac files during conversion")
    parser.add_argument("--check", action="store_true",
                        help="Print a readiness checklist (same as the app's 'Getting started' card) and exit")
    parser.add_argument("--ledger", action="store_true",
                        help="Show the SQLite ledger stats (indexed tracks, bytes, engine/codec split, missing files) and exit")
    parser.add_argument("--ledger-rebuild", action="store_true",
                        help="Wipe and re-index the SQLite ledger from the output folder, then show stats")
    args = parser.parse_args()

    config = Config()
    version = gamdl_version()
    print(f"{BOLD}Music High Res{RESET} · gamdl {version or '(not found)'}")

    if args.check:
        return run_check(config)

    if args.ledger or args.ledger_rebuild:
        return run_ledger(config, args)

    if args.to_flac is not None:
        return run_conversion(config, args)


    urls = [u.strip() for u in args.urls if u.strip()]
    if not urls:
        print("Paste Apple Music URLs, one per line (blank line to start):")
        print("  > ", end="", flush=True)
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    break
                urls.append(line)
                print("  > ", end="", flush=True)
        except KeyboardInterrupt:
            print("\nAborted.")
            return 1

    urls = list(dict.fromkeys(urls))
    if not urls:
        print(f"{RED}No URLs given.{RESET}")
        return 1

    options = {}
    if args.codec:
        options["codec"] = args.codec
    if args.output:
        options["output_path"] = args.output
    if args.wrapper:
        options["use_wrapper"] = True
    if args.no_wrapper:
        options["use_wrapper"] = False

    codec = options.get("codec", config.get("song_codec_priority"))
    out = expand_path(options.get("output_path", config.get("output_path")))
    print(f"\n  Codec priority : {BOLD}{codec}{RESET}")
    print(f"  Output folder  : {out}")
    print(f"  Links          : {len(urls)}")
    print("  Press Ctrl+C to cancel.\n")

    manager = JobManager(config)
    job = manager.start(urls, options)

    last_len = 0
    try:
        while job.status in ("queued", "running"):
            for entry in job.log[last_len:]:
                color = LEVEL_COLORS.get(entry["level"], "")
                print(f"  {color}{entry['text']}{RESET}", flush=True)
                last_len += 1
            time.sleep(0.25)
        for entry in job.log[last_len:]:
            color = LEVEL_COLORS.get(entry["level"], "")
            print(f"  {color}{entry['text']}{RESET}", flush=True)
            last_len += 1
    except KeyboardInterrupt:
        print(f"\n{RED}Cancelling…{RESET}")
        job.cancel()
        job.set_status("cancelled")
        job.wait(timeout=10)

    print()
    if job.status == "done":
        print(f"{GREEN}✓ Done.{RESET} Files are in: {out}")
        return 0
    print(f"{RED}✗ {job.status.title()}. See messages above.{RESET}")
    return 1


def run_check(config: Config) -> int:
    """Print a readiness checklist — the CLI twin of the web app's
    '0 · Getting started' card. Exits 1 if a required item is missing."""
    print(f"\n{BOLD}Readiness check{RESET}")

    def _line(ok, label, detail, hint="", required=False):
        if ok:
            mark = f"{GREEN}✓{RESET}"
        elif required:
            mark = f"{RED}✗{RESET}"
        else:
            mark = f"{YELLOW}!{RESET}"
        detail_s = f" — {detail}" if detail else ""
        line = f"  {mark} {label}{detail_s}"
        if not ok and hint:
            line += f"\n      {hint}"
        return line

    rows = []  # (ok, required)

    # gamdl
    gbin, gver = gamdl_binary(), gamdl_version()
    gver_short = (gver or "").replace("gamdl, version ", "v").strip()
    rows.append((gbin is not None, True))
    print(_line(gbin is not None, "gamdl engine (Apple Music)", gver_short or "",
                "Install: brew install gamdl  (or pip install gamdl)", required=True))

    # ffmpeg + ffprobe
    ff = ffmpeg_binary() is not None and ffprobe_binary() is not None
    ff_full = ffmpeg_version() or ""
    ff_short = ff_full.split(",")[0] if "," in ff_full else (ff_full.split()[2] if len(ff_full.split()) > 2 else ff_full)
    rows.append((ff, True))
    print(_line(ff, "FFmpeg (FLAC, player, quality checks)", ff_short,
                "Install: brew install ffmpeg  (or your package manager)", required=True))

    # gytmdl / votify
    ytm = ytm_binary() is not None
    rows.append((ytm, False))
    print(_line(ytm, "gytmdl (YouTube Music)", ytm_version() or "",
                "Install: .venv/bin/pip install gytmdl"))
    spot = spotify_binary() is not None
    rows.append((spot, False))
    print(_line(spot, "votify (Spotify)", spotify_version() or "",
                "Install: .venv/bin/pip install 'votify[librespot]'"))

    # docker + wrapper (checked before cookies: an authenticated wrapper makes
    # Apple Music cookies optional — gamdl uses the wrapper's session instead)
    docker = wrapperctl.docker_status()
    docker_ok = bool(docker.get("running"))
    rows.append((docker_ok, False))
    print(_line(docker_ok, "Docker Desktop", "running" if docker_ok else "not ready",
                "Only needed for lossless ALAC / Atmos."))
    wrapper_ok = False
    if docker_ok and wrapperctl.wrapper_present():
        try:
            wrapper_ok = wrapperctl.wrapper_status().get("auth_state") == "authenticated"
        except Exception:
            wrapper_ok = False
    rows.append((wrapper_ok, False))
    print(_line(wrapper_ok, "ALAC wrapper (wrapper-v2)", "authenticated" if wrapper_ok else "not ready",
                "Only needed for lossless ALAC / Atmos — see the app's '5 · Wrapper & login' panel."))

    # Apple Music cookies (required only without the wrapper)
    cookies = resolve_cookies_path(config)
    cookies_ok = Path(cookies).exists()
    cookies_required = not wrapper_ok
    rows.append((cookies_ok, cookies_required))
    print(_line(cookies_ok, "Apple Music cookies", cookies,
                "Export cookies.txt from music.apple.com (see README Step 1). Not needed with the wrapper.",
                required=cookies_required))

    # optional per-service cookies (only matter if you use that service)
    spotify_cookies = str(config.get("spotify_cookies_path") or "").strip()
    spotify_cookies_ok = bool(spotify_cookies) and Path(expand_path(spotify_cookies)).exists()
    rows.append((spotify_cookies_ok, False))
    print(_line(spotify_cookies_ok, "Spotify cookies", spotify_cookies or "not configured",
                "Only needed for Spotify downloads — export from open.spotify.com and set it in Settings."))
    ytm_cookies = str(config.get("ytm_cookies_path") or "").strip()
    ytm_cookies_ok = bool(ytm_cookies) and Path(expand_path(ytm_cookies)).exists()
    rows.append((ytm_cookies_ok, False))
    print(_line(ytm_cookies_ok, "YouTube Music cookies", ytm_cookies or "not configured",
                "Only needed for Premium YouTube Music itags (AAC/Opus 256k) — set it in Settings."))

    # output folder writable
    output = expand_path(config.get("output_path"))
    out_ok = False
    try:
        probe = Path(output)
        if not probe.exists():
            probe.mkdir(parents=True, exist_ok=True)
        test_file = probe / ".mhr-write-test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        out_ok = True
    except OSError:
        out_ok = False
    rows.append((out_ok, True))
    print(_line(out_ok, "Output folder", output if out_ok else "not writable",
                "Change the output folder in the app's Settings.", required=True))

    required_ok = sum(1 for ok, req in rows if req and ok)
    required_total = sum(1 for _, req in rows if req)
    print(f"\n  {BOLD}{required_ok}/{required_total} required items ready.{RESET}")
    if required_ok == required_total:
        print(f"  {GREEN}✓ Everything required is in place — paste a link and hit Download.{RESET}")
        return 0
    print(f"  {YELLOW}! Some required pieces are missing — fix the items above.{RESET}")
    return 1


def run_ledger(config: Config, args: argparse.Namespace) -> int:
    """Print (and optionally rebuild) the SQLite ledger stats."""
    out = expand_path(config.get("output_path"))
    if args.ledger_rebuild:
        print(f"\n{BOLD}Rebuilding the SQLite ledger{RESET} from {out}…")
        stats = ledger_rebuild(out)
        print(f"  {GREEN}✓ Re-indexed.{RESET}")
    else:
        stats = ledger_stats(out)

    print(f"\n{BOLD}SQLite ledger{RESET}  ({LEDGER_PATH})")
    print(f"  Indexed tracks : {stats['tracks']}")
    print(f"  Indexed size   : {stats['bytes'] / 1024 / 1024:.1f} MB")
    print(f"  Missing on disk: {stats['missing_count']}")
    if stats["by_engine"]:
        print(f"  By engine      : {', '.join(f'{k}×{v}' for k, v in stats['by_engine'].items())}")
    if stats["by_codec"]:
        print(f"  By codec       : {', '.join(f'{k}×{v}' for k, v in stats['by_codec'].items())}")
    if stats["missing_count"]:
        print(f"\n  {YELLOW}! {stats['missing_count']} recorded file(s) are missing on disk{RESET} —")
        print(f"    deleted or sitting in <output>/.trash/. Re-download them, or run")
        print(f"    `--ledger-rebuild` to drop the stale rows.")
    return 0


def run_conversion(config: Config, args: argparse.Namespace) -> int:
    """Convert ALAC → FLAC in the given folder (or the config output path)."""
    source = args.to_flac or config.get("output_path")
    src = expand_path(source)
    print(f"{BOLD}FLAC conversion{RESET}")
    print(f"  Source folder : {src}")
    print(f"  Overwrite     : {'yes' if args.overwrite_flac else 'no (skip existing .flac)'}")
    print("  Original .m4a files are kept. Press Ctrl+C to cancel.\n")

    manager = JobManager(config)
    job = manager.start_convert(src, overwrite=args.overwrite_flac)

    last_len = 0
    try:
        while job.status in ("queued", "running"):
            for entry in job.log[last_len:]:
                print(f"  {RESET}{entry['text']}", flush=True)
                last_len += 1
            time.sleep(0.25)
        for entry in job.log[last_len:]:
            print(f"  {RESET}{entry['text']}", flush=True)
            last_len += 1
    except KeyboardInterrupt:
        print(f"\n{RED}Cancelling…{RESET}")
        job.cancel()
        job.set_status("cancelled")
        job.wait(timeout=30)

    print()
    if job.status == "done":
        print(f"{GREEN}✓ Done.{RESET} FLAC files are ready next to the originals.")
        return 0
    print(f"{RED}✗ {job.status.title()}. See messages above.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
