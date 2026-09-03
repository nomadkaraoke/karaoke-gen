"""Daily free-community-track picker (requests voting board — Phase 2).

Once per UTC day this:
  1. Claims the day atomically (a create-only lock doc) so only one run can ever
     make a track that day — "one free track per day, total".
  2. Picks the highest-priority eligible open request (top net votes, oldest-first,
     skipping community-rejected net<0). Human votes and (future) trending-agent
     submissions feed the same queue — the picker is source-agnostic.
  3. Grants the requester one free credit and submits the job **as that user**
     (non-admin, so the credit is consumed and the job is owned by them), reusing
     the same search → auto-select → download primitives the web flow uses.
  4. Advances the request open → queued → in_progress and records job_id/owner.

Every side effect is idempotent and gated by durable markers on the request +
the per-day lock's ``phase`` so a retried Scheduler delivery or a crash mid-run
can resume the same day without double-granting credits or making two tracks.

Triggered by Cloud Scheduler (noon US Eastern) via POST /api/internal/community-daily-pick.
Master kill-switch: settings.community_daily_pick_enabled (default off — deploys dark).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.config import get_settings
from backend.models.job import JobCreate, JobStatus
from backend.models.song_request import SongRequest
from backend.services.audio_search_service import (
    AudioSearchError,
    AudioSearchService,
    NoResultsError,
)
from backend.services.job_manager import JobManager
from backend.services.song_request_service import get_song_request_service
from backend.services.theme_service import get_theme_service
from backend.services.user_service import get_user_service
from backend.services.worker_service import get_worker_service

logger = logging.getLogger(__name__)

COMMUNITY_CREDIT_REASON = "community_daily_pick"


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def run_daily_pick(dry_run: bool = False) -> Dict[str, Any]:
    """Claim today, pick the top eligible request, and make it (idempotently).

    Args:
        dry_run: shadow mode — logs the pick and records a "skipped" lock but does
            NOT grant credits or create a job. Also used implicitly when the
            master kill-switch is off.

    Returns a summary dict (status + what it did / would have done).
    """
    service = get_song_request_service()
    settings = get_settings()
    date = _utc_today()

    lock, claimed_new = service.claim_day(date)
    if not claimed_new:
        if lock.phase in ("done", "empty", "skipped"):
            logger.info("daily-pick: day %s already resolved (phase=%s)", date, lock.phase)
            return {"status": "already_done", "date": date, "phase": lock.phase,
                    "request_id": lock.request_id, "job_id": lock.job_id}
        # A prior run claimed but did not finish — resume it below.
        logger.info("daily-pick: resuming incomplete day %s (phase=%s)", date, lock.phase)

    shadow = dry_run or not settings.community_daily_pick_enabled

    # Pick the request (resume uses the one already recorded on the lock).
    if lock.request_id:
        request = service.get_request(lock.request_id)
    else:
        request = service.pick_eligible()

    if request is None:
        # Board empty / nothing eligible → nothing happens today (per spec).
        service.update_lock(date, phase="empty", note="no eligible request")
        logger.info("daily-pick: no eligible request for %s; nothing made", date)
        return {"status": "empty", "date": date}

    if shadow:
        reason = "dry_run" if dry_run else "kill_switch_off"
        service.update_lock(date, phase="skipped", request_id=request.id,
                            note=f"shadow ({reason})")
        logger.info(
            "daily-pick[SHADOW:%s]: would pick request %s — %s - %s (net votes=%d)",
            reason, request.id, request.artist, request.title, request.vote_count,
        )
        return {"status": "shadow", "reason": reason, "date": date,
                "request_id": request.id, "artist": request.artist,
                "title": request.title, "vote_count": request.vote_count}

    return await _make_request(service, settings, date, request)


async def _make_request(service, settings, date: str, request: SongRequest) -> Dict[str, Any]:
    """Drive a picked request to in_progress idempotently. Safe to re-enter."""
    owner = (request.owner_email or request.submitted_by).lower()

    # 1) Claim the request: open -> queued (no-op if already advanced).
    if request.status == "open":
        service.transition_status(
            request.id, "open", "queued",
            picked_at=datetime.now(timezone.utc).isoformat(),
        )
    service.update_lock(date, phase="claimed", request_id=request.id, owner_email=owner)

    # 2) Grant the free credit (guarded so a retry can't re-grant).
    request = service.get_request(request.id) or request
    if not request.community_credit_granted:
        ok, balance, msg = get_user_service().add_credits(
            owner, amount=1, reason=COMMUNITY_CREDIT_REASON,
        )
        if not ok:
            logger.error("daily-pick: credit grant failed for %s: %s", owner, msg)
            return {"status": "error", "step": "grant_credit", "date": date,
                    "request_id": request.id, "message": msg}
        service.mark_credit_granted(request.id)
        logger.info("daily-pick: granted 1 credit to %s (balance=%s)", owner, balance)
    service.update_lock(date, phase="credit_granted")

    # 3) Create the job as the requester (non-admin → consumes the granted credit).
    request = service.get_request(request.id) or request
    job_id = request.job_id
    if not job_id:
        try:
            job_id = _create_community_job(request, owner, settings)
        except Exception as e:  # noqa: BLE001
            logger.exception("daily-pick: job creation failed for request %s", request.id)
            return {"status": "error", "step": "create_job", "date": date,
                    "request_id": request.id, "message": str(e)}
        service.set_job_id(request.id, job_id)
    service.update_lock(date, phase="job_created", job_id=job_id)

    # 4) Kick off search + auto-download for the job (idempotent trigger).
    search_ok = await _search_and_download(job_id, request)

    # 5) Advance to in_progress and assign the owner (starts the 24h handoff clock).
    if request.status in ("open", "queued"):
        service.transition_status(request.id, request.status, "in_progress")
    service.assign_owner(request.id, owner)
    service.update_lock(date, phase="done")

    logger.info(
        "daily-pick: made request %s (job %s, owner %s, search_ok=%s)",
        request.id, job_id, owner, search_ok,
    )
    return {"status": "made", "date": date, "request_id": request.id,
            "job_id": job_id, "owner": owner, "search_ok": search_ok,
            "artist": request.artist, "title": request.title}


def _create_community_job(request: SongRequest, owner: str, settings) -> str:
    """Create a PENDING job owned by the requester, mirroring search_audio."""
    theme_id = get_theme_service().get_default_theme_id()
    job_create = JobCreate(
        artist=request.artist,
        title=request.title,
        theme_id=theme_id,
        enable_cdg=settings.default_enable_cdg,
        enable_txt=settings.default_enable_txt,
        brand_prefix=settings.default_brand_prefix,
        enable_youtube_upload=settings.default_enable_youtube_upload,
        youtube_description=settings.default_youtube_description,
        youtube_description_template=settings.default_youtube_description,
        discord_webhook_url=settings.default_discord_webhook_url,
        dropbox_path=settings.default_dropbox_path,
        gdrive_folder_id=settings.default_gdrive_folder_id,
        user_email=owner,
        audio_search_artist=request.artist,
        audio_search_title=request.title,
        auto_download=True,
        review_mode="auto",
        backing_preference="auto",
    )
    job_manager = JobManager()
    job = job_manager.create_job(job_create, is_admin=False)
    job_manager.update_job(job.job_id, {
        "audio_search_artist": request.artist,
        "audio_search_title": request.title,
        "auto_download": True,
        # Tag the job so the publish hook + stale-review exclusion can find it.
        "state_data.community_request_id": request.id,
    })
    return job.job_id


async def _search_and_download(job_id: str, request: SongRequest) -> bool:
    """Run audio search, auto-select the best source, and trigger the download.

    Returns True if the download was triggered. On no-results/search failure the
    job is parked in AWAITING_AUDIO_SELECTION so the owner can pick a source
    manually during their review — the handoff clock still runs.
    """
    job_manager = JobManager()

    # Skip if the job already progressed past search (idempotent re-entry).
    job = job_manager.get_job(job_id)
    if job and job.status not in (
        JobStatus.PENDING, JobStatus.SEARCHING_AUDIO, JobStatus.AWAITING_AUDIO_SELECTION,
    ):
        return True

    # Prepare the theme style (best-effort), then search.
    from backend.workers.bulk_search_worker import _prepare_theme
    if job:
        _prepare_theme(job, job_manager)

    job_manager.transition_to_state(
        job_id=job_id, new_status=JobStatus.SEARCHING_AUDIO, progress=5,
        message=f"Searching for: {request.artist} - {request.title}",
        raise_on_invalid=False,
    )

    audio_search_service = AudioSearchService()
    try:
        results = await audio_search_service.search_async(request.artist, request.title)
    except (NoResultsError, AudioSearchError) as e:
        logger.warning("daily-pick: search failed for job %s (%s); parking", job_id, e)
        job_manager.transition_to_state(
            job_id=job_id, new_status=JobStatus.AWAITING_AUDIO_SELECTION, progress=10,
            message="No automatic audio sources found. Please choose a source.",
            raise_on_invalid=False,
        )
        return False

    results_dicts = [r.to_dict() for r in results]
    store = {"state_data.audio_search_results": results_dicts,
             "state_data.audio_search_count": len(results_dicts)}
    remote_id = getattr(audio_search_service, "last_remote_search_id", None)
    if remote_id:
        store["state_data.remote_search_id"] = remote_id
    job_manager.update_job(job_id, store)

    best_index = audio_search_service.select_best(results)
    from backend.api.routes.audio_search import _validate_and_prepare_selection
    try:
        _validate_and_prepare_selection(job_id=job_id, selection_index=best_index)
    except Exception as e:  # noqa: BLE001
        logger.warning("daily-pick: validate/prepare failed for job %s: %s", job_id, e)
        job_manager.transition_to_state(
            job_id=job_id, new_status=JobStatus.AWAITING_AUDIO_SELECTION, progress=10,
            message="Please choose an audio source.", raise_on_invalid=False,
        )
        return False

    return await get_worker_service().trigger_audio_download_worker(job_id)
