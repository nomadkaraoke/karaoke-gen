"""Tests for render-video worker shutdown handling.

When Cloud Run autoscales an instance down (or rolls a deployment), it sends
SIGTERM and waits up to 600s before SIGKILL. Without the changes covered
here, an in-flight render would be killed silently and the job would sit at
`rendering_video` indefinitely until an operator manually retried.

The contract:
  - process_render_video must register with worker_registry on entry and
    unregister on exit (even on failure)
  - park_active_render_jobs_for_shutdown must transition any in-flight render
    job to RENDER_PENDING_CAPACITY so the auto-retry scheduler can recover it
  - It must NOT touch jobs that have moved past RENDERING_VIDEO between the
    registry snapshot and the park attempt (defends against the race where a
    worker completes during shutdown)
"""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from backend.models.job import JobStatus
from backend.workers.registry import worker_registry


def _build_minimal_job(status=JobStatus.RENDERING_VIDEO):
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
    job.status = status
    return job


@pytest.fixture(autouse=True)
def reset_worker_registry():
    """Each test starts with an empty registry — these tests touch shared state."""
    worker_registry._active_workers.clear()
    yield
    worker_registry._active_workers.clear()


@pytest.mark.asyncio
async def test_worker_registers_and_unregisters_on_success():
    """The render worker must register with worker_registry and unregister
    on exit so the shutdown hook can wait for it.

    Without registration, Cloud Run would kill the container the moment the
    HTTP request that started the BackgroundTask returns — long before the
    render finishes.
    """
    from backend.workers import render_video_worker as rvw

    job = _build_minimal_job()

    mock_job_manager = MagicMock()
    mock_job_manager.get_job.return_value = job
    mock_job_manager.transition_to_state.return_value = True

    mock_encoding_service = MagicMock()
    mock_encoding_service.is_enabled = True
    # Return a successful render result
    mock_encoding_service.render_video_on_gce = AsyncMock(return_value={
        "output_files": [],
        "metadata": {},
    })

    mock_storage = MagicMock()
    mock_storage.file_exists.return_value = False

    mock_worker_service = MagicMock()
    mock_worker_service.trigger_video_worker = AsyncMock(return_value=True)

    with patch.object(rvw, "JobManager", return_value=mock_job_manager), \
         patch.object(rvw, "StorageService", return_value=mock_storage), \
         patch.object(rvw, "get_settings"), \
         patch.object(rvw, "create_job_logger", return_value=MagicMock()), \
         patch.object(rvw, "setup_job_logging", return_value=MagicMock()), \
         patch.object(rvw, "validate_worker_can_run", return_value=None), \
         patch.object(rvw, "get_encoding_service", return_value=mock_encoding_service), \
         patch("backend.services.worker_service.get_worker_service",
               return_value=mock_worker_service):

        result = await rvw.process_render_video("test-job-id")

    assert result is True
    # After completion, registry must be empty
    assert worker_registry.get_active_workers() == {}


@pytest.mark.asyncio
async def test_worker_unregisters_even_on_unexpected_exception():
    """A finally block must unregister even when the work itself blows up.

    If unregister is skipped on errors, the registry leaks and shutdown park
    would try (incorrectly) to park a job that has already failed.
    """
    from backend.workers import render_video_worker as rvw

    mock_job_manager = MagicMock()
    mock_job_manager.get_job.return_value = _build_minimal_job()

    mock_encoding_service = MagicMock()
    mock_encoding_service.is_enabled = True
    mock_encoding_service.render_video_on_gce = AsyncMock(
        side_effect=RuntimeError("unexpected boom")
    )

    with patch.object(rvw, "JobManager", return_value=mock_job_manager), \
         patch.object(rvw, "StorageService"), \
         patch.object(rvw, "get_settings"), \
         patch.object(rvw, "create_job_logger", return_value=MagicMock()), \
         patch.object(rvw, "setup_job_logging", return_value=MagicMock()), \
         patch.object(rvw, "validate_worker_can_run", return_value=None), \
         patch.object(rvw, "get_encoding_service", return_value=mock_encoding_service):

        result = await rvw.process_render_video("test-job-id")

    assert result is False
    assert worker_registry.get_active_workers() == {}


