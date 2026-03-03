"""
KaraokeNerds community version detection service.

Scrapes karaokenerds.com to check if a song already has community-approved
karaoke versions available on YouTube. Ported from kjbox/kj-controller/karaoke_nerds.py.
"""

import logging
import re
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SEARCH_URL = "https://karaokenerds.com/Search"
REQUEST_TIMEOUT = 8
USER_AGENT = "NomadKaraokeGen/1.0"

# In-memory TTL cache: { cache_key: (expiry_timestamp, data) }
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour


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


def _clean_youtube_url(url: str) -> str:
    """Strip playlist params from YouTube URLs, keep just the video URL."""
    return re.sub(r"&list=[^&]*", "", url)


def _parse_single_track(li) -> dict | None:
    """Parse a single track <li> element from karaokenerds search results."""
    # Brand name: first <a> in the li
    brand_link = li.find("a")
    brand_name = brand_link.get_text(strip=True) if brand_link else ""

    # Brand code: text inside .badge span
    badge = li.find("span", class_="badge")
    brand_code = ""
    if badge:
        brand_code = badge.get_text(strip=True)

    # YouTube URL: link containing youtube.com
    youtube_url = None
    for a in li.find_all("a", href=True):
        href = a["href"]
        if "youtube.com" in href:
            youtube_url = _clean_youtube_url(href)
            break

    # Community: presence of img.check
    is_community = bool(li.find("img", class_="check"))

    if not youtube_url:
        return None

    return {
        "brand_name": brand_name,
        "brand_code": brand_code,
        "youtube_url": youtube_url,
        "is_community": is_community,
    }


def _parse_tracks(details_row) -> list[dict]:
    """Parse track list items from a details row."""
    tracks = []
    for li in details_row.find_all("li", class_="track"):
        track = _parse_single_track(li)
        if track:
            tracks.append(track)
    return tracks


def parse_results(html: str) -> list[dict]:
    """Parse karaokenerds.com search results HTML into structured data."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    tbody = table.find("tbody")
    if not tbody:
        return []

    songs = []
    rows = tbody.find_all("tr", recursive=False)

    i = 0
    while i < len(rows):
        row = rows[i]

        # Song rows have class "group"
        if "group" not in row.get("class", []):
            i += 1
            continue

        # Extract title and artist from the song row
        cells = row.find_all("td")
        if len(cells) < 3:
            i += 1
            continue

        title_link = cells[0].find("a")
        artist_link = cells[1].find("a")
        title = title_link.get_text(strip=True) if title_link else ""
        artist = artist_link.get_text(strip=True) if artist_link else ""

        # The next row should be the details row with tracks
        tracks = []
        if i + 1 < len(rows):
            details_row = rows[i + 1]
            if "details" in details_row.get("class", []):
                tracks = _parse_tracks(details_row)
                i += 2
            else:
                i += 1
        else:
            i += 1

        if title:
            songs.append({
                "title": title,
                "artist": artist,
                "tracks": tracks,
            })

    return songs


async def check_community_versions(artist: str, title: str) -> dict:
    """
    Check if a song has community-approved karaoke versions on karaokenerds.

    Returns a dict with:
      - has_community: bool
      - songs: list of matched songs with community tracks
      - best_youtube_url: URL of the top community version (if any)
    """
    query = f"{artist} {title}"
    cache_key = f"community:{query.lower().strip()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    params = urlencode({"query": query, "webFilter": "OnlyWeb"})
    url = f"{SEARCH_URL}?{params}"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"KaraokeNerds search failed for '{query}': {e}")
        result = {"has_community": False, "songs": [], "best_youtube_url": None}
        _cache_set(cache_key, result)
        return result

    songs = parse_results(resp.text)

    # Filter to only songs that have community tracks with YouTube URLs
    community_songs = []
    best_youtube_url = None

    for song in songs:
        community_tracks = [t for t in song["tracks"] if t["is_community"]]
        if community_tracks:
            community_songs.append({
                "title": song["title"],
                "artist": song["artist"],
                "community_tracks": community_tracks,
            })
            if best_youtube_url is None:
                best_youtube_url = community_tracks[0]["youtube_url"]

    result = {
        "has_community": len(community_songs) > 0,
        "songs": community_songs,
        "best_youtube_url": best_youtube_url,
    }
    _cache_set(cache_key, result)
    return result
