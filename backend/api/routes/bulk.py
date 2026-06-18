"""Bulk Mode API routes.

Lets a user submit up to 100 karaoke jobs at once, by text (artist/title rows)
or by album (MusicBrainz release lookup). Album tracklists and text rows are
enriched with KaraokeNerds community-version availability so the user can skip
tracks that already exist.

Submission (POST /bulk/submit) and progress (GET /bulk/{batch_id}) live here too
(added in later phases).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.api.dependencies import require_auth
from backend.services.auth_service import AuthResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bulk", tags=["bulk"])

MAX_BULK_SONGS = 100


# --- Models -----------------------------------------------------------------


class BulkArtist(BaseModel):
    mbid: Optional[str] = None
    name: str
    disambiguation: str = ""
    type: Optional[str] = None
    country: Optional[str] = None


class BulkAlbum(BaseModel):
    release_group_mbid: str
    title: str
    primary_type: Optional[str] = None
    secondary_types: list[str] = []
    first_release_date: str = ""
    is_studio: bool = True


class BulkEdition(BaseModel):
    release_mbid: str
    title: Optional[str] = None
    status: Optional[str] = None
    date: str = ""
    country: Optional[str] = None
    track_count: int = 0


class BulkTrack(BaseModel):
    position: Optional[int] = None
    title: str
    recording_mbid: Optional[str] = None
    length_ms: Optional[int] = None
    is_extra: bool = False
    extra_reason: str = ""
    # Availability (filled from KaraokeNerds); available=True => already exists
    available: bool = False
    brands: list[str] = []


class BulkTracklistResponse(BaseModel):
    release_mbid: Optional[str] = None
    canonical_release_mbid: Optional[str] = None
    title: Optional[str] = None
    date: str = ""
    tracks: list[BulkTrack] = []
    editions: list[BulkEdition] = []


class AvailabilityItem(BaseModel):
    artist: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)


class AvailabilityRequest(BaseModel):
    tracks: list[AvailabilityItem]


class AvailabilityResult(BaseModel):
    artist: str
    title: str
    available: bool
    brands: list[str] = []
    brand_count: int = 0


class AvailabilityResponse(BaseModel):
    results: list[AvailabilityResult]


# --- Album lookup routes ----------------------------------------------------


@router.get("/album/artists", response_model=list[BulkArtist])
async def album_artists(
    request: Request,
    q: str = Query(..., min_length=2, description="Artist name search"),
    limit: int = Query(8, ge=1, le=25),
    auth_result: AuthResult = Depends(require_auth),
):
    """Search MusicBrainz for artists (album-mode step 1)."""
    from backend.services.musicbrainz_service import get_musicbrainz_service

    try:
        results = await get_musicbrainz_service().search_artists(q, limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.warning("MusicBrainz artist search failed: %s", e)
        raise HTTPException(status_code=502, detail="Artist lookup is temporarily unavailable")
    return results


@router.get("/album/albums", response_model=list[BulkAlbum])
async def album_albums(
    request: Request,
    artist_mbid: str = Query(..., min_length=1),
    auth_result: AuthResult = Depends(require_auth),
):
    """List an artist's albums (release-groups) for the album picker."""
    from backend.services.musicbrainz_service import get_musicbrainz_service

    try:
        return await get_musicbrainz_service().get_albums(artist_mbid)
    except Exception as e:  # noqa: BLE001
        logger.warning("MusicBrainz album list failed: %s", e)
        raise HTTPException(status_code=502, detail="Album lookup is temporarily unavailable")


@router.get("/album/tracklist", response_model=BulkTracklistResponse)
async def album_tracklist(
    request: Request,
    artist: str = Query(..., min_length=1, description="Artist name for availability checks"),
    release_group_mbid: Optional[str] = Query(None),
    release_mbid: Optional[str] = Query(None, description="Specific edition to load"),
    auth_result: AuthResult = Depends(require_auth),
):
    """Resolve an album's canonical tracklist (or a specific edition) and enrich
    each track with KaraokeNerds community-version availability."""
    from backend.services.musicbrainz_service import get_musicbrainz_service
    from backend.services.karaokenerds_service import check_community_versions_batch

    if not release_group_mbid and not release_mbid:
        raise HTTPException(status_code=422, detail="release_group_mbid or release_mbid required")

    mb = get_musicbrainz_service()
    try:
        if release_mbid:
            tracklist = await mb.get_release_tracklist(release_mbid)
            tracklist.setdefault("editions", [])
            tracklist.setdefault("canonical_release_mbid", release_mbid)
        else:
            tracklist = await mb.get_album_tracklist(release_group_mbid)
    except Exception as e:  # noqa: BLE001
        logger.warning("MusicBrainz tracklist failed: %s", e)
        raise HTTPException(status_code=502, detail="Tracklist lookup is temporarily unavailable")

    tracks = tracklist.get("tracks", [])
    # Availability enrichment — never fatal.
    try:
        avail = await check_community_versions_batch(
            [{"artist": artist, "title": t["title"]} for t in tracks]
        )
        for t, a in zip(tracks, avail):
            t["available"] = a["available"]
            t["brands"] = a["brands"]
    except Exception as e:  # noqa: BLE001
        logger.warning("Availability enrichment failed (non-fatal): %s", e)

    return BulkTracklistResponse(
        release_mbid=tracklist.get("release_mbid"),
        canonical_release_mbid=tracklist.get("canonical_release_mbid"),
        title=tracklist.get("title"),
        date=tracklist.get("date", ""),
        tracks=[BulkTrack(**t) for t in tracks],
        editions=[BulkEdition(**e) for e in tracklist.get("editions", [])],
    )


@router.post("/availability", response_model=AvailabilityResponse)
async def bulk_availability(
    request: Request,
    body: AvailabilityRequest,
    auth_result: AuthResult = Depends(require_auth),
):
    """Batch KaraokeNerds availability check for text-mode rows."""
    from backend.services.karaokenerds_service import check_community_versions_batch

    if len(body.tracks) > MAX_BULK_SONGS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_BULK_SONGS} tracks per request")

    results = await check_community_versions_batch(
        [{"artist": t.artist, "title": t.title} for t in body.tracks]
    )
    return AvailabilityResponse(results=[AvailabilityResult(**r) for r in results])
