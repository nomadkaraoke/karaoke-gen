"""
Unit tests for admin audio edit review endpoints.

Tests the GET /api/admin/audio-edit-reviews and
GET /api/admin/audio-edit-reviews/{job_id} endpoints.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from datetime import datetime, timezone

from backend.api.routes.admin import router
from backend.api.dependencies import require_admin
from backend.models.job import Job, JobStatus
from backend.models.audio_edit_session import AudioEditSession, AudioEditSessionSummary


# Create a test app with the admin router
app = FastAPI()
app.include_router(router, prefix="/api")


def get_mock_admin():
    """Override for require_admin dependency."""
    from backend.api.dependencies import AuthResult, UserType
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=999,
        message="Admin authenticated",
        user_email="admin@example.com",
        is_admin=True,
    )


app.dependency_overrides[require_admin] = get_mock_admin


@pytest.fixture
def client():
    return TestClient(app)


def _make_audio_edit_job_doc(job_id, artist="Test Artist", title="Test Song",
                              user_email="user@example.com", status="complete",
                              has_edit_stack=True):
    """Create a mock Firestore document for a job with audio edits."""
    doc = Mock()
    doc.id = job_id
    now = datetime.now(timezone.utc)
    state_data = {}
    if has_edit_stack:
        state_data["audio_edit_stack"] = [
            {"edit_id": "e1", "operation": "cut", "gcs_path": f"jobs/{job_id}/edits/e1.flac"}
        ]

    doc.to_dict.return_value = {
        "artist": artist,
        "title": title,
        "user_email": user_email,
        "status": status,
        "state_data": state_data,
        "file_urls": {},
        "created_at": now,
        "updated_at": now,
    }
    return doc


def _make_session(job_id, trigger="auto", edit_count=3):
    """Create a mock AudioEditSession."""
    return AudioEditSession(
        session_id="sess-1",
        job_id=job_id,
        user_email="user@example.com",
        edit_count=edit_count,
        trigger=trigger,
        audio_duration_seconds=150.0,
        original_duration_seconds=225.0,
        artist="Test Artist",
        title="Test Song",
        summary=AudioEditSessionSummary(
            total_operations=edit_count,
            operations_breakdown={"cut": 2, "mute": 1},
            duration_change_seconds=-75.0,
            net_duration_seconds=150.0,
        ),
    )


class TestListAudioEditReviews:
    """Tests for GET /api/admin/audio-edit-reviews."""

    @patch("backend.services.firestore_service.FirestoreService")
    @patch("backend.api.routes.admin.get_user_service")
    def test_list_returns_jobs_with_edit_stacks(self, mock_get_user_service, MockFS, client):
        """Jobs with audio edit stacks should appear."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [
            _make_audio_edit_job_doc("job-1", has_edit_stack=True),
            _make_audio_edit_job_doc("job-2", has_edit_stack=False),
        ]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs

        # job-2 has no edit stack and no sessions subcollection
        sessions_ref = Mock()
        sessions_ref.limit.return_value.stream.return_value = iter([])
        mock_db.collection.return_value.document.return_value.collection.return_value = sessions_ref

        # Sessions for job-1
        MockFS.return_value.list_audio_edit_sessions.return_value = [
            _make_session("job-1")
        ]

        response = client.get("/api/admin/audio-edit-reviews")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["reviews"][0]["job_id"] == "job-1"
        assert data["reviews"][0]["session_count"] == 1
        assert data["reviews"][0]["total_edits"] == 3

    @patch("backend.services.firestore_service.FirestoreService")
    @patch("backend.api.routes.admin.get_user_service")
    def test_list_search_filter(self, mock_get_user_service, MockFS, client):
        """Search should filter by artist, title, and email."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [
            _make_audio_edit_job_doc("job-1", artist="Beatles", title="Hey Jude"),
            _make_audio_edit_job_doc("job-2", artist="Queen", title="Bohemian Rhapsody"),
        ]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs
        MockFS.return_value.list_audio_edit_sessions.return_value = [_make_session("job-1")]

        response = client.get("/api/admin/audio-edit-reviews?search=beatles")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["reviews"][0]["artist"] == "Beatles"

    @patch("backend.services.firestore_service.FirestoreService")
    @patch("backend.api.routes.admin.get_user_service")
    def test_list_pagination(self, mock_get_user_service, MockFS, client):
        """Pagination should work with offset and limit."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [_make_audio_edit_job_doc(f"job-{i}") for i in range(5)]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs
        MockFS.return_value.list_audio_edit_sessions.return_value = []

        response = client.get("/api/admin/audio-edit-reviews?limit=2&offset=0")
        data = response.json()
        assert len(data["reviews"]) == 2
        assert data["total"] == 5
        assert data["has_more"] is True

    @patch("backend.services.firestore_service.FirestoreService")
    @patch("backend.api.routes.admin.get_user_service")
    def test_list_exclude_test_emails(self, mock_get_user_service, MockFS, client):
        """Test emails should be excluded when exclude_test=true."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [
            _make_audio_edit_job_doc("job-1", user_email="real@example.com"),
            _make_audio_edit_job_doc("job-2", user_email="test@inbox.testmail.app"),
        ]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs
        MockFS.return_value.list_audio_edit_sessions.return_value = []

        response = client.get("/api/admin/audio-edit-reviews?exclude_test=true")
        data = response.json()
        assert data["total"] == 1
        assert data["reviews"][0]["user_email"] == "real@example.com"

    @patch("backend.services.firestore_service.FirestoreService")
    @patch("backend.api.routes.admin.get_user_service")
    def test_list_includes_in_audio_edit_jobs(self, mock_get_user_service, MockFS, client):
        """Jobs in audio edit statuses should appear even without edit stack."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [
            _make_audio_edit_job_doc("job-1", status="in_audio_edit", has_edit_stack=False),
        ]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs
        MockFS.return_value.list_audio_edit_sessions.return_value = []

        response = client.get("/api/admin/audio-edit-reviews")
        data = response.json()
        assert data["total"] == 1
        assert data["reviews"][0]["job_id"] == "job-1"


