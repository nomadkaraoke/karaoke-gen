"""Tests for AudioShake transcriber, specifically AudioUploadOptimizer."""

import os
import pytest
import subprocess
import tempfile
from unittest.mock import MagicMock, patch
from karaoke_gen.lyrics_transcriber.transcribers.audioshake import (
    AudioUploadOptimizer,
    AUDIOSHAKE_SUPPORTED,
    AUDIOSHAKE_SUPPORTED_AUDIO,
    AUDIOSHAKE_SUPPORTED_VIDEO,
    UNCOMPRESSED_FORMATS,
)


class TestAudioUploadOptimizer:
    """Tests for AudioUploadOptimizer format handling."""

    @pytest.fixture
    def optimizer(self):
        """Create an AudioUploadOptimizer with a mock logger."""
        logger = MagicMock()
        return AudioUploadOptimizer(logger)

    # ===== Test AudioShake-supported formats are uploaded directly =====

    @pytest.mark.parametrize("ext", [".mp3", ".aac", ".mp4a"])
    def test_supported_audio_formats_uploaded_directly(self, optimizer, ext):
        """AudioShake-supported audio formats should be uploaded directly."""
        filepath = f"/path/to/audio{ext}"

        result_path, temp_path = optimizer.prepare_for_upload(filepath)

        assert result_path == filepath
        assert temp_path is None
        optimizer.logger.info.assert_called_once()
        assert "directly" in optimizer.logger.info.call_args[0][0]

    def test_flac_files_have_metadata_stripped(self, optimizer):
        """FLAC files should have metadata stripped before upload."""
        filepath = "/path/to/audio.flac"

        with patch.object(optimizer, "_strip_metadata") as mock_strip:
            mock_strip.return_value = ("/tmp/clean.flac", "/tmp/clean.flac")

            result_path, temp_path = optimizer.prepare_for_upload(filepath)

            mock_strip.assert_called_once_with(filepath)
            assert result_path == "/tmp/clean.flac"
            assert temp_path == "/tmp/clean.flac"
            optimizer.logger.info.assert_called_once()
            assert "metadata" in optimizer.logger.info.call_args[0][0].lower()

    @pytest.mark.parametrize("ext", [".mp4", ".mov"])
    def test_supported_video_formats_uploaded_directly(self, optimizer, ext):
        """AudioShake-supported video formats (mp4, mov) should be uploaded directly."""
        filepath = f"/path/to/video{ext}"

        result_path, temp_path = optimizer.prepare_for_upload(filepath)

        assert result_path == filepath
        assert temp_path is None
        optimizer.logger.info.assert_called_once()
        assert "directly" in optimizer.logger.info.call_args[0][0]

    # ===== Test unsupported formats are converted to FLAC =====

    @pytest.mark.parametrize("ext", [".webm", ".ogg", ".opus", ".m4a", ".wma", ".mkv", ".avi", ".alac"])
    def test_unsupported_formats_converted_to_flac(self, optimizer, ext):
        """Unsupported formats should trigger FLAC conversion."""
        filepath = f"/path/to/audio{ext}"

        # Mock _convert_to_flac to avoid actual audio processing
        with patch.object(optimizer, "_convert_to_flac") as mock_convert:
            mock_convert.return_value = ("/tmp/converted.flac", "/tmp/converted.flac")

            result_path, temp_path = optimizer.prepare_for_upload(filepath)

            mock_convert.assert_called_once_with(filepath)
            assert result_path == "/tmp/converted.flac"
            assert temp_path == "/tmp/converted.flac"
            optimizer.logger.info.assert_called_once()
            assert "unsupported" in optimizer.logger.info.call_args[0][0].lower()

    # ===== Test uncompressed formats are converted to FLAC =====

    @pytest.mark.parametrize("ext", [".wav", ".aiff", ".aif", ".pcm"])
    def test_uncompressed_formats_converted_to_flac(self, optimizer, ext):
        """Uncompressed formats should be converted to FLAC for efficient upload."""
        filepath = f"/path/to/audio{ext}"

        # Mock _convert_to_flac to avoid actual audio processing
        with patch.object(optimizer, "_convert_to_flac") as mock_convert:
            mock_convert.return_value = ("/tmp/converted.flac", "/tmp/converted.flac")

            result_path, temp_path = optimizer.prepare_for_upload(filepath)

            mock_convert.assert_called_once_with(filepath)
            assert result_path == "/tmp/converted.flac"
            assert temp_path == "/tmp/converted.flac"
            optimizer.logger.info.assert_called_once()
            assert "uncompressed" in optimizer.logger.info.call_args[0][0].lower()

    # ===== Test format constant definitions =====

    def test_audioshake_supported_audio_formats(self):
        """Verify AudioShake supported audio formats match documentation."""
        expected = {".wav", ".mp3", ".aac", ".flac", ".aiff", ".aif", ".mp4a"}
        assert AUDIOSHAKE_SUPPORTED_AUDIO == expected

    def test_audioshake_supported_video_formats(self):
        """Verify AudioShake supported video formats match documentation."""
        expected = {".mp4", ".mov"}
        assert AUDIOSHAKE_SUPPORTED_VIDEO == expected

    def test_audioshake_supported_is_union(self):
        """AUDIOSHAKE_SUPPORTED should be union of audio and video formats."""
        assert AUDIOSHAKE_SUPPORTED == AUDIOSHAKE_SUPPORTED_AUDIO | AUDIOSHAKE_SUPPORTED_VIDEO

    def test_uncompressed_formats(self):
        """Verify uncompressed formats list."""
        expected = {".wav", ".aiff", ".aif", ".pcm"}
        assert UNCOMPRESSED_FORMATS == expected

    # ===== Test case insensitivity =====

    def test_extension_case_insensitive(self, optimizer):
        """Extension comparison should be case-insensitive."""
        filepath = "/path/to/audio.MP3"  # uppercase

        result_path, temp_path = optimizer.prepare_for_upload(filepath)

        assert result_path == filepath
        assert temp_path is None

    # ===== Test cleanup method =====

    def test_cleanup_removes_temp_file(self, optimizer):
        """cleanup() should remove the temporary file."""
        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as f:
            temp_path = f.name
            f.write(b"test content")

        assert os.path.exists(temp_path)
        optimizer.cleanup(temp_path)
        assert not os.path.exists(temp_path)

    def test_cleanup_handles_none(self, optimizer):
        """cleanup() should handle None gracefully."""
        optimizer.cleanup(None)  # Should not raise

    def test_cleanup_handles_missing_file(self, optimizer):
        """cleanup() should handle non-existent files gracefully."""
        optimizer.cleanup("/nonexistent/path/to/file.flac")  # Should not raise


