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

    # Short-circuit for admin / payment-bypassed jobs: measure duration for
    # observability but do NOT pause, charge, refund, or cancel regardless of
    # how long the audio turns out to be.
    if state.get("payment_bypassed"):
        if actual is not None:
            job_manager.update_job(job_id, {"state_data.duration_actual_seconds": actual})
        logger.info("Job %s: payment_bypassed=True, skipping reconcile (actual=%s s)", job_id, actual)
        return ReconcileResult(action="proceed", actual_seconds=actual)

    if actual is None:
        logger.warning("Job %s: duration probe returned None; proceeding without reconcile", job_id)
        return ReconcileResult(action="proceed", actual_seconds=None)

    job_manager.update_job(job_id, {"state_data.duration_actual_seconds": actual})

    if is_blocked(actual):
        if credits_charged > 0:
            user_service.add_credits(job.user_email, amount=credits_charged,
                                     reason="duration_over_limit_refund", job_id=job_id)
            job_manager.update_job(job_id, {"credit_refunded": True})  # suppress cancel_job's built-in 1-credit refund (we already refunded the full charge)
        job_manager.cancel_job(job_id, reason="Input audio exceeds the 60-minute limit")
        if email_service:
            email_service.send_duration_confirm_expired(
                to_email=job.user_email,
                artist=getattr(job, "artist", None),
                title=getattr(job, "title", None),
                credits_refunded=credits_charged,
            )
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
    from backend.services.email_service import get_email_service

    job_manager = JobManager()
    user_service = get_user_service()
    storage = StorageService()
    # JobManager/StorageService have no singleton accessor; instantiate directly (matches audio_download_worker).
    email_service = get_email_service()

    result = await asyncio.get_running_loop().run_in_executor(
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
