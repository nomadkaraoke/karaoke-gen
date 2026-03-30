"""
Tests for Modal → GCP audio separation migration.

Covers:
- Pipeline decoupling: separation no longer gates lyrics review
- Preset-based model configuration
- Custom output filenames (job_id prefix)
- Screens worker prerequisites relaxation
"""

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

import pytest

from backend.models.job import Job, JobStatus
from backend.services.job_manager import JobManager


# ==================== Fixtures ====================

@pytest.fixture
def mock_firestore_service():
    with patch('backend.services.job_manager.FirestoreService') as mock:
        service = Mock()
        mock.return_value = service
        yield service


@pytest.fixture
def job_manager(mock_firestore_service):
    return JobManager()


def _make_job(job_id="test-001", state_data=None, **kwargs):
    defaults = dict(
        job_id=job_id,
        artist="Test Artist",
        title="Test Song",
        status=JobStatus.DOWNLOADING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        theme_id="default",
    )
    defaults.update(kwargs)
    if state_data is not None:
        defaults["state_data"] = state_data
    return Job(**defaults)


# ==================== P0: Pipeline Decoupling ====================

class TestPipelineDecoupling:
    """
    Critical: verify separation is decoupled from lyrics review path.

    New behavior:
    - check_parallel_processing_complete() only checks lyrics_complete
    - mark_audio_complete() sets flag but does NOT trigger screens
    - mark_lyrics_complete() triggers screens on its own (no audio gate)
    """

    def test_check_parallel_only_requires_lyrics(self, job_manager, mock_firestore_service):
        """lyrics_complete=True is sufficient, even without audio_complete."""
        job = _make_job(state_data={"lyrics_complete": True, "audio_complete": False})
        mock_firestore_service.get_job.return_value = job

        assert job_manager.check_parallel_processing_complete("test-001") is True

    def test_check_parallel_fails_without_lyrics(self, job_manager, mock_firestore_service):
        """lyrics_complete=False means not ready, even if audio_complete=True."""
        job = _make_job(state_data={"lyrics_complete": False, "audio_complete": True})
        mock_firestore_service.get_job.return_value = job

        assert job_manager.check_parallel_processing_complete("test-001") is False

    def test_check_parallel_fails_with_empty_state(self, job_manager, mock_firestore_service):
        """Empty state_data means not ready."""
        job = _make_job(state_data={})
        mock_firestore_service.get_job.return_value = job

        assert job_manager.check_parallel_processing_complete("test-001") is False

    def test_mark_audio_complete_does_not_trigger_screens(self, job_manager, mock_firestore_service):
        """Critical: audio completion must NOT trigger screens worker."""
        job = _make_job(state_data={"lyrics_complete": True})
        mock_firestore_service.get_job.return_value = job
        mock_firestore_service.update_job.return_value = True

        with patch.object(job_manager, '_trigger_screens_worker') as mock_trigger:
            job_manager.mark_audio_complete("test-001")
            mock_trigger.assert_not_called()

    def test_mark_lyrics_complete_triggers_screens_without_audio(self, job_manager, mock_firestore_service):
        """Critical: lyrics completion triggers screens even if audio isn't done."""
        # After update_state_data sets lyrics_complete, get_job returns updated state
        job_after_update = _make_job(state_data={"audio_complete": False, "lyrics_complete": True})
        mock_firestore_service.get_job.return_value = job_after_update
        mock_firestore_service.update_job.return_value = True

        with patch.object(job_manager, '_trigger_screens_worker') as mock_trigger:
            job_manager.mark_lyrics_complete("test-001")
            mock_trigger.assert_called_once_with("test-001")

    def test_mark_lyrics_complete_triggers_screens_with_audio(self, job_manager, mock_firestore_service):
        """Lyrics completion triggers screens when audio is already done too."""
        job_after_update = _make_job(state_data={"audio_complete": True, "lyrics_complete": True})
        mock_firestore_service.get_job.return_value = job_after_update
        mock_firestore_service.update_job.return_value = True

        with patch.object(job_manager, '_trigger_screens_worker') as mock_trigger:
            job_manager.mark_lyrics_complete("test-001")
            mock_trigger.assert_called_once_with("test-001")

    def test_ordering_audio_first_then_lyrics(self, job_manager, mock_firestore_service):
        """Typical flow: audio finishes first, then lyrics triggers screens."""
        mock_firestore_service.update_job.return_value = True

        # Audio completes first — get_job returns state without lyrics_complete
        job_audio_only = _make_job(state_data={"audio_complete": True})
        mock_firestore_service.get_job.return_value = job_audio_only

        with patch.object(job_manager, '_trigger_screens_worker') as mock_trigger:
            job_manager.mark_audio_complete("test-001")
            mock_trigger.assert_not_called()

        # Now lyrics completes — get_job returns state with both flags
        job_both = _make_job(state_data={"audio_complete": True, "lyrics_complete": True})
        mock_firestore_service.get_job.return_value = job_both

        with patch.object(job_manager, '_trigger_screens_worker') as mock_trigger:
            job_manager.mark_lyrics_complete("test-001")
            mock_trigger.assert_called_once()

    def test_ordering_lyrics_first_then_audio(self, job_manager, mock_firestore_service):
        """Less common: lyrics finishes first, triggers screens immediately."""
        mock_firestore_service.update_job.return_value = True

        # Lyrics completes first — get_job returns state with lyrics_complete
        job_lyrics_only = _make_job(state_data={"lyrics_complete": True})
        mock_firestore_service.get_job.return_value = job_lyrics_only

        with patch.object(job_manager, '_trigger_screens_worker') as mock_trigger:
            job_manager.mark_lyrics_complete("test-001")
            mock_trigger.assert_called_once()

        # Audio completes later — should NOT trigger screens again
        job_both = _make_job(state_data={"lyrics_complete": True, "audio_complete": True})
        mock_firestore_service.get_job.return_value = job_both

        with patch.object(job_manager, '_trigger_screens_worker') as mock_trigger:
            job_manager.mark_audio_complete("test-001")
            mock_trigger.assert_not_called()


