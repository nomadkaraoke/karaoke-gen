"""
Unit tests for YouTube queue processor.

Tests the deferred upload processing logic including quota checks,
queue claiming, video downloading, and error handling.
"""

import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch

# Mock Google Cloud before imports
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())


@pytest.mark.asyncio
class TestProcessYouTubeUploadQueue:
    """Test the main queue processing function."""

    @pytest.fixture
    def mock_quota_service(self):
        service = Mock()
        service.check_quota_available.return_value = (True, 9000, "9000 units remaining")
        service.record_operation = Mock()
        return service

    @pytest.fixture
    def mock_queue_service(self):
        service = Mock()
        service.get_queued_uploads.return_value = []
        service.mark_processing.return_value = True
        service.mark_completed = Mock()
        service.mark_failed = Mock()
        return service

    async def test_skips_when_no_quota(self, mock_quota_service, mock_queue_service):
        """Should skip processing when no quota available."""
        mock_quota_service.check_quota_available.return_value = (False, 0, "Quota exhausted")

        with patch('backend.workers.youtube_queue_processor.get_youtube_quota_service', return_value=mock_quota_service), \
             patch('backend.workers.youtube_queue_processor.get_youtube_upload_queue_service', return_value=mock_queue_service), \
             patch('backend.workers.youtube_queue_processor.get_settings'):
            from backend.workers.youtube_queue_processor import process_youtube_upload_queue
            result = await process_youtube_upload_queue()

        assert result["status"] == "skipped"
        assert result["reason"] == "no_quota"
        assert result["processed"] == 0

    async def test_returns_empty_when_no_queued(self, mock_quota_service, mock_queue_service):
        """Should return empty status when no uploads queued."""
        mock_queue_service.get_queued_uploads.return_value = []

        with patch('backend.workers.youtube_queue_processor.get_youtube_quota_service', return_value=mock_quota_service), \
             patch('backend.workers.youtube_queue_processor.get_youtube_upload_queue_service', return_value=mock_queue_service), \
             patch('backend.workers.youtube_queue_processor.get_settings'):
            from backend.workers.youtube_queue_processor import process_youtube_upload_queue
            result = await process_youtube_upload_queue()

        assert result["status"] == "empty"
        assert result["processed"] == 0
        assert result["remaining"] == 0

    async def test_processes_queued_upload(self, mock_quota_service, mock_queue_service):
        """Should process a single queued upload successfully."""
        mock_queue_service.get_queued_uploads.side_effect = [
            [{"job_id": "job-123", "user_email": "user@test.com", "artist": "Artist", "title": "Title", "brand_code": "NOMAD-100"}],
            [],  # After processing, no more queued
        ]

        with patch('backend.workers.youtube_queue_processor.get_youtube_quota_service', return_value=mock_quota_service), \
             patch('backend.workers.youtube_queue_processor.get_youtube_upload_queue_service', return_value=mock_queue_service), \
             patch('backend.workers.youtube_queue_processor.get_settings'), \
             patch('backend.workers.youtube_queue_processor._process_single_upload', new_callable=AsyncMock, return_value="https://youtube.com/watch?v=abc") as mock_upload, \
             patch('backend.workers.youtube_queue_processor._update_job_youtube_url'), \
             patch('backend.workers.youtube_queue_processor._send_youtube_upload_notification', new_callable=AsyncMock):
            from backend.workers.youtube_queue_processor import process_youtube_upload_queue
            result = await process_youtube_upload_queue()

        assert result["status"] == "processed"
        assert result["processed"] == 1
        assert result["failed"] == 0
        mock_queue_service.mark_completed.assert_called_once_with("job-123", "https://youtube.com/watch?v=abc")

    async def test_handles_upload_failure(self, mock_quota_service, mock_queue_service):
        """Should handle upload failure and mark as failed."""
        mock_queue_service.get_queued_uploads.side_effect = [
            [{"job_id": "job-fail", "user_email": "u@t.com", "artist": "A", "title": "T", "brand_code": None}],
            [],
        ]

        with patch('backend.workers.youtube_queue_processor.get_youtube_quota_service', return_value=mock_quota_service), \
             patch('backend.workers.youtube_queue_processor.get_youtube_upload_queue_service', return_value=mock_queue_service), \
             patch('backend.workers.youtube_queue_processor.get_settings'), \
             patch('backend.workers.youtube_queue_processor._process_single_upload', new_callable=AsyncMock, side_effect=Exception("Upload failed")):
            from backend.workers.youtube_queue_processor import process_youtube_upload_queue
            result = await process_youtube_upload_queue()

        assert result["failed"] == 1
        assert result["processed"] == 0
        mock_queue_service.mark_failed.assert_called_once()

    async def test_stops_on_quota_exceeded(self, mock_quota_service, mock_queue_service):
        """Should stop processing when quota exceeded error occurs."""
        mock_queue_service.get_queued_uploads.side_effect = [
            [
                {"job_id": "job-1", "user_email": "u@t.com", "artist": "A", "title": "T", "brand_code": None},
                {"job_id": "job-2", "user_email": "u@t.com", "artist": "A", "title": "T", "brand_code": None},
            ],
            [],
        ]

        with patch('backend.workers.youtube_queue_processor.get_youtube_quota_service', return_value=mock_quota_service), \
             patch('backend.workers.youtube_queue_processor.get_youtube_upload_queue_service', return_value=mock_queue_service), \
             patch('backend.workers.youtube_queue_processor.get_settings'), \
             patch('backend.workers.youtube_queue_processor._process_single_upload', new_callable=AsyncMock, side_effect=Exception("quotaExceeded: daily limit reached")):
            from backend.workers.youtube_queue_processor import process_youtube_upload_queue
            result = await process_youtube_upload_queue()

        # Should stop after first quota exceeded error, not process job-2
        assert result["failed"] == 1
        assert mock_queue_service.mark_failed.call_count == 1

    async def test_skips_unclaimed_entry(self, mock_quota_service, mock_queue_service):
        """Should skip entries that can't be claimed (already processing)."""
        mock_queue_service.get_queued_uploads.side_effect = [
            [{"job_id": "job-claimed", "user_email": "u@t.com", "artist": "A", "title": "T", "brand_code": None}],
            [],
        ]
        mock_queue_service.mark_processing.return_value = False

        with patch('backend.workers.youtube_queue_processor.get_youtube_quota_service', return_value=mock_quota_service), \
             patch('backend.workers.youtube_queue_processor.get_youtube_upload_queue_service', return_value=mock_queue_service), \
             patch('backend.workers.youtube_queue_processor.get_settings'):
            from backend.workers.youtube_queue_processor import process_youtube_upload_queue
            result = await process_youtube_upload_queue()

        assert result["processed"] == 0
        assert result["failed"] == 0

    async def test_rechecks_quota_before_each_upload(self, mock_quota_service, mock_queue_service):
        """Should re-check quota before each upload, not just at the start."""
        mock_queue_service.get_queued_uploads.side_effect = [
            [
                {"job_id": "job-1", "user_email": "u@t.com", "artist": "A", "title": "T", "brand_code": None},
                {"job_id": "job-2", "user_email": "u@t.com", "artist": "A", "title": "T", "brand_code": None},
            ],
            [],
        ]

        # Allow first check, deny second
        mock_quota_service.check_quota_available.side_effect = [
            (True, 9000, "ok"),   # Initial check
            (True, 500, "ok"),    # Before job-1
            (False, 0, "exhausted"),  # Before job-2
        ]

        with patch('backend.workers.youtube_queue_processor.get_youtube_quota_service', return_value=mock_quota_service), \
             patch('backend.workers.youtube_queue_processor.get_youtube_upload_queue_service', return_value=mock_queue_service), \
             patch('backend.workers.youtube_queue_processor.get_settings'), \
             patch('backend.workers.youtube_queue_processor._process_single_upload', new_callable=AsyncMock, return_value="https://youtube.com/watch?v=x"), \
             patch('backend.workers.youtube_queue_processor._update_job_youtube_url'), \
             patch('backend.workers.youtube_queue_processor._send_youtube_upload_notification', new_callable=AsyncMock):
            from backend.workers.youtube_queue_processor import process_youtube_upload_queue
            result = await process_youtube_upload_queue()

        # Only job-1 should have been processed
        assert result["processed"] == 1


