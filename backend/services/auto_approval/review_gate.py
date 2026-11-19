"""Review gate: a job only reaches review once it is fully prepared.

"Fully prepared" (Andrew, 2026-11-19: *"i want the review button to only appear
when a job is 'fully prepared', including with the ai corrections and the lead
vocal waveform images ready to load"*) means, before AWAITING_REVIEW:

1. audio separation has finished, so the stems, the backing-vocals analysis and
   the stem-based checks (timing, vocal gaps) exist;
2. the auto-approval scorer has run with all of that, and either shipped the job
   or sent it to a human;
3. the AI lyric corrections have been generated and applied server-side
   (``pre_apply.ensure_and_pre_apply``; the browser never applies them itself);
4. the review audio transcodes and both review waveforms are cached
   (``review_assets.prepare_review_assets``).

Lyrics usually finish 2-3 min before the stems. Any job whose stems aren't ready
when the screens worker finishes is parked in GENERATING_SCREENS with
``state_data.auto_review_waiting_for_stems`` set (no review link, no
notification). Until 2026-11-19 only jobs whose lyrics would auto-ship were held;
everything else (including every Made For You order) opened review straight
away, before the stems, the backing verdict or the vocal waveform existed.

Whoever atomically claims that flag re-scores and routes the job: the audio
worker once stems land (normal case), the screens worker itself if the stems
landed while it was parking, or recover_stuck_jobs after
``AUDIO_HOLD_TIMEOUT_SECONDS`` (backstop). The re-score either auto-completes the
review or opens human review via ``enter_human_review``.
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
    """Apply AI corrections and prepare review assets, then open the review."""
    from backend.services.auto_approval.pre_apply import ensure_and_pre_apply
    from backend.services.job_manager import JobManager
    from backend.services.review_assets import prepare_review_assets

    job_manager = job_manager or JobManager()

    # Apply the AI corrections + heuristic fixes BEFORE the review-ready
    # notification fires, so the review screen loads one final state.
    # Best-effort + bounded (generates the suggestions on a cache miss).
    pre_apply_result = await ensure_and_pre_apply(job_id)
    if pre_apply_result.get("outcome") == "pre_applied" and job_log:
        job_log.info(
            f"Pre-applied {pre_apply_result.get('applied', 0)} AI corrections "
            "server-side before review notification"
        )
    elif pre_apply_result.get("reason") not in ("already_applied", "corrections_updated_exists"):
        logger.warning(f"[job:{job_id}] Opening review without pre-applied AI corrections: "
                       f"{pre_apply_result}")

    # Stem transcodes + backing waveform + vocal peaks, so the page paints at once.
    await prepare_review_assets(job_id, job_manager, job_log)

    logger.info(f"[job:{job_id}] Screens generated, awaiting combined review")
    job_manager.transition_to_state(
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
    auto_result = await _rescore_if_suggestions_were_missing(job_id, auto_result, job_log)
    if auto_result.get("outcome") in ("auto_completed", "wrong_status"):
        return str(auto_result["outcome"])
    if _stems_ready(job_manager.get_job(job_id)) or not _audio_hold_enabled():
        await enter_human_review(job_id, job_log, job_manager)
        return "review"

    # The hold is usually released by the audio-separation job, which has no LLM
    # credentials: make sure the AI suggestions exist now, on the API service,
    # so the release only has to apply them.
    if (auto_result.get("ai_suggestions_available") is False
            and not auto_result.get("ai_generation_attempted")):
        await _generate_ai_suggestions(job_id)

    job_manager.park_review_for_auto_check(job_id)
    fresh = job_manager.get_job(job_id)
    if fresh and not _stems_ready(fresh):
        msg = "Holding review until audio separation finishes"
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


async def _generate_ai_suggestions(job_id: str) -> bool:
    """Generate (or join the in-flight generation of) the AI suggestion cache.

    Bounded (180s) and best-effort. False when proactive generation is off or
    the attempt raised; the cache may still be missing after True (LLM outage).
    """
    from backend.config import get_settings

    if not getattr(get_settings(), "auto_correct_proactive_enabled", False):
        return False
    try:
        from backend.workers.auto_correct_worker import process_proactive_auto_correct

        await process_proactive_auto_correct(job_id)  # bounded (180s), joins a run in flight
    except Exception as e:  # noqa: BLE001  best-effort, like ensure_and_pre_apply
        logger.warning(f"[job:{job_id}] Waiting for AI suggestions before review failed: {e}")
        return False
    return True


def _stems_ready(job) -> bool:
    return bool(job and (job.state_data or {}).get("audio_complete", False))


def _audio_hold_enabled() -> bool:
    from backend.config import get_settings

    return bool(getattr(get_settings(), "auto_approval_audio_hold_enabled", True))


async def _rescore_if_suggestions_were_missing(
    job_id: str, auto_result: Dict[str, Any], job_log: Optional[logging.Logger] = None
) -> Dict[str, Any]:
    """Score again if the first pass ran before the AI suggestions landed.

    Lyrics finish moments before the proactive auto-correct, and without its
    suggestions the lyrics score "needs review". Opening review on that verdict
    sent the customer a "review your lyrics" push, and the audio worker's re-score
    then auto-approved the job minutes later (4 of 5 auto-approved jobs,
    2026-10-20..11-03). Returns the verdict to route on; the original one when
    suggestions can't be had or the re-score didn't reach a verdict.
    """
    if auto_result.get("outcome") != "review" or auto_result.get("ai_suggestions_available") is not False:
        return auto_result

    from backend.services.auto_approval.executor import maybe_auto_complete_review

    if not await _generate_ai_suggestions(job_id):
        return {**auto_result, "ai_generation_attempted": True}

    rescored = await maybe_auto_complete_review(job_id, trigger="screens_worker")
    outcome = rescored.get("outcome")
    logger.info(f"[job:{job_id}] Re-scored once AI suggestions were ready: {outcome} "
                f"(hold_for_audio={rescored.get('hold_for_audio', False)})")
    if outcome == "auto_completed" and job_log:
        job_log.info("Auto-approved once AI suggestions were ready: review skipped")
    chosen = rescored if outcome in ("review", "auto_completed", "wrong_status") else auto_result
    return {**chosen, "ai_generation_attempted": True}


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
