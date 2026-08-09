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
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
ITUNES_SEARCH = "https://itunes.apple.com/search"
SPOTIFY_EMBED = "https://open.spotify.com/embed"
MAX_TRACKS = 150  # preview cap — protects the app from gigantic playlists

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


# ---------------------------------------------------------------------------
# Apple Music link preview (track counts before downloading)
# ---------------------------------------------------------------------------
# Anchored at the start so a crafted URL can't smuggle a music.apple.com
# substring past the check and point the fetch at an arbitrary host.
APPLE_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?music\.apple\.com/[^/]+/(?P<kind>song|album|playlist|artist|music-video)/(?P<slug>[^/]+)/(?P<id>[A-Za-z0-9.]+)",
    re.I,
)
# Your-Library URLs: music.apple.com/{cc}/library/{playlist|albums|songs|music-videos}/{id}
LIBRARY_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?music\.apple\.com(?:/[a-z]{2})?/library/(?P<kind>playlist|albums|songs|music-videos)/(?P<id>[a-z]\.[A-Za-z0-9]+)",
    re.I,
)


def parse_library_url(url: str) -> dict | None:
    """Extract (kind, url) from an Apple Music "Your Library" share link.

    gamdl natively downloads library URLs (library-songs/albums/playlists), so
    we pass them straight through — this just gives the UI a nice chip and an
    ownership check instead of an "unknown link" error.
    """
    m = LIBRARY_URL_RE.search(url.strip())
    if not m:
        return None
    base = url.strip().split("?", 1)[0]
    return {"kind": "library", "library_kind": m.group("kind"), "url": base if base.startswith("http") else "https://" + base}
JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S,
)


def parse_apple_url(url: str) -> dict | None:
    """Extract (kind, page_url) from an Apple Music share link."""
    m = APPLE_URL_RE.search(url)
    if not m:
        return None
    kind = m.group("kind").lower()
    # A song link is an album URL with ?i=<trackId>.
    if kind == "song" or ("?i=" in url and kind in ("album", "playlist")):
        return {"kind": "song", "url": url}
    if kind == "music-video":
        return {"kind": "video", "url": url}
    # Album / playlist / artist: strip any ?i=<track> song param so we count
    # the whole container, not one track. Keep the scheme — urllib needs it.
    base = url.split("?", 1)[0].strip()
    return {"kind": kind, "url": base if base.startswith("http") else "https://" + base}


# Small TTL memo for apple_preview: the UI debounces, but paste/edit cycles can
# still hit Apple's pages repeatedly, and Apple rate-limits (429). 1 hour is
# plenty for a track-count peek.
_PREVIEW_CACHE: dict[str, tuple[float, dict]] = {}
_PREVIEW_TTL = 3600.0


def _cached_preview(url: str, fetcher):
    """Memoize a fetch result for an hour; only successful results are cached,
    so a transient network error isn't served from cache."""
    now = time.monotonic()
    hit = _PREVIEW_CACHE.get(url)
    if hit and now - hit[0] < _PREVIEW_TTL:
        return hit[1]
    result = fetcher()
    if result.get("ok"):
        _PREVIEW_CACHE[url] = (now, result)
    if len(_PREVIEW_CACHE) > 400:  # bounded
        for k in list(_PREVIEW_CACHE):
            if now - _PREVIEW_CACHE[k][0] >= _PREVIEW_TTL:
                del _PREVIEW_CACHE[k]
    return result


def _jsonld_blocks(html: str) -> list[dict]:
    """Parse every application/ld+json block out of an Apple page."""
    blocks = []
    for m in JSONLD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            blocks.extend(data)
        elif isinstance(data, dict):
            blocks.append(data)
    return blocks


def apple_preview(url: str) -> dict:
    """Peek at an Apple Music link: what it is and how many tracks it has.

    Apple renders SEO JSON-LD (MusicPlaylist / MusicAlbum / …) straight into
    the public page, so we can read the title + track count with zero auth —
    no cookies, no API keys. Used by the UI to show "playlist · 47 tracks"
    before you commit to a big download. Artist links have no finite count.
    Cached for an hour (see _cached_preview) to stay under Apple's rate limits.
    """
    parsed = parse_library_url(url)
    if parsed:
        # Library URLs are passed straight to gamdl; no JSON-LD track count.
        return _cached_preview(parsed["url"], lambda: {
            "ok": True,
            "kind": "library",
            "library_kind": parsed["library_kind"],
            "title": "Your Library",
            "track_count": None,
        })
    parsed = parse_apple_url(url)
    if not parsed:
        return {"ok": False, "error": "Not an Apple Music link."}
    return _cached_preview(parsed["url"], lambda: _apple_preview_fetch(parsed))


