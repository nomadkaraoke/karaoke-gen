"""
Unit tests for review session backup/restore feature.

Tests the ReviewSession model, Firestore CRUD methods, and API endpoints.
"""
import pytest
import json
import hashlib
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

from backend.models.review_session import ReviewSession, ReviewSessionSummary
from backend.models.job import Job, JobStatus


@pytest.fixture(autouse=True)
def ensure_auth_overrides():
    """Ensure auth dependency overrides are set for endpoint tests.

    The global conftest autouse fixture should handle this, but some tests
    in the broader suite can leave dependency_overrides in a dirty state.
    This fixture ensures a clean slate for review session endpoint tests.
    """
    from backend.main import app
    from backend.api.dependencies import require_auth, require_review_auth

    async def mock_require_auth():
        from backend.services.auth_service import UserType, AuthResult
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            remaining_uses=999,
            message="Test admin token",
            is_admin=True,
            user_email="test@example.com",
        )

    async def mock_require_review_auth(job_id: str = "test123"):
        return (job_id, "full")

    app.dependency_overrides[require_auth] = mock_require_auth
    app.dependency_overrides[require_review_auth] = mock_require_review_auth
    yield
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_review_auth, None)


# ============================================
# Model Tests
# ============================================


class TestReviewSessionSummary:
    def test_to_dict(self):
        summary = ReviewSessionSummary(
            total_segments=10,
            total_words=100,
            corrections_made=5,
            changed_words=[{"original": "hello", "corrected": "hallo", "segment_index": 0}],
        )
        d = summary.to_dict()
        assert d["total_segments"] == 10
        assert d["corrections_made"] == 5
        assert len(d["changed_words"]) == 1

    def test_from_dict(self):
        data = {
            "total_segments": 15,
            "total_words": 200,
            "corrections_made": 8,
            "changed_words": [],
        }
        summary = ReviewSessionSummary.from_dict(data)
        assert summary.total_segments == 15
        assert summary.corrections_made == 8

    def test_from_dict_missing_fields(self):
        summary = ReviewSessionSummary.from_dict({})
        assert summary.total_segments == 0
        assert summary.changed_words == []


class TestReviewSession:
    def test_to_dict_and_from_dict_roundtrip(self):
        session = ReviewSession(
            session_id="test-session-1",
            job_id="job-123",
            user_email="user@example.com",
            edit_count=42,
            trigger="preview",
            audio_duration_seconds=222.5,
            artist="Test Artist",
            title="Test Song",
            data_hash="abc123hash",
            correction_data_gcs_path="jobs/job-123/review_sessions/test-session-1.json",
            summary=ReviewSessionSummary(total_segments=10, total_words=100, corrections_made=5),
        )
        d = session.to_dict()
        restored = ReviewSession.from_dict(d)

        assert restored.session_id == "test-session-1"
        assert restored.job_id == "job-123"
        assert restored.edit_count == 42
        assert restored.trigger == "preview"
        assert restored.audio_duration_seconds == 222.5
        assert restored.artist == "Test Artist"
        assert restored.data_hash == "abc123hash"
        assert restored.summary.total_segments == 10

    def test_from_dict_string_timestamps(self):
        data = {
            "session_id": "s1",
            "created_at": "2026-03-07T12:00:00Z",
            "updated_at": "2026-03-07T12:15:00Z",
        }
        session = ReviewSession.from_dict(data)
        assert session.created_at.year == 2026
        assert session.updated_at.minute == 15

    def test_default_values(self):
        session = ReviewSession()
        assert session.trigger == "auto"
        assert session.edit_count == 0
        assert session.audio_duration_seconds is None
        assert session.session_id  # auto-generated


# ============================================
# API Endpoint Tests
# ============================================


