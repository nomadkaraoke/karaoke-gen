"""
Unit tests for worker log subcollection functionality.

Tests the WorkerLogEntry model, FirestoreService subcollection methods,
and JobManager log operations with the feature flag.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone, timedelta
import uuid

# Mock Firestore before imports
import sys
sys.modules['google.cloud.firestore'] = MagicMock()
sys.modules['google.cloud.firestore_v1'] = MagicMock()

from backend.models.worker_log import WorkerLogEntry, DEFAULT_LOG_TTL_DAYS


class TestWorkerLogEntry:
    """Tests for WorkerLogEntry dataclass."""

    def test_create_log_entry(self):
        """Test creating a log entry with factory method."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message="Test message"
        )

        assert entry.job_id == "job123"
        assert entry.worker == "audio"
        assert entry.level == "INFO"
        assert entry.message == "Test message"
        assert entry.id is not None
        assert entry.timestamp is not None
        assert entry.ttl_expiry is not None
        # TTL should be ~30 days from now
        expected_ttl = datetime.now(timezone.utc) + timedelta(days=DEFAULT_LOG_TTL_DAYS)
        assert abs((entry.ttl_expiry - expected_ttl).total_seconds()) < 5

    def test_create_log_entry_truncates_long_message(self):
        """Test that messages longer than 1000 chars are truncated."""
        long_message = "x" * 2000
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message=long_message
        )

        assert len(entry.message) == 1000

    def test_create_log_entry_normalizes_level(self):
        """Test that log level is normalized to uppercase."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="info",
            message="Test"
        )

        assert entry.level == "INFO"

    def test_create_log_entry_custom_ttl(self):
        """Test creating entry with custom TTL."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message="Test",
            ttl_days=7
        )

        expected_ttl = datetime.now(timezone.utc) + timedelta(days=7)
        assert abs((entry.ttl_expiry - expected_ttl).total_seconds()) < 5

    def test_create_log_entry_with_metadata(self):
        """Test creating entry with metadata."""
        metadata = {"file_size": 1024, "duration": 300}
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message="Test",
            metadata=metadata
        )

        assert entry.metadata == metadata

    def test_to_dict(self):
        """Test converting entry to dict for Firestore."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message="Test message",
            metadata={"key": "value"}
        )

        d = entry.to_dict()

        assert d["job_id"] == "job123"
        assert d["worker"] == "audio"
        assert d["level"] == "INFO"
        assert d["message"] == "Test message"
        assert d["metadata"] == {"key": "value"}
        assert "id" in d
        assert "timestamp" in d
        assert "ttl_expiry" in d

    def test_to_dict_without_metadata(self):
        """Test to_dict doesn't include metadata when None."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message="Test"
        )

        d = entry.to_dict()
        assert "metadata" not in d

    def test_to_legacy_dict(self):
        """Test converting to legacy format for API compatibility."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message="Test message"
        )

        d = entry.to_legacy_dict()

        assert "timestamp" in d
        assert d["level"] == "INFO"
        assert d["worker"] == "audio"
        assert d["message"] == "Test message"
        # Should not include id, job_id, ttl_expiry, metadata
        assert "id" not in d
        assert "job_id" not in d
        assert "ttl_expiry" not in d

    def test_from_dict(self):
        """Test creating entry from Firestore document."""
        timestamp = datetime.now(timezone.utc)
        ttl_expiry = timestamp + timedelta(days=30)

        data = {
            "id": "log123",
            "job_id": "job123",
            "timestamp": timestamp,
            "level": "WARNING",
            "worker": "video",
            "message": "Warning message",
            "metadata": {"error_code": 500},
            "ttl_expiry": ttl_expiry
        }

        entry = WorkerLogEntry.from_dict(data)

        assert entry.id == "log123"
        assert entry.job_id == "job123"
        assert entry.level == "WARNING"
        assert entry.worker == "video"
        assert entry.message == "Warning message"
        assert entry.metadata == {"error_code": 500}

    def test_from_dict_with_iso_strings(self):
        """Test creating entry from dict with ISO format strings."""
        data = {
            "timestamp": "2026-01-04T12:00:00Z",
            "level": "INFO",
            "worker": "audio",
            "message": "Test"
        }

        entry = WorkerLogEntry.from_dict(data)

        assert entry.timestamp.tzinfo == timezone.utc
        assert entry.message == "Test"

    def test_from_dict_missing_fields(self):
        """Test creating entry with missing fields uses defaults."""
        data = {
            "message": "Minimal log"
        }

        entry = WorkerLogEntry.from_dict(data)

        assert entry.message == "Minimal log"
        assert entry.level == "INFO"  # Default
        assert entry.worker == "unknown"  # Default
        assert entry.id is not None  # Generated


class TestFirestoreServiceSubcollection:
    """Tests for FirestoreService log subcollection methods."""

    @pytest.fixture
    def mock_db(self):
        """Create mock Firestore database."""
        return MagicMock()

    @pytest.fixture
    def firestore_service(self, mock_db):
        """Create FirestoreService with mocked DB."""
        with patch('backend.services.firestore_service.firestore') as mock_firestore:
            mock_firestore.Client.return_value = mock_db
            from backend.services.firestore_service import FirestoreService
            service = FirestoreService()
            service.db = mock_db
            return service

    def test_append_log_to_subcollection(self, firestore_service, mock_db):
        """Test appending log to subcollection."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message="Test message"
        )

        # Setup mock chain
        mock_collection = MagicMock()
        mock_doc = MagicMock()
        mock_logs_ref = MagicMock()
        mock_log_doc = MagicMock()

        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc
        mock_doc.collection.return_value = mock_logs_ref
        mock_logs_ref.document.return_value = mock_log_doc

        firestore_service.append_log_to_subcollection("job123", entry)

        # Verify correct path: jobs/{job_id}/logs/{log_id}
        mock_db.collection.assert_called_with("jobs")
        mock_collection.document.assert_called_with("job123")
        mock_doc.collection.assert_called_with("logs")
        mock_logs_ref.document.assert_called_with(entry.id)
        mock_log_doc.set.assert_called_once()

    def test_get_logs_from_subcollection(self, firestore_service, mock_db):
        """Test getting logs from subcollection."""
        # Setup mock chain
        mock_collection = MagicMock()
        mock_doc = MagicMock()
        mock_logs_ref = MagicMock()
        mock_query = MagicMock()

        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc
        mock_doc.collection.return_value = mock_logs_ref
        mock_logs_ref.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query

        # Mock document data
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = {
            "timestamp": datetime.now(timezone.utc),
            "level": "INFO",
            "worker": "audio",
            "message": "Log 1"
        }

        mock_query.stream.return_value = iter([mock_doc1])

        logs = firestore_service.get_logs_from_subcollection("job123")

        assert len(logs) == 1
        assert logs[0].message == "Log 1"
        mock_logs_ref.order_by.assert_called_once()

    def test_get_logs_from_subcollection_with_worker_filter(self, firestore_service, mock_db):
        """Test filtering logs by worker."""
        mock_collection = MagicMock()
        mock_doc = MagicMock()
        mock_logs_ref = MagicMock()
        mock_query = MagicMock()

        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc
        mock_doc.collection.return_value = mock_logs_ref
        mock_logs_ref.order_by.return_value = mock_query
        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter([])

        firestore_service.get_logs_from_subcollection("job123", worker="audio")

        # Verify where clause was added
        mock_query.where.assert_called()

    def test_delete_logs_subcollection(self, firestore_service, mock_db):
        """Test deleting all logs in subcollection."""
        mock_collection = MagicMock()
        mock_doc = MagicMock()
        mock_logs_ref = MagicMock()
        mock_batch = MagicMock()

        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc
        mock_doc.collection.return_value = mock_logs_ref
        mock_db.batch.return_value = mock_batch

        # First call returns 2 docs, second call returns empty
        mock_log_doc1 = MagicMock()
        mock_log_doc2 = MagicMock()

        call_count = [0]
        def stream_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return iter([mock_log_doc1, mock_log_doc2])
            return iter([])

        mock_logs_ref.limit.return_value = mock_logs_ref
        mock_logs_ref.stream.side_effect = stream_side_effect

        deleted = firestore_service.delete_logs_subcollection("job123")

        assert deleted == 2
        mock_batch.commit.assert_called()