# ==================== P0: Preset Configuration ====================

class TestPresetConfiguration:
    """
    Test that preset parameters flow through create_audio_processor correctly.
    """

    def test_create_audio_processor_sets_presets(self):
        """Presets are set as attributes on the processor."""
        with patch('backend.workers.audio_worker.AudioProcessor') as MockProcessor:
            from backend.workers.audio_worker import create_audio_processor

            with tempfile.TemporaryDirectory() as temp_dir:
                processor = create_audio_processor(
                    temp_dir,
                    instrumental_preset="instrumental_clean",
                    karaoke_preset="karaoke",
                )

                # Verify presets are set on the returned object
                assert processor.instrumental_preset == "instrumental_clean"
                assert processor.karaoke_preset == "karaoke"

    def test_create_audio_processor_default_presets(self):
        """Default presets are used when not explicitly provided."""
        with patch('backend.workers.audio_worker.AudioProcessor') as MockProcessor:
            from backend.workers.audio_worker import (
                create_audio_processor,
                DEFAULT_INSTRUMENTAL_PRESET,
                DEFAULT_KARAOKE_PRESET,
            )

            with tempfile.TemporaryDirectory() as temp_dir:
                processor = create_audio_processor(temp_dir)

                assert processor.instrumental_preset == DEFAULT_INSTRUMENTAL_PRESET
                assert processor.karaoke_preset == DEFAULT_KARAOKE_PRESET

    def test_default_other_models_is_empty(self):
        """Demucs 6-stem is dropped — DEFAULT_OTHER_MODELS should be empty."""
        from backend.workers.audio_worker import DEFAULT_OTHER_MODELS
        assert DEFAULT_OTHER_MODELS == []

    def test_create_audio_processor_with_explicit_models(self):
        """Explicit model names still work (legacy per-job override)."""
        with patch('backend.workers.audio_worker.AudioProcessor') as MockProcessor:
            from backend.workers.audio_worker import create_audio_processor

            with tempfile.TemporaryDirectory() as temp_dir:
                processor = create_audio_processor(
                    temp_dir,
                    clean_instrumental_model="custom_model.ckpt",
                    backing_vocals_models=["custom_bv.ckpt"],
                )

                call_kwargs = MockProcessor.call_args[1]
                assert call_kwargs["clean_instrumental_model"] == "custom_model.ckpt"
                assert call_kwargs["backing_vocals_models"] == ["custom_bv.ckpt"]