class TestAudioUploadOptimizerConversion:
    """Tests for actual FLAC conversion (requires pydub/ffmpeg)."""

    @pytest.fixture
    def optimizer(self):
        """Create an AudioUploadOptimizer with a mock logger."""
        logger = MagicMock()
        return AudioUploadOptimizer(logger)

    def test_convert_to_flac_with_wav(self, optimizer, tmp_path):
        """Test actual WAV to FLAC conversion."""
        pytest.importorskip("pydub")

        # Create a simple WAV file using pydub
        from pydub import AudioSegment
        from pydub.generators import Sine

        # Generate 1 second of 440Hz sine wave
        sine_wave = Sine(440).to_audio_segment(duration=1000)
        wav_path = tmp_path / "test.wav"
        sine_wave.export(str(wav_path), format="wav")

        # Convert to FLAC
        flac_path, cleanup_path = optimizer._convert_to_flac(str(wav_path))

        try:
            assert flac_path.endswith(".flac")
            assert os.path.exists(flac_path)
            assert cleanup_path == flac_path

            # Verify FLAC file was created with non-zero size
            flac_size = os.path.getsize(flac_path)
            assert flac_size > 0
        finally:
            optimizer.cleanup(cleanup_path)

    @pytest.mark.parametrize("format_name,ext", [
        ("ogg", ".ogg"),
        ("mp3", ".mp3"),
    ])
    def test_convert_to_flac_various_formats(self, optimizer, tmp_path, format_name, ext):
        """Test conversion from various formats to FLAC."""
        pytest.importorskip("pydub")

        from pydub import AudioSegment
        from pydub.generators import Sine

        # Generate 1 second of 440Hz sine wave
        sine_wave = Sine(440).to_audio_segment(duration=1000)
        audio_path = tmp_path / f"test{ext}"
        sine_wave.export(str(audio_path), format=format_name)

        # Convert to FLAC
        flac_path, cleanup_path = optimizer._convert_to_flac(str(audio_path))

        try:
            assert flac_path.endswith(".flac")
            assert os.path.exists(flac_path)
        finally:
            optimizer.cleanup(cleanup_path)

    def test_strip_metadata_produces_clean_flac(self, optimizer, tmp_path):
        """Test that _strip_metadata creates a clean FLAC file without metadata."""
        pytest.importorskip("pydub")

        from pydub import AudioSegment
        from pydub.generators import Sine

        # Generate a FLAC file with metadata
        sine_wave = Sine(440).to_audio_segment(duration=1000)
        flac_path = tmp_path / "test_with_metadata.flac"
        sine_wave.export(str(flac_path), format="flac", tags={"title": "Test", "artist": "Bot"})

        # Strip metadata
        clean_path, cleanup_path = optimizer._strip_metadata(str(flac_path))

        try:
            assert clean_path.endswith(".flac")
            assert os.path.exists(clean_path)
            assert os.path.getsize(clean_path) > 0

            # Verify metadata was stripped using ffprobe
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", clean_path],
                capture_output=True, text=True,
            )
            import json
            probe = json.loads(result.stdout)
            tags = probe.get("format", {}).get("tags", {})
            assert "title" not in tags
            assert "artist" not in tags
        finally:
            optimizer.cleanup(cleanup_path)
