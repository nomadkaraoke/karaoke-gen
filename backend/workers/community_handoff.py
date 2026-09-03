"""24-hour ownership handoff for community picks (requests board — Phase 2).

The daily picker makes a requests-board track owned by the requester and starts a
24h clock. If the owner doesn't complete the (lyrics) review within 24h, the job
is handed to the next up-voter — repeatedly, oldest-first — until someone finishes
or the voter cap is reached, at which point the track is parked ("stalled").

Runs hourly via Cloud Scheduler → POST /api/internal/community-handoffs. Only acts
on a track when its job is actually blocked waiting for the owner (needs a human);
tracks that auto-approved and are still rendering/publishing are left alone.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.config import get_settings
from backend.models.job import JobStatus
from backend.services.email_service import get_email_service
from backend.services.job_manager import JobManager
from backend.services.song_request_service import get_song_request_service
from backend.services.user_service import get_user_service

logger = logging.getLogger(__name__)

# Job states in which the track is blocked waiting for the OWNER to act. Only in
# these states does a stale owner warrant a handoff (a job still separating/
# transcribing, or already rendering after an auto-approval, is not the owner's
# fault and must not be reassigned).
OWNER_BLOCKING_STATUSES = {
    JobStatus.AWAITING_REVIEW,
    JobStatus.IN_REVIEW,
    JobStatus.AWAITING_AUDIO_SELECTION,
    JobStatus.AWAITING_DURATION_CONFIRM,
    JobStatus.AWAITING_AUDIO_EDIT,
    JobStatus.IN_AUDIO_EDIT,
}


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _owner_locale(email: str) -> str:
    try:
        user = get_user_service().get_user(email)
        if user and user.locale:
            return user.locale
    except Exception:
        pass
    return "en"


async def process_community_handoffs() -> Dict[str, Any]:
    """Reassign or park community picks whose owner has gone quiet for >24h."""
    service = get_song_request_service()
    settings = get_settings()
    job_manager = JobManager()
    email_service = get_email_service()

    now = datetime.now(timezone.utc)
    handed_off = 0
    parked = 0
    checked = 0
    errors = []

    for req in service.list_in_progress():
        try:
            assigned = _parse_dt(req.owner_assigned_at)
            if assigned is None:
                continue
            hours = (now - assigned).total_seconds() / 3600
            if hours < settings.community_handoff_hours:
                continue

            job = job_manager.get_job(req.job_id) if req.job_id else None
            if job is None or job.status not in OWNER_BLOCKING_STATUSES:
                # Owner isn't the blocker (still processing / auto-approved / done).
                continue

            checked += 1

            # Cap reached, or no untried up-voters left → park the track.
            tried = {e.lower() for e in (req.attempted_owners or [])}
            candidates = [v for v in service.list_upvoters(req.id) if v not in tried]
            if req.handoff_attempts >= settings.community_handoff_max_attempts or not candidates:
                service.mark_stalled(req.id)
                parked += 1
                logger.info(
                    "handoff: parking request %s (attempts=%d, remaining_voters=%d)",
                    req.id, req.handoff_attempts, len(candidates),
                )
                continue

            new_owner = candidates[0]
            # Compare-and-set the owner FIRST (guards against a stale/overlapping
            # run clobbering a fresher assignment); only then move the job. Skip if
            # another run already advanced this request past the owner we observed.
            if not service.assign_owner(req.id, new_owner, expected_owner=req.owner_email):
                logger.info("handoff: request %s changed owner mid-run; skipping", req.id)
                continue
            # Reassign the job to the next voter (does not move credits — the job
            # was already paid for at creation; review completion needs no credit).
            job_manager.update_job(req.job_id, {"user_email": new_owner})

            try:
                email_service.send_review_reminder(
                    to_email=new_owner,
                    artist=req.artist,
                    title=req.title,
                    job_id=req.job_id,
                    locale=_owner_locale(new_owner),
                )
            except Exception as email_err:
                logger.error("handoff: failed to email new owner for %s: %s", req.id, email_err)

            handed_off += 1
            logger.info(
                "handoff: request %s reassigned to %s (attempt %d)",
                req.id, new_owner, req.handoff_attempts + 1,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("handoff: error processing request %s", getattr(req, "id", "?"))
            errors.append(str(e))

    logger.info(
        "community handoff complete: checked=%d handed_off=%d parked=%d errors=%d",
        checked, handed_off, parked, len(errors),
    )
    return {
        "status": "completed",
        "checked": checked,
        "handed_off": handed_off,
        "parked": parked,
        "errors": errors,
    }
