"""
Tests for audio_edit_service.py — server-side FFmpeg audio editing.

Tests use mocked subprocess calls since we can't guarantee ffmpeg is
available in CI. Integration tests with real audio files are in a
separate test module.
"""

import json
import os
import pytest
from unittest.mock import Mock, patch, MagicMock, call

from backend.services.audio_edit_service import AudioEditService, AudioMetadata


class TestGetMetadata:
    """Test get_metadata and get_metadata_from_gcs."""

    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_get_metadata_parses_ffprobe(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "format": {
                    "duration": "245.3",
                    "format_name": "flac",
                    "size": "35000000",
                },
                "streams": [{
                    "codec_type": "audio",
                    "sample_rate": "44100",
                    "channels": 2,
                }],
            }),
        )

        service = AudioEditService(storage_service=Mock())
        meta = service.get_metadata("/tmp/test.flac")

        assert meta.duration_seconds == 245.3
        assert meta.sample_rate == 44100
        assert meta.channels == 2
        assert meta.format == "flac"
        assert meta.file_size_bytes == 35000000

    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_get_metadata_ffprobe_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="ffprobe error",
        )

        service = AudioEditService(storage_service=Mock())
        with pytest.raises(RuntimeError, match="ffprobe failed"):
            service.get_metadata("/tmp/test.flac")

    @patch("backend.services.audio_edit_service.subprocess.run")
    @patch("backend.services.audio_edit_service.tempfile.TemporaryDirectory")
    def test_get_metadata_from_gcs(self, mock_tmpdir, mock_run):
        mock_tmpdir.return_value.__enter__ = Mock(return_value="/tmp/test")
        mock_tmpdir.return_value.__exit__ = Mock(return_value=False)

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "format": {"duration": "100.0", "format_name": "flac", "size": "1000"},
                "streams": [{"codec_type": "audio", "sample_rate": "44100", "channels": 2}],
            }),
        )

        mock_storage = Mock()
        service = AudioEditService(storage_service=mock_storage)
        meta = service.get_metadata_from_gcs("jobs/123/input/song.flac")

        mock_storage.download_file.assert_called_once()
        assert meta.duration_seconds == 100.0


class TestFFmpegOperations:
    """Test trim, cut, mute, join operations via FFmpeg."""

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_trim_start(self, mock_run, mock_meta):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=200.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

        service = AudioEditService(storage_service=Mock())
        result = service.trim_start("/tmp/input.flac", 30.0, "/tmp/output.flac")

        # Check ffmpeg was called with -ss
        cmd = mock_run.call_args[0][0]
        assert "-ss" in cmd
        assert "30.0" in cmd
        assert result.duration_seconds == 200.0

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_trim_end(self, mock_run, mock_meta):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=120.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=20000000,
        )

        service = AudioEditService(storage_service=Mock())
        result = service.trim_end("/tmp/input.flac", 120.0, "/tmp/output.flac")

        cmd = mock_run.call_args[0][0]
        assert "-t" in cmd
        assert "120.0" in cmd
        assert result.duration_seconds == 120.0

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_cut_region(self, mock_run, mock_meta):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=180.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=25000000,
        )

        service = AudioEditService(storage_service=Mock())
        result = service.cut_region("/tmp/input.flac", 30.0, 45.0, "/tmp/output.flac")

        cmd = mock_run.call_args[0][0]
        assert "-filter_complex" in cmd
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert "atrim=0:30.0" in filter_str
        assert "atrim=45.0" in filter_str
        assert "concat=n=2" in filter_str

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_mute_region(self, mock_run, mock_meta):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=245.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=35000000,
        )

        service = AudioEditService(storage_service=Mock())
        result = service.mute_region("/tmp/input.flac", 10.0, 15.0, "/tmp/output.flac")

        cmd = mock_run.call_args[0][0]
        assert "-af" in cmd
        af_str = cmd[cmd.index("-af") + 1]
        assert "volume=enable='between(t,10.0,15.0)':volume=0" in af_str
        assert result.duration_seconds == 245.0  # Duration preserved

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_join_audio_end(self, mock_run, mock_meta):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=260.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=40000000,
        )

        service = AudioEditService(storage_service=Mock())
        result = service.join_audio("/tmp/main.flac", "/tmp/extra.flac", "end", "/tmp/output.flac")

        cmd = mock_run.call_args[0][0]
        assert "-filter_complex" in cmd
        # For "end", main comes first, extra second
        assert cmd[cmd.index("-i") + 1] == "/tmp/main.flac"

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_join_audio_start(self, mock_run, mock_meta):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=260.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=40000000,
        )

        service = AudioEditService(storage_service=Mock())
        result = service.join_audio("/tmp/main.flac", "/tmp/extra.flac", "start", "/tmp/output.flac")

        cmd = mock_run.call_args[0][0]
        # For "start", extra comes first, main second
        first_input_idx = cmd.index("-i") + 1
        assert cmd[first_input_idx] == "/tmp/extra.flac"

    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_ffmpeg_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Error: something went wrong",
        )

        service = AudioEditService(storage_service=Mock())
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            service.trim_start("/tmp/input.flac", 30.0, "/tmp/output.flac")


