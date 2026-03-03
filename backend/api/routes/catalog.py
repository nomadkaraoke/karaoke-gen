"""
Catalog API routes for song/artist lookup and community version detection.

Provides autocomplete data from karaoke-decide's MusicBrainz + Spotify catalog,
and checks karaokenerds.com for existing community karaoke versions.
"""

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.api.dependencies import require_auth
from backend.services.auth_service import AuthResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])

# Simple per-user rate limiting: 20 requests/minute
# { user_email: [timestamp, timestamp, ...] }
_rate_limit_windows: dict[str, list[float]] = {}
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_WINDOW_SECONDS = 60


def _check_rate_limit(user_email: str) -> None:
    """Check per-user rate limit. Raises 429 if exceeded."""
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    # Get or create window for this user
    if user_email not in _rate_limit_windows:
        _rate_limit_windows[user_email] = []

    # Remove expired entries
    timestamps = _rate_limit_windows[user_email]
    _rate_limit_windows[user_email] = [t for t in timestamps if t > window_start]

    if len(_rate_limit_windows[user_email]) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {RATE_LIMIT_MAX_REQUESTS} catalog requests per minute.",
        )

    _rate_limit_windows[user_email].append(now)


# --- Response Models ---


class ArtistResult(BaseModel):
    """Artist search result from MusicBrainz/Spotify catalog."""
    name: str
    mbid: Optional[str] = None
    disambiguation: Optional[str] = None
    artist_type: Optional[str] = None
    spotify_id: Optional[str] = None
    popularity: Optional[int] = None
    genres: Optional[list[str]] = None
    tags: Optional[list[str]] = None


class TrackResult(BaseModel):
    """Track search result from Spotify catalog."""
    track_name: str
    artist_name: str
    track_id: Optional[str] = None
    artist_id: Optional[str] = None
    popularity: Optional[int] = None
    duration_ms: Optional[int] = None
    explicit: Optional[bool] = None


class CommunityTrack(BaseModel):
    """A community-approved karaoke track from karaokenerds."""
    brand_name: str
    brand_code: str
    youtube_url: str
    is_community: bool = True


class CommunityCheckSong(BaseModel):
    """A song with community karaoke tracks."""
    title: str
    artist: str
    community_tracks: list[CommunityTrack]


class CommunityCheckRequest(BaseModel):
    """Request body for community version check."""
    artist: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)


class CommunityCheckResponse(BaseModel):
    """Response for community version check."""
    has_community: bool
    songs: list[CommunityCheckSong]
    best_youtube_url: Optional[str] = None


# --- Routes ---


@router.get("/artists", response_model=list[ArtistResult])
async def search_artists(
    q: str = Query(..., min_length=2, description="Artist name search query"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results"),
    auth_result: AuthResult = Depends(require_auth),
):
    """
    Search for artists by name. Returns canonical MusicBrainz/Spotify artist data
    for autocomplete in job creation forms.
    """
    _check_rate_limit(auth_result.user_email or auth_result.token_id or "unknown")

    from backend.services.catalog_proxy_service import search_artists
    results = await search_artists(q, limit)
    return results


@router.get("/tracks", response_model=list[TrackResult])
async def search_tracks(
    q: str = Query(..., min_length=2, description="Track name search query"),
    artist: Optional[str] = Query(None, description="Filter by artist name"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results"),
    auth_result: AuthResult = Depends(require_auth),
):
    """
    Search for tracks by name, optionally filtered by artist.
    Returns canonical Spotify track data for autocomplete in job creation forms.
    """
    _check_rate_limit(auth_result.user_email or auth_result.token_id or "unknown")

    from backend.services.catalog_proxy_service import search_tracks
    results = await search_tracks(q, artist, limit)
    return results


@router.post("/community-check", response_model=CommunityCheckResponse)
async def check_community_versions(
    body: CommunityCheckRequest,
    auth_result: AuthResult = Depends(require_auth),
):
    """
    Check if a song already has community-approved karaoke versions on YouTube
    via karaokenerds.com. Non-blocking — just informs the user.
    """
    _check_rate_limit(auth_result.user_email or auth_result.token_id or "unknown")

    from backend.services.karaokenerds_service import check_community_versions
    result = await check_community_versions(body.artist, body.title)
    return result
