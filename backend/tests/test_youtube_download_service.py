"""
Unit tests for YouTubeDownloadService.

Tests the consolidated YouTube download service that handles all YouTube
downloads in the cloud backend.
"""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.youtube_download_service import (
    YouTubeDownloadService,
    YouTubeDownloadError,
    get_youtube_download_service,
    reset_youtube_download_service,
)


class TestYouTubeDownloadService:
    """Tests for YouTubeDownloadService."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_youtube_download_service()

    # =========================================================================
    # Video ID Extraction Tests
    # =========================================================================

    def test_extract_video_id_standard_watch_url(self):
        """Should extract video ID from standard watch URL."""
        service = YouTubeDownloadService()
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert service._extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_short_url(self):
        """Should extract video ID from youtu.be short URL."""
        service = YouTubeDownloadService()
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert service._extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_shorts_url(self):
        """Should extract video ID from YouTube Shorts URL."""
        service = YouTubeDownloadService()
        url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        assert service._extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_embed_url(self):
        """Should extract video ID from embed URL."""
        service = YouTubeDownloadService()
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        assert service._extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_with_extra_params(self):
        """Should extract video ID when URL has extra parameters."""
        service = YouTubeDownloadService()
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLtest&t=120"
        assert service._extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_v_before_other_params(self):
        """Should extract video ID when v is not the first param."""
        service = YouTubeDownloadService()
        url = "https://www.youtube.com/watch?list=PLtest&v=dQw4w9WgXcQ"
        assert service._extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_old_style_url(self):
        """Should extract video ID from old-style /v/ URL."""
        service = YouTubeDownloadService()
        url = "https://www.youtube.com/v/dQw4w9WgXcQ"
        assert service._extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_video_id_invalid_url(self):
        """Should return None for non-YouTube URLs."""
        service = YouTubeDownloadService()
        assert service._extract_video_id("https://vimeo.com/123456") is None
        assert service._extract_video_id("not a url") is None

    # =========================================================================
    # Remote Download Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_download_uses_remote_when_configured(self):
        """When FLACFETCH_API_URL is set, should use remote client."""
        mock_client = MagicMock()
        mock_client.download_by_id = AsyncMock(return_value="download_123")
        mock_client.wait_for_download = AsyncMock(return_value={
            "status": "complete",
            "gcs_path": "gs://bucket/uploads/job123/audio/Artist - Title.flac",
        })

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            service = YouTubeDownloadService()

            result = await service.download(
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                job_id="job123",
                artist="Rick Astley",
                title="Never Gonna Give You Up",
            )

            assert result == "uploads/job123/audio/Artist - Title.flac"
            mock_client.download_by_id.assert_called_once()
            mock_client.wait_for_download.assert_called_once()

    @pytest.mark.asyncio
    async def test_download_by_id_constructs_url(self):
        """download_by_id should construct URL from video ID."""
        mock_client = MagicMock()
        mock_client.download_by_id = AsyncMock(return_value="download_123")
        mock_client.wait_for_download = AsyncMock(return_value={
            "status": "complete",
            "gcs_path": "gs://bucket/uploads/job123/audio/file.flac",
        })

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            service = YouTubeDownloadService()

            result = await service.download_by_id(
                video_id="dQw4w9WgXcQ",
                job_id="job123",
            )

            assert result == "uploads/job123/audio/file.flac"

    @pytest.mark.asyncio
    async def test_download_extracts_gcs_path_from_gs_url(self):
        """Should correctly extract path portion from gs:// URL."""
        mock_client = MagicMock()
        mock_client.download_by_id = AsyncMock(return_value="download_123")
        mock_client.wait_for_download = AsyncMock(return_value={
            "status": "complete",
            "gcs_path": "gs://my-bucket/uploads/job123/audio/test.flac",
        })

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            service = YouTubeDownloadService()

            result = await service.download(
                url="https://youtu.be/dQw4w9WgXcQ",
                job_id="job123",
            )

            assert result == "uploads/job123/audio/test.flac"
            assert not result.startswith("gs://")

    @pytest.mark.asyncio
    async def test_download_handles_remote_failure(self):
        """Should raise YouTubeDownloadError on remote failure."""
        from backend.services.flacfetch_client import FlacfetchServiceError

        mock_client = MagicMock()
        mock_client.download_by_id = AsyncMock(
            side_effect=FlacfetchServiceError("Connection failed")
        )

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            service = YouTubeDownloadService()

            with pytest.raises(YouTubeDownloadError) as exc:
                await service.download(
                    url="https://youtu.be/dQw4w9WgXcQ",
                    job_id="job123",
                )

            assert "Remote download failed" in str(exc.value)

    # =========================================================================
    # Local Fallback Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_download_falls_back_to_local_when_remote_disabled(self):
        """When remote not configured, should use local yt_dlp."""
        mock_storage = MagicMock()
        mock_storage.upload_fileobj = MagicMock()

        mock_file_handler = MagicMock()
        mock_file_handler.download_video = MagicMock(return_value="/tmp/test.webm")
        mock_file_handler.convert_to_wav = MagicMock(return_value="/tmp/test.wav")

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=None  # No remote client
        ), patch(
            'backend.services.youtube_download_service.StorageService',
            return_value=mock_storage
        ), patch(
            'backend.services.youtube_download_service.YouTubeDownloadService._download_local',
            new_callable=AsyncMock,
            return_value="uploads/job123/audio/test.wav"
        ) as mock_local:
            service = YouTubeDownloadService()

            result = await service.download(
                url="https://youtu.be/dQw4w9WgXcQ",
                job_id="job123",
            )

            assert result == "uploads/job123/audio/test.wav"
            mock_local.assert_called_once()

    @pytest.mark.asyncio
    async def test_download_invalid_url_raises_error(self):
        """Should raise error for URLs that can't extract video ID."""
        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=None
        ):
            service = YouTubeDownloadService()

            with pytest.raises(YouTubeDownloadError) as exc:
                await service.download(
                    url="https://vimeo.com/123456",
                    job_id="job123",
                )

            assert "Could not extract video ID" in str(exc.value)

    # =========================================================================
    # Singleton Tests
    # =========================================================================

    def test_get_youtube_download_service_returns_singleton(self):
        """get_youtube_download_service should return same instance."""
        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=None
        ):
            service1 = get_youtube_download_service()
            service2 = get_youtube_download_service()

            assert service1 is service2

    def test_reset_youtube_download_service_clears_singleton(self):
        """reset_youtube_download_service should clear the singleton."""
        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=None
        ):
            service1 = get_youtube_download_service()
            reset_youtube_download_service()
            service2 = get_youtube_download_service()

            assert service1 is not service2

    # =========================================================================
    # Remote Enabled Check
    # =========================================================================

    def test_is_remote_enabled_true_when_client_configured(self):
        """is_remote_enabled should return True when flacfetch client exists."""
        mock_client = MagicMock()

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            service = YouTubeDownloadService()
            assert service.is_remote_enabled() is True

    def test_is_remote_enabled_false_when_no_client(self):
        """is_remote_enabled should return False when no flacfetch client."""
        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=None
        ):
            service = YouTubeDownloadService()
            assert service.is_remote_enabled() is False


