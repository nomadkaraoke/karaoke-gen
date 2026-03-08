"""
Tests for job observability improvements.

Covers:
- TimelineEvent metadata field
- log_to_job helper function
- Timeline metadata in state transitions
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from backend.models.job import TimelineEvent


class TestTimelineEventMetadata:
    """Tests for the metadata field on TimelineEvent."""

    def test_timeline_event_without_metadata(self):
        """Existing events without metadata still work."""
        event = TimelineEvent(
            status="complete",
            timestamp="2026-03-07T12:00:00",
            progress=100,
            message="Done!",
        )
        data = event.model_dump(mode='json')
        assert data["status"] == "complete"
        assert data["metadata"] is None

    def test_timeline_event_with_metadata(self):
        """Events can include structured metadata."""
        metadata = {
            "action": "completed",
            "brand_code": "NOMAD-1234",
            "youtube_url": "https://youtube.com/watch?v=abc123",
            "gdrive_file_ids": {"mp4": "file1", "cdg": "file2"},
        }
        event = TimelineEvent(
            status="complete",
            timestamp="2026-03-07T12:00:00",
            progress=100,
            message="Karaoke generation complete!",
            metadata=metadata,
        )
        data = event.model_dump(mode='json')
        assert data["metadata"]["brand_code"] == "NOMAD-1234"
        assert data["metadata"]["youtube_url"] == "https://youtube.com/watch?v=abc123"
        assert data["metadata"]["gdrive_file_ids"]["mp4"] == "file1"

    def test_timeline_event_with_edit_metadata(self):
        """Edit-initiated events include previous outputs and cleanup results."""
        metadata = {
            "action": "edit_initiated",
            "initiated_by": "user@example.com",
            "edit_number": 1,
            "previous_outputs": {
                "youtube_url": "https://youtube.com/watch?v=old123",
                "brand_code": "NOMAD-0042",
                "dropbox_link": "https://dropbox.com/sh/old",
            },
            "cleanup_results": {
                "youtube": {"status": "success", "video_id": "old123"},
                "dropbox": {"status": "success", "path": "/Karaoke/NOMAD-0042"},
                "gdrive": {"status": "skipped", "reason": "no gdrive_files"},
            },
        }
        event = TimelineEvent(
            status="complete",
            timestamp="2026-03-07T12:00:00",
            message="Track edit initiated by user@example.com (edit #1)",
            metadata=metadata,
        )
        data = event.model_dump(mode='json')
        assert data["metadata"]["previous_outputs"]["youtube_url"] == "https://youtube.com/watch?v=old123"
        assert data["metadata"]["cleanup_results"]["youtube"]["status"] == "success"

    def test_timeline_event_metadata_roundtrip(self):
        """Metadata survives serialization/deserialization."""
        metadata = {"brand_code": "NOMAD-1234", "nested": {"key": "value"}}
        event = TimelineEvent(
            status="complete",
            timestamp="2026-03-07T12:00:00",
            metadata=metadata,
        )
        data = event.model_dump(mode='json')
        restored = TimelineEvent(**data)
        assert restored.metadata == metadata


class TestLogToJob:
    """Tests for the log_to_job helper function."""

    @patch('backend.services.firestore_service._get_log_service')
    def test_log_to_job_writes_to_subcollection(self, mock_get_service):
        """log_to_job creates a WorkerLogEntry and writes to Firestore."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        from backend.services.firestore_service import log_to_job
        log_to_job("job123", "edit", "INFO", "Edit started", {"key": "value"})

        mock_service.append_log_to_subcollection.assert_called_once()
        call_args = mock_service.append_log_to_subcollection.call_args
        assert call_args[0][0] == "job123"
        entry = call_args[0][1]
        assert entry.worker == "edit"
        assert entry.level == "INFO"
        assert entry.message == "Edit started"
        assert entry.metadata == {"key": "value"}

    @patch('backend.services.firestore_service._get_log_service')
    def test_log_to_job_suppresses_errors(self, mock_get_service):
        """log_to_job should never raise — logging failures must be silent."""
        mock_get_service.side_effect = Exception("Firestore down")

        from backend.services.firestore_service import log_to_job
        # Should not raise
        log_to_job("job123", "edit", "INFO", "Edit started")

    @patch('backend.services.firestore_service._get_log_service')
    def test_log_to_job_without_metadata(self, mock_get_service):
        """log_to_job works without metadata."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        from backend.services.firestore_service import log_to_job
        log_to_job("job123", "admin", "WARNING", "Something happened")

        mock_service.append_log_to_subcollection.assert_called_once()
        entry = mock_service.append_log_to_subcollection.call_args[0][1]
        assert entry.metadata is None
