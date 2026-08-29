"""
Unit tests for jobs.py API routes using FastAPI TestClient.

These tests mock the underlying services and test the route logic directly.
"""
import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

from backend.models.job import Job, JobStatus


@pytest.fixture
def mock_job():
    """Create a standard mock job for testing."""
    return Job(
        job_id="test123",
        status=JobStatus.PENDING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song"
    )


@pytest.fixture
def mock_job_manager(mock_job):
    """Create a mock JobManager with common methods."""
    manager = MagicMock()
    manager.get_job.return_value = mock_job
    manager.list_jobs.return_value = [mock_job]
    manager.create_job.return_value = mock_job
    manager.delete_job.return_value = True
    manager.cancel_job.return_value = mock_job
    manager.update_job_status.return_value = None
    manager.update_state_data.return_value = None
    return manager


@pytest.fixture
def mock_worker_service():
    """Create a mock WorkerService."""
    service = MagicMock()
    service.trigger_audio_worker = AsyncMock(return_value=True)
    service.trigger_lyrics_worker = AsyncMock(return_value=True)
    service.trigger_screens_worker = AsyncMock(return_value=True)
    service.trigger_video_worker = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_theme_service():
    """Create a mock ThemeService that returns 'nomad' as default theme."""
    service = MagicMock()
    service.get_default_theme_id.return_value = "nomad"
    service.get_theme.return_value = None
    return service


@pytest.fixture
def client(mock_job_manager, mock_worker_service, mock_theme_service):
    """Create TestClient with mocked dependencies."""
    mock_creds = MagicMock()
    mock_creds.universe_domain = 'googleapis.com'

    # Create a JobManager class that returns our mock instance
    def mock_job_manager_factory(*args, **kwargs):
        return mock_job_manager

    # Patch at the module level where jobs.py imports them
    # Also patch JobManager class used in dependencies.py for auth checks
    with patch('backend.api.routes.jobs.job_manager', mock_job_manager), \
         patch('backend.api.routes.jobs.worker_service', mock_worker_service), \
         patch('backend.api.routes.jobs.get_theme_service', return_value=mock_theme_service), \
         patch('backend.services.job_manager.JobManager', mock_job_manager_factory), \
         patch('backend.services.firestore_service.firestore'), \
         patch('backend.services.storage_service.storage'), \
         patch('google.auth.default', return_value=(mock_creds, 'test-project')):
        from backend.main import app
        yield TestClient(app)


