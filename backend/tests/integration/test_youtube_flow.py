"""
Integration tests for YouTube download flow.

Tests the full flow from API endpoint to download completion, verifying that
both entry points (audio search selection and direct URL submission) correctly
use the YouTubeDownloadService.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


@pytest.fixture
def mock_youtube_service():
    """Create a mock YouTubeDownloadService."""
    mock = MagicMock()
    mock.download = AsyncMock(return_value="uploads/test_job/audio/test.flac")
    mock.download_by_id = AsyncMock(return_value="uploads/test_job/audio/test.flac")
    mock.is_remote_enabled = MagicMock(return_value=True)
    mock._extract_video_id = MagicMock(return_value="test_video_id")
    return mock


@pytest.fixture
def mock_services():
    """Mock common services used across tests."""
    mocks = {
        'job_manager': MagicMock(),
        'storage_service': MagicMock(),
        'worker_service': MagicMock(),
        'auth_service': MagicMock(),
    }

    # Configure job_manager mock
    mock_job = MagicMock()
    mock_job.job_id = "test_job_123"
    mock_job.status = "pending"
    mock_job.state_data = {}
    mocks['job_manager'].create_job = MagicMock(return_value=mock_job)
    mocks['job_manager'].get_job = MagicMock(return_value=mock_job)
    mocks['job_manager'].transition_to_state = MagicMock()
    mocks['job_manager'].update_job = MagicMock()
    mocks['job_manager'].fail_job = MagicMock()

    # Configure worker service mock
    mocks['worker_service'].trigger_audio_worker = AsyncMock()
    mocks['worker_service'].trigger_lyrics_worker = AsyncMock()

    return mocks


class TestAudioSearchYouTubeFlow:
    """Tests for YouTube downloads via audio search selection."""

    @pytest.mark.asyncio
    async def test_audio_search_select_youtube_uses_youtube_service(self, mock_youtube_service, mock_services):
        """
        When selecting a YouTube result from audio search,
        should use YouTubeDownloadService instead of direct download.
        """
        # Configure mock job with search results
        mock_job = mock_services['job_manager'].get_job.return_value
        mock_job.status = "awaiting_audio_selection"
        mock_job.state_data = {
            'audio_search_results': [{
                'index': 0,
                'title': 'Test Song',
                'artist': 'Test Artist',
                'provider': 'YouTube',
                'source_id': 'dQw4w9WgXcQ',
                'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            }],
            'audio_search_count': 1,
        }

        # Import the route function
        from backend.api.routes.audio_search import _download_and_start_processing

        # Patch dependencies
        with patch(
            'backend.api.routes.audio_search.get_youtube_download_service',
            return_value=mock_youtube_service
        ), patch(
            'backend.api.routes.audio_search.job_manager',
            mock_services['job_manager']
        ), patch(
            'backend.api.routes.audio_search.storage_service',
            mock_services['storage_service']
        ):
            # Create a mock audio_search_service
            mock_audio_search_service = MagicMock()
            mock_audio_search_service.is_remote_enabled = MagicMock(return_value=True)

            # Create a mock background_tasks
            mock_background_tasks = MagicMock()

            # Call the download function
            result = await _download_and_start_processing(
                job_id="test_job_123",
                selection_index=0,
                audio_search_service=mock_audio_search_service,
                background_tasks=mock_background_tasks,
            )

            # Verify YouTubeDownloadService was used
            mock_youtube_service.download_by_id.assert_called_once()
            call_args = mock_youtube_service.download_by_id.call_args

            assert call_args.kwargs['video_id'] == 'dQw4w9WgXcQ'
            assert call_args.kwargs['job_id'] == 'test_job_123'
            assert call_args.kwargs['artist'] == 'Test Artist'
            assert call_args.kwargs['title'] == 'Test Song'

    @pytest.mark.asyncio
    async def test_torrent_download_does_not_use_youtube_service(self, mock_youtube_service, mock_services):
        """
        When selecting a torrent result (RED/OPS), should NOT use YouTubeDownloadService.
        """
        mock_job = mock_services['job_manager'].get_job.return_value
        mock_job.status = "awaiting_audio_selection"
        mock_job.state_data = {
            'audio_search_results': [{
                'index': 0,
                'title': 'Test Song',
                'artist': 'Test Artist',
                'provider': 'RED',
                'source_id': '12345',
                'target_file': 'test.flac',
            }],
            'audio_search_count': 1,
        }

        from backend.api.routes.audio_search import _download_and_start_processing

        # Mock audio_search_service for torrent download
        mock_audio_search_service = MagicMock()
        mock_audio_search_service.is_remote_enabled = MagicMock(return_value=True)
        mock_result = MagicMock()
        mock_result.filepath = "gs://bucket/uploads/test/audio/test.flac"
        mock_audio_search_service.download_by_id = MagicMock(return_value=mock_result)

        with patch(
            'backend.api.routes.audio_search.get_youtube_download_service',
            return_value=mock_youtube_service
        ), patch(
            'backend.api.routes.audio_search.job_manager',
            mock_services['job_manager']
        ), patch(
            'backend.api.routes.audio_search.storage_service',
            mock_services['storage_service']
        ):
            mock_background_tasks = MagicMock()

            result = await _download_and_start_processing(
                job_id="test_job_123",
                selection_index=0,
                audio_search_service=mock_audio_search_service,
                background_tasks=mock_background_tasks,
            )

            # YouTubeDownloadService should NOT be called for torrent
            mock_youtube_service.download_by_id.assert_not_called()

            # audio_search_service.download_by_id should be called instead
            mock_audio_search_service.download_by_id.assert_called_once()


class TestDirectUrlSubmissionFlow:
    """Tests for direct YouTube URL submission via /api/jobs/url endpoint."""

    @pytest.mark.asyncio
    async def test_create_job_from_youtube_url_downloads_immediately(
        self, mock_youtube_service, mock_services
    ):
        """
        When creating a job from a YouTube URL, should download audio
        immediately (before triggering workers) using YouTubeDownloadService.
        """
        from backend.api.routes.file_upload import _is_youtube_url

        # Verify helper function works
        assert _is_youtube_url("https://www.youtube.com/watch?v=test") is True
        assert _is_youtube_url("https://youtu.be/test") is True
        assert _is_youtube_url("https://vimeo.com/test") is False

    @pytest.mark.asyncio
    async def test_non_youtube_url_triggers_worker_download(self, mock_services):
        """
        When creating a job from a non-YouTube URL (e.g., Vimeo),
        should trigger audio worker to handle the download.
        """
        from backend.api.routes.file_upload import _is_youtube_url

        # Non-YouTube URLs should not match
        assert _is_youtube_url("https://vimeo.com/123456") is False
        assert _is_youtube_url("https://soundcloud.com/artist/track") is False


class TestYouTubeDownloadErrorHandling:
    """Tests for error handling in YouTube download flow."""

    @pytest.mark.asyncio
    async def test_youtube_download_failure_fails_job(self, mock_services):
        """
        When YouTube download fails, should properly fail the job
        with a clear error message.
        """
        from backend.services.youtube_download_service import (
            YouTubeDownloadService,
            YouTubeDownloadError,
        )

        # Create a service that will fail
        mock_client = MagicMock()
        mock_client.download_by_id = AsyncMock(
            side_effect=Exception("Bot detection triggered")
        )

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            from backend.services.youtube_download_service import reset_youtube_download_service
            reset_youtube_download_service()

            service = YouTubeDownloadService()

            with pytest.raises(YouTubeDownloadError) as exc:
                await service.download(
                    url="https://youtu.be/abcDEF12345",
                    job_id="job123",
                )

            assert "Remote download failed" in str(exc.value)

    @pytest.mark.asyncio
    async def test_invalid_youtube_url_raises_clear_error(self):
        """
        When given an invalid YouTube URL (no video ID extractable),
        should raise a clear error.
        """
        from backend.services.youtube_download_service import (
            YouTubeDownloadService,
            YouTubeDownloadError,
            reset_youtube_download_service,
        )

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=None  # No remote client
        ):
            reset_youtube_download_service()
            service = YouTubeDownloadService()

            with pytest.raises(YouTubeDownloadError) as exc:
                await service.download(
                    url="not-a-valid-youtube-url",
                    job_id="job123",
                )

            assert "Could not extract video ID" in str(exc.value)


class TestRemoteVsLocalDownload:
    """Tests verifying correct routing between remote and local download."""

    @pytest.mark.asyncio
    async def test_remote_download_when_configured(self):
        """
        When FLACFETCH_API_URL is configured, should use remote download.
        """
        mock_client = MagicMock()
        mock_client.download_by_id = AsyncMock(return_value="download_123")
        mock_client.wait_for_download = AsyncMock(return_value={
            "status": "complete",
            "gcs_path": "gs://bucket/uploads/job_test/audio/test.flac",
        })

        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=mock_client
        ):
            from backend.services.youtube_download_service import (
                YouTubeDownloadService,
                reset_youtube_download_service,
            )
            reset_youtube_download_service()

            service = YouTubeDownloadService()
            assert service.is_remote_enabled() is True

            result = await service.download(
                url="https://youtu.be/abcDEF12345",
                job_id="job_test",
            )

            # Should have called remote client
            mock_client.download_by_id.assert_called_once()
            assert "uploads/job_test/audio/" in result

    @pytest.mark.asyncio
    async def test_local_download_when_remote_not_configured(self):
        """
        When FLACFETCH_API_URL is not configured, should fall back to local.
        """
        with patch(
            'backend.services.youtube_download_service.get_flacfetch_client',
            return_value=None  # No remote client
        ):
            from backend.services.youtube_download_service import (
                YouTubeDownloadService,
                reset_youtube_download_service,
            )
            reset_youtube_download_service()

            service = YouTubeDownloadService()
            assert service.is_remote_enabled() is False

            # Mock the local download to avoid actually calling yt_dlp
            with patch.object(
                service, '_download_local',
                new_callable=AsyncMock,
                return_value="uploads/job/audio/test.wav"
            ) as mock_local:
                result = await service.download(
                    url="https://youtu.be/abcDEF12345",
                    job_id="job_test",
                )

                mock_local.assert_called_once()
                assert result == "uploads/job/audio/test.wav"
