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
    # Distribution settings (Mock(spec=Job) attrs would otherwise be truthy Mocks)
    job.is_private = False
    job.tenant_id = None
    job.dropbox_path = None
    job.brand_prefix = None
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


class TestSendCompletionEmailDeliveryGuard:
    """NOMAD-1800: refuse to send a delivery email without its Dropbox link."""

    def _patches(self, mock_job):
        mock_jm = Mock()
        mock_jm.get_job.return_value = mock_job
        mock_ns = Mock()
        mock_ns.get_completion_message.return_value = "Message"
        mock_es = Mock()
        mock_es.send_job_completion.return_value = True
        return mock_jm, mock_ns, mock_es

    def _post(self, client, mock_job, body):
        mock_jm, mock_ns, mock_es = self._patches(mock_job)
        with patch('backend.api.routes.admin.JobManager', return_value=mock_jm), \
             patch('backend.services.job_notification_service.get_job_notification_service', return_value=mock_ns), \
             patch('backend.services.email_service.get_email_service', return_value=mock_es):
            response = client.post(
                "/api/admin/jobs/test-job-123/send-completion-email",
                json=body,
                headers={"Authorization": "Bearer admin-token"},
            )
        return response, mock_jm, mock_es

    def _dropbox_job(self, mock_job, **state):
        mock_job.dropbox_path = "/Karaoke/Tracks-Organized"
        mock_job.brand_prefix = "NOMAD"
        mock_job.state_data = {"brand_code": "NOMAD-1800", **state}
        return mock_job

    def test_refuses_when_dropbox_link_missing(self, client, mock_job):
        self._dropbox_job(mock_job, distribution_warnings=[
            "Dropbox upload failed: WriteError('insufficient_space')"
        ])
        response, _, mock_es = self._post(client, mock_job, {"to_email": "c@example.com"})

        assert response.status_code == 409
        assert "Dropbox is FULL" in response.json()["detail"]
        mock_es.send_job_completion.assert_not_called()

    def test_override_sends_and_clears_held_marker(self, client, mock_job):
        self._dropbox_job(mock_job, completion_email_held={"reason_code": "dropbox_full"})
        response, mock_jm, mock_es = self._post(
            client, mock_job, {"to_email": "c@example.com", "allow_missing_dropbox_link": True}
        )

        assert response.status_code == 200
        mock_es.send_job_completion.assert_called_once()
        mock_jm.update_job.assert_called_once()
        assert "state_data.completion_email_held" in mock_jm.update_job.call_args[0][1]

    def test_sends_and_clears_marker_once_link_is_fixed(self, client, mock_job):
        self._dropbox_job(mock_job, dropbox_link="https://dropbox.com/fixed",
                          completion_email_held={"reason_code": "dropbox_full"})
        response, mock_jm, mock_es = self._post(client, mock_job, {"to_email": "c@example.com"})

        assert response.status_code == 200
        mock_es.send_job_completion.assert_called_once()
        assert "state_data.completion_email_held" in mock_jm.update_job.call_args[0][1]

    def test_job_without_dropbox_delivery_sends_normally(self, client, mock_job):
        mock_job.state_data = {"brand_code": "TEN-0001"}
        response, mock_jm, mock_es = self._post(client, mock_job, {"to_email": "c@example.com"})

        assert response.status_code == 200
        mock_es.send_job_completion.assert_called_once()
        mock_jm.update_job.assert_not_called()


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

    def test_forwards_request_metadata_for_kjbox_login_links(self, mock_job):
        """The idle review email needs the job's request_metadata so kjbox jobs
        (client_id "kjbox") get a one-click sign-in link instead of a bare URL."""
        from backend.api.routes.internal import router as internal_router
        from backend.api.dependencies import require_admin

        test_app = FastAPI()
        test_app.include_router(internal_router, prefix="/api")
        test_app.dependency_overrides[require_admin] = get_mock_admin

        mock_job.status = JobStatus.AWAITING_REVIEW.value
        mock_job.request_metadata = {"client_id": "kjbox"}
        mock_job.state_data = {
            "blocking_state_entered_at": "2024-01-01T00:00:00",
            "blocking_action_type": "lyrics",
            "reminder_sent": False,
        }

        with patch('backend.api.routes.internal.JobManager') as mock_jm_class, \
             patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_ns:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.firestore = Mock()
            mock_jm_class.return_value = mock_jm
            mock_ns = Mock()
            mock_ns.send_action_reminder_email = AsyncMock(return_value=True)
            mock_get_ns.return_value = mock_ns

            response = TestClient(test_app).post(
                "/api/internal/jobs/test-job-123/check-idle-reminder",
                headers={"Authorization": "Bearer admin-token"}
            )

        assert response.status_code == 200
        kwargs = mock_ns.send_action_reminder_email.call_args.kwargs
        assert kwargs["request_metadata"] == {"client_id": "kjbox"}

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
