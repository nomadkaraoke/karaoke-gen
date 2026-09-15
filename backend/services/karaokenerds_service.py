"""
KaraokeNerds community version detection service.

Checks whether a song already has community-approved karaoke versions (free,
directly-playable, on YouTube). This reads OUR OWN daily-refreshed copy of the
KaraokeNerds community catalog — it does NOT scrape or live-query
karaokenerds.com. The catalog is populated by the authorized `kn-data-sync`
export job (the only thing permitted to hit karaokenerds.com) into BigQuery
`karaoke_decide.karaokenerds_community` and mirrored to a gzipped-JSON object in
GCS, which this module loads into an in-process index.

Public API (`check_community_versions` / `check_community_versions_batch`) and
its return shapes are unchanged from the previous scraping implementation, so
callers and the frontend need no changes.
"""

import asyncio
import gzip
import json
import logging
import re
import time
from typing import Any

from google.cloud import storage

from backend.config import settings
from backend.services.kn_brand_names import brand_name_for
from backend.services.match_judge.classifier import normalize_for_match

logger = logging.getLogger(__name__)

# Extract a YouTube video id from any of KaraokeNerds' stored watch-URL forms
# (youtu.be/<id>, /watch?v=<id>, /embed/<id>, /shorts/<id>) so we can emit the
# canonical https://www.youtube.com/watch?v=<id> the old scrape produced.
_YT_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^&]*&)*v=|embed/|shorts/|v/))"
    r"([A-Za-z0-9_-]{11})"
)


def _normalize_youtube_url(watch: str | None) -> str | None:
    """Canonicalize a KaraokeNerds watch URL to youtube.com/watch?v=<id>.

    Returns the canonical URL, or the stripped original if no 11-char id can be
    parsed (never fabricates), or None when empty.
    """
    watch = (watch or "").strip()
    if not watch:
        return None
    m = _YT_ID_RE.search(watch)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return watch

# Every row in the community catalog is, by definition, a community/web track, so
# entries surfaced from it are always flagged as community versions.
_IS_COMMUNITY = True

# In-process index built from the community export, refreshed on a TTL. Structure:
#   { "<norm-artist>\x1f<norm-title>": {"title", "artist", "community_tracks": [...]} }
_index: dict[str, dict[str, Any]] | None = None
_index_expiry: float = 0.0
_index_lock = asyncio.Lock()

# Separator for the (artist, title) match key — a control char that survives
# normalization stripping so it can never appear inside a normalized value.
_KEY_SEP = "\x1f"


def _match_key(artist: str, title: str) -> str:
    return f"{normalize_for_match(artist)}{_KEY_SEP}{normalize_for_match(title)}"