def _apple_preview_fetch(parsed: dict) -> dict:
    """Uncached fetch+parse half of apple_preview."""
    url = parsed["url"]
    try:
        html = _http_get_text(parsed["url"], timeout=20)
    except OSError as e:
        return {"ok": False, "error": f"Could not reach Apple Music: {e}"}

    blocks = _jsonld_blocks(html)
    wanted = {
        "album": "MusicAlbum",
        "playlist": "MusicPlaylist",
        "artist": "MusicGroup",
        "song": "MusicComposition",
        "video": "MusicVideo",
    }
    target = wanted.get(parsed["kind"])
    for b in blocks:
        t = b.get("@type")
        if isinstance(t, list):
            t = t[0] if t else None
        if t != target:
            continue
        # Apple uses "track" on playlists but "tracks" on albums. Artist pages
        # carry a *featured* track list, not the discography gamdl will fetch,
        # so we never show a (misleading) count for artists.
        tracks = b.get("track", b.get("tracks"))
        count = None
        if parsed["kind"] != "artist":
            if isinstance(tracks, list):
                count = len(tracks)
            elif isinstance(tracks, dict):
                count = 1
        artist = None
        by_artist = b.get("byArtist")
        if isinstance(by_artist, dict):
            artist = by_artist.get("name")
        elif isinstance(by_artist, list) and by_artist:
            artist = by_artist[0].get("name") if isinstance(by_artist[0], dict) else None
        return {
            "ok": True,
            "kind": parsed["kind"],
            "title": b.get("name"),
            "artist": artist,
            "track_count": count,
        }
    # Page loaded but JSON-LD didn't match (region redirects etc.) — still
    # report the kind so the UI can show something useful.
    return {"ok": True, "kind": parsed["kind"], "title": None, "track_count": None}


def resolve_spotify(kind: str, spot_id: str) -> tuple[str, list[dict]]:
    """Fetch the track list from Spotify's public embed page.

    Returns (title, [{title, artist, source_id}]).
    """
    if kind == "track":
        raise ValueError("Single Spotify tracks can't be previewed — paste an album or playlist link instead.")
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
            "preview_url": best.get("previewUrl"),  # 30s snippet, free
            "score": best_score,
        }
    return None


def apple_preview_url(url: str, country: str = "US") -> dict | None:
    """Resolve an Apple Music link to a 30-second preview URL (for in-app
    playback before downloading). Returns None when nothing matches."""
    peek = apple_preview(url)
    if not peek.get("ok"):
        return None
    title = (peek.get("title") or "").strip()
    artist = (peek.get("artist") or "").strip()
    if not title:
        return None
    m = search_apple(title, artist, country=country)
    if not m or not m.get("preview_url"):
        return None
    return {"preview_url": m["preview_url"], "title": m.get("apple_name"), "artist": m.get("apple_artist")}


# ---------------------------------------------------------------------------
# New-release tracker (iTunes Search API, album entity)
# ---------------------------------------------------------------------------
def recent_albums(artist: str, country: str = "US", days: int = 90) -> list[dict]:
    """Albums by an artist from the last `days` days (iTunes Search API)."""
    q = urllib.parse.urlencode({"term": artist, "entity": "album", "limit": 10, "country": country})
    data = _http_get_json(f"{ITUNES_SEARCH}?{q}")
    if not data:
        return []
    cutoff = time.time() - days * 86400
    out = []
    for r in data.get("results", []):
        if (r.get("wrapperType") or "") != "collection":
            continue
        release = (r.get("releaseDate") or "")[:10]
        if not release:
            continue
        try:
            if time.mktime(time.strptime(release, "%Y-%m-%d")) < cutoff:
                continue
        except ValueError:
            continue
        out.append({
            "name": r.get("collectionName"),
            "artist": r.get("artistName"),
            "release_date": release,
            "track_count": r.get("trackCount"),
            "url": (r.get("collectionViewUrl") or "").replace("&uo=4", ""),
        })
    return out


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
def preview(url: str, country: str = "US") -> dict:
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

    if len(tracks) > MAX_TRACKS:
        tracks = tracks[:MAX_TRACKS]

    matched = match_tracks(tracks, country=country)
    ok = sum(1 for t in matched if t.get("match"))
    return {
        "source": source,
        "title": title,
        "url": url,
        "total": len(matched),
        "matched": ok,
        "unmatched": len(matched) - ok,
        "truncated": len(tracks) == MAX_TRACKS,
        "tracks": matched,
    }