class TestDownloadVideoFromGCS:
    """Test video file download logic."""

    def test_prioritizes_mkv(self):
        """Should prefer MKV over MP4."""
        from backend.workers.youtube_queue_processor import _download_video_from_gcs

        mock_job = Mock()
        mock_job.file_urls = {
            "finals": {
                "lossless_4k_mkv": "jobs/job-123/finals/lossless_4k_mkv.mkv",
                "lossless_4k_mp4": "jobs/job-123/finals/lossless_4k_mp4.mp4",
            }
        }

        mock_storage = Mock()
        mock_storage.download_file = Mock()

        with patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', return_value=1024):
            result = _download_video_from_gcs("job-123", mock_job, mock_storage, "/tmp/test")

        assert result is not None
        assert result.endswith(".mkv")
        mock_storage.download_file.assert_called_once_with("jobs/job-123/finals/lossless_4k_mkv.mkv", "/tmp/test/video.mkv")

    def test_falls_back_to_mp4(self):
        """Should fall back to MP4 when MKV not available."""
        from backend.workers.youtube_queue_processor import _download_video_from_gcs

        mock_job = Mock()
        mock_job.file_urls = {
            "finals": {
                "lossless_4k_mp4": "jobs/job-123/finals/lossless_4k_mp4.mp4",
            }
        }

        mock_storage = Mock()
        mock_storage.download_file = Mock()

        with patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', return_value=1024):
            result = _download_video_from_gcs("job-123", mock_job, mock_storage, "/tmp/test")

        assert result is not None
        assert result.endswith(".mp4")

    def test_returns_none_when_no_files(self):
        """Should return None when no video files available."""
        from backend.workers.youtube_queue_processor import _download_video_from_gcs

        mock_job = Mock()
        mock_job.file_urls = {}

        mock_storage = Mock()
        result = _download_video_from_gcs("job-123", mock_job, mock_storage, "/tmp/test")

        assert result is None

    def test_returns_none_on_download_failure(self):
        """Should return None when download fails."""
        from backend.workers.youtube_queue_processor import _download_video_from_gcs

        mock_job = Mock()
        mock_job.file_urls = {"finals": {"lossless_4k_mkv": "jobs/job-123/finals/lossless_4k_mkv.mkv"}}

        mock_storage = Mock()
        mock_storage.download_file.side_effect = Exception("Download failed")

        result = _download_video_from_gcs("job-123", mock_job, mock_storage, "/tmp/test")

        assert result is None


