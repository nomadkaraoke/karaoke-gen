"""
Unit tests for admin email endpoints.

Tests the completion message and send email API endpoints.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.api.routes.admin import router
from backend.api.dependencies import require_admin
from backend.models.job import Job, JobStatus


# Create a test app with the admin router
# The router already has prefix="/admin", so we add /api prefix
app = FastAPI()
app.include_router(router, prefix="/api")


def get_mock_admin():
    """Override for require_admin dependency."""
    return ("admin@example.com", "admin", 1)


# Override the require_admin dependency
app.dependency_overrides[require_admin] = get_mock_admin


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_job():
    """Create a mock job."""
    job = Mock(spec=Job)
    job.job_id = "test-job-123"
    job.user_email = "user@example.com"
    job.artist = "Test Artist"
    job.title = "Test Song"
    job.status = JobStatus.COMPLETE
    job.audio_hash = "hash123"
    job.review_token = "review123"
    job.instrumental_token = "inst123"
    job.state_data = {
        "youtube_url": "https://youtube.com/watch?v=test123",
        "dropbox_link": "https://dropbox.com/folder/test",
        "brand_code": "NOMAD-1234",
    }
    return job


class TestGetCompletionMessage:
    """Tests for GET /api/admin/jobs/{job_id}/completion-message endpoint."""

    def test_returns_completion_message(self, client, mock_job):
        """Test successful completion message retrieval."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns:

            # Setup mocks
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.get_completion_message.return_value = "Your video is ready!"
            mock_get_ns.return_value = mock_ns

            response = client.get(
                "/api/admin/jobs/test-job-123/completion-message",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "test-job-123"
            assert data["message"] == "Your video is ready!"
            # Subject format: "NOMAD-1234: Test Artist - Test Song (Your karaoke video is ready!)"
            assert data["subject"] == "NOMAD-1234: Test Artist - Test Song (Your karaoke video is ready!)"
            assert data["youtube_url"] == "https://youtube.com/watch?v=test123"
            assert data["dropbox_url"] == "https://dropbox.com/folder/test"

    def test_returns_404_when_job_not_found(self, client):
        """Test 404 when job doesn't exist."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = None
            mock_jm_class.return_value = mock_jm

            response = client.get(
                "/api/admin/jobs/nonexistent-job/completion-message",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 404

    def test_handles_none_state_data(self, client, mock_job):
        """Test handling of job with None state_data."""
        mock_job.state_data = None

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns:

            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.get_completion_message.return_value = "Your video is ready!"
            mock_get_ns.return_value = mock_ns

            response = client.get(
                "/api/admin/jobs/test-job-123/completion-message",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["youtube_url"] is None
            assert data["dropbox_url"] is None

    def test_default_subject_without_song_info(self, client, mock_job):
        """Test default subject when no artist/title."""
        mock_job.artist = None
        mock_job.title = None

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns:

            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.get_completion_message.return_value = "Message"
            mock_get_ns.return_value = mock_ns

            response = client.get(
                "/api/admin/jobs/test-job-123/completion-message",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["subject"] == "Your karaoke video is ready!"


class TestSendCompletionEmail:
    """Tests for POST /api/admin/jobs/{job_id}/send-completion-email endpoint."""

    def test_sends_email_successfully(self, client, mock_job):
        """Test successful email sending."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns, \
             patch('backend.services.email_service.get_email_service') as mock_get_es:

            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.get_completion_message.return_value = "Your video is ready!"
            mock_get_ns.return_value = mock_ns

            mock_es = Mock()
            mock_es.send_job_completion.return_value = True
            mock_get_es.return_value = mock_es

            response = client.post(
                "/api/admin/jobs/test-job-123/send-completion-email",
                json={"to_email": "customer@example.com", "cc_admin": True},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["job_id"] == "test-job-123"
            assert data["to_email"] == "customer@example.com"

            # Verify email was sent with correct params
            mock_es.send_job_completion.assert_called_once_with(
                to_email="customer@example.com",
                message_content="Your video is ready!",
                artist="Test Artist",
                title="Test Song",
                brand_code="NOMAD-1234",
                cc_admin=True,
            )

    def test_sends_email_without_cc(self, client, mock_job):
        """Test email sending without CC."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns, \
             patch('backend.services.email_service.get_email_service') as mock_get_es:

            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.get_completion_message.return_value = "Message"
            mock_get_ns.return_value = mock_ns

            mock_es = Mock()
            mock_es.send_job_completion.return_value = True
            mock_get_es.return_value = mock_es

            response = client.post(
                "/api/admin/jobs/test-job-123/send-completion-email",
                json={"to_email": "customer@example.com", "cc_admin": False},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200

            # Verify CC was not included
            call_kwargs = mock_es.send_job_completion.call_args.kwargs
            assert call_kwargs["cc_admin"] is False

    def test_returns_404_when_job_not_found(self, client):
        """Test 404 when job doesn't exist."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = None
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/nonexistent-job/send-completion-email",
                json={"to_email": "customer@example.com"},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 404

    def test_returns_500_when_email_fails(self, client, mock_job):
        """Test 500 when email sending fails."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns, \
             patch('backend.services.email_service.get_email_service') as mock_get_es:

            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.get_completion_message.return_value = "Message"
            mock_get_ns.return_value = mock_ns

            mock_es = Mock()
            mock_es.send_job_completion.return_value = False  # Email failed
            mock_get_es.return_value = mock_es

            response = client.post(
                "/api/admin/jobs/test-job-123/send-completion-email",
                json={"to_email": "customer@example.com"},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 500
            assert "Failed to send email" in response.json()["detail"]

    def test_handles_none_state_data(self, client, mock_job):
        """Test handling of job with None state_data."""
        mock_job.state_data = None

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns, \
             patch('backend.services.email_service.get_email_service') as mock_get_es:

            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.get_completion_message.return_value = "Message"
            mock_get_ns.return_value = mock_ns

            mock_es = Mock()
            mock_es.send_job_completion.return_value = True
            mock_get_es.return_value = mock_es

            response = client.post(
                "/api/admin/jobs/test-job-123/send-completion-email",
                json={"to_email": "customer@example.com"},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200

    def test_accepts_any_email_string(self, client, mock_job):
        """Test that any string is currently accepted as email (no validation).

        Documents current behavior: FastAPI/Pydantic doesn't validate email
        format by default. If validation is added later, this test should
        be updated to expect 422 for invalid emails.
        """
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns, \
             patch('backend.services.email_service.get_email_service') as mock_get_es:

            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.get_completion_message.return_value = "Message"
            mock_get_ns.return_value = mock_ns

            mock_es = Mock()
            mock_es.send_job_completion.return_value = True
            mock_get_es.return_value = mock_es

            response = client.post(
                "/api/admin/jobs/test-job-123/send-completion-email",
                json={"to_email": "invalid-email"},  # Not a valid email format
                headers={"Authorization": "Bearer admin-token"}
            )

            # Currently accepts any string - endpoint succeeds
            assert response.status_code == 200
            # Verify the invalid email was passed through
            mock_es.send_job_completion.assert_called_once()
            call_kwargs = mock_es.send_job_completion.call_args.kwargs
            assert call_kwargs["to_email"] == "invalid-email"


class TestIdleReminderEndpoint:
    """Tests for the internal idle reminder endpoint."""

    def test_sends_reminder_when_still_idle(self, mock_job):
        """Test that reminder is sent when user is still idle."""
        from backend.api.routes.internal import router as internal_router
        from backend.api.dependencies import require_admin

        # Create test app for internal router
        # The router already has prefix="/internal", so we add /api prefix
        test_app = FastAPI()
        test_app.include_router(internal_router, prefix="/api")
        test_app.dependency_overrides[require_admin] = get_mock_admin

        mock_job.status = JobStatus.AWAITING_REVIEW.value  # Use .value for string comparison
        mock_job.state_data = {
            "blocking_state_entered_at": "2024-01-01T00:00:00",
            "blocking_action_type": "lyrics",
            "reminder_sent": False,
        }

        with patch('backend.api.routes.internal.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns:

            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.firestore = Mock()  # Mock firestore for update_job call
            mock_jm_class.return_value = mock_jm

            mock_ns = Mock()
            mock_ns.send_action_reminder_email = AsyncMock(return_value=True)
            mock_get_ns.return_value = mock_ns

            test_client = TestClient(test_app)
            response = test_client.post(
                "/api/internal/jobs/test-job-123/check-idle-reminder",
                headers={"Authorization": "Bearer admin-token"}
            )

            # Should succeed and return "sent" status
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "sent"
            assert data["job_id"] == "test-job-123"

    def test_skips_reminder_when_already_sent(self, mock_job):
        """Test that reminder is skipped if already sent."""
        from backend.api.routes.internal import router as internal_router
        from backend.api.dependencies import require_admin

        # Create test app for internal router
        # The router already has prefix="/internal", so we add /api prefix
        test_app = FastAPI()
        test_app.include_router(internal_router, prefix="/api")
        test_app.dependency_overrides[require_admin] = get_mock_admin

        mock_job.status = JobStatus.AWAITING_REVIEW.value  # Use .value for string comparison
        mock_job.state_data = {
            "blocking_state_entered_at": "2024-01-01T00:00:00",
            "blocking_action_type": "lyrics",
            "reminder_sent": True,  # Already sent
        }

        with patch('backend.api.routes.internal.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            test_client = TestClient(test_app)
            response = test_client.post(
                "/api/internal/jobs/test-job-123/check-idle-reminder",
                headers={"Authorization": "Bearer admin-token"}
            )

            # Should succeed with "already_sent" status
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "already_sent"
            assert data["job_id"] == "test-job-123"
