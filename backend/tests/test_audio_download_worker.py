"""
Tests for the audio download worker.

Tests the standalone worker that downloads audio from various sources
(YouTube, Spotify, RED/OPS) and triggers processing workers.
"""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, UTC

from backend.models.job import Job, JobStatus
from backend.workers.audio_download_worker import (
    process_audio_download,
    _extract_gcs_path,
    _download_audio,
    DOWNLOAD_WAIT_TIMEOUT_SECONDS,
)
from backend.services.audio_search_service import DownloadError


def _cloud_run_task_timeout_seconds() -> int:
    """Parse the audio-download-job task timeout from the Pulumi module."""
    import pathlib
    import re

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "infrastructure" / "modules" / "cloud_run.py"
    ).read_text()
    # The audio-download job block sets timeout="NNNNs" right before max_retries.
    m = re.search(r'timeout="(\d+)s",\s*\n\s*max_retries=2,\s*\n\s*vpc_access=', src)
    assert m, "could not locate audio-download-job timeout in cloud_run.py"
    return int(m.group(1))


def test_download_wait_timeout_below_cloud_run_task_timeout():
    """The in-worker download wait MUST expire before Cloud Run SIGKILLs the task.

    If they are equal (the old 600s/600s), the SIGKILL bypasses graceful
    failure handling and leaves an in-flight auto-retry racing manual retries
    (job 1cd29294). Keep meaningful headroom for the final upload + transition.
    """
    task_timeout = _cloud_run_task_timeout_seconds()
    assert DOWNLOAD_WAIT_TIMEOUT_SECONDS < task_timeout, (
        f"download wait {DOWNLOAD_WAIT_TIMEOUT_SECONDS}s must be < Cloud Run "
        f"task timeout {task_timeout}s"
    )
    assert task_timeout - DOWNLOAD_WAIT_TIMEOUT_SECONDS >= 60


def _make_job(
    job_id: str = "test-job-123",
    status=JobStatus.DOWNLOADING_AUDIO,
    source_name: str = "YouTube",
    source_id: str = "dQw4w9WgXcQ",
    state_data: dict = None,
    **kwargs,
) -> Job:
    """Helper to create test Job objects."""
    return Job(
        job_id=job_id,
        artist="Test Artist",
        title="Test Song",
        status=status,
        source_name=source_name,
        source_id=source_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        state_data=state_data or {},
        **kwargs,
    )


class TestExtractGcsPath:
    """Tests for _extract_gcs_path helper."""

    def test_strips_primary_bucket_prefix(self):
        path = "gs://karaoke-gen-storage-nomadkaraoke/uploads/123/audio/song.flac"
        assert _extract_gcs_path(path) == "uploads/123/audio/song.flac"

    def test_strips_alternate_bucket_prefix(self):
        path = "gs://karaoke-gen-storage/uploads/123/audio/song.flac"
        assert _extract_gcs_path(path) == "uploads/123/audio/song.flac"

    def test_unknown_gs_bucket_returned_as_is(self):
        """Unknown bucket gs:// paths are returned unchanged (only known buckets stripped)."""
        path = "gs://other-bucket/uploads/123/audio/song.flac"
        # The function only strips known bucket prefixes
        assert _extract_gcs_path(path) == path

    def test_returns_plain_path_unchanged(self):
        path = "uploads/123/audio/song.flac"
        assert _extract_gcs_path(path) == "uploads/123/audio/song.flac"


