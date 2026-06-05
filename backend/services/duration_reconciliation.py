"""Reconcile a job's credit charge against the actual audio about to be processed.

Called at the convergence point immediately before the separation+transcription
workers are triggered (post-edit if an edit occurred). The probe callable returns
the duration in seconds of `job.input_media_gcs_path` (the to-be-processed audio).
"""
import asyncio
import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional
import logging

from backend.services.pricing import duration_to_credits, is_blocked
from backend.models.job import JobStatus

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    action: str                                  # "proceed" | "pause" | "cancel"
    pending_additional_credits: int = 0
    actual_seconds: Optional[float] = None


def reconcile_duration(
    job_id: str,
    job_manager,
    user_service,
    probe_duration: Callable[[object], Optional[float]],
    email_service=None,
) -> ReconcileResult:
    job = job_manager.get_job(job_id)
    actual = probe_duration(job)
    state = dict(job.state_data or {})
    credits_charged = int(state.get("credits_charged", 1))

    if actual is None:
        logger.warning("Job %s: duration probe returned None; proceeding without reconcile", job_id)
        return ReconcileResult(action="proceed", actual_seconds=None)

    job_manager.update_job(job_id, {"state_data.duration_actual_seconds": actual})

    if is_blocked(actual):
        if credits_charged > 0:
            user_service.add_credits(job.user_email, amount=credits_charged,
                                     reason="duration_over_limit_refund", job_id=job_id)
        job_manager.cancel_job(job_id, reason="Input audio exceeds the 60-minute limit")
        if email_service:
            email_service.send_duration_confirm_expired(job)
        return ReconcileResult(action="cancel", actual_seconds=actual)

    required = duration_to_credits(actual)
    delta = required - credits_charged

    if delta == 0:
        return ReconcileResult(action="proceed", actual_seconds=actual)

    if delta < 0:
        user_service.add_credits(job.user_email, amount=abs(delta),
                                 reason="duration_refund", job_id=job_id)
        job_manager.update_job(job_id, {"state_data.credits_charged": required})
        return ReconcileResult(action="proceed", actual_seconds=actual)

    # delta > 0 : owe more credits — pause for explicit re-confirmation.
    job_manager.update_job(job_id, {
        "state_data.duration_confirm_reason": "reconcile",
        "state_data.pending_additional_credits": delta,
    })
    job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.AWAITING_DURATION_CONFIRM,
        progress=16,
        message=f"This turned out longer than estimated — {delta} more credit(s) needed",
    )
    return ReconcileResult(action="pause", pending_additional_credits=delta, actual_seconds=actual)


# --- Pipeline wiring (impure: uses singletons + ffprobe) ---

def _ffprobe_seconds(job, storage) -> Optional[float]:
    """Header-only ffprobe of job.input_media_gcs_path via a signed URL.

    Mirrors the async _get_audio_duration_ffprobe_signed helper in
    backend/api/routes/jobs.py, but as a sync function suitable for use
    in run_in_executor.
    """
    gcs_path = getattr(job, "input_media_gcs_path", None)
    if not gcs_path:
        return None
    try:
        signed_url = storage.generate_signed_url(gcs_path, expiration_minutes=5)
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", signed_url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception as e:
        logger.warning("Job %s: ffprobe duration failed: %s", getattr(job, "id", "?"), e)
        return None


async def reconcile_and_maybe_pause(job_id: str) -> bool:
    """Resolve real singletons, probe duration, run reconcile_duration, return True if blocked.

    Returns True when the job has been paused (AWAITING_DURATION_CONFIRM) or cancelled,
    meaning the caller must NOT trigger downstream workers.
    Returns False when the job should proceed normally.

    Designed for the no-edit convergence point in audio_download_worker.py.
    """
    # Import at function scope to avoid circular imports.
    from backend.services.job_manager import JobManager
    from backend.services.user_service import get_user_service
    from backend.services.storage_service import StorageService

    job_manager = JobManager()
    user_service = get_user_service()
    storage = StorageService()
    # TODO(task15): pass email_service once send_duration_confirm_expired exists
    email_service = None

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: reconcile_duration(
            job_id,
            job_manager,
            user_service,
            lambda job: _ffprobe_seconds(job, storage),
            email_service,
        ),
    )
    return result.action in ("pause", "cancel")