# ---------------------------------------------------------------------------
# Apple Music / iTunes library.xml import
# ---------------------------------------------------------------------------
def parse_library_xml(path: str) -> dict:
    """Parse an iTunes/Apple Music library.xml (the XML plist from File →
    Library → Export Library). Returns a summary: how many tracks, and the
    playlist names+counts so the UI can let the user pick one.

    Raises ValueError for unreadable/invalid files.
    """
    import plistlib

    try:
        with open(path, "rb") as fh:
            data = plistlib.load(fh)
    except FileNotFoundError:
        raise ValueError(f"File not found: {path}")
    except (plistlib.InvalidFileException, ValueError, OSError) as e:
        raise ValueError(f"Not a valid library.xml: {e}")
    if not isinstance(data, dict):
        raise ValueError("Not a valid library.xml (expected a plist dictionary).")

    tracks = data.get("Tracks") or {}
    playlists = []
    for p in data.get("Playlists") or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("Name") or "").strip()
        items = p.get("Playlist Items") or []
        if name and name.lower() not in ("library", "music", "downloaded"):
            playlists.append({"name": name, "count": len(items)})
    return {
        "ok": True,
        "track_count": len(tracks),
        "playlists": sorted(playlists, key=lambda p: p["name"].lower()),
    }


def import_library_tracks(path: str, playlist: str = "", country: str = "US", limit: int = MAX_TRACKS) -> tuple[list[dict], bool]:
    """Read tracks from a library.xml (optionally filtered to one playlist) and
    match each against the Apple Music catalog. Returns (tracks, truncated) —
    `truncated` is True when the cap cut the list, mirroring preview semantics.
    """
    import plistlib

    with open(path, "rb") as fh:
        data = plistlib.load(fh)
    tracks = data.get("Tracks") or {}

    ids: list[str] = []
    if playlist:
        chosen = None
        for p in data.get("Playlists") or []:
            if (p.get("Name") or "").strip().lower() == playlist.strip().lower():
                chosen = p
                break
        if chosen is None:
            raise ValueError(f"Playlist '{playlist}' not found in that library.")
        for item in chosen.get("Playlist Items") or []:
            tid = item.get("Track ID")
            if tid is not None:
                ids.append(str(tid))
    else:
        ids = [str(t) for t in tracks.keys()]

    truncated = len(ids) > limit
    matched: list[dict] = []
    for tid in ids[:limit]:
        t = tracks.get(tid)
        if not isinstance(t, dict):
            continue
        title = (t.get("Name") or "").strip()
        artist = (t.get("Artist") or "").strip()
        album = (t.get("Album") or "").strip()
        if not title:
            continue
        m = search_apple(title, artist, country=country)
        matched.append({
            "title": title,
            "artist": artist,
            "album": album,
            "source_id": tid,
            "match": m if m else None,
        })
    return matched, truncated


def notify_webhook(url: str, title: str, body: str) -> None:
    """POST a plain-text notification to a webhook (ntfy.sh, Pushover via
    a bridge, Slack/Teams incoming webhook, or anything that accepts JSON).
    Best-effort — never raises."""
    if not url:
        return
    import json as _json
    try:
        payload = _json.dumps({"title": title, "message": body}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "music-high-res"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8):
            pass
    except OSError:
        pass


if __name__ == "__main__":  # quick CLI smoke test:  python3 migrate.py <url>
    import sys
    res = preview(sys.argv[1])
    print(f"{res['source']}: {res['title']} — {res['matched']}/{res['total']} matched")
    for t in res["tracks"]:
        m = t.get("match")
        mark = "✓" if m else "✗"
        print(f"  {mark} {t['artist']} - {t['title']}" + (f"  →  {m['apple_artist']} - {m['apple_name']}" if m else ""))