class TestProcessAudioDownload:
    """Tests for the main process_audio_download function."""

    @pytest.mark.asyncio
    async def test_returns_false_when_job_not_found(self):
        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"):
            mock_jm = MagicMock()
            mock_jm.get_job.return_value = None
            mock_jm_cls.return_value = mock_jm

            result = await process_audio_download("nonexistent-job")
            assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_download_params(self):
        """Job in wrong state with no download params should abort."""
        job = _make_job(
            status=JobStatus.PENDING,
            source_name=None,
            source_id=None,
        )

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"):
            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm_cls.return_value = mock_jm

            result = await process_audio_download("test-job-123")
            assert result is False

    @pytest.mark.asyncio
    async def test_youtube_download_success(self):
        """Successful YouTube download triggers workers."""
        job = _make_job(source_name="YouTube", source_id="dQw4w9WgXcQ")

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory, \
             patch("backend.workers.audio_download_worker.reconcile_and_maybe_pause",
                   new_callable=AsyncMock, return_value=False):

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.mp3", "song.mp3")

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            result = await process_audio_download("test-job-123")

            assert result is True
            mock_jm.update_job.assert_any_call("test-job-123", {
                'input_media_gcs_path': "uploads/test-job-123/audio/song.mp3",
                'filename': "song.mp3",
            })
            # Original audio recorded in file_urls so it shows in the admin files
            # manifest (and can be staged into the Dropbox folder) for URL/search jobs.
            mock_jm.update_file_url.assert_any_call(
                "test-job-123", 'input', 'audio', "uploads/test-job-123/audio/song.mp3"
            )
            mock_jm.transition_to_state.assert_called_once()
            mock_ws.trigger_audio_worker.assert_awaited_once_with("test-job-123")
            mock_ws.trigger_lyrics_worker.assert_awaited_once_with("test-job-123")

    @pytest.mark.asyncio
    async def test_download_error_fails_job(self):
        """DownloadError should fail the job and return False."""
        job = _make_job()

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl:

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm_cls.return_value = mock_jm

            mock_dl.side_effect = DownloadError("Download timed out")

            result = await process_audio_download("test-job-123")

            assert result is False
            mock_jm.fail_job.assert_called_once()
            assert "Download timed out" in mock_jm.fail_job.call_args[0][1]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        JobStatus.DOWNLOADING,
        JobStatus.AWAITING_AUDIO_EDIT,
        JobStatus.AUDIO_COMPLETE,
        JobStatus.TRANSCRIBING,
        JobStatus.GENERATING_SCREENS,
        JobStatus.AWAITING_REVIEW,
        JobStatus.RENDERING_VIDEO,
        JobStatus.COMPLETE,
    ])
    async def test_skips_when_download_already_done(self, status):
        """If status is past DOWNLOADING_AUDIO, the download must not be re-run.

        This prevents Cloud Run Job retries of a successful execution from
        re-downloading, double-transitioning state, and double-triggering
        downstream workers (see job 8a9c74ff).
        """
        job = _make_job(status=status)

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory:

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm_cls.return_value = mock_jm

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            result = await process_audio_download("test-job-123")

            assert result is True, f"Expected idempotent success when status={status.value}"
            mock_dl.assert_not_awaited()
            mock_jm.transition_to_state.assert_not_called()
            mock_jm.fail_job.assert_not_called()
            mock_ws.trigger_audio_worker.assert_not_awaited()
            mock_ws.trigger_lyrics_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_concurrent_run_completing_mid_download_is_idempotent(self):
        """A second run finishing while we download must not fail the job.

        Regression for job 1cd29294: a manual /retry raced this task's Cloud
        Run auto-retry. The loser downloaded, then tried DOWNLOADING ->
        DOWNLOADING, which raised InvalidStateTransitionError, and the generic
        except marked a job the winner had already advanced as FAILED. The
        loser must instead detect the advance and return success without
        transitioning, failing, or re-triggering downstream workers.
        """
        entry_job = _make_job(status=JobStatus.DOWNLOADING_AUDIO)
        # By the time our download finishes, a concurrent run advanced the job.
        advanced_job = _make_job(status=JobStatus.DOWNLOADING)

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory:

            mock_jm = MagicMock()
            # Entry sees DOWNLOADING_AUDIO; the pre-transition re-read sees the
            # concurrent run's advance to DOWNLOADING.
            mock_jm.get_job.side_effect = [entry_job, advanced_job]
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.mp3", "song.mp3")

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            result = await process_audio_download("test-job-123")

            assert result is True
            mock_dl.assert_awaited_once()  # we did download before detecting the race
            mock_jm.transition_to_state.assert_not_called()
            mock_jm.fail_job.assert_not_called()
            mock_ws.trigger_audio_worker.assert_not_awaited()
            mock_ws.trigger_lyrics_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_transition_at_commit_treated_as_success_if_advanced(self):
        """A race that slips past the guard still must not fail the job.

        If the job advances between the pre-transition re-read and the
        transition itself, transition_to_state returns False (raise_on_invalid
        is off); we re-read, see the advance, and return success.
        """
        entry_job = _make_job(status=JobStatus.DOWNLOADING_AUDIO)

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory:

            mock_jm = MagicMock()
            # Entry + pre-transition guard both see DOWNLOADING_AUDIO (guard
            # passes); the transition fails; the post-failure re-read sees the
            # concurrent advance.
            mock_jm.get_job.side_effect = [
                entry_job,
                _make_job(status=JobStatus.DOWNLOADING_AUDIO),
                _make_job(status=JobStatus.DOWNLOADING),
            ]
            mock_jm.transition_to_state.return_value = False
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.mp3", "song.mp3")
            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            result = await process_audio_download("test-job-123")

            assert result is True
            mock_jm.fail_job.assert_not_called()
            mock_ws.trigger_audio_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_state_data_selection_when_available(self):
        """Should prefer state_data search results over job-level params."""
        job = _make_job(
            source_name="YouTube",
            source_id="fallback-id",
            state_data={
                'audio_search_results': [
                    {'provider': 'Spotify', 'source_id': 'spotify123', 'target_file': None, 'url': None},
                ],
                'selected_audio_index': 0,
            },
        )

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory, \
             patch("backend.workers.audio_download_worker.reconcile_and_maybe_pause",
                   new_callable=AsyncMock, return_value=False):

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.flac", "song.flac")

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            await process_audio_download("test-job-123")

            # Should have called with Spotify params from state_data, not YouTube fallback
            call_kwargs = mock_dl.call_args[1]
            assert call_kwargs['source_name'] == 'Spotify'
            assert call_kwargs['source_id'] == 'spotify123'