class TestGetJob:
    """Tests for GET /api/jobs/{job_id}."""
    
    def test_get_job_returns_200(self, client, mock_job_manager, mock_job, auth_headers):
        """Test getting an existing job returns 200."""
        response = client.get("/api/jobs/test123", headers=auth_headers)
        assert response.status_code == 200
    
    def test_get_job_returns_job_data(self, client, mock_job_manager, mock_job, auth_headers):
        """Test response contains job data."""
        response = client.get("/api/jobs/test123", headers=auth_headers)
        data = response.json()
        assert data["job_id"] == "test123"
        assert data["status"] == "pending"
        assert data["artist"] == "Test Artist"
    
    def test_get_nonexistent_job_returns_404(self, mock_worker_service, auth_headers):
        """Test getting non-existent job returns 404."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = None
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'

        def mock_job_manager_factory(*args, **kwargs):
            return mock_job_manager

        with patch('backend.api.routes.jobs.job_manager', mock_job_manager), \
             patch('backend.api.routes.jobs.worker_service', mock_worker_service), \
             patch('backend.services.job_manager.JobManager', mock_job_manager_factory), \
             patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')):
            from backend.main import app
            client = TestClient(app)
            response = client.get("/api/jobs/nonexistent", headers=auth_headers)
            assert response.status_code == 404


class TestListJobs:
    """Tests for GET /api/jobs."""
    
    def test_list_jobs_returns_200(self, client, auth_headers):
        """Test listing jobs returns 200."""
        response = client.get("/api/jobs", headers=auth_headers)
        assert response.status_code == 200
    
    def test_list_jobs_returns_array(self, client, mock_job_manager, auth_headers):
        """Test response is an array of jobs."""
        response = client.get("/api/jobs", headers=auth_headers)
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["job_id"] == "test123"
    
    def test_list_jobs_with_status_filter(self, client, mock_job_manager, auth_headers):
        """Test listing jobs with status filter."""
        response = client.get("/api/jobs?status=pending", headers=auth_headers)
        assert response.status_code == 200
    
    def test_list_jobs_with_limit(self, client, mock_job_manager, auth_headers):
        """Test listing jobs with limit."""
        response = client.get("/api/jobs?limit=10", headers=auth_headers)
        assert response.status_code == 200


class TestCreateJob:
    """Tests for POST /api/jobs."""
    
    def test_create_job_with_url_returns_200(self, client, mock_job_manager, auth_headers):
        """Test creating job with URL returns 200."""
        response = client.post(
            "/api/jobs",
            json={"url": "https://youtube.com/watch?v=test123"},
            headers=auth_headers
        )
        assert response.status_code == 200
    
    def test_create_job_returns_job_id(self, client, mock_job_manager, auth_headers):
        """Test create response contains job_id."""
        response = client.post(
            "/api/jobs",
            json={"url": "https://youtube.com/watch?v=test"},
            headers=auth_headers
        )
        data = response.json()
        assert "job_id" in data
    
    def test_create_job_with_artist_title(self, client, mock_job_manager, auth_headers):
        """Test creating job with artist and title."""
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtube.com/watch?v=test",
                "artist": "Test Artist",
                "title": "Test Song"
            },
            headers=auth_headers
        )
        assert response.status_code == 200


class TestDeleteJob:
    """Tests for DELETE /api/jobs/{job_id}."""
    
    def test_delete_job_returns_200(self, client, mock_job_manager, auth_headers):
        """Test deleting job returns 200."""
        response = client.delete("/api/jobs/test123", headers=auth_headers)
        assert response.status_code == 200
    
    def test_delete_nonexistent_job(self, mock_worker_service, auth_headers):
        """Test deleting non-existent job."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = None  # Job doesn't exist
        mock_job_manager.delete_job.return_value = False
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'

        def mock_job_manager_factory(*args, **kwargs):
            return mock_job_manager

        with patch('backend.api.routes.jobs.job_manager', mock_job_manager), \
             patch('backend.api.routes.jobs.worker_service', mock_worker_service), \
             patch('backend.services.job_manager.JobManager', mock_job_manager_factory), \
             patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')):
            from backend.main import app
            client = TestClient(app)
            response = client.delete("/api/jobs/nonexistent", headers=auth_headers)
            # Either 200 or 404 depending on implementation
            assert response.status_code in [200, 404]


