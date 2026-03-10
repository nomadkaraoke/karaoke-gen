"""
Tests for processing_metadata field and related functionality.

Covers:
- Job model has processing_metadata field
- processing_metadata is in SUMMARY_FIELD_PATHS (regression test)
- update_processing_metadata helper works correctly
- AudioShake transcriber stores asset_id in metadata
- Corrector passes through transcription metadata
- extract_request_metadata includes auth context when provided
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from backend.models.job import Job, JobStatus


class TestProcessingMetadataField:
    """Test the processing_metadata field on the Job model."""

    def test_job_has_processing_metadata_field(self):
        """processing_metadata should exist and default to empty dict."""
        job = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        assert hasattr(job, 'processing_metadata')
        assert job.processing_metadata == {}

    def test_processing_metadata_serializes(self):
        """processing_metadata should serialize to JSON correctly."""
        job = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            processing_metadata={
                "transcription": {
                    "provider": "audioshake",
                    "audioshake_task_id": "task-abc",
                    "audioshake_asset_id": "asset-xyz",
                },
            },
        )
        data = job.model_dump(mode='json')
        assert data['processing_metadata']['transcription']['audioshake_task_id'] == "task-abc"
        assert data['processing_metadata']['transcription']['audioshake_asset_id'] == "asset-xyz"

    def test_processing_metadata_deserializes(self):
        """processing_metadata should deserialize from dict correctly."""
        data = {
            "job_id": "test-123",
            "status": "pending",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "processing_metadata": {
                "separation": {
                    "provider": "modal",
                    "duration_seconds": 420.5,
                },
            },
        }
        job = Job(**data)
        assert job.processing_metadata["separation"]["provider"] == "modal"
        assert job.processing_metadata["separation"]["duration_seconds"] == 420.5


class TestSummaryFieldPaths:
    """Regression tests for SUMMARY_FIELD_PATHS."""

    def test_processing_metadata_in_summary_field_paths(self):
        """processing_metadata must be in SUMMARY_FIELD_PATHS for dashboard queries."""
        from backend.services.firestore_service import FirestoreService
        assert 'processing_metadata' in FirestoreService.SUMMARY_FIELD_PATHS


class TestUpdateProcessingMetadata:
    """Test the JobManager.update_processing_metadata helper."""

    @patch('backend.services.job_manager.FirestoreService')
    @patch('backend.services.job_manager.StorageService')
    def test_update_processing_metadata_uses_dot_notation(self, mock_storage_cls, mock_firestore_cls):
        """update_processing_metadata should use Firestore dot-notation for nested updates."""
        from backend.services.job_manager import JobManager

        mock_firestore = MagicMock()
        mock_firestore_cls.return_value = mock_firestore

        jm = JobManager()
        jm.update_processing_metadata("job-123", "transcription", {
            "provider": "audioshake",
            "audioshake_task_id": "task-abc",
        })

        # Should call update_job with dot-notation key
        mock_firestore.update_job.assert_called_once()
        call_args = mock_firestore.update_job.call_args
        assert call_args[0][0] == "job-123"
        updates = call_args[0][1]
        assert "processing_metadata.transcription" in updates
        assert updates["processing_metadata.transcription"]["provider"] == "audioshake"

    @patch('backend.services.job_manager.FirestoreService')
    @patch('backend.services.job_manager.StorageService')
    def test_update_processing_metadata_swallows_errors(self, mock_storage_cls, mock_firestore_cls):
        """update_processing_metadata should not raise on failure."""
        from backend.services.job_manager import JobManager

        mock_firestore = MagicMock()
        mock_firestore.update_job.side_effect = Exception("Firestore error")
        mock_firestore_cls.return_value = mock_firestore

        jm = JobManager()
        # Should not raise
        jm.update_processing_metadata("job-123", "timing", {"foo": 1})


class TestAudioShakeAssetId:
    """Test that AudioShake transcriber stores asset_id in metadata."""

    def test_convert_result_format_includes_asset_id(self):
        """_convert_result_format should include asset_id from task_data.assetId."""
        from karaoke_gen.lyrics_transcriber.transcribers.audioshake import AudioShakeTranscriber

        # Create a minimal transcriber instance
        transcriber = AudioShakeTranscriber.__new__(AudioShakeTranscriber)
        transcriber.logger = MagicMock()
        transcriber._last_asset_id = "fallback-asset-id"

        raw_data = {
            "task_data": {
                "id": "task-123",
                "assetId": "asset-456",
                "duration": 120.5,
                "targets": [],
            },
            "transcription": {
                "text": "hello world",
                "lines": [
                    {
                        "text": "hello world",
                        "words": [
                            {"text": "hello", "start": 0.0, "end": 0.5},
                            {"text": "world", "start": 0.6, "end": 1.0},
                        ],
                    }
                ],
                "metadata": {"language": "en"},
            },
        }

        result = transcriber._convert_result_format(raw_data)
        assert result.metadata["task_id"] == "task-123"
        assert result.metadata["asset_id"] == "asset-456"
        assert result.metadata["language"] == "en"

    def test_convert_result_format_fallback_asset_id(self):
        """asset_id should fall back to _last_asset_id if not in task_data."""
        from karaoke_gen.lyrics_transcriber.transcribers.audioshake import AudioShakeTranscriber

        transcriber = AudioShakeTranscriber.__new__(AudioShakeTranscriber)
        transcriber.logger = MagicMock()
        transcriber._last_asset_id = "fallback-asset-id"

        raw_data = {
            "task_data": {
                "id": "task-123",
                # No assetId in task_data
                "targets": [],
            },
            "transcription": {
                "text": "hello",
                "lines": [
                    {
                        "text": "hello",
                        "words": [{"text": "hello", "start": 0.0, "end": 0.5}],
                    }
                ],
                "metadata": {},
            },
        }

        result = transcriber._convert_result_format(raw_data)
        assert result.metadata["asset_id"] == "fallback-asset-id"


class TestCorrectorMetadataPassthrough:
    """Test that corrector passes through transcription metadata."""

    def test_correction_result_includes_transcription_metadata(self):
        """CorrectionResult.metadata should include transcription_metadata from primary transcription."""
        from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector
        from karaoke_gen.lyrics_transcriber.types import (
            TranscriptionResult, TranscriptionData, LyricsSegment, Word
        )

        # Create a minimal corrector
        corrector = LyricsCorrector(cache_dir="/tmp", enabled_handlers=[])

        # Create transcription result with metadata
        word = Word(id="w1", text="hello", start_time=0.0, end_time=0.5)
        segment = LyricsSegment(id="s1", text="hello", words=[word], start_time=0.0, end_time=0.5)
        transcription_data = TranscriptionData(
            text="hello",
            words=[word],
            segments=[segment],
            source="AudioShake",
            metadata={
                "task_id": "task-123",
                "asset_id": "asset-456",
                "language": "en",
            },
        )
        transcription_result = TranscriptionResult(
            name="AudioShake",
            priority=1,
            result=transcription_data,
        )

        # Run corrector with no reference lyrics (will skip correction but still build result)
        result = corrector.run(
            transcription_results=[transcription_result],
            lyrics_results={},
        )

        # Check that transcription_metadata is passed through
        assert "transcription_metadata" in result.metadata
        assert result.metadata["transcription_metadata"]["task_id"] == "task-123"
        assert result.metadata["transcription_metadata"]["asset_id"] == "asset-456"


class TestExtractRequestMetadataAuthContext:
    """Test that extract_request_metadata includes auth context."""

    def _make_request(self):
        """Create a minimal mock request."""
        request = MagicMock()
        request.headers = {
            "user-agent": "test-agent",
            "x-forwarded-for": "1.2.3.4",
        }
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        return request

    def test_auth_context_included_when_provided(self):
        """Auth context should be in metadata when auth_result is provided."""
        from backend.api.routes.file_upload import extract_request_metadata

        auth_result = MagicMock()
        auth_result.auth_method = "session"
        auth_result.user_type = MagicMock()
        auth_result.user_type.value = "unlimited"
        auth_result.remaining_uses = 42

        metadata = extract_request_metadata(self._make_request(), auth_result=auth_result)
        assert metadata['auth_method'] == "session"
        assert metadata['user_type'] == "unlimited"
        assert metadata['credits_at_creation'] == 42

    def test_no_auth_context_without_auth_result(self):
        """No auth context should be added when auth_result is None."""
        from backend.api.routes.file_upload import extract_request_metadata

        metadata = extract_request_metadata(self._make_request())
        assert 'auth_method' not in metadata
        assert 'user_type' not in metadata
        assert 'credits_at_creation' not in metadata

    def test_audio_search_extract_includes_auth(self):
        """audio_search extract_request_metadata should also support auth_result."""
        from backend.api.routes.audio_search import extract_request_metadata

        auth_result = MagicMock()
        auth_result.auth_method = "api_key"
        auth_result.user_type = MagicMock()
        auth_result.user_type.value = "limited"
        auth_result.remaining_uses = 5

        metadata = extract_request_metadata(self._make_request(), auth_result=auth_result)
        assert metadata['auth_method'] == "api_key"
        assert metadata['user_type'] == "limited"
        assert metadata['credits_at_creation'] == 5