class TestCloudRunRetryPending:
    """Tests for the cloud_run_retry_pending signal set on fail_job.

    Cloud Run Job auto-retries failed task attempts. When the worker fails
    on attempt N and N < max_retries, the worker must mark the job so the
    UI can show "Retrying automatically..." instead of a Retry button, and
    so the /retry endpoint refuses to start a second parallel execution.
    """

    @pytest.mark.asyncio
    async def test_marks_retry_pending_when_attempts_remain(self):
        """Failure on attempt 0 (with attempts 1,2 still possible) marks retry pending."""
        job = _make_job()

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch.dict(os.environ, {"CLOUD_RUN_TASK_ATTEMPT": "0"}, clear=False):

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm_cls.return_value = mock_jm

            mock_dl.side_effect = DownloadError("Download timed out")

            result = await process_audio_download("test-job-123")

            assert result is False
            # state_data.cloud_run_retry_pending should be set before fail_job
            update_calls = [c for c in mock_jm.update_state_data.call_args_list
                            if c.args[1] == 'cloud_run_retry_pending']
            assert len(update_calls) == 1, "Expected exactly one cloud_run_retry_pending update"
            pending = update_calls[0].args[2]
            assert pending['expected_attempt'] == 1
            assert 'expires_at' in pending
            mock_jm.fail_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_clears_stale_retry_pending_on_final_attempt(self):
        """Failure on the final attempt must clear any marker left by an
        earlier attempt — otherwise the UI shows "Retrying automatically..."
        for the marker's full TTL after the job has actually failed
        permanently, blocking the user from clicking Retry.
        """
        job = _make_job()

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch.dict(os.environ, {"CLOUD_RUN_TASK_ATTEMPT": "2"}, clear=False):

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm_cls.return_value = mock_jm

            mock_dl.side_effect = DownloadError("Download timed out")

            result = await process_audio_download("test-job-123")

            assert result is False
            # Final attempt must NOT write a new pending dict, but MUST clear
            # any stale marker (set to None).
            pending_writes = [
                c for c in mock_jm.update_state_data.call_args_list
                if c.args[1] == 'cloud_run_retry_pending' and c.args[2] is not None
            ]
            pending_clears = [
                c for c in mock_jm.update_state_data.call_args_list
                if c.args[1] == 'cloud_run_retry_pending' and c.args[2] is None
            ]
            assert pending_writes == [], "Final attempt must not write a new pending marker"
            assert len(pending_clears) == 1, (
                f"Final attempt must clear stale marker; got: {mock_jm.update_state_data.call_args_list}"
            )
            mock_jm.fail_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_clears_retry_pending_and_error_on_success(self):
        """Successful download clears any stale retry-pending flag and error message."""
        job = _make_job(
            error_message="Audio download failed: previous attempt timeout",
            state_data={'cloud_run_retry_pending': {'expires_at': '2026-05-16T16:00:00Z', 'expected_attempt': 1}},
        )

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory, \
             patch("backend.workers.audio_download_worker.reconcile_and_maybe_pause",
                   new_callable=AsyncMock, return_value=False):

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.mp3", "song.mp3")

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            result = await process_audio_download("test-job-123")

            assert result is True
            # Look at update_job calls to find the error-clearing one
            error_clear_calls = [
                c for c in mock_jm.update_job.call_args_list
                if isinstance(c.args[1], dict)
                and c.args[1].get('error_message') is None
                and 'error_details' in c.args[1]
            ]
            assert len(error_clear_calls) >= 1, (
                f"Expected an update_job call clearing error_message; got: "
                f"{mock_jm.update_job.call_args_list}"
            )
            # Look for cloud_run_retry_pending being cleared (set to None)
            pending_clears = [
                c for c in mock_jm.update_state_data.call_args_list
                if c.args[1] == 'cloud_run_retry_pending' and c.args[2] is None
            ]
            assert len(pending_clears) == 1, (
                f"Expected cloud_run_retry_pending to be cleared; got state_data updates: "
                f"{mock_jm.update_state_data.call_args_list}"
            )


