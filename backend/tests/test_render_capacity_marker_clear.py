"""The "waiting for encoding server" marker is cleared once a retried render really runs.

Incident 2026-10-06 (job ebfe4344): after the encoding worker was fixed, the
retried render ran normally but the job card still said "(waiting for encoding
server availability)" because state_data.render_pending_capacity from the earlier
parked attempts was never removed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import JobStatus


def _job(state_data, status=JobStatus.REVIEW_COMPLETE):
    job = MagicMock()
    job.artist = "Randy Vild"
    job.title = "We'll Never Die"
    job.input_media_gcs_path = "jobs/test/audio.flac"
    job.style_assets = {}
    job.style_params_gcs_path = None
    job.subtitle_offset_ms = 0
    job.prep_only = False
    job.state_data = {"worker_generation": 1, "is_duet": False, **state_data}
    job.file_urls = {}
    job.status = status
    return job


async def _run(state_data, ticks):
    from backend.workers import render_video_worker as rvw

    job_start = _job(state_data)
    job_after = _job(state_data, status=JobStatus.RENDERING_VIDEO)
    jm = MagicMock()
    calls = {"n": 0}

    def _get_job(_job_id):
        calls["n"] += 1
        return job_start if calls["n"] == 1 else job_after

    jm.get_job.side_effect = _get_job
    jm.transition_to_state.return_value = True

    async def _render(_job_id, _config, progress_callback=None):
        for tick in ticks:
            progress_callback(tick)
        return {"output_files": ["gs://b/jobs/test/videos/with_vocals.mkv"], "metadata": {}}

    encoding_service = MagicMock()
    encoding_service.is_enabled = True
    encoding_service.render_video_on_gce = AsyncMock(side_effect=_render)
    storage = MagicMock()
    storage.file_exists.return_value = False

    with patch.object(rvw, "JobManager", return_value=jm), \
         patch.object(rvw, "StorageService", return_value=storage), \
         patch.object(rvw, "get_settings"), \
         patch.object(rvw, "create_job_logger", return_value=MagicMock()), \
         patch.object(rvw, "setup_job_logging", return_value=MagicMock()), \
         patch.object(rvw, "validate_worker_can_run", return_value=None), \
         patch.object(rvw, "get_encoding_service", return_value=encoding_service):
        result = await rvw.process_render_video("ebfe4344")
    return result, jm


PENDING = {"render_pending_capacity": {"attempt_count": 7, "last_code": "worker_infra_failure"}}


@pytest.mark.asyncio
async def test_marker_cleared_once_on_first_real_progress():
    result, jm = await _run(PENDING, ticks=[10, 50, 100])

    assert result is True
    clears = [
        c for c in jm.delete_state_data_key.call_args_list
        if c.args == ("ebfe4344", "render_pending_capacity")
    ]
    assert len(clears) == 1


@pytest.mark.asyncio
async def test_marker_kept_until_worker_reports_progress():
    # A zero-progress tick (queued on the worker) is not proof the render is running.
    _, jm = await _run(PENDING, ticks=[0])

    assert not any(
        c.args == ("ebfe4344", "render_pending_capacity")
        for c in jm.delete_state_data_key.call_args_list
    )


@pytest.mark.asyncio
async def test_no_delete_when_job_was_never_parked():
    _, jm = await _run({}, ticks=[10, 50])

    assert not any(
        c.args == ("ebfe4344", "render_pending_capacity")
        for c in jm.delete_state_data_key.call_args_list
    )