class TestGetAudioEditReviewDetail:
    """Tests for GET /api/admin/audio-edit-reviews/{job_id}."""

    @patch("backend.api.routes.admin.StorageService")
    @patch("backend.api.routes.admin.JobManager")
    def test_returns_404_for_missing_job(self, MockJobManager, MockStorage, client):
        MockJobManager.return_value.get_job.return_value = None
        response = client.get("/api/admin/audio-edit-reviews/nonexistent")
        assert response.status_code == 404

    @patch("backend.services.audio_transcoding_service.AudioTranscodingService")
    @patch("backend.services.firestore_service.FirestoreService")
    @patch("backend.api.routes.admin.StorageService")
    @patch("backend.api.routes.admin.JobManager")
    def test_returns_full_detail(self, MockJobManager, MockStorage, MockFS, MockTranscoding, client):
        """Should return job metadata, sessions, edit stack, and audio URLs."""
        job = Mock(spec=Job)
        job.job_id = "test-job"
        job.artist = "Test Artist"
        job.title = "Test Song"
        job.user_email = "user@example.com"
        job.status = JobStatus.COMPLETE
        job.created_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
        job.updated_at = datetime(2026, 3, 2, tzinfo=timezone.utc)
        job.input_media_gcs_path = "jobs/test-job/input/edited.flac"
        job.state_data = {
            "audio_edit_stack": [
                {"edit_id": "e1", "operation": "cut", "gcs_path": "jobs/test-job/edits/e1.flac",
                 "duration_before": 225, "duration_after": 150, "timestamp": "2026-03-01T10:00:00"},
            ],
            "original_input_media_gcs_path": "jobs/test-job/input/original.flac",
        }
        MockJobManager.return_value.get_job.return_value = job

        storage = MockStorage.return_value
        storage.file_exists.return_value = False

        session = _make_session("test-job", trigger="submit")
        MockFS.return_value.list_audio_edit_sessions.return_value = [session]

        response = client.get("/api/admin/audio-edit-reviews/test-job")
        assert response.status_code == 200
        data = response.json()

        assert data["job"]["job_id"] == "test-job"
        assert data["job"]["artist"] == "Test Artist"
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["trigger"] == "submit"
        assert len(data["edit_stack"]) == 1
        assert data["edit_stack"][0]["operation"] == "cut"

    @patch("backend.services.firestore_service.FirestoreService")
    @patch("backend.api.routes.admin.StorageService")
    @patch("backend.api.routes.admin.JobManager")
    def test_handles_empty_state_gracefully(self, MockJobManager, MockStorage, MockFS, client):
        """Should handle jobs with no edit data gracefully."""
        job = Mock(spec=Job)
        job.job_id = "test-job"
        job.artist = "Test"
        job.title = "Song"
        job.user_email = "u@e.com"
        job.status = JobStatus.AWAITING_AUDIO_EDIT
        job.created_at = None
        job.updated_at = None
        job.input_media_gcs_path = None
        job.state_data = {}
        MockJobManager.return_value.get_job.return_value = job

        storage = MockStorage.return_value
        storage.file_exists.return_value = False

        MockFS.return_value.list_audio_edit_sessions.return_value = []

        response = client.get("/api/admin/audio-edit-reviews/test-job")
        assert response.status_code == 200
        data = response.json()
        assert data["sessions"] == []
        assert data["edit_stack"] == []
        assert data["edit_log"] is None
