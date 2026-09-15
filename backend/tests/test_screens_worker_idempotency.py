"""Screens worker idempotency.

The screens worker can be dispatched more than once — the lyrics worker (primary
trigger) and the audio worker (fallback trigger) may both fire it, and Cloud Tasks
can redeliver. A duplicate dispatch that arrives once the job has advanced past the
pre-screens stage must no-op rather than re-run the (now invalid)
GENERATING_SCREENS transition and regenerate screens.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.models.job import Job, JobStatus


def _job(status):
    return Job(
        job_id="dup-1", artist="A", title="B", status=status,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        state_data={"lyrics_complete": True, "audio_complete": True},
    )


def _patched(screens_worker, mock_jm):
    return (
        patch.object(screens_worker, "JobManager", return_value=mock_jm),
        patch.object(screens_worker, "StorageService"),
        patch.object(screens_worker, "get_settings"),
        patch.object(screens_worker, "create_job_logger"),
        patch.object(screens_worker, "setup_job_logging"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    JobStatus.GENERATING_SCREENS,
    JobStatus.AWAITING_REVIEW,
    JobStatus.REVIEW_COMPLETE,
    JobStatus.COMPLETE,
])
async def test_generate_screens_noops_when_already_advanced(status):
    from backend.workers import screens_worker
    mock_jm = MagicMock()
    mock_jm.get_job.return_value = _job(status)

    p1, p2, p3, p4, p5 = _patched(screens_worker, mock_jm)
    with p1, p2, p3, p4, p5:
        result = await screens_worker.generate_screens("dup-1")

    assert result is True
    # Duplicate dispatch must NOT perform the (invalid) GENERATING_SCREENS transition.
    mock_jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_generate_screens_proceeds_from_downloading():
    """A first, legitimate dispatch (status DOWNLOADING) passes the idempotency
    guard and proceeds to validate prerequisites."""
    from backend.workers import screens_worker
    mock_jm = MagicMock()
    mock_jm.get_job.return_value = _job(JobStatus.DOWNLOADING)

    p1, p2, p3, p4, p5 = _patched(screens_worker, mock_jm)
    with p1, p2, p3, p4, p5, \
         patch.object(screens_worker, "_validate_prerequisites", return_value=False) as mock_val:
        result = await screens_worker.generate_screens("dup-1")

    # Guard passed → prerequisites were checked (stubbed False to stop early before work).
    mock_val.assert_called_once()
    assert result is False
