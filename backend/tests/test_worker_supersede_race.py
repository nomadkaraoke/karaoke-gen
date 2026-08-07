"""
Regression tests for the admin-reset / render race (job 7f457087).

Scenario: an operator hits the admin "Review" reset button while a render is
still in flight. The render finishes a moment later and attempts its normal
terminal transition (rendering_video -> instrumental_selected), which is now
illegal because the job was reset back to review. Previously this raised
InvalidStateTransitionError, was caught by the worker's generic handler, and
flipped a perfectly-good reset to `failed` with the confusing message:

    Video render failed: Invalid state transition for job 7f457087:
    in_review -> instrumental_selected

These tests lock in the hardened behaviour:

  * A superseded render (status reset OR generation bumped) is discarded
    quietly — it must NOT call fail_job and must NOT write stale outputs.
  * An InvalidStateTransitionError at the terminal step is treated as
    supersession (graceful bail), never a job failure.
  * A normal, un-superseded render still transitions to INSTRUMENTAL_SELECTED.
"""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from backend.models.job import JobStatus
from backend.exceptions import InvalidStateTransitionError
from backend.workers.supersede import capture_generation, check_superseded


# ---------------------------------------------------------------------------
# supersede helper unit tests
# ---------------------------------------------------------------------------

def _job(generation=0, status=JobStatus.RENDERING_VIDEO):
    job = MagicMock()
    job.state_data = {"worker_generation": generation}
    job.status = status
    return job


def test_capture_generation_defaults_to_zero():
    job = MagicMock()
    job.state_data = {}
    assert capture_generation(job) == 0
    job.state_data = None
    assert capture_generation(job) == 0


def test_check_superseded_none_when_current():
    jm = MagicMock()
    jm.get_job.return_value = _job(generation=3, status=JobStatus.RENDERING_VIDEO)
    assert check_superseded(jm, "j", 3, {JobStatus.RENDERING_VIDEO}) is None


def test_check_superseded_detects_generation_bump():
    jm = MagicMock()
    jm.get_job.return_value = _job(generation=4, status=JobStatus.RENDERING_VIDEO)
    reason = check_superseded(jm, "j", 3, {JobStatus.RENDERING_VIDEO})
    assert reason and "generation" in reason


def test_check_superseded_detects_status_reset():
    jm = MagicMock()
    # Status strings are how Job.status deserialises (use_enum_values=True)
    jm.get_job.return_value = _job(generation=3, status="awaiting_review")
    reason = check_superseded(jm, "j", 3, {JobStatus.RENDERING_VIDEO})
    assert reason and "status" in reason


def test_check_superseded_when_job_deleted():
    jm = MagicMock()
    jm.get_job.return_value = None
    assert check_superseded(jm, "j", 0, {JobStatus.RENDERING_VIDEO})


# ---------------------------------------------------------------------------
# JobManager.bump_worker_generation
# ---------------------------------------------------------------------------

def test_bump_worker_generation_uses_atomic_increment():
    from backend.services.job_manager import JobManager

    jm = JobManager.__new__(JobManager)  # bypass __init__/Firestore connect
    jm.firestore = MagicMock()
    bumped_job = MagicMock()
    bumped_job.state_data = {"worker_generation": 5}
    jm.get_job = MagicMock(return_value=bumped_job)

    # Patch the Increment symbol the production code imports so this assertion is
    # robust even when another test has replaced the firestore_v1 module with a
    # mock (real-vs-mock class identity would otherwise flake in the full suite).
    with patch("google.cloud.firestore_v1.Increment") as mock_incr:
        mock_incr.return_value = "INCREMENT(1)"
        result = jm.bump_worker_generation("job-x")

    assert result == 5
    mock_incr.assert_called_once_with(1)  # atomic +1, not a read-modify-write
    args, _ = jm.firestore.update_job.call_args
    payload = args[1]
    assert payload["state_data.worker_generation"] == "INCREMENT(1)"


def test_bump_worker_generation_is_best_effort():
    """A failed bump must never raise — the status fence still protects us."""
    from backend.services.job_manager import JobManager

    jm = JobManager.__new__(JobManager)
    jm.firestore = MagicMock()
    jm.firestore.update_job.side_effect = RuntimeError("firestore down")
    jm.get_job = MagicMock()

    assert jm.bump_worker_generation("job-x") is None


# ---------------------------------------------------------------------------
# render worker (GCE path) supersession behaviour
# ---------------------------------------------------------------------------

def _render_job(generation=1):
    job = MagicMock()
    job.artist = "John Maus"
    job.title = "The Fear"
    job.input_media_gcs_path = "jobs/test/audio.flac"
    job.style_assets = {}
    job.style_params_gcs_path = None
    job.subtitle_offset_ms = 0
    job.prep_only = False
    job.state_data = {"worker_generation": generation, "is_duet": False}
    job.file_urls = {}
    job.status = JobStatus.RENDERING_VIDEO
    return job


def _gce_result():
    return {
        "output_files": ["gs://test-bucket/jobs/test/videos/with_vocals.mkv"],
        "metadata": {},
    }


