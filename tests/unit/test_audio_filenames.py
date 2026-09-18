"""Tests for backend.utils.audio_filenames.

Regression tests for the 2026-09-17 incident where an uploaded MP3 with a
~1.2 MB ID3 tag was downloaded to temp files named `*.flac`, making ffmpeg
commit to the flac demuxer (extension fallback) and fail every consumer of
the input audio (waveform, review transcode, preview encode, duration probe).
"""

import os
from unittest.mock import MagicMock

from backend.utils.audio_filenames import audio_content_type, local_audio_filename


class TestLocalAudioFilename:
    def test_preserves_mp3_extension(self):
        assert local_audio_filename(
            "jobs/e026e727/input/Don Omar - El Elegido (Video Oficial) - (192 Kbps).mp3",
            "audio",
        ) == "audio.mp3"

    def test_preserves_flac_extension(self):
        assert local_audio_filename(
            "jobs/x/stems/backing_vocals.flac", "backing_vocals"
        ) == "backing_vocals.flac"

    def test_lowercases_extension(self):
        assert local_audio_filename("jobs/x/input/SONG.MP3", "input") == "input.mp3"

    def test_unknown_extension_falls_back_to_bare_stem(self):
        # Bare name = no misleading hint; ffmpeg relies on content probing.
        assert local_audio_filename("jobs/x/input/file.xyz", "audio") == "audio"

    def test_no_extension_falls_back_to_bare_stem(self):
        assert local_audio_filename("jobs/x/input/rawfile", "audio") == "audio"

    def test_m4a_and_wav(self):
        assert local_audio_filename("a/b/song.m4a", "audio") == "audio.m4a"
        assert local_audio_filename("a/b/song.wav", "audio") == "audio.wav"

    def test_dots_in_filename_only_last_suffix_used(self):
        assert local_audio_filename("a/b/artist - title (feat. x).mp3", "audio") == "audio.mp3"


class TestAudioContentType:
    def test_common_types(self):
        assert audio_content_type("a/b/song.flac") == "audio/flac"
        assert audio_content_type("a/b/song.mp3") == "audio/mpeg"
        assert audio_content_type("a/b/song.M4A") == "audio/mp4"
        assert audio_content_type("a/b/song.ogg") == "audio/ogg"

    def test_unknown_extension_is_octet_stream(self):
        assert audio_content_type("a/b/file.xyz") == "application/octet-stream"
        assert audio_content_type("a/b/file") == "application/octet-stream"

    def test_transcode_fallback_uses_source_content_type(self):
        """Regression: the transcode-failure fallback must not serve an MP3
        source as audio/flac."""
        from backend.services.audio_transcoding_service import AudioTranscodingService
        from unittest.mock import MagicMock

        storage = MagicMock()
        storage.download_bytes.return_value = b"mp3bytes"
        service = AudioTranscodingService(storage_service=storage)
        service.transcode_if_needed = MagicMock(side_effect=RuntimeError("ffmpeg failed"))

        data, content_type = service.get_review_audio_bytes("jobs/j1/input/song.mp3")
        assert data == b"mp3bytes"
        assert content_type == "audio/mpeg"


class TestServicesUseSourceExtension:
    """The services must download GCS audio to a temp name matching the
    source object's extension, not a hardcoded `.flac`."""

    MP3_PATH = "jobs/j1/input/Song (192 Kbps).mp3"

    def _capture_download(self):
        captured = {}

        def fake_download(gcs_path, local_path):
            captured[gcs_path] = local_path
            # Write a stub so downstream code that opens the file can proceed
            # far enough for our assertion (tests stub out the analyzers).
            with open(local_path, "wb") as f:
                f.write(b"\x00")
            return local_path

        return captured, fake_download

    def test_transcoding_service_temp_name_matches_source(self):
        from backend.services.audio_transcoding_service import AudioTranscodingService

        captured, fake_download = self._capture_download()
        storage = MagicMock()
        storage.download_file.side_effect = fake_download

        service = AudioTranscodingService(storage_service=storage)
        try:
            service._transcode_and_upload(self.MP3_PATH, "jobs/j1/review-audio/out.ogg")
        except Exception:
            pass  # ffmpeg on a stub file fails; we only assert the temp name
        assert os.path.basename(captured[self.MP3_PATH]) == "input.mp3"

    def test_analysis_service_waveform_temp_name_matches_source(self):
        from backend.services.audio_analysis_service import AudioAnalysisService

        captured, fake_download = self._capture_download()
        storage = MagicMock()
        storage.download_file.side_effect = fake_download

        service = AudioAnalysisService(storage_service=storage)
        service.waveform_generator = MagicMock()
        service.waveform_generator.generate_data_only.return_value = ([0.0], 1.0)

        service.get_waveform_data(self.MP3_PATH, "j1", num_points=10)
        assert os.path.basename(captured[self.MP3_PATH]) == "backing_vocals.mp3"
