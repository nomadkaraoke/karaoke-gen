"""
Unit tests for request models.

These tests validate Pydantic request model validation and serialization.
"""
import pytest
from pydantic import ValidationError

from backend.models.requests import (
    URLSubmissionRequest,
    UploadSubmissionRequest,
    CorrectionsSubmission,
    InstrumentalSelection,
    StartReviewRequest,
    CancelJobRequest,
    RetryJobRequest
)


class TestURLSubmissionRequest:
    """Tests for URLSubmissionRequest model."""
    
    def test_valid_youtube_url(self):
        """Test valid YouTube URL is accepted."""
        request = URLSubmissionRequest(url="https://youtube.com/watch?v=test123")
        assert str(request.url) == "https://youtube.com/watch?v=test123"
    
    def test_valid_youtube_short_url(self):
        """Test valid YouTube short URL is accepted."""
        request = URLSubmissionRequest(url="https://youtu.be/test123")
        assert str(request.url) == "https://youtu.be/test123"
    
    def test_optional_artist_title(self):
        """Test artist and title are optional."""
        request = URLSubmissionRequest(url="https://youtube.com/watch?v=test")
        assert request.artist is None
        assert request.title is None
    
    def test_with_artist_title(self):
        """Test request with artist and title."""
        request = URLSubmissionRequest(
            url="https://youtube.com/watch?v=test",
            artist="Test Artist",
            title="Test Song"
        )
        assert request.artist == "Test Artist"
        assert request.title == "Test Song"
    
    def test_url_required(self):
        """Test URL is required."""
        with pytest.raises(ValidationError):
            URLSubmissionRequest()
    
    def test_empty_url_rejected(self):
        """Test empty URL is rejected."""
        with pytest.raises(ValidationError):
            URLSubmissionRequest(url="")


class TestCorrectionsSubmission:
    """Tests for CorrectionsSubmission model."""
    
    def test_valid_corrections(self):
        """Test valid corrections submission."""
        corrections = CorrectionsSubmission(
            corrections={
                "lines": [
                    {"text": "Hello world", "start": 0.0, "end": 1.0}
                ],
                "metadata": {"source": "test"}
            }
        )
        assert "lines" in corrections.corrections
        assert "metadata" in corrections.corrections
    
    def test_missing_lines_rejected(self):
        """Test corrections without 'lines' field is rejected."""
        with pytest.raises(ValidationError):
            CorrectionsSubmission(corrections={"metadata": {}})
    
    def test_missing_metadata_rejected(self):
        """Test corrections without 'metadata' field is rejected."""
        with pytest.raises(ValidationError):
            CorrectionsSubmission(corrections={"lines": []})
    
    def test_corrections_required(self):
        """Test corrections field is required."""
        with pytest.raises(ValidationError):
            CorrectionsSubmission()
    
    def test_with_user_notes(self):
        """Test corrections with user notes."""
        corrections = CorrectionsSubmission(
            corrections={"lines": [], "metadata": {}},
            user_notes="Fixed typo in line 3"
        )
        assert corrections.user_notes == "Fixed typo in line 3"


class TestInstrumentalSelection:
    """Tests for InstrumentalSelection model."""
    
    def test_valid_clean_selection(self):
        """Test selecting clean instrumental."""
        selection = InstrumentalSelection(selection="clean")
        assert selection.selection == "clean"
    
    def test_valid_with_backing_selection(self):
        """Test selecting instrumental with backing vocals."""
        selection = InstrumentalSelection(selection="with_backing")
        assert selection.selection == "with_backing"
    
    def test_selection_required(self):
        """Test selection is required."""
        with pytest.raises(ValidationError):
            InstrumentalSelection()
    
    def test_invalid_selection_rejected(self):
        """Test invalid selection values are rejected."""
        with pytest.raises(ValidationError):
            InstrumentalSelection(selection="invalid_option")


class TestUploadSubmissionRequest:
    """Tests for UploadSubmissionRequest model."""
    
    def test_valid_upload_request(self):
        """Test valid upload submission."""
        request = UploadSubmissionRequest(
            artist="Test Artist",
            title="Test Song"
        )
        assert request.artist == "Test Artist"
        assert request.title == "Test Song"
    
    def test_artist_required(self):
        """Test artist is required."""
        with pytest.raises(ValidationError):
            UploadSubmissionRequest(title="Test Song")
    
    def test_title_required(self):
        """Test title is required."""
        with pytest.raises(ValidationError):
            UploadSubmissionRequest(artist="Test Artist")
    
    def test_default_options(self):
        """Test default option values."""
        request = UploadSubmissionRequest(artist="Test", title="Test")
        # CDG/TXT disabled by default (requires style config)
        assert request.enable_cdg is False
        assert request.enable_txt is False
        # YouTube upload default is None (use server default)
        assert request.enable_youtube_upload is None


class TestStartReviewRequest:
    """Tests for StartReviewRequest model."""
    
    def test_valid_request(self):
        """Test valid start review request."""
        request = StartReviewRequest()
        assert request is not None


class TestCancelJobRequest:
    """Tests for CancelJobRequest model."""
    
    def test_valid_request(self):
        """Test valid cancel request."""
        request = CancelJobRequest()
        assert request is not None
    
    def test_with_reason(self):
        """Test cancel with reason."""
        request = CancelJobRequest(reason="User requested")
        assert request.reason == "User requested"
    
    def test_reason_optional(self):
        """Test reason is optional."""
        request = CancelJobRequest()
        assert request.reason is None


class TestRetryJobRequest:
    """Tests for RetryJobRequest model."""
    
    def test_valid_request(self):
        """Test valid retry request."""
        request = RetryJobRequest()
        assert request is not None
    
    def test_with_from_stage(self):
        """Test retry from specific stage."""
        request = RetryJobRequest(from_stage="transcription")
        assert request.from_stage == "transcription"
    
    def test_from_stage_optional(self):
        """Test from_stage is optional."""
        request = RetryJobRequest()
        assert request.from_stage is None