def test_park_active_render_jobs_parks_in_flight_render():
    """park_active_render_jobs_for_shutdown must transition active render
    jobs to RENDER_PENDING_CAPACITY with the WORKER_SHUTDOWN code marker."""
    from backend.workers import render_video_worker as rvw

    # Manually populate the registry to simulate an in-flight render
    worker_registry._active_workers["job-A"] = {"render-video"}

    mock_job_manager = MagicMock()
    mock_job_manager.get_job.return_value = _build_minimal_job(JobStatus.RENDERING_VIDEO)
    mock_job_manager.transition_to_state.return_value = True

    with patch.object(rvw, "JobManager", return_value=mock_job_manager):
        parked = rvw.park_active_render_jobs_for_shutdown()

    assert parked == 1

    # Should have written render_pending_capacity metadata with WORKER_SHUTDOWN code
    state_calls = [
        c for c in mock_job_manager.update_state_data.call_args_list
        if c.args[1] == "render_pending_capacity"
    ]
    assert state_calls
    pending_meta = state_calls[-1].args[2]
    assert pending_meta["last_code"] == rvw.RENDER_WORKER_SHUTDOWN_CODE

    # Should transition to RENDER_PENDING_CAPACITY
    transitions = mock_job_manager.transition_to_state.call_args_list
    assert any(
        c.kwargs.get("new_status") == JobStatus.RENDER_PENDING_CAPACITY
        for c in transitions
    )


def test_park_active_render_jobs_skips_completed_jobs():
    """If the worker completed concurrently and the job moved past
    RENDERING_VIDEO, the park must NOT clobber its terminal state.

    Race: the worker_registry snapshot is taken at the start of the park;
    between that and the per-job park call, the worker may finish and
    transition the job to INSTRUMENTAL_SELECTED. Parking it back to
    RENDER_PENDING_CAPACITY would re-trigger render and produce duplicate
    work.
    """
    from backend.workers import render_video_worker as rvw

    worker_registry._active_workers["job-already-done"] = {"render-video"}

    mock_job_manager = MagicMock()
    mock_job_manager.get_job.return_value = _build_minimal_job(
        status=JobStatus.INSTRUMENTAL_SELECTED  # past render
    )

    with patch.object(rvw, "JobManager", return_value=mock_job_manager):
        parked = rvw.park_active_render_jobs_for_shutdown()

    assert parked == 0
    mock_job_manager.update_state_data.assert_not_called()
    mock_job_manager.transition_to_state.assert_not_called()


def test_park_active_render_jobs_returns_zero_when_no_active_renders():
    """No-op when nothing render-related is in flight (e.g. only audio jobs)."""
    from backend.workers import render_video_worker as rvw

    worker_registry._active_workers["job-X"] = {"audio", "lyrics"}

    mock_job_manager = MagicMock()

    with patch.object(rvw, "JobManager", return_value=mock_job_manager):
        parked = rvw.park_active_render_jobs_for_shutdown()

    assert parked == 0
    # JobManager should not even be queried when there's nothing to do
    mock_job_manager.get_job.assert_not_called()


def test_park_active_render_jobs_continues_after_per_job_failure():
    """One failing park must not block parking the rest — we have ~120s
    before SIGKILL and we need to make a best effort."""
    from backend.workers import render_video_worker as rvw

    worker_registry._active_workers["job-fails"] = {"render-video"}
    worker_registry._active_workers["job-ok"] = {"render-video"}

    mock_job_manager = MagicMock()

    def get_job_side_effect(job_id):
        if job_id == "job-fails":
            raise RuntimeError("Firestore unavailable")
        return _build_minimal_job(JobStatus.RENDERING_VIDEO)

    mock_job_manager.get_job.side_effect = get_job_side_effect
    mock_job_manager.transition_to_state.return_value = True

    with patch.object(rvw, "JobManager", return_value=mock_job_manager):
        parked = rvw.park_active_render_jobs_for_shutdown()

    # The successful job should still have been parked
    assert parked == 1