class TestUpdateJobYouTubeUrl:
    """Test updating job state_data with YouTube URL."""

    def test_updates_state_data(self):
        """Should set youtube_url and clear queued flag."""
        from backend.workers.youtube_queue_processor import _update_job_youtube_url

        mock_job = Mock()
        mock_job.state_data = {"brand_code": "NOMAD-100"}
        mock_job_manager = Mock()
        mock_job_manager.get_job.return_value = mock_job

        with patch('backend.workers.youtube_queue_processor.JobManager', return_value=mock_job_manager):
            _update_job_youtube_url("job-123", "https://youtube.com/watch?v=abc")

        update_call = mock_job_manager.update_job.call_args[0]
        assert update_call[0] == "job-123"
        state_data = update_call[1]["state_data"]
        assert state_data["youtube_url"] == "https://youtube.com/watch?v=abc"
        assert state_data["youtube_upload_queued"] is False

    def test_handles_missing_job(self):
        """Should not raise when job not found."""
        from backend.workers.youtube_queue_processor import _update_job_youtube_url

        mock_job_manager = Mock()
        mock_job_manager.get_job.return_value = None

        with patch('backend.workers.youtube_queue_processor.JobManager', return_value=mock_job_manager):
            # Should not raise
            _update_job_youtube_url("nonexistent", "https://youtube.com/watch?v=abc")

        mock_job_manager.update_job.assert_not_called()