class TestJobManagerLogging:
    """Tests for JobManager log methods with feature flag."""

    @pytest.fixture
    def mock_firestore_service(self):
        """Mock FirestoreService."""
        with patch('backend.services.job_manager.FirestoreService') as mock:
            service = MagicMock()
            mock.return_value = service
            yield service

    @pytest.fixture
    def mock_storage_service(self):
        """Mock StorageService."""
        with patch('backend.services.job_manager.StorageService') as mock:
            service = MagicMock()
            mock.return_value = service
            yield service

    def test_append_worker_log_uses_subcollection_when_enabled(
        self, mock_firestore_service, mock_storage_service
    ):
        """Test that logs go to subcollection when feature is enabled."""
        with patch('backend.services.job_manager.settings') as mock_settings:
            mock_settings.use_log_subcollection = True

            from backend.services.job_manager import JobManager
            manager = JobManager()

            manager.append_worker_log(
                job_id="job123",
                worker="audio",
                level="INFO",
                message="Test message"
            )

            # Should call subcollection method
            mock_firestore_service.append_log_to_subcollection.assert_called_once()
            # Should NOT call legacy method
            mock_firestore_service.append_worker_log.assert_not_called()

    def test_append_worker_log_uses_array_when_disabled(
        self, mock_firestore_service, mock_storage_service
    ):
        """Test that logs go to embedded array when feature is disabled."""
        with patch('backend.services.job_manager.settings') as mock_settings:
            mock_settings.use_log_subcollection = False

            from backend.services.job_manager import JobManager
            manager = JobManager()

            manager.append_worker_log(
                job_id="job123",
                worker="audio",
                level="INFO",
                message="Test message"
            )

            # Should call legacy method
            mock_firestore_service.append_worker_log.assert_called_once()
            # Should NOT call subcollection method
            mock_firestore_service.append_log_to_subcollection.assert_not_called()

    def test_get_worker_logs_from_subcollection(
        self, mock_firestore_service, mock_storage_service
    ):
        """Test getting logs from subcollection."""
        with patch('backend.services.job_manager.settings') as mock_settings:
            mock_settings.use_log_subcollection = True

            entry = WorkerLogEntry.create(
                job_id="job123",
                worker="audio",
                level="INFO",
                message="Test"
            )
            mock_firestore_service.get_logs_from_subcollection.return_value = [entry]

            from backend.services.job_manager import JobManager
            manager = JobManager()

            logs = manager.get_worker_logs("job123")

            assert len(logs) == 1
            assert logs[0]["message"] == "Test"
            mock_firestore_service.get_logs_from_subcollection.assert_called_once()

    def test_get_worker_logs_falls_back_to_array(
        self, mock_firestore_service, mock_storage_service
    ):
        """Test fallback to embedded array for legacy jobs."""
        with patch('backend.services.job_manager.settings') as mock_settings:
            mock_settings.use_log_subcollection = True

            # Subcollection returns empty
            mock_firestore_service.get_logs_from_subcollection.return_value = []

            # Mock job with legacy logs
            from backend.models.job import Job, JobStatus
            mock_job = Job(
                job_id="job123",
                status=JobStatus.PENDING,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                worker_logs=[
                    {"timestamp": "2026-01-04T12:00:00Z", "level": "INFO", "worker": "audio", "message": "Legacy log"}
                ]
            )
            mock_firestore_service.get_job.return_value = mock_job

            from backend.services.job_manager import JobManager
            manager = JobManager()

            logs = manager.get_worker_logs("job123")

            assert len(logs) == 1
            assert logs[0]["message"] == "Legacy log"

    def test_delete_job_deletes_logs_subcollection(
        self, mock_firestore_service, mock_storage_service
    ):
        """Test that deleting a job also deletes its logs subcollection."""
        mock_firestore_service.get_job.return_value = None
        mock_firestore_service.delete_logs_subcollection.return_value = 5

        from backend.services.job_manager import JobManager
        manager = JobManager()

        manager.delete_job("job123", delete_files=False)

        mock_firestore_service.delete_logs_subcollection.assert_called_once_with("job123")
        mock_firestore_service.delete_job.assert_called_once_with("job123")