class TestDeleteJobBrandCodeRecycling:
    """Tests for published-output cleanup + brand code recycling in DELETE /api/jobs/{job_id}.

    Recycling must ONLY happen via the distribution cleanup (which gates it on
    Dropbox + GDrive success). Blind-recycling on delete hands the same NOMAD-####
    to a future job while the old files are still in the public share (duplicate
    brand code incident, NOMAD-1583, 2026-08-29).
    """

    @staticmethod
    def _job_with_brand(job_id, outputs_deleted_at=None):
        return Job(
            job_id=job_id,
            status=JobStatus.COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            state_data={"brand_code": "NOMAD-0042"},
            outputs_deleted_at=outputs_deleted_at,
        )

    def _delete(self, job, auth_headers, mock_worker_service, mock_theme_service, cleanup_patch):
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = job
        mock_job_manager.delete_job.return_value = True
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'

        with patch('backend.api.routes.jobs.job_manager', mock_job_manager), \
             patch('backend.api.routes.jobs.worker_service', mock_worker_service), \
             patch('backend.api.routes.jobs.get_theme_service', return_value=mock_theme_service), \
             patch('backend.services.job_manager.JobManager', lambda *a, **k: mock_job_manager), \
             patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')), \
             patch('backend.api.routes.jobs._run_distribution_cleanup', cleanup_patch) as mock_cleanup:
            from backend.main import app
            client = TestClient(app)
            response = client.delete(f"/api/jobs/{job.job_id}", headers=auth_headers)
        return response, mock_job_manager, mock_cleanup

    def test_published_outputs_cleaned_up_before_deletion(self, mock_worker_service, mock_theme_service, auth_headers):
        """A job with a brand code and outputs never cleaned up gets full distribution
        cleanup (which owns the gated recycle) before the record is deleted."""
        job = self._job_with_brand("test-bc-123", outputs_deleted_at=None)
        cleanup = MagicMock(return_value={"youtube": {"status": "success"}})

        response, mock_job_manager, mock_cleanup = self._delete(
            job, auth_headers, mock_worker_service, mock_theme_service, cleanup)

        assert response.status_code == 200
        mock_cleanup.assert_called_once_with("test-bc-123", job)
        mock_job_manager.delete_job.assert_called_once()
        assert response.json()["distribution_cleanup"] == {"youtube": {"status": "success"}}

    def test_outputs_already_deleted_skips_cleanup_and_recycle(self, mock_worker_service, mock_theme_service, auth_headers):
        """A job whose outputs were already deleted (admin delete-outputs recycled the
        code then) must NOT be cleaned up or recycled again — re-recycling a code that
        a new job may have since claimed would create duplicates."""
        job = self._job_with_brand("test-bc-456", outputs_deleted_at=datetime.now(UTC))
        cleanup = MagicMock()

        response, mock_job_manager, mock_cleanup = self._delete(
            job, auth_headers, mock_worker_service, mock_theme_service, cleanup)

        assert response.status_code == 200
        mock_cleanup.assert_not_called()
        mock_job_manager.delete_job.assert_called_once()
        assert "distribution_cleanup" not in response.json()

    def test_delete_files_false_rejected_for_published_job(self, mock_worker_service, mock_theme_service, auth_headers):
        """Published outputs can't be preserved when their job record is deleted —
        the record holds the only file IDs/paths needed to ever clean them up."""
        job = self._job_with_brand("test-bc-df", outputs_deleted_at=None)
        cleanup = MagicMock()
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = job
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'

        with patch('backend.api.routes.jobs.job_manager', mock_job_manager), \
             patch('backend.api.routes.jobs.worker_service', mock_worker_service), \
             patch('backend.api.routes.jobs.get_theme_service', return_value=mock_theme_service), \
             patch('backend.services.job_manager.JobManager', lambda *a, **k: mock_job_manager), \
             patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')), \
             patch('backend.api.routes.jobs._run_distribution_cleanup', cleanup):
            from backend.main import app
            client = TestClient(app)
            response = client.delete("/api/jobs/test-bc-df?delete_files=false", headers=auth_headers)

        assert response.status_code == 400
        cleanup.assert_not_called()
        mock_job_manager.delete_job.assert_not_called()

    def test_incomplete_cleanup_aborts_deletion(self, mock_worker_service, mock_theme_service, auth_headers):
        """If any provider cleanup reports failed/partial/error, the job record must
        be kept so the cleanup can be retried with its IDs intact."""
        job = self._job_with_brand("test-bc-incomplete", outputs_deleted_at=None)
        cleanup = MagicMock(return_value={
            "youtube": {"status": "success"},
            "dropbox": {"status": "failed", "path": "/x"},
            "gdrive": {"status": "success"},
        })

        response, mock_job_manager, _mock_cleanup = self._delete(
            job, auth_headers, mock_worker_service, mock_theme_service, cleanup)

        assert response.status_code == 502
        assert "dropbox" in response.json()["detail"]
        mock_job_manager.delete_job.assert_not_called()

    def test_unexpected_cleanup_error_aborts_deletion(self, mock_worker_service, mock_theme_service, auth_headers):
        """If cleanup crashes outright (per-service failures are handled internally),
        the job record must be preserved — deleting it would orphan the outputs with
        no way to resolve their IDs later."""
        job = self._job_with_brand("test-bc-789", outputs_deleted_at=None)
        cleanup = MagicMock(side_effect=RuntimeError("boom"))

        response, mock_job_manager, _mock_cleanup = self._delete(
            job, auth_headers, mock_worker_service, mock_theme_service, cleanup)

        assert response.status_code == 500
        mock_job_manager.delete_job.assert_not_called()

    def test_no_brand_code_skips_recycling(self, client, mock_job_manager, auth_headers):
        """Test that deletion without brand code doesn't attempt recycling."""
        with patch('backend.services.brand_code_service.get_brand_code_service') as mock_get_svc:
            response = client.delete("/api/jobs/test123", headers=auth_headers)

            assert response.status_code == 200
            mock_get_svc.assert_not_called()


