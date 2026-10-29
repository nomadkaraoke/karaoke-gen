"""Review gate: hold confident-lyrics jobs out of review until stems land.

Lyrics finish ~2-3 min before stem separation. When the screens worker scores a
job whose lyrics would auto-ship but whose stems aren't ready, opening review
right away loses the auto-ship: users open the editor within seconds
(AWAITING_REVIEW -> IN_REVIEW), so the audio worker's re-score finds a human in
the editor and backs off. Prod 2026-08-31..10-29: 109/340 jobs were last scored
without stems, so they never got a backing verdict, and lyrics-auto jobs went to
a human anyway.

So for those jobs the screens worker parks the job in GENERATING_SCREENS with
``state_data.auto_review_waiting_for_stems`` set (no review link, no
notification). Whoever atomically claims that flag re-scores and routes the job:
the audio worker once stems land (normal case), the screens worker itself if the
stems landed while it was parking, or recover_stuck_jobs after
``AUDIO_HOLD_TIMEOUT_SECONDS`` (backstop). The re-score either auto-completes
the review or opens human review exactly like the non-held path.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from backend.models.job import JobStatus

logger = logging.getLogger(__name__)

# Separation normally lands within a few minutes of lyrics; past this the
# recover_stuck_jobs sweep releases the hold into human review.
AUDIO_HOLD_TIMEOUT_SECONDS = 20 * 60


async def enter_human_review(
    job_id: str, job_log: Optional[logging.Logger] = None, job_manager=None
) -> None:
    """Pre-apply AI corrections server-side, then open the combined review."""
    from backend.services.auto_approval.pre_apply import ensure_and_pre_apply
    from backend.services.job_manager import JobManager

    # Apply the AI corrections + heuristic fixes BEFORE the review-ready
    # notification fires, so the review screen loads one final state (no
    # in-browser auto-apply race). Best-effort + bounded: on any miss the UI
    # falls back to its client-side on-load apply.
    pre_apply_result = await ensure_and_pre_apply(job_id)
    if pre_apply_result.get("outcome") == "pre_applied" and job_log:
        job_log.info(
            f"Pre-applied {pre_apply_result.get('applied', 0)} AI corrections "
            "server-side before review notification"
        )

    logger.info(f"[job:{job_id}] Screens generated, awaiting combined review")
    (job_manager or JobManager()).transition_to_state(
        job_id=job_id,
        new_status=JobStatus.AWAITING_REVIEW,
        progress=55,
        message="Ready for review. Please review lyrics and select your instrumental.",
    )


async def route_after_scoring(
    job_id: str,
    auto_result: Dict[str, Any],
    job_log: Optional[logging.Logger] = None,
    job_manager=None,
) -> str:
    """Screens-worker path for a job the first scoring did not auto-complete.

    Returns "held", "review", or the outcome of an immediate re-score.
    """
    from backend.services.job_manager import JobManager

    job_manager = job_manager or JobManager()
    if not auto_result.get("hold_for_audio"):
        await enter_human_review(job_id, job_log, job_manager)
        return "review"

    job_manager.park_review_for_auto_check(job_id)
    fresh = job_manager.get_job(job_id)
    if fresh and not (fresh.state_data or {}).get("audio_complete", False):
        msg = "Lyrics look auto-approvable: holding review until audio separation finishes"
        logger.info(f"[job:{job_id}] {msg}")
        if job_log:
            job_log.info(msg)
        return "held"

    # Stems landed while we were parking. The audio worker may already have
    # claimed the hold; the claim decides who re-scores.
    if job_manager.claim_auto_review_hold(job_id):
        logger.info(f"[job:{job_id}] Stems landed while holding review — re-scoring now")
        return await _route_claimed(job_id, job_log, job_manager)
    return "released_elsewhere"


async def release_audio_hold(job_id: str, reason: str) -> Optional[str]:
    """Release a held job (audio worker / stuck-job sweep).

    Returns None if the job wasn't held (or someone else already claimed it),
    else the routing outcome ("auto_completed", "review", ...).
    """
    from backend.services.job_manager import JobManager

    job_manager = JobManager()
    if not job_manager.claim_auto_review_hold(job_id):
        return None
    logger.info(f"[job:{job_id}] Releasing auto-review hold ({reason})")
    return await _route_claimed(job_id, None, job_manager)


async def _route_claimed(job_id: str, job_log, job_manager) -> str:
    """Route a job whose hold we just claimed. The claim already cleared the
    marker, so on failure fall back to human review, and if even that fails put
    the marker back so the recover_stuck_jobs sweep retries (never strand it)."""
    try:
        return await _rescore_and_route(job_id, job_log, job_manager)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[job:{job_id}] Re-score after hold release failed: {e}; opening review",
                     exc_info=True)
    try:
        job = job_manager.get_job(job_id)
        if job and job.status == JobStatus.GENERATING_SCREENS:
            await enter_human_review(job_id, job_log, job_manager)
        return "review"
    except Exception as e:  # noqa: BLE001
        logger.error(f"[job:{job_id}] Opening review after hold release failed: {e}; re-holding",
                     exc_info=True)
        job_manager.park_review_for_auto_check(job_id)
        return "error"


async def _rescore_and_route(
    job_id: str, job_log: Optional[logging.Logger] = None, job_manager=None
) -> str:
    from backend.services.auto_approval.executor import maybe_auto_complete_review
    from backend.services.job_manager import JobManager

    job_manager = job_manager or JobManager()
    job = job_manager.get_job(job_id)
    if not job or job.status != JobStatus.GENERATING_SCREENS:
        return "wrong_status"

    result = await maybe_auto_complete_review(job_id, trigger="audio_hold")
    if result.get("outcome") == "auto_completed":
        if job_log:
            job_log.info("Auto-approved after audio completion: review skipped")
        return "auto_completed"

    # Anything short of auto-complete (review verdict, abort, error, or audio
    # still missing on a timeout release) goes to a human, as before the hold.
    job = job_manager.get_job(job_id)
    if job and job.status == JobStatus.GENERATING_SCREENS:
        await enter_human_review(job_id, job_log, job_manager)
        return "review"
    return str(result.get("outcome"))


def audio_hold_expired(job, now: Optional[datetime] = None) -> bool:
    """True when a held job has waited longer than AUDIO_HOLD_TIMEOUT_SECONDS."""
    from backend.services.job_manager import JobManager

    since = (job.state_data or {}).get(JobManager.AUTO_REVIEW_HOLD_KEY)
    if not since or not isinstance(since, str):
        return False
    try:
        started = datetime.fromisoformat(since)
    except ValueError:
        return True  # unparseable marker: release rather than strand the job
    now = now or datetime.utcnow()
    return (now - started).total_seconds() > AUDIO_HOLD_TIMEOUT_SECONDS