class TestApplyEdit:
    """Test the apply_edit method that orchestrates download/edit/upload."""

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    @patch("backend.services.audio_edit_service.tempfile.TemporaryDirectory")
    def test_apply_edit_trim_start(self, mock_tmpdir, mock_run, mock_meta):
        mock_tmpdir.return_value.__enter__ = Mock(return_value="/tmp/edit")
        mock_tmpdir.return_value.__exit__ = Mock(return_value=False)
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=200.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

        mock_storage = Mock()
        service = AudioEditService(storage_service=mock_storage)

        metadata, result_path = service.apply_edit(
            input_gcs_path="jobs/123/input/song.flac",
            operation="trim_start",
            params={"end_seconds": 30.0},
            output_gcs_path="jobs/123/audio_edit/edit_abc.flac",
            job_id="123",
        )

        mock_storage.download_file.assert_called_once()
        mock_storage.upload_file.assert_called_once()
        assert result_path == "jobs/123/audio_edit/edit_abc.flac"
        assert metadata.duration_seconds == 200.0

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    @patch("backend.services.audio_edit_service.tempfile.TemporaryDirectory")
    def test_apply_edit_join_downloads_both_files(self, mock_tmpdir, mock_run, mock_meta):
        mock_tmpdir.return_value.__enter__ = Mock(return_value="/tmp/edit")
        mock_tmpdir.return_value.__exit__ = Mock(return_value=False)
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=260.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=40000000,
        )

        mock_storage = Mock()
        service = AudioEditService(storage_service=mock_storage)

        metadata, result_path = service.apply_edit(
            input_gcs_path="jobs/123/input/song.flac",
            operation="join_end",
            params={"upload_gcs_path": "jobs/123/audio_edit/upload_xyz.flac"},
            output_gcs_path="jobs/123/audio_edit/edit_abc.flac",
            job_id="123",
        )

        # Should download both the main audio and the upload
        assert mock_storage.download_file.call_count == 2

    @patch("backend.services.audio_edit_service.tempfile.TemporaryDirectory")
    def test_apply_edit_unknown_operation(self, mock_tmpdir):
        mock_tmpdir.return_value.__enter__ = Mock(return_value="/tmp/edit")
        mock_tmpdir.return_value.__exit__ = Mock(return_value=False)

        mock_storage = Mock()
        service = AudioEditService(storage_service=mock_storage)

        with pytest.raises(ValueError, match="Unknown operation"):
            service.apply_edit(
                input_gcs_path="jobs/123/input/song.flac",
                operation="reverse",
                params={},
                output_gcs_path="jobs/123/audio_edit/edit_abc.flac",
                job_id="123",
            )


class TestStateTransitions:
    """Test that new job states and transitions are correctly defined."""

    def test_new_states_exist(self):
        from backend.models.job import JobStatus

        assert hasattr(JobStatus, 'AWAITING_AUDIO_EDIT')
        assert hasattr(JobStatus, 'IN_AUDIO_EDIT')
        assert hasattr(JobStatus, 'AUDIO_EDIT_COMPLETE')
        assert JobStatus.AWAITING_AUDIO_EDIT.value == "awaiting_audio_edit"
        assert JobStatus.IN_AUDIO_EDIT.value == "in_audio_edit"
        assert JobStatus.AUDIO_EDIT_COMPLETE.value == "audio_edit_complete"

    def test_downloading_can_transition_to_awaiting_audio_edit(self):
        from backend.models.job import JobStatus, STATE_TRANSITIONS

        allowed = STATE_TRANSITIONS[JobStatus.DOWNLOADING]
        assert JobStatus.AWAITING_AUDIO_EDIT in allowed

    def test_awaiting_audio_edit_transitions(self):
        from backend.models.job import JobStatus, STATE_TRANSITIONS

        allowed = STATE_TRANSITIONS[JobStatus.AWAITING_AUDIO_EDIT]
        assert JobStatus.IN_AUDIO_EDIT in allowed
        assert JobStatus.AUDIO_EDIT_COMPLETE in allowed
        assert JobStatus.FAILED in allowed
        assert JobStatus.CANCELLED in allowed

    def test_in_audio_edit_transitions(self):
        from backend.models.job import JobStatus, STATE_TRANSITIONS

        allowed = STATE_TRANSITIONS[JobStatus.IN_AUDIO_EDIT]
        assert JobStatus.AWAITING_AUDIO_EDIT in allowed
        assert JobStatus.AUDIO_EDIT_COMPLETE in allowed
        assert JobStatus.FAILED in allowed
        assert JobStatus.CANCELLED in allowed

    def test_audio_edit_complete_continues_to_processing(self):
        from backend.models.job import JobStatus, STATE_TRANSITIONS

        allowed = STATE_TRANSITIONS[JobStatus.AUDIO_EDIT_COMPLETE]
        assert JobStatus.SEPARATING_STAGE1 in allowed
        assert JobStatus.TRANSCRIBING in allowed
        assert JobStatus.GENERATING_SCREENS in allowed
        assert JobStatus.FAILED in allowed