def _build_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build the (artist, title) -> song lookup from raw community rows.

    Each raw row carries ``Artist, Title, Brand, Watch`` (YouTube URL). Rows for
    the same normalized (artist, title) are grouped; their community tracks are
    deduped by (brand, url).
    """
    index: dict[str, dict[str, Any]] = {}
    for row in items:
        artist = (row.get("Artist") or "").strip()
        title = (row.get("Title") or "").strip()
        if not artist or not title:
            continue
        # The export stores the brand *code* (e.g. "NOMAD"); resolve the human
        # name for display and keep the code, matching the old scrape's output.
        brand_code = (row.get("Brand") or "").strip()
        youtube_url = _normalize_youtube_url(row.get("Watch"))

        key = _match_key(artist, title)
        song = index.get(key)
        if song is None:
            # Keep the first-seen display casing for artist/title.
            song = {"title": title, "artist": artist, "community_tracks": [], "_seen": set()}
            index[key] = song

        dedup = (brand_code, youtube_url)
        if dedup in song["_seen"]:
            continue
        song["_seen"].add(dedup)
        song["community_tracks"].append({
            "brand_name": brand_name_for(brand_code),
            "brand_code": brand_code,
            "youtube_url": youtube_url,
            "is_community": _IS_COMMUNITY,
        })

    # Drop the internal dedup bookkeeping before returning.
    for song in index.values():
        song.pop("_seen", None)
    return index


def _load_index() -> dict[str, dict[str, Any]]:
    """Download and parse the community export gzip from GCS (blocking)."""
    client = storage.Client(project=settings.google_cloud_project)
    bucket = client.bucket(settings.kn_community_bucket)
    blob = bucket.blob(settings.kn_community_blob)
    raw = blob.download_as_bytes()
    data = json.loads(gzip.decompress(raw))
    if isinstance(data, dict) and "Items" in data:
        items = data["Items"]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError(f"Unexpected community export shape: {type(data).__name__}")
    index = _build_index(items)
    logger.info(
        "Loaded KaraokeNerds community index: %d songs from gs://%s/%s",
        len(index), settings.kn_community_bucket, settings.kn_community_blob,
    )
    return index


async def _get_index() -> dict[str, dict[str, Any]]:
    """Return the community index, refreshing from GCS when the TTL has expired.

    Failures are non-fatal: a stale index keeps serving; if there is no index
    yet, an empty one is returned (every song reads as "no community version",
    which keeps tracks selectable — the same fail-open behaviour the scraper had).
    """
    global _index, _index_expiry
    if _index is not None and time.monotonic() < _index_expiry:
        return _index

    async with _index_lock:
        # Re-check after acquiring the lock — another coroutine may have loaded it.
        if _index is not None and time.monotonic() < _index_expiry:
            return _index
        try:
            fresh = await asyncio.to_thread(_load_index)
            _index = fresh
            _index_expiry = time.monotonic() + max(60, settings.kn_community_ttl_seconds)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load KaraokeNerds community index: %s", e)
            if _index is None:
                # No prior data — serve empty until the short retry window elapses.
                _index = {}
            # Retry soon whether we're serving empty or a stale index, so an
            # outage doesn't make every request re-attempt the load immediately.
            _index_expiry = time.monotonic() + 60
        return _index


def _lookup(index: dict[str, dict[str, Any]], artist: str, title: str) -> dict:
    """Build a check result for one (artist, title) from the index."""
    song = index.get(_match_key(artist, title))
    if not song or not song.get("community_tracks"):
        return {"has_community": False, "songs": [], "best_youtube_url": None}

    best_youtube_url = None
    for track in song["community_tracks"]:
        if track.get("youtube_url"):
            best_youtube_url = track["youtube_url"]
            break

    return {
        "has_community": True,
        "songs": [{
            "title": song["title"],
            "artist": song["artist"],
            "community_tracks": song["community_tracks"],
        }],
        "best_youtube_url": best_youtube_url,
    }


async def check_community_versions(artist: str, title: str) -> dict:
    """
    Check if a song has community-approved karaoke versions.

    Reads our own community catalog (never karaokenerds.com). Returns a dict with:
      - has_community: bool
      - songs: list of matched songs with community tracks
      - best_youtube_url: URL of the top community version (if any)
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return {"has_community": False, "songs": [], "best_youtube_url": None}
    index = await _get_index()
    return _lookup(index, artist, title)


async def check_community_versions_batch(
    songs: list[dict], concurrency: int = 5
) -> list[dict]:
    """Check community-version availability for many songs (Bulk Mode).

    ``songs`` is a list of ``{"artist": str, "title": str}``. Returns a list in the
    same order, each ``{"artist", "title", "available": bool, "brands": [str],
    "brand_count": int, "versions": [{"brand": str, "url": str}]}``. ``versions`` is
    the per-community-version detail (deduped by brand, first YouTube URL kept) the UI
    uses to render clickable links; ``brands``/``brand_count`` are retained for
    back-compat. Lookups hit our in-process community index (no network per song), so
    ``concurrency`` is accepted for back-compat but no longer meaningful. A missing
    catalog degrades to ``available=False`` (the track simply stays selectable).
    """
    _ = concurrency  # retained for call-site compatibility; lookups are in-memory now
    index = await _get_index()

    def _one(song: dict) -> dict:
        artist = (song.get("artist") or "").strip()
        title = (song.get("title") or "").strip()
        if not artist or not title:
            return {"artist": artist, "title": title, "available": False,
                    "brands": [], "brand_count": 0, "versions": []}
        res = _lookup(index, artist, title)
        versions: list[dict] = []
        versioned_brands: set[str] = set()
        brands: list[str] = []
        for matched in res.get("songs", []):
            for track in matched.get("community_tracks", []):
                name = track.get("brand_name")
                if not name:
                    continue
                if name not in brands:
                    brands.append(name)
                # A clickable version needs a URL; brand still counts as "exists" without one.
                url = track.get("youtube_url")
                if url and name not in versioned_brands:
                    versioned_brands.add(name)
                    versions.append({"brand": name, "url": url})
        return {
            "artist": artist,
            "title": title,
            "available": bool(res.get("has_community")),
            "brands": brands,
            "brand_count": len(brands),
            "versions": versions,
        }

    return [_one(s) for s in songs]


def _reset_index_for_tests() -> None:
    """Clear the module-level cache (used by tests to isolate index state)."""
    global _index, _index_expiry
    _index = None
    _index_expiry = 0.0
