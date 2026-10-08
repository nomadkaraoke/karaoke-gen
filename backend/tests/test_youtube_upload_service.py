"""
Tests for YouTubeUploadService.

Tests cover:
- Authentication with pre-stored credentials
- Authentication with client secrets file
- Duplicate video detection (exact and fuzzy matching)
- Video deletion
- Video upload with metadata and thumbnail
- Title truncation
- Dry run mode
"""

import os
import pytest
from unittest.mock import MagicMock, patch, mock_open

from backend.services.youtube_upload_service import YouTubeUploadService, get_youtube_upload_service


class TestYouTubeUploadServiceInit:
    """Test service initialization."""

    def test_init_with_credentials(self):
        """Test initialization with pre-stored credentials."""
        creds = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_secret",
        }
        service = YouTubeUploadService(
            credentials=creds,
            non_interactive=True
        )
        assert service.credentials == creds
        assert service.non_interactive is True

    def test_init_with_client_secrets(self):
        """Test initialization with client secrets file."""
        service = YouTubeUploadService(
            client_secrets_file="/path/to/secrets.json"
        )
        assert service.client_secrets_file == "/path/to/secrets.json"
        assert service.non_interactive is False

    def test_init_default_values(self):
        """Test default values on initialization."""
        service = YouTubeUploadService()
        assert service.credentials is None
        assert service.client_secrets_file is None
        assert service.non_interactive is False
        assert service.server_side_mode is False
        assert service.dry_run is False


