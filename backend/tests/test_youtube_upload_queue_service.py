"""
Unit tests for YouTubeUploadQueueService.

Tests queue management for deferred YouTube uploads including
queuing, claiming, completion, failure handling, and retries.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch

# Mock Google Cloud before imports
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())


class TestYouTubeUploadQueueService:
    """Test YouTubeUploadQueueService functionality."""

    @pytest.fixture
    def mock_db(self):
        mock = MagicMock()
        return mock

    @pytest.fixture
    def mock_settings(self):
        settings = Mock()
        settings.google_cloud_project = "test-project"
        return settings

    @pytest.fixture
    def queue_service(self, mock_db, mock_settings):
        with patch('backend.services.youtube_upload_queue_service.settings', mock_settings):
            from backend.services.youtube_upload_queue_service import YouTubeUploadQueueService
            service = YouTubeUploadQueueService(db=mock_db)
            return service

    # =========================================================================
    # queue_upload Tests
    # =========================================================================

    def test_queue_upload_creates_document(self, queue_service, mock_db):
        """Queuing an upload should create a Firestore document."""
        queue_service.queue_upload(
            job_id="job-123",
            user_email="user@example.com",
            artist="Test Artist",
            title="Test Song",
            brand_code="NOMAD-1234",
            reason="quota_exceeded",
        )

        mock_db.collection.return_value.document.assert_called_with("job-123")
        call_args = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args["job_id"] == "job-123"
        assert call_args["status"] == "queued"
        assert call_args["user_email"] == "user@example.com"
        assert call_args["artist"] == "Test Artist"
        assert call_args["title"] == "Test Song"
        assert call_args["brand_code"] == "NOMAD-1234"
        assert call_args["reason"] == "quota_exceeded"
        assert call_args["attempts"] == 0
        assert call_args["max_attempts"] == 5
        assert call_args["youtube_url"] is None

    def test_queue_upload_default_reason(self, queue_service, mock_db):
        """Default reason should be quota_exceeded."""
        queue_service.queue_upload(
            job_id="job-456",
            user_email="user@example.com",
            artist="Artist",
            title="Title",
            brand_code=None,
        )

        call_args = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args["reason"] == "quota_exceeded"
        assert call_args["brand_code"] is None

    # =========================================================================
    # get_queued_uploads Tests
    # =========================================================================

    def test_get_queued_uploads_returns_dicts(self, queue_service, mock_db):
        """Should return list of dicts for queued entries."""
        mock_doc1 = Mock()
        mock_doc1.to_dict.return_value = {"job_id": "job-1", "status": "queued"}
        mock_doc2 = Mock()
        mock_doc2.to_dict.return_value = {"job_id": "job-2", "status": "queued"}

        mock_query = mock_db.collection.return_value
        mock_query.where.return_value.order_by.return_value.limit.return_value.stream.return_value = [
            mock_doc1, mock_doc2
        ]

        results = queue_service.get_queued_uploads(limit=10)

        assert len(results) == 2
        assert results[0]["job_id"] == "job-1"
        assert results[1]["job_id"] == "job-2"

    def test_get_queued_uploads_empty(self, queue_service, mock_db):
        """Should return empty list when no queued uploads."""
        mock_query = mock_db.collection.return_value
        mock_query.where.return_value.order_by.return_value.limit.return_value.stream.return_value = []

        results = queue_service.get_queued_uploads()
        assert results == []

    # =========================================================================
    # mark_processing Tests
    # =========================================================================

    def test_mark_processing_success(self, queue_service, mock_db):
        """Should claim a queued entry for processing."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"status": "queued", "attempts": 0, "max_attempts": 5}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        result = queue_service.mark_processing("job-123")

        assert result is True

    def test_mark_processing_not_queued(self, queue_service, mock_db):
        """Should not claim an entry that's already processing."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"status": "processing", "attempts": 1, "max_attempts": 5}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        result = queue_service.mark_processing("job-123")

        assert result is False

    def test_mark_processing_not_found(self, queue_service, mock_db):
        """Should return False for non-existent entry."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        result = queue_service.mark_processing("nonexistent")

        assert result is False

    def test_mark_processing_max_attempts_exceeded(self, queue_service, mock_db):
        """Should mark as failed when max attempts exceeded."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"status": "queued", "attempts": 5, "max_attempts": 5}
        mock_doc_ref = mock_db.collection.return_value.document.return_value
        mock_doc_ref.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        result = queue_service.mark_processing("job-123")

        assert result is False

    # =========================================================================
    # mark_completed Tests
    # =========================================================================

    def test_mark_completed(self, queue_service, mock_db):
        """Should update status and set youtube_url."""
        queue_service.mark_completed("job-123", "https://youtube.com/watch?v=abc123")

        mock_db.collection.return_value.document.assert_called_with("job-123")
        update_data = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        assert update_data["status"] == "completed"
        assert update_data["youtube_url"] == "https://youtube.com/watch?v=abc123"
        assert "completed_at" in update_data

    # =========================================================================
    # mark_failed Tests
    # =========================================================================

    def test_mark_failed_resets_to_queued_under_max_attempts(self, queue_service, mock_db):
        """Should reset to queued if under max attempts."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"attempts": 2, "max_attempts": 5}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        queue_service.mark_failed("job-123", "Connection timeout")

        mock_db.transaction.assert_called_once()

    def test_mark_failed_permanent_at_max_attempts(self, queue_service, mock_db):
        """Should mark permanently failed at max attempts."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"attempts": 5, "max_attempts": 5}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        queue_service.mark_failed("job-123", "Quota exceeded")

        mock_db.transaction.assert_called_once()

    def test_mark_failed_nonexistent_entry(self, queue_service, mock_db):
        """Should silently return for non-existent entry."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        # Should not raise
        queue_service.mark_failed("nonexistent", "Some error")

    # =========================================================================
    # retry_upload Tests
    # =========================================================================

    def test_retry_upload_from_failed(self, queue_service, mock_db):
        """Should reset a failed entry to queued."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"status": "failed", "attempts": 5}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = queue_service.retry_upload("job-123")

        assert result is True
        update_data = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        assert update_data["status"] == "queued"
        assert update_data["attempts"] == 0
        assert update_data["last_error"] is None

    def test_retry_upload_not_found(self, queue_service, mock_db):
        """Should return False for non-existent entry."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = queue_service.retry_upload("nonexistent")
        assert result is False

    def test_retry_upload_completed_not_retryable(self, queue_service, mock_db):
        """Should return False for completed entries (not retryable)."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"status": "completed"}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = queue_service.retry_upload("job-123")
        assert result is False

    def test_retry_upload_processing_not_retryable(self, queue_service, mock_db):
        """Should return False for entries currently processing."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"status": "processing"}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = queue_service.retry_upload("job-123")
        assert result is False

    # =========================================================================
    # get_queue_stats Tests
    # =========================================================================

    def test_get_queue_stats(self, queue_service, mock_db):
        """Should return aggregate counts by status."""
        def mock_stream_for_status(status):
            counts = {"queued": 3, "processing": 1, "failed": 2, "completed": 10}
            return [Mock() for _ in range(counts.get(status, 0))]

        def mock_where(field, op, value):
            result = Mock()
            result.select.return_value.stream.return_value = mock_stream_for_status(value)
            return result

        mock_db.collection.return_value.where = mock_where

        stats = queue_service.get_queue_stats()

        assert stats["queued"] == 3
        assert stats["processing"] == 1
        assert stats["failed"] == 2
        assert stats["completed"] == 10
        assert stats["total"] == 16
