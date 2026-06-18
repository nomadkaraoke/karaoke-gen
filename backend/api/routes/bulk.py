"""Bulk Mode API routes.

Lets a user submit up to 100 karaoke jobs at once, by text (artist/title rows)
or by album (MusicBrainz release lookup). Album tracklists and text rows are
enriched with KaraokeNerds community-version availability so the user can skip
tracks that already exist.

Submission (POST /bulk/submit) and progress (GET /bulk/{batch_id}) live here too
(added in later phases).
"""

import logging
import uuid
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


# --- Submission -------------------------------------------------------------


class BulkSong(BaseModel):
    artist: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    display_artist: Optional[str] = None
    display_title: Optional[str] = None


class BulkSettings(BaseModel):
    auto_select_if_lossless: bool = True
    is_private: bool = False
    skip_audio_edit: bool = True
    skip_customization: bool = True


class BulkSubmitRequest(BaseModel):
    songs: list[BulkSong]
    settings: BulkSettings = Field(default_factory=BulkSettings)


class BulkSubmitResponse(BaseModel):
    batch_id: str
    job_ids: list[str]
    total: int


@router.post("/submit", response_model=BulkSubmitResponse)
async def bulk_submit(
    request: Request,
    body: BulkSubmitRequest,
    auth_result: AuthResult = Depends(require_auth),
):
    """Create one job per song, gate on credits upfront, and dispatch the batch
    search worker. Jobs start parked (auto_download=False) and the worker either
    auto-selects a confident lossless match or leaves them awaiting selection."""
    from backend.config import get_settings
    from backend.exceptions import InsufficientCreditsError
    from backend.models.job import JobCreate
    from backend.services.job_manager import JobManager
    from backend.services.job_defaults_service import resolve_cdg_txt_defaults
    from backend.services.theme_service import get_theme_service
    from backend.services.worker_service import get_worker_service

    n = len(body.songs)
    if n == 0:
        raise HTTPException(status_code=422, detail="At least one song is required")
    if n > MAX_BULK_SONGS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_BULK_SONGS} songs per batch")

    user_email = auth_result.user_email
    is_admin = bool(auth_result.is_admin)

    # Upfront credit gate (1 credit/song minimum). Admins bypass.
    if not is_admin and user_email:
        from backend.services.user_service import get_user_service

        available = get_user_service().check_credits(user_email)
        if available < n:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "insufficient_credits",
                    "message": "You don't have enough credits for this batch.",
                    "credits_available": available,
                    "credits_required": n,
                },
            )

    settings = get_settings()
    theme_service = get_theme_service()
    theme_id = theme_service.get_default_theme_id()
    resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(theme_id, None, None)

    batch_id = uuid.uuid4().hex[:12]
    bulk_settings = body.settings.model_dump()
    job_manager = JobManager()
    job_ids: list[str] = []

    for song in body.songs:
        display_artist = (song.display_artist or song.artist).strip()
        display_title = (song.display_title or song.title).strip()
        request_metadata = {
            "created_from": "bulk",
            "batch_id": batch_id,
            "user_email": user_email,
        }
        job_create = JobCreate(
            artist=display_artist,
            title=display_title,
            theme_id=theme_id,
            enable_cdg=resolved_cdg,
            enable_txt=resolved_txt,
            user_email=user_email,
            audio_search_artist=song.artist.strip(),
            audio_search_title=song.title.strip(),
            auto_download=False,
            is_private=body.settings.is_private,
            brand_prefix=settings.default_brand_prefix,
            enable_youtube_upload=settings.default_enable_youtube_upload,
            youtube_description=settings.default_youtube_description,
            discord_webhook_url=settings.default_discord_webhook_url,
            dropbox_path=settings.default_dropbox_path,
            gdrive_folder_id=settings.default_gdrive_folder_id,
            request_metadata=request_metadata,
        )
        try:
            job = job_manager.create_job(job_create, is_admin=is_admin)
        except InsufficientCreditsError:
            # Ran out mid-batch (e.g. concurrent spend after the gate). Stop here;
            # already-created jobs are valid and will proceed.
            logger.warning("[batch:%s] ran out of credits after %d/%d jobs", batch_id, len(job_ids), n)
            break

        job_manager.update_job(
            job.job_id,
            {
                "state_data.batch_id": batch_id,
                "state_data.bulk_settings": bulk_settings,
                "state_data.bulk_pending_search": True,
            },
        )
        job_ids.append(job.job_id)

    if not job_ids:
        raise HTTPException(status_code=402, detail="No jobs could be created")

    await get_worker_service().trigger_bulk_search_worker(batch_id)
    logger.info("[batch:%s] created %d jobs, dispatched bulk-search worker", batch_id, len(job_ids))

    return BulkSubmitResponse(batch_id=batch_id, job_ids=job_ids, total=len(job_ids))