class TestCancelJob:
    """Tests for POST /api/jobs/{job_id}/cancel."""
    
    def test_cancel_job_returns_200(self, client, mock_job_manager, mock_job, auth_headers):
        """Test cancelling job returns 200."""
        mock_job_manager.cancel_job.return_value = mock_job
        response = client.post(
            "/api/jobs/test123/cancel",
            json={},
            headers=auth_headers
        )
        assert response.status_code == 200


class TestSubmitCorrections:
    """Tests for POST /api/jobs/{job_id}/corrections."""
    
    def test_submit_corrections_returns_200(self, client, mock_job_manager, mock_job, auth_headers):
        """Test submitting corrections returns 200."""
        # Job needs to be in AWAITING_REVIEW or IN_REVIEW status
        review_job = Job(
            job_id="test123",
            status=JobStatus.IN_REVIEW,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test"
        )
        mock_job_manager.get_job.return_value = review_job
        
        response = client.post(
            "/api/jobs/test123/corrections",
            json={
                "corrections": {
                    "lines": [],
                    "metadata": {"source": "test"}
                }
            },
            headers=auth_headers
        )
        # Should be 200 or validation error
        assert response.status_code in [200, 400, 422]


class TestSelectInstrumental:
    """Tests for POST /api/jobs/{job_id}/select-instrumental."""
    
    def test_select_instrumental_requires_selection_field(self, client, mock_job_manager, mock_job, auth_headers):
        """Test instrumental selection requires selection field."""
        response = client.post(
            "/api/jobs/test123/select-instrumental",
            json={},  # Missing selection field
            headers=auth_headers
        )
        # Missing field should cause validation error
        assert response.status_code == 422


class TestStartReview:
    """Tests for POST /api/jobs/{job_id}/start-review."""

    def test_start_review_endpoint_exists(self, client, mock_job_manager, mock_job, auth_headers):
        """Test start review endpoint exists."""
        response = client.post("/api/jobs/test123/start-review", headers=auth_headers)
        # Should not be 404 or 405
        assert response.status_code not in [404, 405]