class TestAudioEditPreGeneration:
    """Tests for pre-generation of editor assets when requires_audio_edit is set."""

    @pytest.mark.asyncio
    async def test_pregenerates_assets_when_audio_edit_requested(self):
        """Should transcode OGG + cache waveform before transitioning to awaiting_audio_edit."""
        job = _make_job(state_data={'requires_audio_edit': True})

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.services.audio_transcoding_service.AudioTranscodingService") as MockTS, \
             patch("backend.services.audio_analysis_service.AudioAnalysisService") as MockAS:

            mock_jm = MagicMock()
            # First get_job returns the initial job, second returns the one with requires_audio_edit
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.flac", "song.flac")

            mock_ts = MagicMock()
            MockTS.return_value = mock_ts

            mock_as = MagicMock()
            mock_as.cache_waveform_data.return_value = ([0.1, 0.5], 200.0)
            MockAS.return_value = mock_as

            result = await process_audio_download("test-job-123")

            assert result is True
            # Should have transcoded OGG
            mock_ts.transcode_if_needed.assert_called_once_with("uploads/test-job-123/audio/song.flac")
            # Should have cached waveform
            mock_as.cache_waveform_data.assert_called_once_with(
                "uploads/test-job-123/audio/song.flac",
                "test-job-123",
                "jobs/test-job-123/audio_edit/waveform_original.json",
            )
            # Should have stored cache path in state_data
            mock_jm.update_state_data.assert_any_call(
                "test-job-123", "audio_edit_waveform_cache_path",
                "jobs/test-job-123/audio_edit/waveform_original.json",
            )
            # Should have transitioned to awaiting_audio_edit
            calls = mock_jm.transition_to_state.call_args_list
            assert any(c.kwargs.get('new_status') == JobStatus.AWAITING_AUDIO_EDIT for c in calls)

    @pytest.mark.asyncio
    async def test_pregeneration_failure_is_nonfatal(self):
        """Pre-generation failure should not prevent the job from reaching awaiting_audio_edit."""
        job = _make_job(state_data={'requires_audio_edit': True})

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.services.audio_transcoding_service.AudioTranscodingService") as MockTS:

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.flac", "song.flac")

            # Transcoding fails
            mock_ts = MagicMock()
            mock_ts.transcode_if_needed.side_effect = RuntimeError("ffmpeg not found")
            MockTS.return_value = mock_ts

            result = await process_audio_download("test-job-123")

            # Should still succeed and transition to awaiting_audio_edit
            assert result is True
            calls = mock_jm.transition_to_state.call_args_list
            assert any(c.kwargs.get('new_status') == JobStatus.AWAITING_AUDIO_EDIT for c in calls)


