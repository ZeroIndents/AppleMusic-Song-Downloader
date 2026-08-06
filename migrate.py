"""migrate.py — migrate albums/playlists from Spotify or YouTube Music to Apple Music.

Pipeline for a pasted link:

    resolve_tracks(url)   → [{title, artist, source_id}]   (Spotify embed scrape / yt-dlp)
    match_tracks(tracks)  → [{title, artist, apple_url, ...}]  (iTunes Search API)

The Apple Music URLs that match can then be handed straight to gamdl via the
existing /api/download endpoint. No extra accounts or API keys are required:

  * Spotify: the public embed page (open.spotify.com/embed/...) renders the full
    track list server-side; we parse its __NEXT_DATA__ JSON.
  * YouTube / YouTube Music: yt-dlp (installed in the venv) extracts flat
    playlists/albums without downloading anything.
  * Apple Music matching: the public iTunes Search API.

Both sources are used strictly to read *metadata* (track titles + artists) that
the user already has in their own library/playlists.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request

from downloader import PROJECT_DIR

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
ITUNES_SEARCH = "https://itunes.apple.com/search"
SPOTIFY_EMBED = "https://open.spotify.com/embed"
YTDLP_MIN = (2024, 1, 0)

# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------
SPOTIFY_RE = re.compile(r"open\.spotify\.com/(?P<kind>album|playlist|track)/(?P<id>[A-Za-z0-9]+)", re.I)
YT_RE = re.compile(r"(?:youtube\.com|music\.youtube\.com|youtu\.be)/(?P<kind>watch|playlist|browse|shorts)?", re.I)


def parse_url(url: str) -> dict | None:
    """Identify the source service and extract (kind, id) from a share link."""
    url = url.strip()
    m = SPOTIFY_RE.search(url)
    if m:
        return {"source": "spotify", "kind": m.group("kind"), "id": m.group("id")}
    if "youtube.com" in url or "youtu.be" in url:
        # youtube watch?/playlist?list= / music browse
        kind = "watch"
        ytid = None
        parsed = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parsed.query)
        if q.get("list"):
            kind, ytid = ("playlist", q["list"][0])
        elif q.get("v"):
            kind, ytid = ("watch", q["v"][0])
        elif "music.youtube.com" in url and "/browse/" in url:
            kind = "browse"
            ytid = url.split("/browse/")[-1].split("?")[0].strip("/")
        elif "/playlist/" in url:
            kind, ytid = ("playlist", url.split("/playlist/")[-1].split("?")[0].strip("/"))
        elif parsed.path.startswith("/shorts/"):
            kind, ytid = ("watch", parsed.path.split("/")[-1])
        if ytid:
            return {"source": "youtube", "kind": kind, "id": ytid}
    return None


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------
def _http_get_json(url: str, timeout: int = 25) -> dict | None:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None


def _http_get_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def resolve_spotify(kind: str, spot_id: str) -> tuple[str, list[dict]]:
    """Fetch the track list from Spotify's public embed page.

    Returns (title, [{title, artist, source_id}]).
    """
    page = _http_get_text(f"{SPOTIFY_EMBED}/{kind}/{spot_id}")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
    if not m:
        raise RuntimeError("Spotify didn't return track data — the link may be private or invalid.")
    data = json.loads(m.group(1))
    ent = (
        data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity", {})
    )
    track_list = ent.get("trackList") or []
    if not track_list:
        raise RuntimeError(f"Couldn't find tracks — is the {kind} public? (embed returned no track list)")
    title = ent.get("name") or spot_id
    tracks = []
    for t in track_list:
        ttitle = (t.get("title") or "").strip()
        if not ttitle:
            continue
        artist = (t.get("subtitle") or "").replace("\xa0", " ").strip()
        tracks.append({
            "title": ttitle,
            "artist": artist,
            "source_id": (t.get("uri") or "").replace("spotify:track:", ""),
        })
    return title, tracks


# ---------------------------------------------------------------------------
# YouTube / YouTube Music (via yt-dlp)
# ---------------------------------------------------------------------------
_YT_ARTIST_RE = re.compile(r"^(.+?)\s*[-–—]\s*(.+)$")
_YT_TAG_RE = re.compile(r"\s*(\[[^\]]*\]|\([^)]*(?:audio|official|video|lyric)[^)]*\)|\(official[^)]*\)|\(hd\))\s*$", re.I)


def _clean_yt_title(raw: str) -> tuple[str, str]:
    """Split 'Artist - Song [tag]' into (artist, title)."""
    raw = re.sub(r"\s*\[[^\]]*\]\s*$", "", raw or "")
    m = _YT_ARTIST_RE.match(raw)
    if m:
        artist = m.group(1).strip()
        title = m.group(2).strip()
    else:
        artist, title = "", raw.strip()
    title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    return artist, title


def resolve_youtube(url: str) -> tuple[str, list[dict]]:
    """Extract the track list from a YouTube/YouTube Music URL with yt-dlp (flat, no download)."""
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp is missing — run: .venv/bin/pip install yt-dlp")

    # Ask yt-dlp to read the playlist/video metadata only (never downloads audio).
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("yt-dlp couldn't read that link.")

    title = info.get("title") or "YouTube link"
    entries = info.get("entries")
    if not entries:  # single video
        artist, ttitle = _clean_yt_title(info.get("title") or "")
        return title, [{
            "title": ttitle or (info.get("title") or ""),
            "artist": artist or (info.get("uploader") or info.get("channel") or ""),
            "source_id": info.get("id") or "",
        }]

    tracks = []
    for e in entries:
        if not e:
            continue
        raw = e.get("title") or ""
        artist, ttitle = _clean_yt_title(raw)
        if not artist:
            artist = e.get("artist") or e.get("uploader") or e.get("channel") or ""
        if ttitle:
            tracks.append({"title": ttitle, "artist": artist.strip(), "source_id": e.get("id") or ""})
    if not tracks:
        raise RuntimeError("No tracks found in that playlist.")
    return title, tracks


# ---------------------------------------------------------------------------
# Apple Music matching (iTunes Search API)
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    """Lowercase and strip punctuation so 'Weird Fishes / Arpeggi' == 'Weird Fishes/Arpeggi'."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _score_match(result: dict, title: str, artist: str) -> int:
    rname = (result.get("trackName") or "").strip()
    rart = (result.get("artistName") or "").strip()
    t = title.strip()
    a = artist.strip()
    nr, nt, nart, na = _norm(rname), _norm(t), _norm(rart), _norm(a)
    s = 0
    if nr == nt:
        s += 3
    elif nt in nr or nr in nt:
        s += 1
    if na:
        a_parts = [p.strip() for p in nart.split(",")] + [nart]
        if any(na == p or na in p or p in na for p in a_parts):
            s += 2
    return s


