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
import html
import logging
from datetime import datetime, timezone
from typing import Any, Dict

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
COMMUNITY_REVIEW_ADMIN_EMAIL = "andrew@nomadkaraoke.com"


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def run_daily_pick(dry_run: bool = False) -> Dict[str, Any]:
    """Claim today, walk the ranked requests, and make the first one that has no
    existing community karaoke version (idempotently).

    For each candidate in rank order we run the same KaraokeNerds check the job
    flow uses. A candidate that already has a community version is flagged for
    Andrew's review (kept off auto-make, emailed to him) and we move on to the
    next — so a fresh, non-duplicate free track still ships that day.

    Args:
        dry_run: shadow mode — logs what it would make/flag but persists nothing.
            Also used implicitly when the master kill-switch is off.
    """
    service = get_song_request_service()
    settings = get_settings()
    date = _utc_today()

    # Shadow mode (explicit dry-run OR the master kill-switch is off) must NOT
    # claim the day or persist flags — otherwise a manual dry-run would burn the
    # per-day lock and block the real scheduler run. It only peeks.
    shadow = dry_run or not settings.community_daily_pick_enabled
    if shadow:
        reason = "dry_run" if dry_run else "kill_switch_off"
        chosen, flagged = await _select_candidate(service, shadow=True)
        logger.info(
            "daily-pick[SHADOW:%s]: would make %s; would flag %d existing-version dup(s)",
            reason, f"{chosen.artist} - {chosen.title}" if chosen else "nothing", len(flagged),
        )
        return {"status": "shadow", "reason": reason, "date": date,
                "request_id": chosen.id if chosen else None,
                "artist": chosen.artist if chosen else None,
                "title": chosen.title if chosen else None,
                "would_flag": [f["request"].id for f in flagged]}

    # Real path — claim the day atomically so at most one run proceeds.
    lock, claimed_new = service.claim_day(date)
    if not claimed_new:
        if lock.phase in ("done", "empty"):
            logger.info("daily-pick: day %s already resolved (phase=%s)", date, lock.phase)
            return {"status": "already_done", "date": date, "phase": lock.phase,
                    "request_id": lock.request_id, "job_id": lock.job_id}
        logger.info("daily-pick: resuming incomplete day %s (phase=%s)", date, lock.phase)

    # Resume: a prior run already chose a request — finish making it.
    if lock.request_id:
        request = service.get_request(lock.request_id)
        if request is None:
            service.update_lock(date, phase="empty", note="chosen request vanished")
            return {"status": "empty", "date": date}
        return await _make_request(service, settings, date, request)

    # Fresh: walk candidates, flag existing-version dups, pick the first clean one.
    chosen, flagged = await _select_candidate(service, shadow=False)
    newly_flagged = [f for f in flagged if f["newly"]]
    if newly_flagged:
        _email_admin_flagged(newly_flagged)

    if chosen is None:
        service.update_lock(
            date, phase="empty", note=f"no clean candidate ({len(flagged)} flagged)"
        )
        logger.info(
            "daily-pick: nothing makeable for %s (%d flagged for review)", date, len(flagged)
        )
        return {"status": "empty", "date": date,
                "flagged": [f["request"].id for f in flagged]}

    result = await _make_request(service, settings, date, chosen)
    result["flagged"] = [f["request"].id for f in flagged]
    return result


async def _select_candidate(service, shadow: bool):
    """Walk ranked pick-candidates, KaraokeNerds-checking each. Returns
    (chosen_request | None, flagged) where flagged is a list of
    {request, versions, newly} for candidates that already have a community
    version. In real mode dups are persisted as review_state=pending (idempotent;
    ``newly`` is True only the first time). Shadow mode persists nothing."""
    from backend.services.karaokenerds_service import check_community_versions

    chosen = None
    flagged = []
    for req in service.list_pick_candidates():
        try:
            result = await check_community_versions(req.artist, req.title)
        except Exception:  # noqa: BLE001 (defensive — the service already swallows)
            logger.warning(
                "daily-pick: community check errored for %s - %s; treating as clean",
                req.artist, req.title, exc_info=True,
            )
            result = {"has_community": False}

        if result.get("has_community"):
            versions = _versions_payload(result)
            newly = True if shadow else service.set_review_pending(req.id, versions)
            logger.info(
                "daily-pick: %s existing community version for %s — %s - %s",
                "would flag" if shadow else "flagged", req.id, req.artist, req.title,
            )
            flagged.append({"request": req, "versions": versions, "newly": newly})
            continue

        chosen = req
        break
    return chosen, flagged