class TestFfprobeSeconds:
    """Tests for the _ffprobe_seconds helper in duration_reconciliation."""

    def test_ffprobe_seconds_parses_duration(self):
        """Should return float duration when ffprobe succeeds."""
        from backend.services.duration_reconciliation import _ffprobe_seconds

        job = MagicMock()
        job.id = "job-abc"
        job.input_media_gcs_path = "gs://b/a.flac"

        storage = MagicMock()
        storage.generate_signed_url.return_value = "https://signed"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"format": {"duration": "1957.8"}}'

        with patch("backend.services.duration_reconciliation.subprocess.run",
                   return_value=mock_result) as mock_run:
            result = _ffprobe_seconds(job, storage)

        assert result == 1957.8
        storage.generate_signed_url.assert_called_once_with("gs://b/a.flac", expiration_minutes=5)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "ffprobe" in args[0]
        assert "https://signed" in args

    def test_ffprobe_seconds_returns_none_on_error(self):
        """Should return None when ffprobe exits with non-zero returncode."""
        from backend.services.duration_reconciliation import _ffprobe_seconds

        job = MagicMock()
        job.id = "job-abc"
        job.input_media_gcs_path = "gs://b/a.flac"

        storage = MagicMock()
        storage.generate_signed_url.return_value = "https://signed"

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "No such file"

        with patch("backend.services.duration_reconciliation.subprocess.run",
                   return_value=mock_result):
            result = _ffprobe_seconds(job, storage)

        assert result is None

    def test_ffprobe_seconds_returns_none_when_no_gcs_path(self):
        """Should return None when job has no input_media_gcs_path."""
        from backend.services.duration_reconciliation import _ffprobe_seconds

        job = MagicMock()
        job.id = "job-abc"
        job.input_media_gcs_path = None

        storage = MagicMock()

        result = _ffprobe_seconds(job, storage)

        assert result is None
        storage.generate_signed_url.assert_not_called()