# ==================== P0: Custom Output Names Contract ====================

class TestCustomOutputNames:
    """
    Test that AudioProcessor uses job_id-based custom_output_names
    and organizes results by known filenames.
    """

    def test_job_id_used_as_file_prefix(self):
        """When job_id is set, it's used as the filename prefix in custom_output_names."""
        from karaoke_gen.audio_processor import AudioProcessor
        import logging

        processor = AudioProcessor(
            logger=logging.getLogger("test"),
            log_level=logging.INFO,
            log_formatter=None,
            model_file_dir=None,
            lossless_output_format="FLAC",
            clean_instrumental_model="test.ckpt",
            backing_vocals_models=[],  # No stage 2
            other_stems_models=[],
            ffmpeg_base_command="ffmpeg",
        )
        processor.instrumental_preset = "instrumental_clean"
        processor.karaoke_preset = None  # No stage 2
        processor.job_id = "abc123"

        mock_client = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            stems_dir = os.path.join(temp_dir, "stems")
            os.makedirs(stems_dir)

            # Create expected output files
            for name in ["abc123_mixed_vocals.flac", "abc123_mixed_instrumental.flac"]:
                with open(os.path.join(stems_dir, name), "w") as f:
                    f.write("fake audio")

            mock_client.separate_audio_and_wait.return_value = {
                "status": "completed",
                "downloaded_files": [
                    os.path.join(stems_dir, "abc123_mixed_vocals.flac"),
                    os.path.join(stems_dir, "abc123_mixed_instrumental.flac"),
                ],
                "files": {},
            }

            input_file = os.path.join(temp_dir, "test.flac")
            with open(input_file, "w") as f:
                f.write("fake input")

            with patch.object(processor, '_create_stems_directory', return_value=stems_dir):
                with patch.object(processor, '_normalize_audio_files'):
                    with patch('karaoke_gen.audio_processor.AudioSeparatorAPIClient', return_value=mock_client):
                        result = processor._process_audio_separation_remote(
                            input_file, "Test - Song", temp_dir, "http://fake-api",
                        )

            # Verify custom_output_names used job_id prefix
            call_kwargs = mock_client.separate_audio_and_wait.call_args[1]
            assert call_kwargs["custom_output_names"]["Vocals"] == "abc123_mixed_vocals"
            assert call_kwargs["custom_output_names"]["Instrumental"] == "abc123_mixed_instrumental"

            # Verify results found by known filenames
            assert result["clean_instrumental"]["vocals"].endswith("abc123_mixed_vocals.flac")
            assert "abc123_mixed_instrumental" in result["clean_instrumental"]["instrumental"]

    def test_stage1_results_found_by_known_filenames(self):
        """Stage 1 results are located by predictable filenames, not parsed from model names."""
        from karaoke_gen.audio_processor import AudioProcessor
        import logging

        processor = AudioProcessor(
            logger=logging.getLogger("test"),
            log_level=logging.INFO,
            log_formatter=None,
            model_file_dir=None,
            lossless_output_format="FLAC",
            clean_instrumental_model="test.ckpt",
            backing_vocals_models=[],
            other_stems_models=[],
            ffmpeg_base_command="ffmpeg",
        )
        processor.instrumental_preset = "instrumental_clean"
        processor.karaoke_preset = None  # No stage 2
        processor.job_id = "job42"

        with tempfile.TemporaryDirectory() as temp_dir:
            stems_dir = os.path.join(temp_dir, "stems")
            os.makedirs(stems_dir)

            # Create the files the API would produce
            vocals_path = os.path.join(stems_dir, "job42_mixed_vocals.flac")
            instrumental_path = os.path.join(stems_dir, "job42_mixed_instrumental.flac")
            with open(vocals_path, "w") as f:
                f.write("fake vocals")
            with open(instrumental_path, "w") as f:
                f.write("fake instrumental")

            mock_client = Mock()
            mock_client.separate_audio_and_wait.return_value = {
                "status": "completed",
                "downloaded_files": [vocals_path, instrumental_path],
                "files": {},
            }

            # Write a fake input file
            input_file = os.path.join(temp_dir, "input.flac")
            with open(input_file, "w") as f:
                f.write("fake input")

            with patch.object(processor, '_create_stems_directory', return_value=stems_dir):
                with patch('karaoke_gen.audio_processor.AudioSeparatorAPIClient', return_value=mock_client):
                    with patch.object(processor, '_normalize_audio_files'):
                        result = processor._process_audio_separation_remote(
                            input_file, "Test - Song", temp_dir, "http://fake-api",
                        )

            # Should find vocals and instrumental by known filenames
            assert result["clean_instrumental"]["vocals"] == vocals_path
            assert "mixed_instrumental" in result["clean_instrumental"]["instrumental"]


