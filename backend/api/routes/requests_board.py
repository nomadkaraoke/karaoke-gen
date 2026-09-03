"""
Public song-request voting board API (requests.nomadkaraoke.com).

Anyone signed in with an email magic link can submit a song request (auto-corrected
artist/title) and cast one vote per calendar day. The board itself (GET /requests) is
readable without auth so it can be linked from YouTube descriptions and browsed freely.

Phase 2 (daily auto-picker, free-credit grant, ownership handoff, voter emails) is not
here — see docs/archive/2026-09-02-requests-voting-board-plan.md.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from backend.api.dependencies import require_auth, security, get_token_from_request
from backend.services.auth_service import AuthResult, get_auth_service
from backend.services.match_judge.classifier import normalize_for_match
from backend.models.song_request import (
    BoardResponse,
    DailyVoteStatus,
    SongRequest,
    SongRequestPublic,
    SubmitRequestBody,
    SubmitResponse,
    VoteBody,
)
from backend.services.song_request_service import (
    RequestNotFound,
    SubmissionRateLimited,
    get_song_request_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests-board", tags=["requests-board"])


async def optional_user_email(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """Resolve the caller's email if a valid token is present, else None.

    Unlike require_auth this never raises — the board is publicly viewable, we just
    annotate the viewer's own vote when they happen to be signed in.
    """
    token = await get_token_from_request(request, credentials, None)
    if not token:
        return None
    try:
        result = get_auth_service().validate_token_full(token)
        return result.user_email if result.is_valid else None
    except Exception:
        return None


def _was_corrected(req: SongRequest) -> bool:
    return (
        normalize_for_match(req.artist_raw) != normalize_for_match(req.artist)
        or normalize_for_match(req.title_raw) != normalize_for_match(req.title)
    )


def _to_public(req: SongRequest, your_vote: Optional[int] = None) -> SongRequestPublic:
    return SongRequestPublic(
        id=req.id,
        artist=req.artist,
        title=req.title,
        status=req.status,
        source=req.source,
        vote_count=req.vote_count,
        created_at=req.created_at.isoformat(),
        youtube_url=req.youtube_url,
        was_corrected=_was_corrected(req),
        your_vote=your_vote,
    )


@router.get("/requests", response_model=BoardResponse)
async def list_requests(
    viewer_email: Optional[str] = Depends(optional_user_email),
):
    """The ranked board: active requests + recently published, with the caller's vote."""
    service = get_song_request_service()
    active = service.list_active()
    published = service.list_published()

    daily_vote = service.get_daily_vote(viewer_email) if viewer_email else None
    voted_request_id = daily_vote.request_id if daily_vote else None
    voted_value = daily_vote.value if daily_vote else None

    return BoardResponse(
        requests=[
            _to_public(r, your_vote=voted_value if r.id == voted_request_id else None)
            for r in active
        ],
        published=[_to_public(r) for r in published],
        voted_today=bool(daily_vote) if viewer_email is not None else None,
        your_vote_request_id=voted_request_id,
    )


@router.post("/requests", response_model=SubmitResponse)
async def submit_request(
    body: SubmitRequestBody,
    auth_result: AuthResult = Depends(require_auth),
):
    """Submit a song request (requires sign-in). Auto-corrects artist/title and dedupes."""
    if not auth_result.user_email:
        raise HTTPException(status_code=403, detail="A signed-in email is required to submit.")

    service = get_song_request_service()
    try:
        request, already_existed, canonical_artist, canonical_title = await service.submit_request(
            auth_result.user_email, body.artist, body.title
        )
    except SubmissionRateLimited:
        raise HTTPException(
            status_code=429,
            detail="You've submitted a lot today — please come back tomorrow.",
        )

    daily = service.get_daily_vote(auth_result.user_email)
    your_vote = daily.value if daily and daily.request_id == request.id else None
    return SubmitResponse(
        status="already_exists" if already_existed else "created",
        request=_to_public(request, your_vote=your_vote),
        canonical_artist=canonical_artist,
        canonical_title=canonical_title,
        was_corrected=_was_corrected(request),
    )


@router.post("/requests/{request_id}/vote", response_model=SongRequestPublic)
async def vote(
    request_id: str,
    body: VoteBody,
    auth_result: AuthResult = Depends(require_auth),
):
    """Cast/move/undo your single daily vote on a request."""
    if not auth_result.user_email:
        raise HTTPException(status_code=403, detail="A signed-in email is required to vote.")

    service = get_song_request_service()
    try:
        service.cast_vote(auth_result.user_email, request_id, body.direction)
    except RequestNotFound:
        raise HTTPException(status_code=404, detail="That request no longer exists.")

    updated = service.get_request(request_id)
    if not updated:
        raise HTTPException(status_code=404, detail="That request no longer exists.")
    daily = service.get_daily_vote(auth_result.user_email)
    your_vote = daily.value if daily and daily.request_id == request_id else None
    return _to_public(updated, your_vote=your_vote)


@router.get("/me", response_model=DailyVoteStatus)
async def my_daily_status(
    auth_result: AuthResult = Depends(require_auth),
):
    """The caller's daily-vote status (has today's vote been used, and on what)."""
    service = get_song_request_service()
    daily = service.get_daily_vote(auth_result.user_email) if auth_result.user_email else None
    return DailyVoteStatus(
        voted_today=bool(daily),
        request_id=daily.request_id if daily else None,
        value=daily.value if daily else None,
    )
