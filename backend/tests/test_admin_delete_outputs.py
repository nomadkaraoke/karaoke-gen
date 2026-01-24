"""
Unit tests for admin delete job outputs endpoint.

Tests the POST /api/admin/jobs/{job_id}/delete-outputs endpoint that allows admins
to delete distributed outputs (YouTube, Dropbox, Google Drive) while preserving
the job record.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from datetime import datetime

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


@pytest.fixture
def mock_complete_job():
    """Create a mock job in COMPLETE status with distribution data."""
    job = Mock(spec=Job)
    job.job_id = "test-job-123"
    job.user_email = "user@example.com"
    job.artist = "Test Artist"
    job.title = "Test Title"
    job.status = "complete"
    job.dropbox_path = "/Karaoke/Organized"
    job.outputs_deleted_at = None
    job.outputs_deleted_by = None
    job.state_data = {
        "youtube_url": "https://youtu.be/abc123",
        "brand_code": "NOMAD-1234",
        "dropbox_link": "https://dropbox.com/...",
        "gdrive_files": {"mp4": "file_id_1", "mp4_720p": "file_id_2"},
    }
    job.timeline = []
    return job


@pytest.fixture
def mock_job_no_outputs():
    """Create a mock job in COMPLETE status without distribution data."""
    job = Mock(spec=Job)
    job.job_id = "test-job-456"
    job.user_email = "user@example.com"
    job.artist = "Test Artist"
    job.title = "Test Title"
    job.status = "complete"
    job.dropbox_path = None
    job.outputs_deleted_at = None
    job.outputs_deleted_by = None
    job.state_data = {}
    job.timeline = []
    return job


class TestDeleteOutputsSuccess:
    """Tests for successful output deletion."""

    def test_delete_outputs_success(self, client, mock_complete_job):
        """Test successfully deleting outputs from a complete job."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            # Mock Firestore
            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["job_id"] == "test-job-123"
            assert "outputs_deleted_at" in data

    def test_delete_outputs_clears_state_data_keys(self, client, mock_complete_job):
        """Test that output-related state_data keys are listed as cleared."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            # Mock Firestore
            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200
            data = response.json()
            # Should list cleared keys
            assert "youtube_url" in data["cleared_state_data"]
            assert "brand_code" in data["cleared_state_data"]
            assert "dropbox_link" in data["cleared_state_data"]
            assert "gdrive_files" in data["cleared_state_data"]

    def test_delete_outputs_job_without_distribution(self, client, mock_job_no_outputs):
        """Test deleting outputs from job that has no distribution data."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job_no_outputs
            mock_jm_class.return_value = mock_jm

            # Mock Firestore
            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-456/delete-outputs")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            # All services should be skipped
            assert data["deleted_services"]["youtube"]["status"] == "skipped"
            assert data["deleted_services"]["dropbox"]["status"] == "skipped"
            assert data["deleted_services"]["gdrive"]["status"] == "skipped"
            # No keys to clear
            assert data["cleared_state_data"] == []


class TestDeleteOutputsValidation:
    """Tests for validation on delete outputs endpoint."""

    def test_rejects_non_terminal_status(self, client, mock_complete_job):
        """Test that jobs not in terminal states are rejected."""
        mock_complete_job.status = "encoding"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 400
            assert "terminal" in response.json()["detail"].lower()

    def test_rejects_awaiting_review_status(self, client, mock_complete_job):
        """Test that AWAITING_REVIEW is rejected (not terminal)."""
        mock_complete_job.status = "awaiting_review"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 400

    def test_accepts_prep_complete_status(self, client, mock_complete_job):
        """Test that prep_complete is accepted as terminal."""
        mock_complete_job.status = "prep_complete"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200

    def test_accepts_failed_status(self, client, mock_complete_job):
        """Test that failed is accepted as terminal."""
        mock_complete_job.status = "failed"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200

    def test_rejects_already_deleted(self, client, mock_complete_job):
        """Test that already-deleted outputs cannot be deleted again."""
        mock_complete_job.outputs_deleted_at = datetime(2026, 1, 9, 12, 0, 0)

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 400
            assert "already deleted" in response.json()["detail"].lower()

    def test_returns_404_for_missing_job(self, client):
        """Test 404 when job doesn't exist."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = None
            mock_jm_class.return_value = mock_jm

            response = client.post("/api/admin/jobs/nonexistent/delete-outputs")

            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()


class TestDeleteOutputsServices:
    """Tests for service deletion behavior."""

    def test_handles_missing_youtube(self, client, mock_complete_job):
        """Test handling when job has no YouTube URL."""
        mock_complete_job.state_data = {"brand_code": "NOMAD-1234"}

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200
            data = response.json()
            assert data["deleted_services"]["youtube"]["status"] == "skipped"

    def test_handles_missing_dropbox_path(self, client, mock_complete_job):
        """Test handling when job has no dropbox_path."""
        mock_complete_job.dropbox_path = None
        mock_complete_job.state_data = {"youtube_url": "https://youtu.be/abc123"}

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200
            data = response.json()
            assert data["deleted_services"]["dropbox"]["status"] == "skipped"


class TestDeleteOutputsLogging:
    """Tests for logging on delete outputs endpoint."""

    def test_logs_admin_action(self, client, mock_complete_job):
        """Test that admin delete action is logged."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service, \
             patch('backend.api.routes.admin.logger') as mock_logger:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200
            mock_logger.info.assert_called()
            log_message = mock_logger.info.call_args[0][0]
            assert "admin" in log_message.lower() or "deleted" in log_message.lower()