def _versions_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a KaraokeNerds check result into the subset we store on the request
    (for the admin queue + the reject-to-voters email)."""
    tracks = []
    for song in result.get("songs", []) or []:
        for tr in song.get("community_tracks", []) or []:
            if tr.get("youtube_url"):
                tracks.append({
                    "brand_name": tr.get("brand_name") or "Unknown",
                    "youtube_url": tr.get("youtube_url"),
                })
    return {"best_youtube_url": result.get("best_youtube_url"), "tracks": tracks[:10]}


def _email_admin_flagged(flagged) -> None:
    """Email Andrew a summary of newly-flagged picks (referrals.py pattern)."""
    try:
        from backend.services.email_service import get_email_service

        rows = []
        for f in flagged:
            r = f["request"]
            v = f["versions"] or {}
            links = ", ".join(
                f'<a href="{html.escape(t["youtube_url"])}">{html.escape(t["brand_name"])}</a>'
                for t in (v.get("tracks") or [])[:3] if t.get("youtube_url")
            )
            rows.append(
                f"<li><strong>{html.escape(r.artist)} - {html.escape(r.title)}</strong> "
                f"({r.vote_count} votes) — existing: {links or html.escape(v.get('best_youtube_url') or '?')}</li>"
            )
        html_content = (
            f"<p>The daily community picker flagged {len(flagged)} top request(s) that already "
            f"have community karaoke versions online:</p><ul>{''.join(rows)}</ul>"
            f"<p>Keep / make ours / reject at "
            f'<a href="https://gen.nomadkaraoke.com/admin/community-reviews">/admin/community-reviews</a>.</p>'
        )
        get_email_service().provider.send_email(
            to_email=COMMUNITY_REVIEW_ADMIN_EMAIL,
            subject=f"[Requests board] {len(flagged)} pick(s) need review — existing karaoke versions",
            html_content=html_content,
            text_content=(
                f"{len(flagged)} flagged requests-board picks already have community versions. "
                f"Review at https://gen.nomadkaraoke.com/admin/community-reviews"
            ),
        )
        logger.info("daily-pick: emailed admin about %d flagged pick(s)", len(flagged))
    except Exception:  # noqa: BLE001
        logger.exception("daily-pick: failed to email admin about flagged picks")


async def _make_request(service, settings, date: str, request: SongRequest) -> Dict[str, Any]:
    """Drive a chosen request to in_progress idempotently, threading the per-day
    lock's phase markers. Safe to re-enter (resume)."""
    owner = (request.owner_email or request.submitted_by).lower()

    def phase_cb(p, **kw):
        service.update_lock(date, phase=p, **kw)

    result = await _provision_and_start(service, settings, request, owner, phase_cb)
    result["date"] = date
    return result


async def _provision_and_start(service, settings, request, owner, phase_cb=None) -> Dict[str, Any]:
    """Grant the free credit, create the job as the requester, kick off search +
    download, and advance the request to in_progress + assign the owner. Idempotent
    and re-entrant. ``phase_cb(phase, **fields)`` (optional) records progress on the
    daily lock for the picker; the admin "make ours" path passes None.

    Returns a "made"/"error" summary (no date — callers add their own context)."""
    def phase(p, **kw):
        if phase_cb:
            phase_cb(p, **kw)

    # 1) Claim the request: open -> queued (no-op if already advanced).
    if request.status == "open":
        service.transition_status(
            request.id, "open", "queued",
            picked_at=datetime.now(timezone.utc).isoformat(),
        )
    phase("claimed", request_id=request.id, owner_email=owner)

    # 2) Grant the free credit (guarded so a retry can't re-grant).
    request = service.get_request(request.id) or request
    if not request.community_credit_granted:
        ok, balance, msg = get_user_service().add_credits(
            owner, amount=1, reason=COMMUNITY_CREDIT_REASON,
        )
        if not ok:
            logger.error("daily-pick: credit grant failed for %s: %s", owner, msg)
            return {"status": "error", "step": "grant_credit",
                    "request_id": request.id, "message": msg}
        service.mark_credit_granted(request.id)
        logger.info("daily-pick: granted 1 credit to %s (balance=%s)", owner, balance)
    phase("credit_granted")

    # 3) Create the job as the requester (non-admin → consumes the granted credit).
    request = service.get_request(request.id) or request
    job_id = request.job_id
    if not job_id:
        try:
            job_id = _create_community_job(request, owner, settings)
        except Exception as e:  # noqa: BLE001
            logger.exception("daily-pick: job creation failed for request %s", request.id)
            return {"status": "error", "step": "create_job",
                    "request_id": request.id, "message": str(e)}
        service.set_job_id(request.id, job_id)
    phase("job_created", job_id=job_id)

    # 4) Kick off search + auto-download for the job (idempotent trigger).
    search_ok = await _search_and_download(job_id, request)

    # 5) Advance to in_progress and assign the owner (starts the 24h handoff clock).
    if request.status in ("open", "queued"):
        service.transition_status(request.id, request.status, "in_progress")
    service.assign_owner(request.id, owner)
    phase("done")

    logger.info(
        "daily-pick: made request %s (job %s, owner %s, search_ok=%s)",
        request.id, job_id, owner, search_ok,
    )
    return {"status": "made", "request_id": request.id,
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