class TestSaveReviewSessionEndpoint:
    """Tests for POST /api/review/{job_id}/sessions"""

    @patch("backend.api.routes.review.StorageService")
    @patch("backend.api.routes.review.FirestoreService")
    @patch("backend.api.routes.review.JobManager")
    def test_save_session_success(self, MockJobManager, MockFirestore, MockStorage, test_client):
        mock_job = MagicMock(spec=Job)
        mock_job.artist = "Test Artist"
        mock_job.title = "Test Song"
        mock_job.state_data = {"backing_vocals_analysis": {"total_duration_seconds": 180.0}}

        MockJobManager.return_value.get_job.return_value = mock_job
        MockFirestore.return_value.get_latest_review_session_hash.return_value = None
        MockStorage.return_value.upload_json.return_value = None
        MockFirestore.return_value.save_review_session.return_value = "session-id"

        response = test_client.post(
            "/api/review/job-123/sessions",
            json={
                "correction_data": {"corrected_segments": [], "original_segments": []},
                "edit_count": 5,
                "trigger": "manual",
                "summary": {"total_segments": 10, "total_words": 100, "corrections_made": 5},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "saved"
        assert "session_id" in data

    @patch("backend.api.routes.review.StorageService")
    @patch("backend.api.routes.review.FirestoreService")
    @patch("backend.api.routes.review.JobManager")
    def test_save_session_deduplication(self, MockJobManager, MockFirestore, MockStorage, test_client):
        """Should skip save if data hash matches latest session."""
        mock_job = MagicMock(spec=Job)
        mock_job.artist = "Test Artist"
        mock_job.title = "Test Song"
        mock_job.state_data = {}

        correction_data = {"corrected_segments": [{"text": "hello"}], "original_segments": []}
        data_json = json.dumps(correction_data, sort_keys=True, default=str)
        expected_hash = hashlib.sha256(data_json.encode()).hexdigest()

        MockJobManager.return_value.get_job.return_value = mock_job
        MockFirestore.return_value.get_latest_review_session_hash.return_value = expected_hash

        response = test_client.post(
            "/api/review/job-123/sessions",
            json={"correction_data": correction_data, "edit_count": 5, "trigger": "auto"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "skipped"
        assert response.json()["reason"] == "identical_data"
        # GCS upload should NOT have been called
        MockStorage.return_value.upload_json.assert_not_called()

    @patch("backend.api.routes.review.JobManager")
    def test_save_session_job_not_found(self, MockJobManager, test_client):
        MockJobManager.return_value.get_job.return_value = None
        response = test_client.post(
            "/api/review/job-123/sessions",
            json={"correction_data": {"foo": "bar"}, "edit_count": 1},
        )
        assert response.status_code == 404

    @patch("backend.api.routes.review.JobManager")
    def test_save_session_missing_correction_data(self, MockJobManager, test_client):
        mock_job = MagicMock(spec=Job)
        MockJobManager.return_value.get_job.return_value = mock_job
        response = test_client.post(
            "/api/review/job-123/sessions",
            json={"edit_count": 1},
        )
        assert response.status_code == 400


class TestListReviewSessionsEndpoint:
    """Tests for GET /api/review/{job_id}/sessions"""

    @patch("backend.api.routes.review.FirestoreService")
    def test_list_sessions(self, MockFirestore, test_client):
        session = ReviewSession(
            session_id="s1",
            job_id="job-123",
            user_email="user@test.com",
            edit_count=10,
            trigger="auto",
            artist="Artist",
            title="Song",
        )
        MockFirestore.return_value.list_review_sessions.return_value = [session]

        response = test_client.get("/api/review/job-123/sessions")
        assert response.status_code == 200
        data = response.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "s1"
        assert data["sessions"][0]["edit_count"] == 10

    @patch("backend.api.routes.review.FirestoreService")
    def test_list_sessions_empty(self, MockFirestore, test_client):
        MockFirestore.return_value.list_review_sessions.return_value = []
        response = test_client.get("/api/review/job-123/sessions")
        assert response.status_code == 200
        assert response.json()["sessions"] == []


class TestGetReviewSessionEndpoint:
    """Tests for GET /api/review/{job_id}/sessions/{session_id}"""

    @patch("backend.api.routes.review.StorageService")
    @patch("backend.api.routes.review.FirestoreService")
    def test_get_session_with_data(self, MockFirestore, MockStorage, test_client):
        session = ReviewSession(
            session_id="s1",
            job_id="job-123",
            correction_data_gcs_path="jobs/job-123/review_sessions/s1.json",
        )
        MockFirestore.return_value.get_review_session.return_value = session
        MockStorage.return_value.download_json.return_value = {"corrected_segments": []}

        response = test_client.get("/api/review/job-123/sessions/s1")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "s1"
        assert data["correction_data"] == {"corrected_segments": []}

    @patch("backend.api.routes.review.FirestoreService")
    def test_get_session_not_found(self, MockFirestore, test_client):
        MockFirestore.return_value.get_review_session.return_value = None
        response = test_client.get("/api/review/job-123/sessions/nonexistent")
        assert response.status_code == 404


class TestDeleteReviewSessionEndpoint:
    """Tests for DELETE /api/review/{job_id}/sessions/{session_id}"""

    @patch("backend.api.routes.review.StorageService")
    @patch("backend.api.routes.review.FirestoreService")
    def test_delete_session(self, MockFirestore, MockStorage, test_client):
        session = ReviewSession(
            session_id="s1",
            job_id="job-123",
            correction_data_gcs_path="jobs/job-123/review_sessions/s1.json",
        )
        MockFirestore.return_value.get_review_session.return_value = session

        response = test_client.delete("/api/review/job-123/sessions/s1")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        MockStorage.return_value.delete_file.assert_called_once()
        MockFirestore.return_value.delete_review_session.assert_called_once()

    @patch("backend.api.routes.review.FirestoreService")
    def test_delete_session_not_found(self, MockFirestore, test_client):
        MockFirestore.return_value.get_review_session.return_value = None
        response = test_client.delete("/api/review/job-123/sessions/nonexistent")
        assert response.status_code == 404


class TestSearchReviewSessionsEndpoint:
    """Tests for GET /api/review/sessions/search"""

    @patch("backend.api.routes.review.FirestoreService")
    def test_search_sessions(self, MockFirestore, test_client):
        MockFirestore.return_value.search_review_sessions.return_value = [
            {"session_id": "s1", "job_id": "j1", "artist": "Artist A"},
        ]
        response = test_client.get("/api/review/sessions/search?q=Artist")
        assert response.status_code == 200
        assert len(response.json()["sessions"]) == 1

    @patch("backend.api.routes.review.FirestoreService")
    def test_search_sessions_empty(self, MockFirestore, test_client):
        MockFirestore.return_value.search_review_sessions.return_value = []
        response = test_client.get("/api/review/sessions/search?q=nonexistent")
        assert response.status_code == 200
        assert response.json()["sessions"] == []