async def _run_gce_render(job_start, job_after, transition_side_effect=None):
    """Drive process_render_video down the GCE path with controlled get_job snapshots."""
    from backend.workers import render_video_worker as rvw

    jm = MagicMock()
    seq = [job_start]

    def _get_job(_job_id):
        return seq.pop(0) if seq else job_after

    jm.get_job.side_effect = _get_job
    if transition_side_effect is not None:
        jm.transition_to_state.side_effect = transition_side_effect
    else:
        jm.transition_to_state.return_value = True

    encoding_service = MagicMock()
    encoding_service.is_enabled = True
    encoding_service.render_video_on_gce = AsyncMock(return_value=_gce_result())

    storage = MagicMock()
    storage.file_exists.return_value = False

    worker_service = MagicMock()
    worker_service.trigger_video_worker = AsyncMock(return_value=True)

    with patch.object(rvw, "JobManager", return_value=jm), \
         patch.object(rvw, "StorageService", return_value=storage), \
         patch.object(rvw, "get_settings"), \
         patch.object(rvw, "create_job_logger", return_value=MagicMock()), \
         patch.object(rvw, "setup_job_logging", return_value=MagicMock()), \
         patch.object(rvw, "validate_worker_can_run", return_value=None), \
         patch.object(rvw, "get_encoding_service", return_value=encoding_service), \
         patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
        result = await rvw.process_render_video("7f457087")

    return result, jm


@pytest.mark.asyncio
async def test_render_superseded_by_status_reset_does_not_fail_job():
    """Admin reset moved the job to awaiting_review mid-render → discard, don't fail."""
    job_start = _render_job(generation=1)
    job_after = _render_job(generation=1)
    job_after.status = "awaiting_review"  # reset out from under the render

    result, jm = await _run_gce_render(job_start, job_after)

    assert result is False
    jm.fail_job.assert_not_called()
    # No stale outputs written and no terminal INSTRUMENTAL_SELECTED transition
    jm.update_file_url.assert_not_called()
    instrumental_transitions = [
        c for c in jm.transition_to_state.call_args_list
        if c.kwargs.get("new_status") == JobStatus.INSTRUMENTAL_SELECTED
    ]
    assert not instrumental_transitions


@pytest.mark.asyncio
async def test_render_superseded_by_generation_bump_does_not_fail_job():
    """A newer render was triggered (generation bumped) → discard the stale run."""
    job_start = _render_job(generation=1)
    job_after = _render_job(generation=2)  # newer run took over

    result, jm = await _run_gce_render(job_start, job_after)

    assert result is False
    jm.fail_job.assert_not_called()
    jm.update_file_url.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_terminal_transition_is_graceful_not_failure():
    """
    The tight-window case: status/generation still look valid when we check, but
    the reset lands during the terminal transition itself. transition_to_state
    raises InvalidStateTransitionError — the worker must bail gracefully, exactly
    reproducing and fixing the 7f457087 incident.
    """
    job_start = _render_job(generation=1)
    job_after = _render_job(generation=1)  # not superseded by the pre-write checks

    invalid = InvalidStateTransitionError(
        "Invalid state transition for job 7f457087: in_review -> instrumental_selected",
        job_id="7f457087",
        from_status="in_review",
        to_status="instrumental_selected",
        valid_transitions=["review_complete", "awaiting_review", "failed"],
    )
    # 1st transition (RENDERING_VIDEO) ok, 2nd (INSTRUMENTAL_SELECTED) raises
    result, jm = await _run_gce_render(
        job_start, job_after, transition_side_effect=[True, invalid]
    )

    assert result is False
    jm.fail_job.assert_not_called()


@pytest.mark.asyncio
async def test_progress_callback_skips_update_when_superseded():
    """A stale render's progress ticks must not drag a reset job's progress bar."""
    from backend.workers import render_video_worker as rvw

    job_start = _render_job(generation=1)
    superseded = _render_job(generation=2)  # reset bumped the fence mid-render

    jm = MagicMock()
    calls = {"n": 0}

    def _get_job(_job_id):
        calls["n"] += 1
        return job_start if calls["n"] == 1 else superseded

    jm.get_job.side_effect = _get_job
    jm.transition_to_state.return_value = True

    async def _render(_job_id, _config, progress_callback=None):
        progress_callback(50)  # encoder emits a tick on the now-stale job
        return _gce_result()

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
        result = await rvw.process_render_video("7f457087")

    assert result is False  # bails after render (superseded)
    jm.fail_job.assert_not_called()
    # No progress write happened during the superseded tick
    progress_writes = [
        c for c in jm.update_job.call_args_list
        if len(c.args) > 1 and isinstance(c.args[1], dict) and "progress" in c.args[1]
    ]
    assert not progress_writes, "Superseded render must not stamp progress onto the job"


@pytest.mark.asyncio
async def test_normal_render_still_completes():
    """Un-superseded render must still write outputs and transition to INSTRUMENTAL_SELECTED."""
    job_start = _render_job(generation=1)
    job_after = _render_job(generation=1)  # same gen, still rendering_video

    result, jm = await _run_gce_render(job_start, job_after)

    assert result is True
    jm.fail_job.assert_not_called()
    instrumental_transitions = [
        c for c in jm.transition_to_state.call_args_list
        if c.kwargs.get("new_status") == JobStatus.INSTRUMENTAL_SELECTED
    ]
    assert instrumental_transitions, "Normal render must reach INSTRUMENTAL_SELECTED"
    # with_vocals output recorded
    assert any(
        c.args[1:3] == ("videos", "with_vocals")
        for c in jm.update_file_url.call_args_list
    )
