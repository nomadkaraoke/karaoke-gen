"""
Tests for Encoding Interface.

Tests cover:
- EncodingInput and EncodingOutput dataclasses
- LocalEncodingBackend implementation
- Backend factory function
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from backend.services.encoding_interface import (
    EncodingInput,
    EncodingOutput,
    EncodingBackend,
    LocalEncodingBackend,
    GCEEncodingBackend,
    get_encoding_backend,
)


class TestEncodingInput:
    """Test EncodingInput dataclass."""

    def test_required_fields(self):
        """Test that required fields must be provided."""
        input_config = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        assert input_config.title_video_path == "/path/title.mov"
        assert input_config.end_video_path is None

    def test_all_fields(self):
        """Test all fields including optional ones."""
        input_config = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            end_video_path="/path/end.mov",
            artist="Test Artist",
            title="Test Title",
            brand_code="NOMAD-1234",
            output_dir="/output",
            options={"quality": "high"}
        )
        assert input_config.artist == "Test Artist"
        assert input_config.brand_code == "NOMAD-1234"
        assert input_config.options["quality"] == "high"


class TestEncodingOutput:
    """Test EncodingOutput dataclass."""

    def test_success_output(self):
        """Test successful output."""
        output = EncodingOutput(
            success=True,
            lossless_4k_mp4_path="/output/video.mp4",
            encoding_backend="local"
        )
        assert output.success is True
        assert output.error_message is None

    def test_failure_output(self):
        """Test failure output."""
        output = EncodingOutput(
            success=False,
            error_message="Encoding failed",
            encoding_backend="gce"
        )
        assert output.success is False
        assert output.error_message == "Encoding failed"

    def test_output_files_dict(self):
        """Test output_files dictionary."""
        output = EncodingOutput(
            success=True,
            output_files={
                "lossless_4k_mp4": "/output/lossless.mp4",
                "720p_mp4": "/output/720p.mp4"
            }
        )
        assert "lossless_4k_mp4" in output.output_files
        assert output.output_files["720p_mp4"] == "/output/720p.mp4"


class TestLocalEncodingBackend:
    """Test LocalEncodingBackend implementation."""

    def test_name(self):
        """Test backend name."""
        backend = LocalEncodingBackend()
        assert backend.name == "local"

    def test_init_with_dry_run(self):
        """Test initialization with dry run."""
        backend = LocalEncodingBackend(dry_run=True)
        assert backend.dry_run is True

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_is_available_success(self, mock_run):
        """Test availability check when FFmpeg is installed."""
        mock_run.return_value = MagicMock(returncode=0)

        backend = LocalEncodingBackend()
        available = await backend.is_available()

        assert available is True

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_is_available_not_found(self, mock_run):
        """Test availability check when FFmpeg is not installed."""
        mock_run.side_effect = FileNotFoundError()

        backend = LocalEncodingBackend()
        available = await backend.is_available()

        assert available is False

    @pytest.mark.asyncio
    @patch.object(LocalEncodingBackend, "is_available")
    @patch.object(LocalEncodingBackend, "_get_service")
    async def test_get_status(self, mock_get_service, mock_is_available):
        """Test status retrieval."""
        mock_is_available.return_value = True
        mock_service = MagicMock()
        mock_service.hwaccel_available = True
        mock_service.video_encoder = "h264_nvenc"
        mock_get_service.return_value = mock_service

        backend = LocalEncodingBackend()
        status = await backend.get_status()

        assert status["backend"] == "local"
        assert status["available"] is True

    @pytest.mark.asyncio
    @patch("asyncio.to_thread")
    @patch.object(LocalEncodingBackend, "_get_service")
    async def test_encode_success(self, mock_get_service, mock_to_thread):
        """Test successful encoding."""
        from backend.services.local_encoding_service import EncodingResult

        mock_result = EncodingResult(
            success=True,
            output_files={"key": "/path/file.mp4"}
        )
        mock_to_thread.return_value = mock_result

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        backend = LocalEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Test Artist",
            title="Test Title",
            output_dir="/output"
        )

        output = await backend.encode(input_config)

        assert output.success is True
        assert output.encoding_backend == "local"
        assert output.encoding_time_seconds is not None

    @pytest.mark.asyncio
    @patch("asyncio.to_thread")
    @patch.object(LocalEncodingBackend, "_get_service")
    async def test_encode_failure(self, mock_get_service, mock_to_thread):
        """Test encoding failure."""
        from backend.services.local_encoding_service import EncodingResult

        mock_result = EncodingResult(
            success=False,
            output_files={},
            error="FFmpeg failed"
        )
        mock_to_thread.return_value = mock_result

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        backend = LocalEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Test",
            title="Test",
        )

        output = await backend.encode(input_config)

        assert output.success is False
        assert output.error_message == "FFmpeg failed"


class TestGCEEncodingBackend:
    """Test GCEEncodingBackend implementation."""

    def test_name(self):
        """Test backend name."""
        backend = GCEEncodingBackend()
        assert backend.name == "gce"

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_is_available_enabled(self, mock_get_service):
        """Test availability when GCE is enabled."""
        mock_service = MagicMock()
        mock_service.is_enabled = True
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        available = await backend.is_available()

        assert available is True

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_is_available_disabled(self, mock_get_service):
        """Test availability when GCE is disabled."""
        mock_service = MagicMock()
        mock_service.is_enabled = False
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        available = await backend.is_available()

        assert available is False

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_missing_gcs_paths(self, mock_get_service):
        """Test encoding fails without GCS paths."""
        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            # Missing GCS paths in options
        )

        output = await backend.encode(input_config)

        assert output.success is False
        assert "gcs_path" in output.error_message.lower()

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_success(self, mock_get_service):
        """Test successful GCE encoding."""
        mock_service = MagicMock()
        mock_service.encode_videos = AsyncMock(return_value={
            "status": "complete",
            "output_files": {
                "mp4_4k_lossless": "gs://bucket/output/lossless.mp4",
                "mp4_720p": "gs://bucket/output/720p.mp4",
            }
        })
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Test Artist",
            title="Test Title",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        output = await backend.encode(input_config)

        assert output.success is True
        assert output.encoding_backend == "gce"
        mock_service.encode_videos.assert_called_once()

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_failure(self, mock_get_service):
        """Test GCE encoding failure handling."""
        mock_service = MagicMock()
        mock_service.encode_videos = AsyncMock(side_effect=Exception("GCE worker error"))
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        output = await backend.encode(input_config)

        assert output.success is False
        assert "GCE worker error" in output.error_message

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_handles_list_result(self, mock_get_service):
        """Test GCE encoding handles list response gracefully.

        This would have caught: 'list' object has no attribute 'get' error
        when GCE worker returns a list instead of a dict.
        """
        mock_service = MagicMock()
        # Simulate GCE worker returning a list instead of dict
        mock_service.encode_videos = AsyncMock(return_value=[
            {"output_files": {"mp4_4k_lossless": "gs://bucket/output/lossless.mp4"}}
        ])
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        # This should not raise an error
        output = await backend.encode(input_config)

        # Should still succeed by extracting from the list
        assert output.success is True
        assert output.lossless_4k_mp4_path == "gs://bucket/output/lossless.mp4"


class TestGetEncodingBackend:
    """Test encoding backend factory function."""

    def test_get_local_backend(self):
        """Test getting local backend."""
        backend = get_encoding_backend("local")
        assert isinstance(backend, LocalEncodingBackend)
        assert backend.name == "local"

    @patch.object(GCEEncodingBackend, "_get_service")
    def test_get_auto_backend_gce_disabled(self, mock_get_service):
        """Test getting auto backend falls back to local when GCE disabled."""
        mock_service = MagicMock()
        mock_service.is_enabled = False
        mock_get_service.return_value = mock_service

        backend = get_encoding_backend("auto")
        assert isinstance(backend, LocalEncodingBackend)

    def test_get_local_backend_with_options(self):
        """Test getting local backend with options."""
        backend = get_encoding_backend("local", dry_run=True)
        assert backend.dry_run is True

    def test_get_gce_backend(self):
        """Test getting GCE backend."""
        backend = get_encoding_backend("gce")
        assert isinstance(backend, GCEEncodingBackend)
        assert backend.name == "gce"

    def test_get_unknown_backend_raises(self):
        """Test that unknown backend raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_encoding_backend("unknown")
        assert "Unknown encoding backend type" in str(exc_info.value)

    def test_get_gce_backend_with_options(self):
        """Test getting GCE backend with common options like dry_run.

        This ensures all backends accept the same kwargs, preventing
        errors when get_encoding_backend() passes **kwargs to different backends.
        """
        # This would have caught: GCEEncodingBackend.__init__() got an unexpected keyword argument 'dry_run'
        backend = get_encoding_backend("gce", dry_run=True)
        assert isinstance(backend, GCEEncodingBackend)
        assert backend.dry_run is True

    @patch.object(GCEEncodingBackend, "_get_service")
    def test_all_backends_accept_dry_run(self, mock_get_service):
        """Test that all backend types accept dry_run parameter.

        This is an integration test to ensure the factory function
        can pass dry_run to any backend without TypeError.
        """
        mock_service = MagicMock()
        mock_service.is_enabled = False
        mock_get_service.return_value = mock_service

        for backend_type in ["local", "gce", "auto"]:
            # This should not raise TypeError
            backend = get_encoding_backend(backend_type, dry_run=True)
            assert backend is not None