def search_apple(title: str, artist: str, country: str = "US") -> dict | None:
    """Best-match a track against the iTunes Search API, or None."""
    candidates = []
    terms = [f"{artist} {title}".strip()] if artist else []
    if title not in terms:
        terms.append(title)
    for term in terms:
        q = urllib.parse.urlencode({"term": term, "entity": "song", "limit": 5, "country": country})
        data = _http_get_json(f"{ITUNES_SEARCH}?{q}")
        if not data:
            continue
        for r in data.get("results", []):
            candidates.append((_score_match(r, title, artist), r))
        if candidates:
            break  # good enough — don't over-fetch
    candidates.sort(key=lambda c: c[0], reverse=True)
    best_score, best = candidates[0]
    if best_score >= 3:
        view_url = (best.get("trackViewUrl") or "").replace("&uo=4", "")
        return {
            "apple_id": best.get("trackId"),
            "apple_url": view_url,
            "apple_name": best.get("trackName"),
            "apple_artist": best.get("artistName"),
            "apple_album": best.get("collectionName"),
            "score": best_score,
        }
    return None


def match_tracks(tracks: list[dict], country: str = "US") -> list[dict]:
    """Return tracks enriched with their Apple Music match (or None)."""
    out = []
    for t in tracks:
        m = search_apple(t["title"], t["artist"], country=country)
        out.append({**t, **({"match": m} if m else {"match": None})})
    return out


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def preview(url: str) -> dict:
    """Full pipeline: resolve the link, then match every track on Apple Music."""
    parsed = parse_url(url)
    if not parsed:
        raise ValueError("That doesn't look like a Spotify or YouTube Music link.")
    if parsed["source"] == "spotify":
        title, tracks = resolve_spotify(parsed["kind"], parsed["id"])
        source = "spotify"
    else:
        title, tracks = resolve_youtube(url)
        source = "youtube"

    matched = match_tracks(tracks)
    ok = sum(1 for t in matched if t.get("match"))
    return {
        "source": source,
        "title": title,
        "url": url,
        "total": len(matched),
        "matched": ok,
        "unmatched": len(matched) - ok,
        "tracks": matched,
    }


if __name__ == "__main__":  # quick CLI smoke test:  python3 migrate.py <url>
    import sys
    res = preview(sys.argv[1])
    print(f"{res['source']}: {res['title']} — {res['matched']}/{res['total']} matched")
    for t in res["tracks"]:
        m = t.get("match")
        mark = "✓" if m else "✗"
        print(f"  {mark} {t['artist']} - {t['title']}" + (f"  →  {m['apple_artist']} - {m['apple_name']}" if m else ""))