class TestDeleteOutputsAuthorization:
    """Tests for authorization on the delete outputs endpoint."""

    def test_requires_admin_access(self, client, mock_complete_job):
        """Test that non-admin users cannot access the endpoint."""
        original_override = app.dependency_overrides.get(require_admin)

        def get_non_admin():
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = get_non_admin

        try:
            response = client.post(
                "/api/admin/jobs/test-job-123/delete-outputs",
                headers={"Authorization": "Bearer user-token"}
            )
            assert response.status_code == 403
        finally:
            if original_override:
                app.dependency_overrides[require_admin] = original_override
            else:
                app.dependency_overrides[require_admin] = get_mock_admin


class TestDeleteOutputsGCSFinals:
    """Tests for GCS finals folder deletion."""

    def test_delete_outputs_deletes_gcs_finals(self, client, mock_complete_job):
        """Test that GCS finals folder is deleted when outputs are deleted."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service, \
             patch('backend.services.storage_service.StorageService') as mock_storage_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            # Mock storage service
            mock_storage = Mock()
            mock_storage.delete_folder.return_value = 4  # 4 files deleted
            mock_storage_class.return_value = mock_storage

            # Mock Firestore
            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200
            data = response.json()

            # Verify GCS finals was deleted
            mock_storage.delete_folder.assert_called_once_with("jobs/test-job-123/finals/")
            assert "gcs_finals" in data["deleted_services"]
            assert data["deleted_services"]["gcs_finals"]["status"] == "success"
            assert data["deleted_services"]["gcs_finals"]["deleted_count"] == 4
            assert data["deleted_services"]["gcs_finals"]["path"] == "jobs/test-job-123/finals/"

    def test_delete_outputs_gcs_success_with_zero_files(self, client, mock_complete_job):
        """Test that GCS deletion reports success even when no files exist."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service, \
             patch('backend.services.storage_service.StorageService') as mock_storage_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            # Mock storage service - no files to delete
            mock_storage = Mock()
            mock_storage.delete_folder.return_value = 0
            mock_storage_class.return_value = mock_storage

            # Mock Firestore
            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            assert response.status_code == 200
            data = response.json()

            # Still success even with zero files deleted
            assert data["deleted_services"]["gcs_finals"]["status"] == "success"
            assert data["deleted_services"]["gcs_finals"]["deleted_count"] == 0

    def test_delete_outputs_continues_if_gcs_fails(self, client, mock_complete_job):
        """Test that other deletions continue even if GCS deletion fails."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service, \
             patch('backend.services.storage_service.StorageService') as mock_storage_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            # Mock storage service to fail
            mock_storage = Mock()
            mock_storage.delete_folder.side_effect = Exception("GCS error")
            mock_storage_class.return_value = mock_storage

            # Mock Firestore
            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/test-job-123/delete-outputs")

            # Should still succeed (partial success) with error for GCS
            assert response.status_code == 200
            data = response.json()
            assert "gcs_finals" in data["deleted_services"]
            assert data["deleted_services"]["gcs_finals"]["status"] == "error"
            assert "GCS error" in data["deleted_services"]["gcs_finals"]["error"]

    def test_delete_outputs_gcs_uses_correct_path(self, client, mock_complete_job):
        """Test that GCS deletion uses the correct path format."""
        mock_complete_job.job_id = "abc-123-xyz"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_user_service, \
             patch('backend.services.storage_service.StorageService') as mock_storage_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_complete_job
            mock_jm_class.return_value = mock_jm

            mock_storage = Mock()
            mock_storage.delete_folder.return_value = 2
            mock_storage_class.return_value = mock_storage

            mock_db = Mock()
            mock_job_ref = Mock()
            mock_db.collection.return_value.document.return_value = mock_job_ref
            mock_user_service.return_value.db = mock_db

            response = client.post("/api/admin/jobs/abc-123-xyz/delete-outputs")

            assert response.status_code == 200
            # Verify path format is jobs/{job_id}/finals/
            mock_storage.delete_folder.assert_called_once_with("jobs/abc-123-xyz/finals/")
