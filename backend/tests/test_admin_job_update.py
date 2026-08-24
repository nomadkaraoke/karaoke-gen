"""
Unit tests for admin job update endpoint.

Tests the PATCH /api/admin/jobs/{job_id} endpoint that allows admins
to update editable job fields.
"""
import pytest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

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
def mock_job():
    """Create a mock job for testing."""
    job = Mock(spec=Job)
    job.job_id = "test-job-123"
    job.user_email = "user@example.com"
    job.artist = "Original Artist"
    job.title = "Original Title"
    job.status = JobStatus.AWAITING_REVIEW
    job.theme_id = "nomad"
    job.enable_cdg = False
    job.enable_txt = False
    job.enable_youtube_upload = True
    job.customer_email = None
    job.customer_notes = None
    job.brand_prefix = None
    job.non_interactive = False
    job.prep_only = False
    job.discord_webhook_url = None
    job.youtube_description = None
    job.youtube_description_template = None
    job.file_urls = {}
    job.state_data = {}
    job.created_at = "2026-01-09T10:00:00Z"
    job.updated_at = "2026-01-09T10:30:00Z"
    return job


class TestUpdateJob:
    """Tests for PATCH /api/admin/jobs/{job_id} endpoint."""

    def test_update_artist_field(self, client, mock_job):
        """Test updating the artist field."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"artist": "New Artist"},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["job_id"] == "test-job-123"
            assert "artist" in data["updated_fields"]

            # Verify update_job was called with correct args
            mock_jm.update_job.assert_called_once()
            call_args = mock_jm.update_job.call_args
            assert call_args[0][0] == "test-job-123"
            assert call_args[0][1]["artist"] == "New Artist"

    def test_update_multiple_fields(self, client, mock_job):
        """Test updating multiple fields at once."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={
                    "artist": "New Artist",
                    "title": "New Title",
                    "theme_id": "default",
                    "enable_cdg": True,
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert len(data["updated_fields"]) == 4
            assert "artist" in data["updated_fields"]
            assert "title" in data["updated_fields"]
            assert "theme_id" in data["updated_fields"]
            assert "enable_cdg" in data["updated_fields"]

    def test_update_user_email(self, client, mock_job):
        """Test updating user_email field."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_get_us:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm
            mock_get_us.return_value.get_user.return_value = Mock()  # user exists

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"user_email": "newuser@example.com"},
            )

            assert response.status_code == 200
            call_args = mock_jm.update_job.call_args
            assert call_args[0][1]["user_email"] == "newuser@example.com"

    def test_update_boolean_fields(self, client, mock_job):
        """Test updating boolean fields."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={
                    "enable_cdg": True,
                    "enable_txt": True,
                    "enable_youtube_upload": False,
                    "non_interactive": True,
                    "prep_only": True,
                },
            )

            assert response.status_code == 200
            call_args = mock_jm.update_job.call_args
            assert call_args[0][1]["enable_cdg"] is True
            assert call_args[0][1]["enable_txt"] is True
            assert call_args[0][1]["enable_youtube_upload"] is False

    def test_rejects_non_editable_job_id(self, client, mock_job):
        """Test that job_id cannot be updated."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"job_id": "new-job-id"},
            )

            assert response.status_code == 400
            assert "not editable" in response.json()["detail"].lower()

    def test_rejects_non_editable_created_at(self, client, mock_job):
        """Test that created_at cannot be updated."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"created_at": "2025-01-01T00:00:00Z"},
            )

            assert response.status_code == 400
            assert "not editable" in response.json()["detail"].lower()

    def test_rejects_status_update(self, client, mock_job):
        """Test that status cannot be updated via PATCH (use reset endpoint)."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"status": "complete"},
            )

            assert response.status_code == 400
            assert "not editable" in response.json()["detail"].lower()

    def test_rejects_state_data_update(self, client, mock_job):
        """Test that state_data cannot be updated directly."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"state_data": {"foo": "bar"}},
            )

            assert response.status_code == 400
            assert "not editable" in response.json()["detail"].lower()

    def test_returns_404_when_job_not_found(self, client):
        """Test 404 when job doesn't exist."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = None
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/nonexistent-job",
                json={"artist": "New Artist"},
            )

            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()

    def test_returns_400_for_empty_update(self, client, mock_job):
        """Test 400 when no valid fields are provided."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={},
            )

            assert response.status_code == 400
            assert "no valid" in response.json()["detail"].lower()

    def test_logs_admin_changes(self, client, mock_job):
        """Test that admin changes are logged."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.logger') as mock_logger:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"artist": "New Artist"},
            )

            assert response.status_code == 200
            # Verify logging was called
            mock_logger.info.assert_called()
            log_message = mock_logger.info.call_args[0][0]
            assert "admin@example.com" in log_message.lower() or "admin" in log_message.lower()

    def test_update_customer_fields(self, client, mock_job):
        """Test updating made-for-you customer fields."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={
                    "customer_email": "customer@example.com",
                    "customer_notes": "Special request: add extra countdown",
                },
            )

            assert response.status_code == 200
            call_args = mock_jm.update_job.call_args
            assert call_args[0][1]["customer_email"] == "customer@example.com"
            assert call_args[0][1]["customer_notes"] == "Special request: add extra countdown"


