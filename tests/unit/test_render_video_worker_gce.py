"""Tests for render_video_worker GCE routing."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


def _make_mock_job(
    job_id="test-job-123",
    artist="Test Artist",
    title="Test Song",
    input_media_gcs_path="jobs/test-job-123/audio/input.flac",
    style_params_gcs_path=None,
    style_assets=None,
    subtitle_offset_ms=0,
    prep_only=False,
):
    """Create a mock job object with all required attributes."""
    job = MagicMock()
    job.artist = artist
    job.title = title
    job.input_media_gcs_path = input_media_gcs_path
    job.style_params_gcs_path = style_params_gcs_path
    job.style_assets = style_assets or {}
    job.subtitle_offset_ms = subtitle_offset_ms
    job.prep_only = prep_only
    job.state_data = {}
    job.file_urls = {}
    return job


# All the patches needed to isolate process_render_video
WORKER_PATCHES = {
    "job_manager_cls": "backend.workers.render_video_worker.JobManager",
    "storage_cls": "backend.workers.render_video_worker.StorageService",
    "get_settings": "backend.workers.render_video_worker.get_settings",
    "get_encoding_service": "backend.workers.render_video_worker.get_encoding_service",
    "create_job_logger": "backend.workers.render_video_worker.create_job_logger",
    "setup_job_logging": "backend.workers.render_video_worker.setup_job_logging",
    "validate_worker_can_run": "backend.workers.render_video_worker.validate_worker_can_run",
    "job_span": "backend.workers.render_video_worker.job_span",
    "job_logging_context": "backend.workers.render_video_worker.job_logging_context",
}


class TestRenderVideoWorkerGCERouting:
    """Test that render_video_worker delegates to GCE when encoding service is enabled."""

    def _run_with_mocks(self, job, gce_result=None, encoding_enabled=True):
        """Run process_render_video with all dependencies mocked.

        Returns a dict of all mock objects for assertions.
        """
        if gce_result is None:
            gce_result = {
                "output_files": [
                    "gs://test-bucket/jobs/test-job-123/videos/with_vocals.mkv",
                    "gs://test-bucket/jobs/test-job-123/lyrics/karaoke.ass",
                    "gs://test-bucket/jobs/test-job-123/lyrics/karaoke.lrc",
                    "gs://test-bucket/jobs/test-job-123/lyrics/corrected.txt",
                ],
                "metadata": {
                    "countdown_padding_added": False,
                    "countdown_padding_seconds": 0,
                },
            }

        mocks = {}
        patches = []

        for name, target in WORKER_PATCHES.items():
            p = patch(target)
            patches.append(p)
            mocks[name] = p.start()

        # Also patch worker_service import used in the GCE path
        worker_svc_patch = patch("backend.services.worker_service.get_worker_service")
        patches.append(worker_svc_patch)
        mocks["get_worker_service"] = worker_svc_patch.start()

        try:
            # Configure JobManager
            job_manager = MagicMock()
            mocks["job_manager_cls"].return_value = job_manager
            job_manager.get_job.return_value = job

            # Configure StorageService
            storage = MagicMock()
            mocks["storage_cls"].return_value = storage
            storage.file_exists.return_value = False  # no updated corrections by default

            # Configure settings
            settings = MagicMock()
            settings.gcs_bucket_name = "test-bucket"
            mocks["get_settings"].return_value = settings

            # Configure encoding service
            encoding_service = MagicMock()
            encoding_service.is_enabled = encoding_enabled
            encoding_service.render_video_on_gce = AsyncMock(return_value=gce_result)
            mocks["get_encoding_service"].return_value = encoding_service

            # Configure logging mocks
            job_log = MagicMock()
            mocks["create_job_logger"].return_value = job_log
            mocks["setup_job_logging"].return_value = MagicMock()
            mocks["validate_worker_can_run"].return_value = None

            # Configure job_span as context manager
            span_mock = MagicMock()
            span_cm = MagicMock()
            span_cm.__enter__ = MagicMock(return_value=span_mock)
            span_cm.__exit__ = MagicMock(return_value=False)
            mocks["job_span"].return_value = span_cm

            # Configure job_logging_context as context manager
            log_ctx = MagicMock()
            log_ctx.__enter__ = MagicMock(return_value=None)
            log_ctx.__exit__ = MagicMock(return_value=False)
            mocks["job_logging_context"].return_value = log_ctx

            # Configure worker_service
            worker_service = MagicMock()
            worker_service.trigger_video_worker = AsyncMock()
            mocks["get_worker_service"].return_value = worker_service

            # Store configured instances for assertions
            mocks["job_manager"] = job_manager
            mocks["storage"] = storage
            mocks["encoding_service"] = encoding_service
            mocks["job_log"] = job_log
            mocks["span"] = span_mock
            mocks["worker_service"] = worker_service

            from backend.workers.render_video_worker import process_render_video

            result = asyncio.get_event_loop().run_until_complete(
                process_render_video("test-job-123")
            )
            mocks["result"] = result
            return mocks
        finally:
            for p in patches:
                p.stop()

    def test_delegates_to_gce_when_enabled(self):
        """When encoding_service.is_enabled is True, render_video_on_gce should be called."""
        job = _make_mock_job()
        mocks = self._run_with_mocks(job, encoding_enabled=True)

        # Verify GCE path was taken
        assert mocks["result"] is True
        mocks["encoding_service"].render_video_on_gce.assert_called_once()

        # Verify render config has correct fields
        call_args = mocks["encoding_service"].render_video_on_gce.call_args
        job_id_arg = call_args[0][0]
        render_config = call_args[0][1]

        assert job_id_arg == "test-job-123"
        assert render_config["artist"] == "Test Artist"
        assert render_config["title"] == "Test Song"
        assert "gs://test-bucket/" in render_config["audio_gcs_path"]
        assert render_config["original_corrections_gcs_path"] == "gs://test-bucket/jobs/test-job-123/lyrics/corrections.json"
        assert render_config["output_gcs_prefix"] == "gs://test-bucket/jobs/test-job-123/"
        assert render_config["video_resolution"] == "4k"
        assert render_config["subtitle_offset_ms"] == 0

    def test_gce_path_updates_file_urls(self):
        """Verify file_urls are updated from GCE response output_files."""
        job = _make_mock_job()
        gce_result = {
            "output_files": [
                "gs://test-bucket/jobs/test-job-123/videos/with_vocals.mkv",
                "gs://test-bucket/jobs/test-job-123/lyrics/karaoke.ass",
                "gs://test-bucket/jobs/test-job-123/lyrics/karaoke.lrc",
                "gs://test-bucket/jobs/test-job-123/lyrics/corrected.txt",
            ],
            "metadata": {
                "countdown_padding_added": False,
                "countdown_padding_seconds": 0,
            },
        }
        mocks = self._run_with_mocks(job, gce_result=gce_result, encoding_enabled=True)

        jm = mocks["job_manager"]
        # Check that update_file_url was called for each output type
        update_calls = {
            (call[0][1], call[0][2]): call[0][3]
            for call in jm.update_file_url.call_args_list
        }

        assert ("videos", "with_vocals") in update_calls
        assert ("lyrics", "ass") in update_calls
        assert ("lyrics", "lrc") in update_calls
        assert ("lyrics", "corrected_txt") in update_calls

    def test_gce_path_handles_countdown_padding(self):
        """Verify countdown padding metadata is stored when present."""
        job = _make_mock_job()
        job.state_data = {"lyrics_metadata": {"existing": True}}
        gce_result = {
            "output_files": [
                "gs://test-bucket/jobs/test-job-123/videos/with_vocals.mkv",
            ],
            "metadata": {
                "countdown_padding_added": True,
                "countdown_padding_seconds": 3,
            },
        }
        mocks = self._run_with_mocks(job, gce_result=gce_result, encoding_enabled=True)

        jm = mocks["job_manager"]
        # Should have called update_state_data with lyrics_metadata
        update_calls = [
            c for c in jm.update_state_data.call_args_list
            if c[0][1] == "lyrics_metadata"
        ]
        assert len(update_calls) == 1
        metadata = update_calls[0][0][2]
        assert metadata["has_countdown_padding"] is True
        assert metadata["countdown_padding_seconds"] == 3
        assert metadata["existing"] is True  # preserved existing data

    def test_gce_path_triggers_video_worker_when_not_prep_only(self):
        """After GCE render, should trigger video worker for final encoding."""
        job = _make_mock_job(prep_only=False)
        mocks = self._run_with_mocks(job, encoding_enabled=True)

        mocks["worker_service"].trigger_video_worker.assert_called_once_with("test-job-123")
        # Should transition to INSTRUMENTAL_SELECTED
        transition_calls = mocks["job_manager"].transition_to_state.call_args_list
        final_transition = transition_calls[-1]
        from backend.models.job import JobStatus
        assert final_transition[1]["new_status"] == JobStatus.INSTRUMENTAL_SELECTED

    def test_gce_path_stops_at_prep_complete_when_prep_only(self):
        """In prep_only mode, should not trigger video worker."""
        job = _make_mock_job(prep_only=True)
        mocks = self._run_with_mocks(job, encoding_enabled=True)

        mocks["worker_service"].trigger_video_worker.assert_not_called()
        # Should transition to PREP_COMPLETE
        transition_calls = mocks["job_manager"].transition_to_state.call_args_list
        final_transition = transition_calls[-1]
        from backend.models.job import JobStatus
        assert final_transition[1]["new_status"] == JobStatus.PREP_COMPLETE

    def test_falls_back_to_local_when_gce_disabled(self):
        """When encoding_service.is_enabled is False, should NOT call render_video_on_gce.

        We can't fully test the local path without mocking OutputGenerator etc.,
        so we just verify the GCE path is NOT taken.
        """
        job = _make_mock_job()
        # The local path will fail because we haven't mocked OutputGenerator,
        # but that's fine - we just verify GCE was NOT called
        try:
            mocks = self._run_with_mocks(job, encoding_enabled=False)
        except Exception:
            pass  # Local path will fail due to missing mocks, that's expected

        # This would have been set if _run_with_mocks completed
        # Just verify via the encoding_service mock
        # Re-run with patches to check
        encoding_svc = MagicMock()
        encoding_svc.is_enabled = False
        encoding_svc.render_video_on_gce = AsyncMock()

        with patch("backend.workers.render_video_worker.get_encoding_service", return_value=encoding_svc):
            with patch("backend.workers.render_video_worker.JobManager") as jm_cls:
                with patch("backend.workers.render_video_worker.StorageService"):
                    with patch("backend.workers.render_video_worker.get_settings"):
                        with patch("backend.workers.render_video_worker.create_job_logger"):
                            with patch("backend.workers.render_video_worker.setup_job_logging"):
                                with patch("backend.workers.render_video_worker.validate_worker_can_run", return_value=None):
                                    with patch("backend.workers.render_video_worker.job_span") as js:
                                        with patch("backend.workers.render_video_worker.job_logging_context") as jlc:
                                            span_cm = MagicMock()
                                            span_cm.__enter__ = MagicMock(return_value=MagicMock())
                                            span_cm.__exit__ = MagicMock(return_value=False)
                                            js.return_value = span_cm
                                            log_ctx = MagicMock()
                                            log_ctx.__enter__ = MagicMock(return_value=None)
                                            log_ctx.__exit__ = MagicMock(return_value=False)
                                            jlc.return_value = log_ctx
                                            jm_cls.return_value.get_job.return_value = job
                                            try:
                                                from backend.workers.render_video_worker import process_render_video
                                                asyncio.get_event_loop().run_until_complete(
                                                    process_render_video("test-job-123")
                                                )
                                            except Exception:
                                                pass  # Local path will fail
                                            encoding_svc.render_video_on_gce.assert_not_called()

    def test_gce_path_includes_updated_corrections_when_present(self):
        """When updated corrections exist in GCS, they should be in render_config."""
        job = _make_mock_job()
        mocks = self._run_with_mocks.__wrapped__ if hasattr(self._run_with_mocks, '__wrapped__') else None

        # Run with storage.file_exists returning True for updated corrections
        gce_result = {
            "output_files": ["gs://test-bucket/jobs/test-job-123/videos/with_vocals.mkv"],
            "metadata": {"countdown_padding_added": False, "countdown_padding_seconds": 0},
        }

        # We need a custom run to set file_exists=True
        patches_list = []
        mock_dict = {}
        for name, target in WORKER_PATCHES.items():
            p = patch(target)
            patches_list.append(p)
            mock_dict[name] = p.start()

        worker_svc_patch = patch("backend.services.worker_service.get_worker_service")
        patches_list.append(worker_svc_patch)
        mock_dict["get_worker_service"] = worker_svc_patch.start()

        try:
            job_manager = MagicMock()
            mock_dict["job_manager_cls"].return_value = job_manager
            job_manager.get_job.return_value = job

            storage = MagicMock()
            mock_dict["storage_cls"].return_value = storage
            storage.file_exists.return_value = True  # Updated corrections exist

            settings = MagicMock()
            settings.gcs_bucket_name = "test-bucket"
            mock_dict["get_settings"].return_value = settings

            encoding_service = MagicMock()
            encoding_service.is_enabled = True
            encoding_service.render_video_on_gce = AsyncMock(return_value=gce_result)
            mock_dict["get_encoding_service"].return_value = encoding_service

            job_log = MagicMock()
            mock_dict["create_job_logger"].return_value = job_log
            mock_dict["setup_job_logging"].return_value = MagicMock()
            mock_dict["validate_worker_can_run"].return_value = None

            span_cm = MagicMock()
            span_cm.__enter__ = MagicMock(return_value=MagicMock())
            span_cm.__exit__ = MagicMock(return_value=False)
            mock_dict["job_span"].return_value = span_cm

            log_ctx = MagicMock()
            log_ctx.__enter__ = MagicMock(return_value=None)
            log_ctx.__exit__ = MagicMock(return_value=False)
            mock_dict["job_logging_context"].return_value = log_ctx

            worker_service = MagicMock()
            worker_service.trigger_video_worker = AsyncMock()
            mock_dict["get_worker_service"].return_value = worker_service

            from backend.workers.render_video_worker import process_render_video
            asyncio.get_event_loop().run_until_complete(process_render_video("test-job-123"))

            call_args = encoding_service.render_video_on_gce.call_args
            render_config = call_args[0][1]
            assert "updated_corrections_gcs_path" in render_config
            assert render_config["updated_corrections_gcs_path"] == "gs://test-bucket/jobs/test-job-123/lyrics/corrections_updated.json"
        finally:
            for p in patches_list:
                p.stop()

    def test_gce_path_includes_style_params(self):
        """Style params GCS path should be included in render_config."""
        job = _make_mock_job(
            style_params_gcs_path="jobs/test-job-123/styles/params.json",
            style_assets={"background": "jobs/test-job-123/styles/bg.png"},
        )
        mocks = self._run_with_mocks(job, encoding_enabled=True)

        call_args = mocks["encoding_service"].render_video_on_gce.call_args
        render_config = call_args[0][1]
        assert render_config["style_params_gcs_path"] == "gs://test-bucket/jobs/test-job-123/styles/params.json"
        assert render_config["style_assets"]["background"] == "gs://test-bucket/jobs/test-job-123/styles/bg.png"