# ==================== P1: Screens Worker Prerequisites ====================

class TestScreensWorkerPrerequisites:
    """
    Test that screens worker no longer requires audio_complete.
    """

    def test_validate_prerequisites_passes_without_audio(self):
        """Screens can generate with only lyrics_complete (audio still in progress)."""
        from backend.workers.screens_worker import _validate_prerequisites

        job = _make_job(state_data={"lyrics_complete": True, "audio_complete": False})
        assert _validate_prerequisites(job) is True

    def test_validate_prerequisites_passes_with_both(self):
        """Normal case: both audio and lyrics complete."""
        from backend.workers.screens_worker import _validate_prerequisites

        job = _make_job(state_data={"lyrics_complete": True, "audio_complete": True})
        assert _validate_prerequisites(job) is True

    def test_validate_prerequisites_fails_without_lyrics(self):
        """Missing lyrics_complete should fail."""
        from backend.workers.screens_worker import _validate_prerequisites

        job = _make_job(state_data={"lyrics_complete": False, "audio_complete": True})
        assert _validate_prerequisites(job) is False

    def test_validate_prerequisites_fails_with_empty_state(self):
        """Empty state_data should fail."""
        from backend.workers.screens_worker import _validate_prerequisites

        job = _make_job(state_data={})
        assert _validate_prerequisites(job) is False

    def test_validate_prerequisites_fails_without_theme(self):
        """Missing theme_id should fail."""
        from backend.workers.screens_worker import _validate_prerequisites

        job = _make_job(state_data={"lyrics_complete": True}, theme_id=None)
        assert _validate_prerequisites(job) is False

    def test_validate_prerequisites_fails_without_artist_title(self):
        """Missing artist or title should fail."""
        from backend.workers.screens_worker import _validate_prerequisites

        job = _make_job(state_data={"lyrics_complete": True}, artist="", title="Test")
        assert _validate_prerequisites(job) is False


# ==================== GCS URI Passthrough ====================

