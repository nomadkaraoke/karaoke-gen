"""Tests for render-video worker capacity-error handling.

When the GCE encoding worker can't be started because the zone is exhausted,
the render worker must:
  - NOT fail the job (so the auto-retry scheduler can pick it up)
  - Park the job in RENDER_PENDING_CAPACITY with a user-friendly message
  - Record attempt metadata in state_data.render_pending_capacity for the
    scheduler to use for backoff / max-wait checks
"""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from backend.models.job import JobStatus
from backend.services.encoding_errors import EncodingWorkerCapacityError


def _build_minimal_job():
    """Job mock that lets process_render_video reach the GCE call site."""
    job = MagicMock()
    job.artist = "Test Artist"
    job.title = "Test Title"
    job.input_media_gcs_path = "jobs/test/audio.flac"
    job.style_assets = {}
    job.style_params_gcs_path = None
    job.subtitle_offset_ms = 0
    job.prep_only = False
    job.state_data = {}
    job.file_urls = {}
    return job


@pytest.mark.asyncio
async def test_capacity_error_parks_job_for_retry():
    """A capacity error must transition to RENDER_PENDING_CAPACITY, not FAILED.

    This is the core contract: jobs blocked on GCE capacity are recoverable,
    and the auto-retry scheduler will pick them up. Calling fail_job here
    would lose the job to the user and require manual intervention.
    """
    from backend.workers import render_video_worker as rvw

    capacity_error = EncodingWorkerCapacityError(
        "VM encoding-worker-b could not be started in us-central1-c: "
        "ZONE_RESOURCE_POOL_EXHAUSTED — out of capacity",
        vm_name="encoding-worker-b",
        zone="us-central1-c",
        code="ZONE_RESOURCE_POOL_EXHAUSTED",
    )

    mock_job_manager = MagicMock()
    mock_job_manager.get_job.return_value = _build_minimal_job()
    mock_job_manager.transition_to_state.return_value = True

    mock_encoding_service = MagicMock()
    mock_encoding_service.is_enabled = True
    mock_encoding_service.render_video_on_gce = AsyncMock(side_effect=capacity_error)

    mock_storage = MagicMock()
    mock_storage.file_exists.return_value = False

    with patch.object(rvw, "JobManager", return_value=mock_job_manager), \
         patch.object(rvw, "StorageService", return_value=mock_storage), \
         patch.object(rvw, "get_settings"), \
         patch.object(rvw, "create_job_logger", return_value=MagicMock()), \
         patch.object(rvw, "setup_job_logging", return_value=MagicMock()), \
         patch.object(rvw, "validate_worker_can_run", return_value=None), \
         patch.object(rvw, "get_encoding_service", return_value=mock_encoding_service):

        result = await rvw.process_render_video("test-job-id")

    assert result is False
    mock_job_manager.fail_job.assert_not_called()

    # state_data.render_pending_capacity persists attempt metadata
    state_data_calls = [
        call_args for call_args in mock_job_manager.update_state_data.call_args_list
        if call_args.args[1] == "render_pending_capacity"
    ]
    assert state_data_calls, (
        "Expected update_state_data('test-job-id', 'render_pending_capacity', ...) call"
    )
    pending_meta = state_data_calls[-1].args[2]
    assert pending_meta["attempt_count"] == 1
    assert pending_meta["last_code"] == "ZONE_RESOURCE_POOL_EXHAUSTED"
    assert pending_meta["last_zone"] == "us-central1-c"
    assert pending_meta["last_vm"] == "encoding-worker-b"
    assert "first_seen_at" in pending_meta
    assert "last_attempt_at" in pending_meta

    # Final state transition is RENDER_PENDING_CAPACITY with a user-friendly message
    transitions = mock_job_manager.transition_to_state.call_args_list
    capacity_transition = next(
        (c for c in transitions if c.kwargs.get("new_status") == JobStatus.RENDER_PENDING_CAPACITY),
        None,
    )
    assert capacity_transition is not None, (
        "Worker must transition to RENDER_PENDING_CAPACITY on capacity error"
    )
    user_message = capacity_transition.kwargs.get("message", "")
    assert "automatically" in user_message.lower() or "auto-retry" in user_message.lower(), (
        f"User-facing message must indicate auto-retry. Got: {user_message!r}"
    )


