"""Idempotent "user opened review" transition shared by review entry points.

Both ``POST /api/jobs/{id}/start-review`` and ``GET /api/review/{id}/correction-data``
move a job AWAITING_REVIEW -> IN_REVIEW. Clients fire them concurrently (React
double-mount, iOS in-app browsers, two tabs), so the loser of the race must not
fail with "in_review -> in_review".
"""
from typing import Any, Literal, Optional

from backend.exceptions import InvalidStateTransitionError
from backend.models.job import JobStatus

ReviewOpenResult = Literal["started", "already", "refused"]


def ensure_in_review(
    job_manager: Any,
    job_id: str,
    current_status: Optional[Any],
    message: str,
) -> ReviewOpenResult:
    """Move a job to IN_REVIEW exactly once, tolerating concurrent callers.

    Returns:
        "started": this call performed the transition (do one-time side effects).
        "already": job was already IN_REVIEW (or another caller won the race).
        "refused": the transition was refused without raising (caller decides).

    Raises InvalidStateTransitionError for any invalid transition other than losing
    the race to IN_REVIEW. Blocking: call via ``asyncio.to_thread`` from async code.
    """
    if current_status == JobStatus.IN_REVIEW:
        return "already"
    try:
        ok = job_manager.transition_to_state(
            job_id=job_id, new_status=JobStatus.IN_REVIEW, message=message
        )
    except InvalidStateTransitionError:
        current = job_manager.get_job(job_id)
        if current and current.status == JobStatus.IN_REVIEW:
            return "already"
        raise
    return "started" if ok else "refused"
