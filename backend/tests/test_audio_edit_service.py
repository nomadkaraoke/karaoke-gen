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

    @staticmethod
    def _fade_graph(mock_run):
        cmd = mock_run.call_args[0][0]
        assert "-filter_complex" in cmd
        return cmd[cmd.index("-filter_complex") + 1]

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_fade_in_at_start(self, mock_run, mock_meta):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=245.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=35000000,
        )

        service = AudioEditService(storage_service=Mock())
        result = service.fade_region("/tmp/input.flac", 0.0, 3.0, "in", "/tmp/output.flac")

        graph = self._fade_graph(mock_run)
        assert "[0]atrim=start=0.0:end=3.0,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=3.0[fade]" in graph
        assert "[pre]" not in graph  # nothing before the fade
        assert "[0]atrim=start=3.0,asetpts=PTS-STARTPTS[post]" in graph
        assert "[fade][post]concat=n=2:v=0:a=1[out]" in graph
        assert result.duration_seconds == 245.0  # Duration preserved

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_fade_out_at_end(self, mock_run, mock_meta):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=245.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=35000000,
        )

        service = AudioEditService(storage_service=Mock())
        result = service.fade_region("/tmp/input.flac", 240.0, 245.0, "out", "/tmp/output.flac")

        graph = self._fade_graph(mock_run)
        assert "[0]atrim=start=0:end=240.0,asetpts=PTS-STARTPTS[pre]" in graph
        # Reaches the clip end: trims open-ended so no samples are lost
        assert "[0]atrim=start=240.0,asetpts=PTS-STARTPTS,afade=t=out:st=0:d=5.0[fade]" in graph
        assert "[post]" not in graph
        assert "[pre][fade]concat=n=2:v=0:a=1[out]" in graph
        assert result.duration_seconds == 245.0

    @pytest.mark.parametrize("direction", ["in", "out"])
    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_mid_track_fade_leaves_surrounding_audio_untouched(self, mock_run, mock_meta, direction):
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=245.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=35000000,
        )

        service = AudioEditService(storage_service=Mock())
        service.fade_region("/tmp/input.flac", 50.0, 55.0, direction, "/tmp/output.flac")

        graph = self._fade_graph(mock_run)
        assert "[0]atrim=start=0:end=50.0,asetpts=PTS-STARTPTS[pre]" in graph
        assert f"[0]atrim=start=50.0:end=55.0,asetpts=PTS-STARTPTS,afade=t={direction}:st=0:d=5.0[fade]" in graph
        assert "[0]atrim=start=55.0,asetpts=PTS-STARTPTS[post]" in graph
        assert "[pre][fade][post]concat=n=3:v=0:a=1[out]" in graph

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_fade_snaps_selections_near_edges(self, mock_run, mock_meta):
        # Matches the UI's 1s edge tolerance: a drag that starts at 0.6s means "from the start"
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=245.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=35000000,
        )
        service = AudioEditService(storage_service=Mock())

        service.fade_region("/tmp/input.flac", 0.6, 4.0, "in", "/tmp/output.flac")
        assert "afade=t=in:st=0:d=4.0[fade]" in self._fade_graph(mock_run)

        service.fade_region("/tmp/input.flac", 240.0, 244.5, "out", "/tmp/output.flac")
        graph = self._fade_graph(mock_run)
        assert "[0]atrim=start=240.0,asetpts=PTS-STARTPTS,afade=t=out:st=0:d=5.0[fade]" in graph

    def test_fade_region_rejects_invalid_direction(self):
        service = AudioEditService(storage_service=Mock())
        with pytest.raises(ValueError, match="Invalid fade direction"):
            service.fade_region("/tmp/input.flac", 0.0, 3.0, "sideways", "/tmp/output.flac")

    def test_fade_region_rejects_inverted_region(self):
        service = AudioEditService(storage_service=Mock())
        with pytest.raises(ValueError, match="Invalid fade region"):
            service.fade_region("/tmp/input.flac", 3.0, 3.0, "in", "/tmp/output.flac")

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    def test_fade_region_rejects_end_beyond_duration(self, mock_meta):
        mock_meta.return_value = AudioMetadata(
            duration_seconds=245.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=35000000,
        )
        service = AudioEditService(storage_service=Mock())
        with pytest.raises(ValueError, match="exceeds clip duration"):
            service.fade_region("/tmp/input.flac", 0.0, 300.0, "in", "/tmp/output.flac")

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