class TestYouTubeUploadServiceAuthentication:
    """Test authentication methods."""

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_authenticate_with_prestored_credentials(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test authentication using pre-stored credentials."""
        # Setup mocks
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds
        mock_youtube = MagicMock()
        mock_build.return_value = mock_youtube

        creds = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_secret",
            "scopes": ["https://www.googleapis.com/auth/youtube"],
        }
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        # Access youtube_service to trigger authentication
        result = service.youtube_service

        assert result == mock_youtube
        mock_credentials_class.assert_called_once()
        mock_build.assert_called_once_with('youtube', 'v3', credentials=mock_creds)

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_authenticate_refreshes_expired_token(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test that expired tokens are refreshed."""
        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "test_refresh"
        mock_credentials_class.return_value = mock_creds

        creds = {
            "token": "expired_token",
            "refresh_token": "test_refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_secret",
        }
        service = YouTubeUploadService(credentials=creds, non_interactive=True)
        service.youtube_service

        mock_creds.refresh.assert_called_once()

    def test_authenticate_non_interactive_no_credentials_raises(self):
        """Test that non-interactive mode without credentials raises error."""
        service = YouTubeUploadService(non_interactive=True)

        with pytest.raises(Exception) as exc_info:
            service._authenticate()

        assert "non-interactive mode" in str(exc_info.value).lower()


class TestYouTubeUploadServiceChannelId:
    """Test channel ID retrieval."""

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_get_channel_id_success(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test successful channel ID retrieval."""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        mock_channels = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "items": [{"id": "UC123456"}]
        }
        mock_channels.list.return_value = mock_list
        mock_youtube.channels.return_value = mock_channels
        mock_build.return_value = mock_youtube

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        channel_id = service.get_channel_id()

        assert channel_id == "UC123456"

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_get_channel_id_no_items(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test channel ID returns None when no items found."""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        mock_channels = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {"items": []}
        mock_channels.list.return_value = mock_list
        mock_youtube.channels.return_value = mock_channels
        mock_build.return_value = mock_youtube

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        channel_id = service.get_channel_id()

        assert channel_id is None


class TestYouTubeUploadServiceDuplicateCheck:
    """Test duplicate video detection."""

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_check_duplicate_exact_match_found(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test duplicate detection with exact match in server mode."""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        # Mock channels().list() for get_channel_id
        mock_channels = MagicMock()
        mock_channels_list = MagicMock()
        mock_channels_list.execute.return_value = {"items": [{"id": "UC123456"}]}
        mock_channels.list.return_value = mock_channels_list
        mock_youtube.channels.return_value = mock_channels

        # Mock search().list() for duplicate check
        mock_search = MagicMock()
        mock_search_list = MagicMock()
        mock_search_list.execute.return_value = {
            "items": [{
                "id": {"videoId": "VIDEO123"},
                "snippet": {
                    "channelId": "UC123456",
                    "title": "Test Artist - Test Song (Karaoke)"
                }
            }]
        }
        mock_search.list.return_value = mock_search_list
        mock_youtube.search.return_value = mock_search
        mock_build.return_value = mock_youtube

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(
            credentials=creds,
            non_interactive=True,
            server_side_mode=True
        )

        exists, video_id, video_url = service.check_duplicate(
            "Test Artist - Test Song (Karaoke)"
        )

        assert exists is True
        assert video_id == "VIDEO123"
        assert "VIDEO123" in video_url

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_check_duplicate_no_match(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test duplicate detection when no match found."""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        mock_channels = MagicMock()
        mock_channels_list = MagicMock()
        mock_channels_list.execute.return_value = {"items": [{"id": "UC123456"}]}
        mock_channels.list.return_value = mock_channels_list
        mock_youtube.channels.return_value = mock_channels

        mock_search = MagicMock()
        mock_search_list = MagicMock()
        mock_search_list.execute.return_value = {"items": []}
        mock_search.list.return_value = mock_search_list
        mock_youtube.search.return_value = mock_search
        mock_build.return_value = mock_youtube

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        exists, video_id, video_url = service.check_duplicate("Some Title")

        assert exists is False
        assert video_id is None
        assert video_url is None

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_check_duplicate_skips_other_channels(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test that videos from other channels are skipped."""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        mock_channels = MagicMock()
        mock_channels_list = MagicMock()
        mock_channels_list.execute.return_value = {"items": [{"id": "UC123456"}]}
        mock_channels.list.return_value = mock_channels_list
        mock_youtube.channels.return_value = mock_channels

        # Return video from different channel
        mock_search = MagicMock()
        mock_search_list = MagicMock()
        mock_search_list.execute.return_value = {
            "items": [{
                "id": {"videoId": "VIDEO123"},
                "snippet": {
                    "channelId": "UC_DIFFERENT",  # Different channel
                    "title": "Test Title"
                }
            }]
        }
        mock_search.list.return_value = mock_search_list
        mock_youtube.search.return_value = mock_search
        mock_build.return_value = mock_youtube

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        exists, video_id, video_url = service.check_duplicate("Test Title")

        assert exists is False


class TestYouTubeUploadServiceDelete:
    """Test video deletion."""

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_delete_video_success(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test successful video deletion."""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        mock_videos = MagicMock()
        mock_delete = MagicMock()
        mock_delete.execute.return_value = None
        mock_videos.delete.return_value = mock_delete
        mock_youtube.videos.return_value = mock_videos
        mock_build.return_value = mock_youtube

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        result = service.delete_video("VIDEO123")

        assert result is True
        mock_videos.delete.assert_called_once_with(id="VIDEO123")

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_delete_video_failure(
        self, mock_request, mock_credentials_class, mock_build
    ):
        """Test video deletion failure handling."""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        mock_videos = MagicMock()
        mock_delete = MagicMock()
        mock_delete.execute.side_effect = Exception("API Error")
        mock_videos.delete.return_value = mock_delete
        mock_youtube.videos.return_value = mock_videos
        mock_build.return_value = mock_youtube

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        result = service.delete_video("VIDEO123")

        assert result is False

    def test_delete_video_dry_run(self):
        """Test video deletion in dry run mode."""
        service = YouTubeUploadService(dry_run=True)

        result = service.delete_video("VIDEO123")

        assert result is True


class TestYouTubeUploadServiceTitleTruncation:
    """Test title truncation."""

    def test_truncate_title_short_title(self):
        """Test that short titles are not truncated."""
        title = "Short Title"
        result = YouTubeUploadService.truncate_title(title)
        assert result == title

    def test_truncate_title_exact_length(self):
        """Test title at exact max length."""
        title = "A" * 95
        result = YouTubeUploadService.truncate_title(title, max_length=95)
        assert result == title
        assert len(result) == 95

    def test_truncate_title_long_title(self):
        """Test that long titles are truncated at word boundary."""
        title = "This is a very long title that exceeds the maximum length and needs to be truncated properly at a word boundary"
        result = YouTubeUploadService.truncate_title(title, max_length=50)
        assert len(result) <= 50
        assert result.endswith("...")

    def test_truncate_title_no_space(self):
        """Test truncation of title without spaces."""
        title = "A" * 100
        result = YouTubeUploadService.truncate_title(title, max_length=50)
        assert len(result) <= 50


class TestYouTubeUploadServiceUpload:
    """Test video upload."""

    @patch("googleapiclient.http.MediaFileUpload")
    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    def test_upload_video_success(
        self, mock_request, mock_credentials_class, mock_build, mock_media_upload
    ):
        """Test successful video upload."""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        # Mock channels for get_channel_id
        mock_channels = MagicMock()
        mock_channels_list = MagicMock()
        mock_channels_list.execute.return_value = {"items": [{"id": "UC123456"}]}
        mock_channels.list.return_value = mock_channels_list
        mock_youtube.channels.return_value = mock_channels

        # Mock search for duplicate check (no duplicates)
        mock_search = MagicMock()
        mock_search_list = MagicMock()
        mock_search_list.execute.return_value = {"items": []}
        mock_search.list.return_value = mock_search_list
        mock_youtube.search.return_value = mock_search

        # Mock video insert
        mock_videos = MagicMock()
        mock_insert = MagicMock()
        mock_insert.execute.return_value = {"id": "NEW_VIDEO_ID"}
        mock_videos.insert.return_value = mock_insert
        mock_youtube.videos.return_value = mock_videos
        mock_build.return_value = mock_youtube

        mock_media_upload.return_value = MagicMock()

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        video_id, video_url = service.upload_video(
            video_path="/path/to/video.mkv",
            title="Test Artist - Test Song (Karaoke)",
            description="Test description",
            tags=["karaoke", "test"]
        )

        assert video_id == "NEW_VIDEO_ID"
        assert "NEW_VIDEO_ID" in video_url

    def test_upload_video_dry_run(self):
        """Test video upload in dry run mode."""
        service = YouTubeUploadService(dry_run=True)

        video_id, video_url = service.upload_video(
            video_path="/path/to/video.mkv",
            title="Test Title",
            description="Test description"
        )

        assert video_id == "dry_run_video_id"
        assert "dry_run_video_id" in video_url

    @patch("googleapiclient.http.MediaFileUpload")
    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("google.auth.transport.requests.Request")
    @patch("os.path.isfile")
    def test_upload_video_with_thumbnail(
        self, mock_isfile, mock_request, mock_credentials_class,
        mock_build, mock_media_upload
    ):
        """Test video upload with thumbnail."""
        mock_isfile.return_value = True
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_credentials_class.return_value = mock_creds

        mock_youtube = MagicMock()
        mock_channels = MagicMock()
        mock_channels_list = MagicMock()
        mock_channels_list.execute.return_value = {"items": [{"id": "UC123456"}]}
        mock_channels.list.return_value = mock_channels_list
        mock_youtube.channels.return_value = mock_channels

        mock_search = MagicMock()
        mock_search_list = MagicMock()
        mock_search_list.execute.return_value = {"items": []}
        mock_search.list.return_value = mock_search_list
        mock_youtube.search.return_value = mock_search

        mock_videos = MagicMock()
        mock_insert = MagicMock()
        mock_insert.execute.return_value = {"id": "NEW_VIDEO_ID"}
        mock_videos.insert.return_value = mock_insert
        mock_youtube.videos.return_value = mock_videos

        # Mock thumbnail upload
        mock_thumbnails = MagicMock()
        mock_set = MagicMock()
        mock_set.execute.return_value = None
        mock_thumbnails.set.return_value = mock_set
        mock_youtube.thumbnails.return_value = mock_thumbnails

        mock_build.return_value = mock_youtube
        mock_media_upload.return_value = MagicMock()

        creds = {"token": "test", "refresh_token": "test", "token_uri": "uri",
                 "client_id": "id", "client_secret": "secret"}
        service = YouTubeUploadService(credentials=creds, non_interactive=True)

        video_id, video_url = service.upload_video(
            video_path="/path/to/video.mkv",
            title="Test Title",
            description="Test description",
            thumbnail_path="/path/to/thumbnail.jpg"
        )

        assert video_id == "NEW_VIDEO_ID"
        mock_thumbnails.set.assert_called_once()


class TestGetYouTubeUploadService:
    """Test factory function."""

    def test_get_service_creates_instance(self):
        """Test that factory function creates a new instance."""
        # Reset global
        import backend.services.youtube_upload_service as module
        module._youtube_upload_service = None

        service = get_youtube_upload_service(
            credentials={"token": "test"},
            non_interactive=True
        )

        assert service is not None
        assert isinstance(service, YouTubeUploadService)

    def test_get_service_with_client_secrets(self):
        """Test factory function with client secrets file."""
        import backend.services.youtube_upload_service as module
        module._youtube_upload_service = None

        service = get_youtube_upload_service(
            client_secrets_file="/path/to/secrets.json"
        )

        assert service is not None
        assert service.client_secrets_file == "/path/to/secrets.json"


class TestYouTubeUploadSessionRestart:
    """A lost resumable session (404/410) restarts the upload; other errors don't."""

    @staticmethod
    def _http_error(status):
        from googleapiclient.errors import HttpError

        resp = MagicMock()
        resp.status = status
        resp.reason = "Gone"
        return HttpError(resp, b'{"error": {"message": "Gone"}}', uri="https://youtube.googleapis.com/upload")

    def _service(self, insert_execute_side_effect):
        service = YouTubeUploadService(credentials={"token": "t"}, non_interactive=True, server_side_mode=True)
        youtube = MagicMock()
        youtube.videos.return_value.insert.return_value.execute.side_effect = insert_execute_side_effect
        service._youtube_service = youtube
        return service, youtube

    @patch("time.sleep")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_410_restarts_with_fresh_session(self, mock_media, mock_sleep):
        service, youtube = self._service([self._http_error(410), {"id": "VID"}])

        with patch.object(service, "check_duplicate", return_value=(False, None, None)):
            video_id, video_url = service.upload_video(video_path="/v.mkv", title="A - B (Karaoke)", description="d")

        assert video_id == "VID"
        assert youtube.videos.return_value.insert.call_count == 2
        assert mock_media.call_count == 2  # new MediaFileUpload per session
        youtube.videos.return_value.insert.return_value.execute.assert_called_with(
            num_retries=YouTubeUploadService.UPLOAD_NUM_RETRIES
        )
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_410_on_every_attempt_raises(self, mock_media, mock_sleep):
        attempts = YouTubeUploadService.UPLOAD_SESSION_ATTEMPTS
        service, youtube = self._service([self._http_error(410)] * attempts)

        with patch.object(service, "check_duplicate", return_value=(False, None, None)):
            with pytest.raises(Exception, match="410"):
                service.upload_video(video_path="/v.mkv", title="A - B (Karaoke)", description="d")

        assert youtube.videos.return_value.insert.call_count == attempts
        assert mock_sleep.call_count == attempts - 1

    @patch("time.sleep")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_quota_403_is_not_restarted(self, mock_media, mock_sleep):
        service, youtube = self._service([self._http_error(403)])

        with patch.object(service, "check_duplicate", return_value=(False, None, None)):
            with pytest.raises(Exception, match="403"):
                service.upload_video(video_path="/v.mkv", title="A - B (Karaoke)", description="d")

        assert youtube.videos.return_value.insert.call_count == 1
        mock_sleep.assert_not_called()