class TestSubmitEditLog:
    """Tests for POST /api/jobs/{job_id}/edit-log."""

    def test_submit_edit_log_returns_200(self, client, mock_job_manager, mock_job, auth_headers):
        """Test submitting a valid edit log returns 200."""
        edit_log = {
            "session_id": "sess-abc",
            "job_id": "test123",
            "audio_hash": "hash123",
            "started_at": "2026-03-01T00:00:00.000Z",
            "entries": [
                {
                    "id": "e1",
                    "timestamp": "2026-03-01T00:01:00.000Z",
                    "operation": "word_change",
                    "segment_id": "s1",
                    "segment_index": 0,
                    "word_ids_before": ["w1"],
                    "word_ids_after": ["w1"],
                    "text_before": "helo",
                    "text_after": "hello",
                    "feedback": {"reason": "misheard_word", "timestamp": "2026-03-01T00:01:05.000Z"}
                }
            ]
        }
        with patch('backend.api.routes.jobs.StorageService') as mock_storage_cls:
            mock_storage = MagicMock()
            mock_storage_cls.return_value = mock_storage
            response = client.post("/api/jobs/test123/edit-log", json=edit_log, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["entries_count"] == 1
        assert data["feedback_count"] == 1

    def test_submit_edit_log_stores_to_gcs(self, client, mock_job_manager, mock_job, auth_headers):
        """Test edit log is stored to GCS with correct path."""
        edit_log = {
            "session_id": "sess-xyz",
            "job_id": "test123",
            "audio_hash": "hash123",
            "started_at": "2026-03-01T00:00:00.000Z",
            "entries": []
        }
        with patch('backend.api.routes.jobs.StorageService') as mock_storage_cls:
            mock_storage = MagicMock()
            mock_storage_cls.return_value = mock_storage
            client.post("/api/jobs/test123/edit-log", json=edit_log, headers=auth_headers)
            mock_storage.upload_json.assert_called_once_with(
                "jobs/test123/lyrics/edit_log_sess-xyz.json",
                edit_log
            )

    def test_submit_edit_log_updates_firestore(self, client, mock_job_manager, mock_job, auth_headers):
        """Test edit log updates Firestore state_data."""
        edit_log = {
            "session_id": "sess-123",
            "job_id": "test123",
            "audio_hash": "h",
            "started_at": "2026-03-01T00:00:00.000Z",
            "entries": []
        }
        with patch('backend.api.routes.jobs.StorageService') as mock_storage_cls:
            mock_storage_cls.return_value = MagicMock()
            client.post("/api/jobs/test123/edit-log", json=edit_log, headers=auth_headers)
        mock_job_manager.update_state_data.assert_any_call(
            "test123", "last_edit_log_path", "jobs/test123/lyrics/edit_log_sess-123.json"
        )
        mock_job_manager.update_state_data.assert_any_call(
            "test123", "last_edit_log_session", "sess-123"
        )

    def test_submit_edit_log_nonexistent_job_returns_404(self, mock_worker_service, auth_headers):
        """Test submitting to nonexistent job returns 404."""
        mock_jm = MagicMock()
        mock_jm.get_job.return_value = None
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'

        def mock_jm_factory(*args, **kwargs):
            return mock_jm

        with patch('backend.api.routes.jobs.job_manager', mock_jm), \
             patch('backend.api.routes.jobs.worker_service', mock_worker_service), \
             patch('backend.services.job_manager.JobManager', mock_jm_factory), \
             patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')):
            from backend.main import app
            test_client = TestClient(app)
            response = test_client.post(
                "/api/jobs/nonexistent/edit-log",
                json={"session_id": "s", "entries": []},
                headers=auth_headers
            )
            assert response.status_code == 404

    def test_submit_edit_log_counts_feedback_correctly(self, client, mock_job_manager, mock_job, auth_headers):
        """Test feedback count excludes no_response entries."""
        edit_log = {
            "session_id": "sess-1",
            "job_id": "test123",
            "audio_hash": "h",
            "started_at": "2026-03-01T00:00:00.000Z",
            "entries": [
                {"id": "e1", "feedback": {"reason": "misheard_word"}},
                {"id": "e2", "feedback": {"reason": "no_response"}},
                {"id": "e3", "feedback": None},
                {"id": "e4", "feedback": {"reason": "wrong_lyrics"}},
            ]
        }
        with patch('backend.api.routes.jobs.StorageService') as mock_storage_cls:
            mock_storage_cls.return_value = MagicMock()
            response = client.post("/api/jobs/test123/edit-log", json=edit_log, headers=auth_headers)
        data = response.json()
        assert data["entries_count"] == 4
        assert data["feedback_count"] == 2  # misheard_word + wrong_lyrics