class TestReconcileAndMaybePause:
    """Tests for the reconcile_and_maybe_pause pipeline-wiring function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_action_is_pause(self):
        """Should return True (meaning: block worker) when reconcile says pause."""
        from backend.services.duration_reconciliation import (
            reconcile_and_maybe_pause, ReconcileResult,
        )

        # JobManager/StorageService are imported at function scope inside reconcile_and_maybe_pause,
        # so we patch them at their source modules. get_user_service is also at source module.
        with patch("backend.services.duration_reconciliation.reconcile_duration",
                   return_value=ReconcileResult(action="pause", pending_additional_credits=1)), \
             patch("backend.services.job_manager.JobManager"), \
             patch("backend.services.user_service.get_user_service"), \
             patch("backend.services.storage_service.StorageService"):
            result = await reconcile_and_maybe_pause("job-xyz")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_action_is_cancel(self):
        """Should return True (meaning: block worker) when reconcile says cancel."""
        from backend.services.duration_reconciliation import (
            reconcile_and_maybe_pause, ReconcileResult,
        )

        with patch("backend.services.duration_reconciliation.reconcile_duration",
                   return_value=ReconcileResult(action="cancel")), \
             patch("backend.services.job_manager.JobManager"), \
             patch("backend.services.user_service.get_user_service"), \
             patch("backend.services.storage_service.StorageService"):
            result = await reconcile_and_maybe_pause("job-xyz")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_action_is_proceed(self):
        """Should return False (meaning: allow workers) when reconcile says proceed."""
        from backend.services.duration_reconciliation import (
            reconcile_and_maybe_pause, ReconcileResult,
        )

        with patch("backend.services.duration_reconciliation.reconcile_duration",
                   return_value=ReconcileResult(action="proceed")), \
             patch("backend.services.job_manager.JobManager"), \
             patch("backend.services.user_service.get_user_service"), \
             patch("backend.services.storage_service.StorageService"):
            result = await reconcile_and_maybe_pause("job-xyz")

        assert result is False


class TestNoEditPathRespectsPause:
    """Tests that the no-edit path in process_audio_download respects reconcile_and_maybe_pause."""

    @pytest.mark.asyncio
    async def test_workers_not_triggered_when_paused(self):
        """When reconcile_and_maybe_pause returns True, trigger_audio/lyrics_worker must not be called."""
        job = _make_job(source_name="YouTube", source_id="dQw4w9WgXcQ")

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory, \
             patch("backend.workers.audio_download_worker.reconcile_and_maybe_pause",
                   new_callable=AsyncMock, return_value=True) as mock_ramp:

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.mp3", "song.mp3")

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            result = await process_audio_download("test-job-123")

            assert result is True
            mock_ramp.assert_awaited_once_with("test-job-123")
            mock_ws.trigger_audio_worker.assert_not_awaited()
            mock_ws.trigger_lyrics_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_workers_triggered_when_not_paused(self):
        """When reconcile_and_maybe_pause returns False, workers proceed as normal."""
        job = _make_job(source_name="YouTube", source_id="dQw4w9WgXcQ")

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory, \
             patch("backend.workers.audio_download_worker.reconcile_and_maybe_pause",
                   new_callable=AsyncMock, return_value=False) as mock_ramp:

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.mp3", "song.mp3")

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            result = await process_audio_download("test-job-123")

            assert result is True
            mock_ramp.assert_awaited_once_with("test-job-123")
            mock_ws.trigger_audio_worker.assert_awaited_once_with("test-job-123")
            mock_ws.trigger_lyrics_worker.assert_awaited_once_with("test-job-123")


class TestDownloadAudio:
    """Tests for _download_audio routing."""

    @pytest.mark.asyncio
    async def test_routes_youtube_download(self):
        mock_storage = MagicMock()

        with patch("backend.workers.audio_download_worker._download_youtube", new_callable=AsyncMock) as mock_yt:
            mock_yt.return_value = ("uploads/j/audio/song.mp3", "song.mp3")

            result = await _download_audio(
                job_id="j", source_name="YouTube", source_id="vid123",
                target_file=None, download_url=None, remote_search_id=None,
                selection_index=None, selected={}, storage_service=mock_storage,
            )

            assert result == ("uploads/j/audio/song.mp3", "song.mp3")
            mock_yt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_red_to_torrent(self):
        mock_storage = MagicMock()

        with patch("backend.workers.audio_download_worker._download_torrent", new_callable=AsyncMock) as mock_torrent:
            mock_torrent.return_value = ("uploads/j/audio/song.flac", "song.flac")

            await _download_audio(
                job_id="j", source_name="RED", source_id="123",
                target_file="track.flac", download_url=None, remote_search_id=None,
                selection_index=None, selected={}, storage_service=mock_storage,
            )

            mock_torrent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_spotify_download(self):
        mock_storage = MagicMock()

        with patch("backend.workers.audio_download_worker._download_spotify", new_callable=AsyncMock) as mock_sp:
            mock_sp.return_value = ("uploads/j/audio/song.ogg", "song.ogg")

            await _download_audio(
                job_id="j", source_name="Spotify", source_id="track123",
                target_file=None, download_url=None, remote_search_id=None,
                selection_index=None, selected={}, storage_service=mock_storage,
            )

            mock_sp.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_for_unsupported_source(self):
        mock_storage = MagicMock()

        with pytest.raises(DownloadError, match="Unsupported audio source"):
            await _download_audio(
                job_id="j", source_name="SoundCloud", source_id="123",
                target_file=None, download_url=None, remote_search_id=None,
                selection_index=None, selected={}, storage_service=mock_storage,
            )