class TestWorkerLogEntryEdgeCases:
    """Edge case tests for WorkerLogEntry."""

    def test_create_with_empty_message(self):
        """Test creating entry with empty message."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message=""
        )
        assert entry.message == ""

    def test_create_with_unicode_message(self):
        """Test creating entry with Unicode characters."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="INFO",
            message="Processing song: 日本語の曲 - アーティスト"
        )
        assert "日本語" in entry.message

    def test_create_with_newlines_in_message(self):
        """Test creating entry with newlines."""
        entry = WorkerLogEntry.create(
            job_id="job123",
            worker="audio",
            level="ERROR",
            message="Error occurred:\nLine 1\nLine 2"
        )
        assert "\n" in entry.message

    def test_from_dict_handles_missing_ttl_expiry(self):
        """Test from_dict creates default TTL when missing."""
        data = {
            "timestamp": datetime.now(timezone.utc),
            "level": "INFO",
            "worker": "audio",
            "message": "Test"
        }

        entry = WorkerLogEntry.from_dict(data)

        assert entry.ttl_expiry is not None
        # Should be ~30 days from now
        expected = datetime.now(timezone.utc) + timedelta(days=30)
        assert abs((entry.ttl_expiry - expected).total_seconds()) < 10


class TestDocSizeRecovery:
    """Self-healing when a job document hits Firestore's 1MB size limit.

    A large legacy embedded ``worker_logs`` array (superseded by the logs
    subcollection) can push an old job document to ~1MB, after which EVERY
    write — including a customer submitting lyrics corrections — fails with
    Firestore's "exceeds the maximum allowed size" 400. update_job /
    update_job_status drop the deprecated array and retry once.
    """

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def firestore_service(self, mock_db):
        with patch('backend.services.firestore_service.firestore') as mock_firestore:
            mock_firestore.Client.return_value = mock_db
            from backend.services.firestore_service import FirestoreService
            service = FirestoreService()
            service.db = mock_db
            return service

    @staticmethod
    def _size_error():
        return Exception(
            "400 Document 'projects/nomadkaraoke/databases/(default)/documents/"
            "jobs/d93747cd' cannot be written because its size (1,048,592 bytes) "
            "exceeds the maximum allowed size of 1,048,576 bytes."
        )

    def test_update_job_recovers_by_clearing_worker_logs(self, firestore_service, mock_db):
        doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = doc_ref
        # payload write fails (too big), worker_logs delete succeeds, retry succeeds
        doc_ref.update.side_effect = [self._size_error(), None, None]

        firestore_service.update_job(
            "d93747cd", {"state_data.corrected_lyrics": {"corrected_segment_count": 5}}
        )

        assert doc_ref.update.call_count == 3
        # Second write clears the legacy embedded worker_logs array
        delete_payload = doc_ref.update.call_args_list[1][0][0]
        assert "worker_logs" in delete_payload
        # Third write re-applies the caller's original field
        retry_payload = doc_ref.update.call_args_list[2][0][0]
        assert "state_data.corrected_lyrics" in retry_payload

    def test_update_job_reraises_non_size_errors(self, firestore_service, mock_db):
        doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = doc_ref
        doc_ref.update.side_effect = Exception("PERMISSION_DENIED")

        with pytest.raises(Exception, match="PERMISSION_DENIED"):
            firestore_service.update_job("j", {"foo": "bar"})
        # No recovery attempt for unrelated errors
        assert doc_ref.update.call_count == 1

    def test_worker_logs_write_does_not_recurse(self, firestore_service, mock_db):
        """A write that is itself setting worker_logs must not self-clear+retry."""
        doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = doc_ref
        doc_ref.update.side_effect = self._size_error()

        with pytest.raises(Exception, match="maximum allowed size"):
            firestore_service.update_job("j", {"worker_logs": ["x"]})
        assert doc_ref.update.call_count == 1

    def test_update_job_status_recovers_by_clearing_worker_logs(self, firestore_service, mock_db):
        doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = doc_ref
        doc_ref.update.side_effect = [self._size_error(), None, None]

        from backend.models.job import JobStatus
        firestore_service.update_job_status("d93747cd", JobStatus.IN_REVIEW)

        assert doc_ref.update.call_count == 3
        delete_payload = doc_ref.update.call_args_list[1][0][0]
        assert "worker_logs" in delete_payload


