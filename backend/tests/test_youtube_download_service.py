"""
Unit tests for YouTubeDownloadService.

Tests the consolidated YouTube download service that handles all YouTube
downloads in the cloud backend.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.flacfetch_client import FlacfetchServiceError
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
    # Generic (non-YouTube) URL Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_download_generic_url_uses_url_source(self):
        """Non-YouTube URLs route through flacfetch's generic 'URL' source."""
        mock_client = MagicMock()
        mock_client.download_by_id = AsyncMock(return_value="download_123")
        mock_client.wait_for_download = AsyncMock(return_value={
            "status": "complete",
            "gcs_path": "gs://bucket/uploads/job123/audio/Artist - Title.m4a",
        })

        fb_url = "https://www.facebook.com/share/v/1EnC8Bi5Uq/"
        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            service = YouTubeDownloadService()

            result = await service.download(
                url=fb_url,
                job_id="job123",
                artist="Artist",
                title="Title",
            )

            assert result == "uploads/job123/audio/Artist - Title.m4a"
            call_kwargs = mock_client.download_by_id.call_args.kwargs
            assert call_kwargs["source_name"] == "URL"
            assert call_kwargs["download_url"] == fb_url
            assert call_kwargs["source_id"] == fb_url

    @pytest.mark.asyncio
    async def test_download_raises_when_remote_not_configured(self):
        """No silent local fallback: hard-fail when flacfetch isn't configured."""
        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=None
        ):
            service = YouTubeDownloadService()

            with pytest.raises(YouTubeDownloadError) as exc:
                await service.download(
                    url="https://youtu.be/dQw4w9WgXcQ",
                    job_id="job123",
                )

            assert "flacfetch" in str(exc.value).lower()

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


class TestCheckAvailability:
    """Tests for YouTubeDownloadService.check_availability()."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_youtube_download_service()

    @pytest.mark.asyncio
    async def test_returns_result_when_available(self):
        """Should return availability result for available video."""
        mock_client = AsyncMock()
        mock_client.check_youtube = AsyncMock(return_value={
            "available": True,
            "video_id": "dQw4w9WgXcQ",
            "title": "Test Video",
        })

        with patch("backend.services.youtube_download_service.get_flacfetch_client", return_value=mock_client):
            service = YouTubeDownloadService()
            result = await service.check_availability("https://youtube.com/watch?v=dQw4w9WgXcQ")

        assert result is not None
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_returns_result_when_geo_restricted(self):
        """Should return availability result for geo-restricted video."""
        mock_client = AsyncMock()
        mock_client.check_youtube = AsyncMock(return_value={
            "available": False,
            "video_id": "-yV25PrHglw",
            "is_geo_restricted": True,
            "error": "Not available in your region",
        })

        with patch("backend.services.youtube_download_service.get_flacfetch_client", return_value=mock_client):
            service = YouTubeDownloadService()
            result = await service.check_availability("https://youtube.com/watch?v=-yV25PrHglw")

        assert result is not None
        assert result["available"] is False
        assert result["is_geo_restricted"] is True

    @pytest.mark.asyncio
    async def test_returns_none_when_remote_not_configured(self):
        """Should return None when no remote flacfetch configured."""
        with patch("backend.services.youtube_download_service.get_flacfetch_client", return_value=None):
            service = YouTubeDownloadService()
            result = await service.check_availability("https://youtube.com/watch?v=dQw4w9WgXcQ")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self):
        """Should return None on network error (graceful degradation)."""
        mock_client = AsyncMock()
        mock_client.check_youtube = AsyncMock(
            side_effect=FlacfetchServiceError("Connection refused")
        )

        with patch("backend.services.youtube_download_service.get_flacfetch_client", return_value=mock_client):
            service = YouTubeDownloadService()
            result = await service.check_availability("https://youtube.com/watch?v=dQw4w9WgXcQ")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self):
        """Should return None for URLs that don't parse as YouTube."""
        mock_client = AsyncMock()

        with patch("backend.services.youtube_download_service.get_flacfetch_client", return_value=mock_client):
            service = YouTubeDownloadService()
            result = await service.check_availability("https://not-youtube.com/video")

        assert result is None
        mock_client.check_youtube.assert_not_called()