class TestChangeTempo:
    """Whole-track, pitch-preserving tempo change."""

    @pytest.fixture(autouse=True)
    def _reset_rubberband_cache(self):
        AudioEditService._rubberband_available = None
        yield
        AudioEditService._rubberband_available = None

    @staticmethod
    def _meta(duration):
        return AudioMetadata(
            duration_seconds=duration, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_slow_down_uses_rubberband_when_available(self, mock_run, mock_meta):
        AudioEditService._rubberband_available = True
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = self._meta(250.0)

        service = AudioEditService(storage_service=Mock())
        result = service.change_tempo("/tmp/input.flac", 0.8, "/tmp/output.flac")

        cmd = mock_run.call_args[0][0]
        af_str = cmd[cmd.index("-af") + 1]
        assert af_str.startswith("rubberband=tempo=0.8")
        assert "pitchq=quality" in af_str
        assert cmd[-1] == "/tmp/output.flac"
        assert result.duration_seconds == 250.0

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_falls_back_to_atempo_without_rubberband(self, mock_run, mock_meta):
        AudioEditService._rubberband_available = False
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = self._meta(160.0)

        service = AudioEditService(storage_service=Mock())
        service.change_tempo("/tmp/input.flac", 1.25, "/tmp/output.flac")

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("-af") + 1] == "atempo=1.25"

    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_rubberband_detection_parses_filter_list(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=" .. atempo            A->A       Adjust audio tempo.\n"
                                 " .. rubberband        A->A       Apply time-stretching.\n",
        )
        assert AudioEditService._has_rubberband() is True
        # Cached — no second probe
        assert AudioEditService._has_rubberband() is True
        assert mock_run.call_count == 1

    @patch("backend.services.audio_edit_service.subprocess.run")
    def test_rubberband_detection_missing_filter(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=" .. atempo  A->A  Adjust audio tempo.\n")
        assert AudioEditService._has_rubberband() is False

    @pytest.mark.parametrize("factor", [0.49, 1.51, 0, -1, 3.0])
    def test_rejects_out_of_range_factor(self, factor):
        service = AudioEditService(storage_service=Mock())
        with pytest.raises(ValueError, match="between"):
            service.change_tempo("/tmp/input.flac", factor, "/tmp/output.flac")

    def test_rejects_noop_factor(self):
        service = AudioEditService(storage_service=Mock())
        with pytest.raises(ValueError, match="1.0"):
            service.change_tempo("/tmp/input.flac", 1.0, "/tmp/output.flac")

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    @patch("backend.services.audio_edit_service.tempfile.TemporaryDirectory")
    def test_apply_edit_dispatches_tempo(self, mock_tmpdir, mock_run, mock_meta):
        AudioEditService._rubberband_available = True
        mock_tmpdir.return_value.__enter__ = Mock(return_value="/tmp/edit")
        mock_tmpdir.return_value.__exit__ = Mock(return_value=False)
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = self._meta(222.2)

        mock_storage = Mock()
        service = AudioEditService(storage_service=mock_storage)
        metadata, result_path = service.apply_edit(
            input_gcs_path="jobs/123/input/song.flac",
            operation="tempo",
            params={"factor": 0.9},
            output_gcs_path="jobs/123/audio_edit/edit_t.flac",
            job_id="123",
        )

        cmd = mock_run.call_args[0][0]
        assert "rubberband=tempo=0.9" in cmd[cmd.index("-af") + 1]
        mock_storage.upload_file.assert_called_once()
        assert result_path == "jobs/123/audio_edit/edit_t.flac"
        assert metadata.duration_seconds == 222.2


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
    def test_apply_edit_fade_in(self, mock_tmpdir, mock_run, mock_meta):
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
            operation="fade_in",
            params={"start_seconds": 0.0, "end_seconds": 3.0},
            output_gcs_path="jobs/123/audio_edit/edit_abc.flac",
            job_id="123",
        )

        cmd = mock_run.call_args[0][0]
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "afade=t=in:st=0:d=3.0" in graph
        assert metadata.duration_seconds == 200.0  # Duration preserved

    @patch("backend.services.audio_edit_service.AudioEditService.get_metadata")
    @patch("backend.services.audio_edit_service.subprocess.run")
    @patch("backend.services.audio_edit_service.tempfile.TemporaryDirectory")
    def test_apply_edit_fade_out(self, mock_tmpdir, mock_run, mock_meta):
        mock_tmpdir.return_value.__enter__ = Mock(return_value="/tmp/edit")
        mock_tmpdir.return_value.__exit__ = Mock(return_value=False)
        mock_run.return_value = MagicMock(returncode=0)
        mock_meta.return_value = AudioMetadata(
            duration_seconds=200.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

        mock_storage = Mock()
        service = AudioEditService(storage_service=mock_storage)

        service.apply_edit(
            input_gcs_path="jobs/123/input/song.flac",
            operation="fade_out",
            params={"start_seconds": 197.0, "end_seconds": 200.0},
            output_gcs_path="jobs/123/audio_edit/edit_abc.flac",
            job_id="123",
        )

        cmd = mock_run.call_args[0][0]
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "[0]atrim=start=197.0,asetpts=PTS-STARTPTS,afade=t=out:st=0:d=3.0[fade]" in graph

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


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
class TestRealFFmpeg:
    """Run the real filter graphs on a generated tone — catches graph/syntax mistakes mocks can't."""

    @pytest.fixture
    def tone(self, tmp_path):
        import subprocess
        path = tmp_path / "tone.flac"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=20:sample_rate=44100", "-ac", "2", "-c:a", "flac", str(path)],
            check=True,
        )
        return str(path)

    @staticmethod
    def _mean_db(path, start, end):
        import subprocess
        err = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(end - start), "-i", path,
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        ).stderr
        line = next(l for l in err.splitlines() if "mean_volume" in l)
        return float(line.split(":")[-1].replace("dB", "").strip())

    @pytest.mark.parametrize("direction", ["in", "out"])
    def test_mid_track_fade(self, tone, tmp_path, direction):
        service = AudioEditService(storage_service=Mock())
        out = str(tmp_path / "faded.flac")
        meta = service.fade_region(tone, 8.0, 12.0, direction, out)

        assert meta.duration_seconds == pytest.approx(20.0, abs=0.01)
        original = self._mean_db(tone, 1, 2)
        # Outside the selection: untouched
        assert self._mean_db(out, 5, 7) == pytest.approx(original, abs=0.5)
        assert self._mean_db(out, 13, 15) == pytest.approx(original, abs=0.5)
        # Inside: quiet at the silent end of the ramp, full at the other
        quiet, loud = ((8.0, 8.2), (11.8, 12.0)) if direction == "in" else ((11.8, 12.0), (8.0, 8.2))
        assert self._mean_db(out, *quiet) < original - 15
        assert self._mean_db(out, *loud) == pytest.approx(original, abs=1.5)

    def test_tempo_changes_duration(self, tone, tmp_path):
        service = AudioEditService(storage_service=Mock())
        meta = service.change_tempo(tone, 0.8, str(tmp_path / "slow.flac"))
        assert meta.duration_seconds == pytest.approx(25.0, abs=0.1)