class TestUpdateJobAuthorization:
    """Tests for authorization on the update endpoint."""

    def test_requires_admin_access(self, client, mock_job):
        """Test that non-admin users cannot access the endpoint."""
        original_override = app.dependency_overrides.get(require_admin)

        def get_non_admin():
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = get_non_admin

        try:
            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"artist": "New Artist"},
                headers={"Authorization": "Bearer user-token"}
            )
            assert response.status_code == 403
        finally:
            if original_override:
                app.dependency_overrides[require_admin] = original_override
            else:
                app.dependency_overrides[require_admin] = get_mock_admin


class TestUpdateJobUserEmailAutoCreate:
    """Tests for auto-creating a user account when user_email is reassigned."""

    def _patch(self, mock_job, existing_user):
        """Return patched JobManager + user_service context managers."""
        jm_patch = patch('backend.api.routes.admin.JobManager')
        us_patch = patch('backend.api.routes.admin.get_user_service')
        return jm_patch, us_patch

    def test_creates_user_when_email_unknown(self, client, mock_job):
        """Reassigning to an email with no account auto-creates the account."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_get_us:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            mock_us = Mock()
            mock_us.get_user.return_value = None  # no account exists
            mock_get_us.return_value = mock_us

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"user_email": "brandnew@example.com"},
            )

            assert response.status_code == 200
            mock_us.get_or_create_user.assert_called_once_with("brandnew@example.com")
            assert "Created new user account for brandnew@example.com" in response.json()["message"]

    def test_does_not_create_when_user_exists(self, client, mock_job):
        """Reassigning to an existing user's email does not create anything."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_get_us:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            mock_us = Mock()
            mock_us.get_user.return_value = Mock()  # account exists
            mock_get_us.return_value = mock_us

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"user_email": "existing@example.com"},
            )

            assert response.status_code == 200
            mock_us.get_or_create_user.assert_not_called()
            assert "Created new user account" not in response.json()["message"]

    def test_email_is_normalized_to_lowercase(self, client, mock_job):
        """Mixed-case emails are lowercased before saving to the job."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_get_us:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            mock_us = Mock()
            mock_us.get_user.return_value = None
            mock_get_us.return_value = mock_us

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"user_email": "  Schreiber.Uwe@GoogleMail.com "},
            )

            assert response.status_code == 200
            call_args = mock_jm.update_job.call_args
            assert call_args[0][1]["user_email"] == "schreiber.uwe@googlemail.com"
            mock_us.get_or_create_user.assert_called_once_with("schreiber.uwe@googlemail.com")

    def test_other_field_updates_skip_user_lookup(self, client, mock_job):
        """Updating non-email fields never touches the user service."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.get_user_service') as mock_get_us:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.patch(
                "/api/admin/jobs/test-job-123",
                json={"artist": "Someone Else"},
            )

            assert response.status_code == 200
            mock_get_us.assert_not_called()

    def test_blank_user_email_rejected(self, client, mock_job):
        """Whitespace-only or non-email values for user_email return 400."""
        for bad_value in ("   ", "not-an-email"):
            with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
                 patch('backend.api.routes.admin.get_user_service') as mock_get_us:
                mock_jm = Mock()
                mock_jm.get_job.return_value = mock_job
                mock_jm_class.return_value = mock_jm

                response = client.patch(
                    "/api/admin/jobs/test-job-123",
                    json={"user_email": bad_value},
                )

                assert response.status_code == 400
                assert "valid email" in response.json()["detail"]
                mock_jm.update_job.assert_not_called()