@pytest.mark.asyncio
async def test_capacity_error_increments_attempt_count_on_subsequent_attempts():
    """Repeat capacity errors must accumulate attempt count and preserve first_seen_at."""
    from backend.workers import render_video_worker as rvw

    job = _build_minimal_job()
    job.state_data = {
        "render_pending_capacity": {
            "first_seen_at": "2026-05-05T09:00:00+00:00",
            "last_attempt_at": "2026-05-05T09:05:00+00:00",
            "attempt_count": 3,
            "last_code": "ZONE_RESOURCE_POOL_EXHAUSTED",
            "last_zone": "us-central1-c",
            "last_vm": "encoding-worker-b",
        }
    }

    capacity_error = EncodingWorkerCapacityError(
        "exhausted",
        vm_name="encoding-worker-b",
        zone="us-central1-c",
        code="ZONE_RESOURCE_POOL_EXHAUSTED",
    )

    mock_job_manager = MagicMock()
    mock_job_manager.get_job.return_value = job
    mock_job_manager.transition_to_state.return_value = True

    mock_encoding_service = MagicMock()
    mock_encoding_service.is_enabled = True
    mock_encoding_service.render_video_on_gce = AsyncMock(side_effect=capacity_error)

    with patch.object(rvw, "JobManager", return_value=mock_job_manager), \
         patch.object(rvw, "StorageService"), \
         patch.object(rvw, "get_settings"), \
         patch.object(rvw, "create_job_logger", return_value=MagicMock()), \
         patch.object(rvw, "setup_job_logging", return_value=MagicMock()), \
         patch.object(rvw, "validate_worker_can_run", return_value=None), \
         patch.object(rvw, "get_encoding_service", return_value=mock_encoding_service):

        await rvw.process_render_video("test-job-id")

    state_data_calls = [
        c for c in mock_job_manager.update_state_data.call_args_list
        if c.args[1] == "render_pending_capacity"
    ]
    assert state_data_calls
    pending_meta = state_data_calls[-1].args[2]
    assert pending_meta["attempt_count"] == 4
    # first_seen_at preserved across attempts so the scheduler can enforce
    # an absolute max-wait deadline (e.g. 24h).
    assert pending_meta["first_seen_at"] == "2026-05-05T09:00:00+00:00"


@pytest.mark.asyncio
async def test_non_capacity_exception_still_fails_with_clear_message():
    """Non-capacity exceptions must still fail the job, but with a useful message.

    Regression guard: bare TimeoutError() previously produced an empty
    `Video render failed: ` message. Use repr() fallback when str() is empty.
    """
    from backend.workers import render_video_worker as rvw

    mock_job_manager = MagicMock()
    mock_job_manager.get_job.return_value = _build_minimal_job()

    mock_encoding_service = MagicMock()
    mock_encoding_service.is_enabled = True
    # Bare TimeoutError(), no message — exactly the failure mode users hit
    mock_encoding_service.render_video_on_gce = AsyncMock(side_effect=TimeoutError())

    with patch.object(rvw, "JobManager", return_value=mock_job_manager), \
         patch.object(rvw, "StorageService"), \
         patch.object(rvw, "get_settings"), \
         patch.object(rvw, "create_job_logger", return_value=MagicMock()), \
         patch.object(rvw, "setup_job_logging", return_value=MagicMock()), \
         patch.object(rvw, "validate_worker_can_run", return_value=None), \
         patch.object(rvw, "get_encoding_service", return_value=mock_encoding_service):

        result = await rvw.process_render_video("test-job-id")

    assert result is False
    mock_job_manager.fail_job.assert_called_once()
    fail_message = mock_job_manager.fail_job.call_args.args[1]
    # Empty f"{e}" produced "Video render failed: " — must not happen anymore.
    assert fail_message != "Video render failed: "
    assert "TimeoutError" in fail_message or fail_message.strip().endswith(":") is False