class TestYouTubeDownloadServiceIntegration:
    """Integration tests that verify full download flow with mocked external services."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_youtube_download_service()

    @pytest.mark.asyncio
    async def test_remote_download_flow_complete(self):
        """Test complete remote download flow with proper state transitions."""
        mock_client = MagicMock()

        # Track call sequence
        call_sequence = []

        async def mock_download_by_id(**kwargs):
            call_sequence.append(('download_by_id', kwargs))
            return "download_abc123"

        async def mock_wait_for_download(download_id, **kwargs):
            call_sequence.append(('wait_for_download', download_id))
            return {
                "status": "complete",
                "gcs_path": "gs://karaoke-bucket/uploads/job_test/audio/Artist - Song.opus",
            }

        mock_client.download_by_id = mock_download_by_id
        mock_client.wait_for_download = mock_wait_for_download

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            service = YouTubeDownloadService()

            # Use a valid 11-character video ID (YouTube IDs are always 11 chars)
            result = await service.download(
                url="https://www.youtube.com/watch?v=abcDEF12345",
                job_id="job_test",
                artist="Test Artist",
                title="Test Song",
            )

            # Verify result
            assert result == "uploads/job_test/audio/Artist - Song.opus"

            # Verify call sequence
            assert len(call_sequence) == 2
            assert call_sequence[0][0] == 'download_by_id'
            assert call_sequence[0][1]['source_name'] == 'YouTube'
            assert call_sequence[0][1]['source_id'] == 'abcDEF12345'
            assert call_sequence[1][0] == 'wait_for_download'
            assert call_sequence[1][1] == 'download_abc123'

    @pytest.mark.asyncio
    async def test_output_filename_sanitization(self):
        """Test that artist/title are sanitized for output filename."""
        mock_client = MagicMock()
        mock_client.download_by_id = AsyncMock(return_value="download_123")
        mock_client.wait_for_download = AsyncMock(return_value={
            "status": "complete",
            "gcs_path": "gs://bucket/uploads/job/audio/file.flac",
        })

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            service = YouTubeDownloadService()

            # Use a valid 11-character video ID
            await service.download(
                url="https://youtu.be/abcDEF12345",
                job_id="job123",
                artist="Artist's Name",  # Contains apostrophe
                title="Title: With/Symbols",  # Contains colon and slash
            )

            # Check that download_by_id was called with output_filename
            call_args = mock_client.download_by_id.call_args
            assert call_args is not None

            # Verify output_filename was passed and is sanitized
            call_kwargs = call_args.kwargs
            assert 'output_filename' in call_kwargs
            output_filename = call_kwargs['output_filename']

            # The filename should NOT contain special chars that break filenames
            # (apostrophe, colon, slash are typically sanitized)
            assert ':' not in output_filename
            assert '/' not in output_filename
            # Should contain artist and title parts
            assert 'Artist' in output_filename
            assert 'Title' in output_filename