class TestAppendWorkerLogRouting:
    """Legacy dict-based append_worker_log must respect the subcollection flag
    so it stops bloating the embedded worker_logs array."""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def firestore_service(self, mock_db):
        with patch('backend.services.firestore_service.firestore') as mock_firestore:
            mock_firestore.Client.return_value = mock_db
            from backend.services.firestore_service import FirestoreService
            service = FirestoreService()
            service.db = mock_db
            return service

    def test_routes_to_subcollection_when_flag_on(self, firestore_service):
        with patch('backend.services.firestore_service.settings') as mock_settings:
            mock_settings.use_log_subcollection = True
            with patch.object(firestore_service, 'append_log_to_subcollection') as mock_sub:
                firestore_service.append_worker_log(
                    "job123",
                    {"timestamp": "t", "worker": "review", "level": "INFO", "message": "hi"},
                )
                mock_sub.assert_called_once()
                entry = mock_sub.call_args[0][1]
                assert entry.worker == "review"
                assert entry.level == "INFO"
                assert entry.message == "hi"

    def test_uses_embedded_array_when_flag_off(self, firestore_service, mock_db):
        doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = doc_ref
        with patch('backend.services.firestore_service.settings') as mock_settings:
            mock_settings.use_log_subcollection = False
            with patch.object(firestore_service, 'append_log_to_subcollection') as mock_sub:
                firestore_service.append_worker_log(
                    "job123",
                    {"timestamp": "t", "worker": "review", "level": "INFO", "message": "hi"},
                )
                mock_sub.assert_not_called()
                doc_ref.update.assert_called_once()
