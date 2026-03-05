"""
Unit tests for admin edit reviews endpoints.

Tests the GET /api/admin/edit-reviews and GET /api/admin/edit-reviews/{job_id}
endpoints for reviewing user lyrics corrections.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from datetime import datetime, timezone

from backend.api.routes.admin import router
from backend.api.dependencies import require_admin
from backend.models.job import Job, JobStatus


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


# Override the require_admin dependency
app.dependency_overrides[require_admin] = get_mock_admin


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


def _make_job_doc(job_id, artist="Test Artist", title="Test Song",
                  user_email="user@example.com", status="complete",
                  has_edit_log=True, has_corrections_updated=True):
    """Create a mock Firestore document for a job."""
    doc = Mock()
    doc.id = job_id
    now = datetime.now(timezone.utc)
    state_data = {}
    if has_edit_log:
        state_data["last_edit_log_path"] = f"jobs/{job_id}/lyrics/edit_log_session1.json"
        state_data["last_edit_log_session"] = "session1"

    file_urls = {"lyrics": {"corrections": f"jobs/{job_id}/lyrics/corrections.json"}}
    if has_corrections_updated:
        file_urls["lyrics"]["corrections_updated"] = f"jobs/{job_id}/lyrics/corrections_updated.json"

    doc.to_dict.return_value = {
        "artist": artist,
        "title": title,
        "user_email": user_email,
        "status": status,
        "state_data": state_data,
        "file_urls": file_urls,
        "created_at": now,
        "updated_at": now,
    }
    return doc


class TestListEditReviews:
    """Tests for GET /api/admin/edit-reviews."""

    @patch("backend.api.routes.admin.get_user_service")
    def test_list_returns_only_jobs_with_edit_logs(self, mock_get_user_service, client):
        """Only jobs with edit logs should appear."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [
            _make_job_doc("job-1", has_edit_log=True),
            _make_job_doc("job-2", has_edit_log=False),
            _make_job_doc("job-3", has_edit_log=True),
        ]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs

        response = client.get("/api/admin/edit-reviews")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["reviews"]) == 2
        job_ids = [r["job_id"] for r in data["reviews"]]
        assert "job-1" in job_ids
        assert "job-3" in job_ids
        assert "job-2" not in job_ids

    @patch("backend.api.routes.admin.get_user_service")
    def test_list_search_filter(self, mock_get_user_service, client):
        """Search should filter by artist, title, and email."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [
            _make_job_doc("job-1", artist="Beatles", title="Hey Jude"),
            _make_job_doc("job-2", artist="Queen", title="Bohemian Rhapsody"),
        ]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs

        response = client.get("/api/admin/edit-reviews?search=beatles")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["reviews"][0]["artist"] == "Beatles"

    @patch("backend.api.routes.admin.get_user_service")
    def test_list_pagination(self, mock_get_user_service, client):
        """Pagination should work with offset and limit."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [_make_job_doc(f"job-{i}") for i in range(5)]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs

        response = client.get("/api/admin/edit-reviews?limit=2&offset=0")
        data = response.json()
        assert len(data["reviews"]) == 2
        assert data["total"] == 5
        assert data["has_more"] is True

    @patch("backend.api.routes.admin.get_user_service")
    def test_list_exclude_test(self, mock_get_user_service, client):
        """Test emails should be excluded when exclude_test=true."""
        mock_db = Mock()
        mock_get_user_service.return_value.db = mock_db

        docs = [
            _make_job_doc("job-1", user_email="real@example.com"),
            _make_job_doc("job-2", user_email="test@inbox.testmail.app"),
        ]
        mock_db.collection.return_value.limit.return_value.stream.return_value = docs

        response = client.get("/api/admin/edit-reviews?exclude_test=true")
        data = response.json()
        assert data["total"] == 1
        assert data["reviews"][0]["user_email"] == "real@example.com"


class TestGetEditReviewDetail:
    """Tests for GET /api/admin/edit-reviews/{job_id}."""

    @patch("backend.api.routes.admin.StorageService")
    @patch("backend.api.routes.admin.JobManager")
    def test_returns_404_for_missing_job(self, MockJobManager, MockStorage, client):
        MockJobManager.return_value.get_job.return_value = None
        response = client.get("/api/admin/edit-reviews/nonexistent")
        assert response.status_code == 404

    @patch("backend.services.audio_transcoding_service.AudioTranscodingService")
    @patch("backend.api.routes.admin.StorageService")
    @patch("backend.api.routes.admin.JobManager")
    def test_returns_full_snapshot(self, MockJobManager, MockStorage, MockTranscoding, client):
        """Should return all available data for a job."""
        job = Mock(spec=Job)
        job.job_id = "test-job"
        job.artist = "Test Artist"
        job.title = "Test Song"
        job.user_email = "user@example.com"
        job.status = JobStatus.COMPLETE
        job.created_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
        job.updated_at = datetime(2026, 3, 2, tzinfo=timezone.utc)
        job.input_media_gcs_path = "jobs/test-job/input.flac"
        job.file_urls = {
            "lyrics": {
                "corrections": "jobs/test-job/lyrics/corrections.json",
                "corrections_updated": "jobs/test-job/lyrics/corrections_updated.json",
            }
        }
        job.state_data = {
            "last_edit_log_path": "jobs/test-job/lyrics/edit_log_session1.json",
        }
        MockJobManager.return_value.get_job.return_value = job

        storage = MockStorage.return_value
        storage.download_json.side_effect = lambda path: {
            "jobs/test-job/lyrics/corrections.json": {"original": True},
            "jobs/test-job/lyrics/corrections_updated.json": {"updated": True},
            "jobs/test-job/lyrics/edit_log_session1.json": {"entries": []},
            "jobs/test-job/lyrics/annotations.json": {"annotations": []},
        }.get(path, {})
        storage.file_exists.return_value = True

        response = client.get("/api/admin/edit-reviews/test-job")
        assert response.status_code == 200
        data = response.json()

        assert data["job"]["job_id"] == "test-job"
        assert data["job"]["artist"] == "Test Artist"
        assert data["original_corrections"] == {"original": True}
        assert data["updated_corrections"] == {"updated": True}
        assert data["edit_log"] == {"entries": []}
        assert data["annotations"] == {"annotations": []}

    @patch("backend.api.routes.admin.StorageService")
    @patch("backend.api.routes.admin.JobManager")
    def test_handles_missing_gcs_files_gracefully(self, MockJobManager, MockStorage, client):
        """Should return null for missing GCS files, not error."""
        job = Mock(spec=Job)
        job.job_id = "test-job"
        job.artist = "Test"
        job.title = "Song"
        job.user_email = "u@e.com"
        job.status = JobStatus.COMPLETE
        job.created_at = None
        job.updated_at = None
        job.input_media_gcs_path = None
        job.file_urls = {"lyrics": {}}
        job.state_data = {}
        MockJobManager.return_value.get_job.return_value = job

        storage = MockStorage.return_value
        storage.file_exists.return_value = False
        storage.download_json.side_effect = Exception("Not found")

        response = client.get("/api/admin/edit-reviews/test-job")
        assert response.status_code == 200
        data = response.json()
        assert data["original_corrections"] is None
        assert data["updated_corrections"] is None
        assert data["edit_log"] is None
        assert data["annotations"] is None
        assert data["audio_url"] is None