class TestGCSURIPassthrough:
    """
    Test that Stage 1 uses gcs_uri when input_gcs_uri is set on the processor.
    Fixes 413 Request Entity Too Large for large FLAC files.
    """

    def test_stage1_uses_gcs_uri_when_set(self):
        """When input_gcs_uri is set, Stage 1 passes gcs_uri instead of file_path."""
        from karaoke_gen.audio_processor import AudioProcessor
        import logging

        processor = AudioProcessor(
            logger=logging.getLogger("test"),
            log_level=logging.INFO,
            log_formatter=None,
            model_file_dir=None,
            lossless_output_format="FLAC",
            clean_instrumental_model="test.ckpt",
            backing_vocals_models=[],
            other_stems_models=[],
            ffmpeg_base_command="ffmpeg",
        )
        processor.instrumental_preset = "instrumental_clean"
        processor.karaoke_preset = None
        processor.job_id = "gcs-test-001"
        processor.input_gcs_uri = "gs://karaoke-gen-storage-nomadkaraoke/jobs/gcs-test-001/input/song.flac"

        mock_client = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            stems_dir = os.path.join(temp_dir, "stems")
            os.makedirs(stems_dir)

            for name in ["gcs-test-001_mixed_vocals.flac", "gcs-test-001_mixed_instrumental.flac"]:
                with open(os.path.join(stems_dir, name), "w") as f:
                    f.write("fake audio")

            mock_client.separate_audio_and_wait.return_value = {
                "status": "completed",
                "downloaded_files": [
                    os.path.join(stems_dir, "gcs-test-001_mixed_vocals.flac"),
                    os.path.join(stems_dir, "gcs-test-001_mixed_instrumental.flac"),
                ],
                "files": {},
            }

            input_file = os.path.join(temp_dir, "song.flac")
            with open(input_file, "w") as f:
                f.write("fake input")

            with patch.object(processor, '_create_stems_directory', return_value=stems_dir):
                with patch.object(processor, '_normalize_audio_files'):
                    with patch('karaoke_gen.audio_processor.AudioSeparatorAPIClient', return_value=mock_client):
                        processor._process_audio_separation_remote(
                            input_file, "Test - Song", temp_dir, "http://fake-api",
                        )

            # Verify gcs_uri was passed instead of file_path
            call_kwargs = mock_client.separate_audio_and_wait.call_args[1]
            assert call_kwargs["gcs_uri"] == "gs://karaoke-gen-storage-nomadkaraoke/jobs/gcs-test-001/input/song.flac"
            # Verify file_path was NOT passed as a positional arg
            call_args = mock_client.separate_audio_and_wait.call_args[0]
            assert len(call_args) == 0

    def test_stage1_uses_file_upload_when_no_gcs_uri(self):
        """When input_gcs_uri is not set, Stage 1 uses file upload as before."""
        from karaoke_gen.audio_processor import AudioProcessor
        import logging

        processor = AudioProcessor(
            logger=logging.getLogger("test"),
            log_level=logging.INFO,
            log_formatter=None,
            model_file_dir=None,
            lossless_output_format="FLAC",
            clean_instrumental_model="test.ckpt",
            backing_vocals_models=[],
            other_stems_models=[],
            ffmpeg_base_command="ffmpeg",
        )
        processor.instrumental_preset = "instrumental_clean"
        processor.karaoke_preset = None
        processor.job_id = "file-test-001"
        # No input_gcs_uri set

        mock_client = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            stems_dir = os.path.join(temp_dir, "stems")
            os.makedirs(stems_dir)

            for name in ["file-test-001_mixed_vocals.flac", "file-test-001_mixed_instrumental.flac"]:
                with open(os.path.join(stems_dir, name), "w") as f:
                    f.write("fake audio")

            mock_client.separate_audio_and_wait.return_value = {
                "status": "completed",
                "downloaded_files": [
                    os.path.join(stems_dir, "file-test-001_mixed_vocals.flac"),
                    os.path.join(stems_dir, "file-test-001_mixed_instrumental.flac"),
                ],
                "files": {},
            }

            input_file = os.path.join(temp_dir, "song.flac")
            with open(input_file, "w") as f:
                f.write("fake input")

            with patch.object(processor, '_create_stems_directory', return_value=stems_dir):
                with patch.object(processor, '_normalize_audio_files'):
                    with patch('karaoke_gen.audio_processor.AudioSeparatorAPIClient', return_value=mock_client):
                        processor._process_audio_separation_remote(
                            input_file, "Test - Song", temp_dir, "http://fake-api",
                        )

            # Verify file_path was passed as positional arg (old behavior)
            call_args = mock_client.separate_audio_and_wait.call_args[0]
            assert call_args[0] == input_file
            # Verify gcs_uri was NOT in kwargs
            call_kwargs = mock_client.separate_audio_and_wait.call_args[1]
            assert "gcs_uri" not in call_kwargs
