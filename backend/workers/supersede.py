"""
Worker supersession fencing.

A long-running worker (render-video, video) can be "superseded" mid-flight when
an operator resets the job (admin "Review"/"Audio"/"Reprocess" buttons), a user
cancels it, or a newer run of the same stage is triggered. When that happens the
worker's result is stale and must be DISCARDED — not written back and, above all,
not used to fail a job the operator deliberately moved.

Two independent nets detect supersession:

1. Status fence (Layer A) — the job is no longer in a status this worker owns.
   Catches admin resets that move the job backwards (e.g. rendering_video ->
   awaiting_review) even when no new worker has been triggered yet.

2. Generation fence (Layer B) — ``state_data.worker_generation`` is bumped every
   time a render/video worker is triggered. A worker captures the generation at
   start; if it later differs, a newer run has taken over. This closes the race
   where a stale run finishes just after a new one started and would otherwise
   overwrite the newer run's outputs.

Together these guarantee that no sequence of admin resets / re-submissions can
push a job into an invalid state or clobber a fresher render.
"""

from typing import Iterable, Optional

from backend.models.job import JobStatus


def capture_generation(job) -> int:
    """Read the worker-generation fence off a job snapshot (0 if unset)."""
    state_data = getattr(job, "state_data", None) or {}
    try:
        return int(state_data.get("worker_generation", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_status(status) -> Optional[JobStatus]:
    """Job.status is deserialised with use_enum_values=True (a plain str)."""
    if isinstance(status, JobStatus):
        return status
    try:
        return JobStatus(status)
    except (ValueError, TypeError):
        return None


def check_superseded(
    job_manager,
    job_id: str,
    captured_generation: int,
    expected_statuses: Iterable[JobStatus],
) -> Optional[str]:
    """
    Re-read the job and decide whether this worker has been superseded.

    Args:
        job_manager: JobManager instance (re-reads fresh from Firestore).
        job_id: Job being processed.
        captured_generation: Value from :func:`capture_generation` at worker start.
        expected_statuses: Statuses this worker legitimately owns right now.

    Returns:
        A human-readable reason string if superseded, else ``None``.
    """
    job = job_manager.get_job(job_id)
    if job is None:
        return "job no longer exists"

    current_generation = capture_generation(job)
    if current_generation != captured_generation:
        return (
            f"worker generation changed "
            f"({captured_generation} -> {current_generation}) — a newer run took over"
        )

    status = _normalize_status(job.status)
    expected = set(expected_statuses)
    if status not in expected:
        status_value = status.value if status else repr(job.status)
        expected_values = sorted(s.value for s in expected)
        return (
            f"job status is {status_value}, no longer one of {expected_values} "
            f"(reset or cancelled out from under this worker)"
        )

    return None
