"""
Proxy service for karaoke-decide catalog API.

Forwards artist and track search requests to the karaoke-decide backend,
providing autocomplete data from MusicBrainz + Spotify catalogs.
"""

import logging
import time
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# In-memory TTL cache: { cache_key: (expiry_timestamp, data) }
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes

KARAOKE_DECIDE_BASE_URL = "https://decide.nomadkaraoke.com"
REQUEST_TIMEOUT = 10  # seconds


def _get_base_url() -> str:
    """Get the karaoke-decide API base URL from config or default."""
    return getattr(settings, "karaoke_decide_api_url", None) or KARAOKE_DECIDE_BASE_URL


def _cache_get(key: str) -> Any | None:
    """Get a value from cache if it exists and hasn't expired."""
    if key in _cache:
        expiry, data = _cache[key]
        if time.monotonic() < expiry:
            return data
        del _cache[key]
    return None


def _cache_set(key: str, data: Any, ttl: float = CACHE_TTL_SECONDS) -> None:
    """Store a value in cache with TTL."""
    _cache[key] = (time.monotonic() + ttl, data)


async def search_artists(query: str, limit: int = 10) -> list[dict]:
    """
    Search for artists via karaoke-decide catalog API.

    Returns list of artist dicts with canonical MusicBrainz names.
    Falls back to empty list on any error.
    """
    cache_key = f"artists:{query.lower()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    base_url = _get_base_url()
    url = f"{base_url}/api/catalog/artists"
    params = {"q": query, "limit": limit}

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            # karaoke-decide wraps results: {"artists": [...], "total": N}
            results = data.get("artists", data) if isinstance(data, dict) else data
            _cache_set(cache_key, results)
            return results
    except Exception as e:
        logger.warning(f"Failed to search artists from karaoke-decide: {e}")
        return []


async def search_tracks(query: str, artist: str | None = None, limit: int = 10) -> list[dict]:
    """
    Search for tracks via karaoke-decide catalog API.

    Returns list of track dicts with canonical Spotify/MusicBrainz names.
    Falls back to empty list on any error.
    """
    cache_key = f"tracks:{query.lower()}:{(artist or '').lower()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    base_url = _get_base_url()
    url = f"{base_url}/api/catalog/tracks"
    params: dict[str, Any] = {"q": query, "limit": limit}
    if artist:
        params["artist"] = artist

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            # karaoke-decide wraps results: {"tracks": [...], "total": N}
            results = data.get("tracks", data) if isinstance(data, dict) else data
            _cache_set(cache_key, results)
            return results
    except Exception as e:
        logger.warning(f"Failed to search tracks from karaoke-decide: {e}")
        return []
