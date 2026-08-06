#!/usr/bin/env python3
"""Music High Res — CLI downloader.

Terminal fallback for the web app. Uses the same config.json settings.

Usage:
    python3 cli.py "https://music.apple.com/us/album/..." [more urls...]
    python3 cli.py --codec alac "https://..."
    python3 cli.py --output "~/Music/Apple Music" "https://..."

With no URLs, you'll be prompted to paste them (one per line, blank line to finish).
"""

from __future__ import annotations

import argparse
import sys
import time

from downloader import Config, JobManager, expand_path, gamdl_version

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
    args = parser.parse_args()

    config = Config()
    version = gamdl_version()
    print(f"{BOLD}Music High Res{RESET} · gamdl {version or '(not found)'}")

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
