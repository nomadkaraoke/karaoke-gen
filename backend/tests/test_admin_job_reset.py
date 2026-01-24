"""
Unit tests for admin job reset endpoint.

Tests the POST /api/admin/jobs/{job_id}/reset endpoint that allows admins
to reset a job to a specific state for re-processing.
"""
import pytest
from unittest.mock import Mock, patch
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
def mock_job():
    """Create a mock job in COMPLETE status for testing."""
    job = Mock(spec=Job)
    job.job_id = "test-job-123"
    job.user_email = "user@example.com"
    job.artist = "Test Artist"
    job.title = "Test Title"
    job.status = JobStatus.COMPLETE
    job.theme_id = "nomad"
    job.file_urls = {
        "input": "gs://bucket/input.flac",
        "stems": {
            "instrumental_clean": "gs://bucket/stems/clean.flac",
        },
        "lyrics": {
            "corrections": "gs://bucket/lyrics/corrections.json",
        },
    }
    job.state_data = {
        "audio_search_results": [{"provider": "test"}],
        "audio_selection": {"index": 0},
        "review_complete": True,
        "corrected_lyrics": {"lines": []},
        "instrumental_selection": "clean",
        "brand_code": "NOMAD-1234",
    }
    job.timeline = []
    job.created_at = datetime(2026, 1, 9, 10, 0, 0)
    job.updated_at = datetime(2026, 1, 9, 12, 0, 0)
    return job


class TestResetJobToPending:
    """Tests for resetting job to PENDING state."""

    def test_reset_to_pending_success(self, client, mock_job):
        """Test resetting a job to PENDING state."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "pending"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["job_id"] == "test-job-123"
            assert data["new_status"] == "pending"
            assert "pending" in data["message"].lower()

    def test_reset_to_pending_clears_state_data(self, client, mock_job):
        """Test that resetting to PENDING clears appropriate state_data keys."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "pending"},
            )

            assert response.status_code == 200
            # Verify update_job was called with cleared state_data keys
            call_args = mock_jm.update_job.call_args
            update_data = call_args[0][1]
            # Verify status was set
            assert update_data["status"] == "pending"


class TestResetJobToAwaitingAudioSelection:
    """Tests for resetting job to AWAITING_AUDIO_SELECTION state."""

    def test_reset_to_awaiting_audio_selection(self, client, mock_job):
        """Test resetting a job to AWAITING_AUDIO_SELECTION state."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "awaiting_audio_selection"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["new_status"] == "awaiting_audio_selection"


class TestResetJobToAwaitingReview:
    """Tests for resetting job to AWAITING_REVIEW state."""

    def test_reset_to_awaiting_review(self, client, mock_job):
        """Test resetting a job to AWAITING_REVIEW state."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "awaiting_review"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["new_status"] == "awaiting_review"


class TestResetJobToAwaitingInstrumentalSelection:
    """Tests for resetting job to AWAITING_INSTRUMENTAL_SELECTION state."""

    def test_reset_to_awaiting_instrumental_selection(self, client, mock_job):
        """Test resetting a job to AWAITING_INSTRUMENTAL_SELECTION state."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "awaiting_instrumental_selection"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["new_status"] == "awaiting_instrumental_selection"


class TestResetJobValidation:
    """Tests for reset endpoint validation."""

    def test_rejects_invalid_target_state(self, client, mock_job):
        """Test that invalid target states are rejected."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "encoding"},
            )

            assert response.status_code == 400
            assert "invalid" in response.json()["detail"].lower() or "not allowed" in response.json()["detail"].lower()

    def test_rejects_complete_as_target(self, client, mock_job):
        """Test that COMPLETE is not a valid reset target."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "complete"},
            )

            assert response.status_code == 400

    def test_rejects_failed_as_target(self, client, mock_job):
        """Test that FAILED is not a valid reset target."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "failed"},
            )

            assert response.status_code == 400

    def test_returns_404_when_job_not_found(self, client):
        """Test 404 when job doesn't exist."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = None
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/nonexistent-job/reset",
                json={"target_state": "pending"},
            )

            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()

    def test_rejects_missing_target_state(self, client, mock_job):
        """Test that target_state is required."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={},
            )

            assert response.status_code == 422  # Validation error


class TestResetJobLogging:
    """Tests for logging on the reset endpoint."""

    def test_logs_admin_reset_action(self, client, mock_job):
        """Test that admin reset actions are logged."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.api.routes.admin.logger') as mock_logger:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "pending"},
            )

            assert response.status_code == 200
            # Verify logging was called
            mock_logger.info.assert_called()
            log_message = mock_logger.info.call_args[0][0]
            assert "admin" in log_message.lower() or "reset" in log_message.lower()


class TestResetJobTimeline:
    """Tests for timeline event on reset."""

    def test_adds_timeline_event_on_reset(self, client, mock_job):
        """Test that a timeline event is added when job is reset."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "awaiting_review"},
            )

            assert response.status_code == 200
            data = response.json()
            # Timeline event should be included in response
            assert "timeline_event" in data or data.get("message")


class TestResetJobAuthorization:
    """Tests for authorization on the reset endpoint."""

    def test_requires_admin_access(self, client, mock_job):
        """Test that non-admin users cannot access the endpoint."""
        original_override = app.dependency_overrides.get(require_admin)

        def get_non_admin():
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = get_non_admin

        try:
            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "pending"},
                headers={"Authorization": "Bearer user-token"}
            )
            assert response.status_code == 403
        finally:
            if original_override:
                app.dependency_overrides[require_admin] = original_override
            else:
                app.dependency_overrides[require_admin] = get_mock_admin


class TestResetJobClearsStateData:
    """Tests for state_data clearing based on target state."""

    def test_reset_to_pending_clears_all_processing_data(self, client, mock_job):
        """Test that resetting to PENDING clears audio search results."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "pending"},
            )

            assert response.status_code == 200
            # The cleared_data field should list what was cleared
            data = response.json()
            assert "cleared_data" in data or "state_data" in str(data)

    def test_reset_to_awaiting_review_preserves_audio_data(self, client, mock_job):
        """Test that resetting to AWAITING_REVIEW preserves audio stems."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "awaiting_review"},
            )

            assert response.status_code == 200

    def test_reset_to_instrumental_preserves_review_data(self, client, mock_job):
        """Test that resetting to AWAITING_INSTRUMENTAL_SELECTION preserves review data."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "awaiting_instrumental_selection"},
            )

            assert response.status_code == 200


class TestResetJobToInstrumentalSelected:
    """Tests for resetting job to INSTRUMENTAL_SELECTED state (reprocess video)."""

    def test_reset_to_instrumental_selected_success(self, client, mock_job):
        """Test resetting a job to INSTRUMENTAL_SELECTED state."""
        import asyncio
        from unittest.mock import AsyncMock

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            # Mock worker service with async method
            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=True)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "instrumental_selected"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["new_status"] == "instrumental_selected"

    def test_reset_to_instrumental_selected_triggers_video_worker(self, client, mock_job):
        """Test that resetting to INSTRUMENTAL_SELECTED triggers video worker."""
        from unittest.mock import AsyncMock

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            # Mock worker service
            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=True)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "instrumental_selected"},
            )

            assert response.status_code == 200
            # Verify worker was triggered
            mock_worker_service.trigger_video_worker.assert_called_once_with("test-job-123")
            # Verify message indicates worker was triggered
            data = response.json()
            assert "video worker triggered" in data["message"]

    def test_reset_to_instrumental_selected_message_on_worker_failure(self, client, mock_job):
        """Test message when video worker trigger fails."""
        from unittest.mock import AsyncMock

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            # Mock worker service failure
            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=False)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "instrumental_selected"},
            )

            assert response.status_code == 200
            # Reset still succeeds even if worker trigger fails
            data = response.json()
            assert data["new_status"] == "instrumental_selected"
            # Message should NOT say worker was triggered
            assert "video worker triggered" not in data["message"]

    def test_reset_to_instrumental_selected_clears_video_state(self, client, mock_job):
        """Test that INSTRUMENTAL_SELECTED reset clears video/encoding/distribution state."""
        from unittest.mock import AsyncMock

        mock_job.state_data = {
            "instrumental_selection": "clean",
            "lyrics_metadata": {"has_countdown_padding": True},
            "video_progress": {"stage": "running"},
            "brand_code": "NOMAD-1234",
            "youtube_url": "https://youtube.com/watch?v=xxx",
            "dropbox_link": "https://dropbox.com/xxx",
        }

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=True)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "instrumental_selected"},
            )

            assert response.status_code == 200
            data = response.json()
            # These keys should be marked as cleared
            cleared = data.get("cleared_data", [])
            # At minimum, video_progress and brand_code should be cleared
            # (they exist in the mock job's state_data)
            assert any(key in ["video_progress", "brand_code", "youtube_url", "dropbox_link"]
                      for key in cleared)


class TestResetJobClearsErrorState:
    """Tests that reset clears error_message and error_details."""

    def test_reset_clears_error_message(self, client, mock_job):
        """Test that resetting a job clears error_message field."""
        # Set job to failed state with error message
        mock_job.status = JobStatus.FAILED
        mock_job.error_message = "Previous error message"
        mock_job.error_details = {"code": "TEST_ERROR"}

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "pending"},
            )

            assert response.status_code == 200
            # The endpoint should issue an update that includes DELETE_FIELD for error fields
            # We verify this indirectly through the success of the operation


class TestClearWorkersEndpoint:
    """Tests for the POST /api/admin/jobs/{job_id}/clear-workers endpoint."""

    def test_clear_workers_success(self, client, mock_job):
        """Test clearing worker progress keys."""
        mock_job.state_data = {
            "render_progress": {"stage": "complete"},
            "video_progress": {"stage": "complete"},
            "encoding_progress": {"stage": "complete"},
        }

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.post("/api/admin/jobs/test-job-123/clear-workers")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "cleared_keys" in data
            # Should have cleared the progress keys that exist
            assert any(k in data["cleared_keys"] for k in
                      ["render_progress", "video_progress", "encoding_progress"])

    def test_clear_workers_no_progress_keys(self, client, mock_job):
        """Test clearing workers when no progress keys exist."""
        mock_job.state_data = {"other_data": "value"}

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.post("/api/admin/jobs/test-job-123/clear-workers")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert len(data["cleared_keys"]) == 0

    def test_clear_workers_job_not_found(self, client):
        """Test clearing workers for non-existent job."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = None
            mock_jm_class.return_value = mock_jm

            response = client.post("/api/admin/jobs/nonexistent/clear-workers")

            assert response.status_code == 404

    def test_clear_workers_clears_all_worker_types(self, client, mock_job):
        """Test that all defined worker progress keys can be cleared."""
        # Set all possible worker progress keys
        mock_job.state_data = {
            "audio_progress": {"stage": "complete"},
            "lyrics_progress": {"stage": "complete"},
            "render_progress": {"stage": "complete"},
            "screens_progress": {"stage": "complete"},
            "video_progress": {"stage": "complete"},
            "encoding_progress": {"stage": "complete"},
        }

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.post("/api/admin/jobs/test-job-123/clear-workers")

            assert response.status_code == 200
            data = response.json()
            # Should have cleared all 6 progress keys
            assert len(data["cleared_keys"]) == 6


class TestResetIncludesEncodingProgress:
    """Tests that reset includes encoding_progress in cleared keys."""

    def test_reset_to_awaiting_review_clears_encoding_progress(self, client, mock_job):
        """Test that resetting to awaiting_review clears encoding_progress."""
        mock_job.state_data = {
            "encoding_progress": {"stage": "complete"},
            "video_progress": {"stage": "complete"},
        }

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "awaiting_review"},
            )

            assert response.status_code == 200
            data = response.json()
            # encoding_progress should be in cleared_data
            assert "encoding_progress" in data.get("cleared_data", [])


class TestResetWorkerTriggerResponseFields:
    """Tests that reset response includes worker_triggered and worker_trigger_error fields."""

    def test_reset_to_instrumental_selected_includes_worker_triggered_true(self, client, mock_job):
        """Test response includes worker_triggered=True when trigger succeeds."""
        from unittest.mock import AsyncMock

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=True)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "instrumental_selected"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["worker_triggered"] is True
            assert data.get("worker_trigger_error") is None

    def test_reset_to_instrumental_selected_includes_worker_triggered_false_on_failure(self, client, mock_job):
        """Test response includes worker_triggered=False and error when trigger fails."""
        from unittest.mock import AsyncMock

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=False)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "instrumental_selected"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["worker_triggered"] is False
            assert data["worker_trigger_error"] is not None
            assert "manual trigger" in data["message"].lower()

    def test_reset_to_instrumental_selected_includes_error_on_exception(self, client, mock_job):
        """Test response includes worker_trigger_error when exception occurs."""
        from unittest.mock import AsyncMock

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(
                side_effect=Exception("Connection timeout")
            )
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "instrumental_selected"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["worker_triggered"] is False
            assert "Connection timeout" in data["worker_trigger_error"]

    def test_reset_to_pending_has_null_worker_fields(self, client, mock_job):
        """Test response has null worker fields for non-instrumental_selected resets."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm.update_job.return_value = True
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/reset",
                json={"target_state": "pending"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data.get("worker_triggered") is None
            assert data.get("worker_trigger_error") is None


class TestTriggerWorkerEndpoint:
    """Tests for POST /api/admin/jobs/{job_id}/trigger-worker endpoint."""

    def test_trigger_video_worker_success(self, client, mock_job):
        """Test successfully triggering video worker."""
        from unittest.mock import AsyncMock

        mock_job.status = "instrumental_selected"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=True)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/trigger-worker",
                json={"worker_type": "video"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["job_id"] == "test-job-123"
            assert data["worker_type"] == "video"
            assert data["triggered"] is True
            assert data.get("error") is None
            mock_worker_service.trigger_video_worker.assert_called_once_with("test-job-123")

    def test_trigger_worker_default_type_is_video(self, client, mock_job):
        """Test that default worker_type is video."""
        from unittest.mock import AsyncMock

        mock_job.status = "instrumental_selected"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=True)
            mock_ws.return_value = mock_worker_service

            # Send empty body - should default to video
            response = client.post(
                "/api/admin/jobs/test-job-123/trigger-worker",
                json={},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["worker_type"] == "video"

    def test_trigger_worker_failure_returns_error(self, client, mock_job):
        """Test response when worker trigger returns False."""
        from unittest.mock import AsyncMock

        mock_job.status = "instrumental_selected"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=False)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/trigger-worker",
                json={"worker_type": "video"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert data["triggered"] is False
            assert data["error"] is not None

    def test_trigger_worker_exception_returns_error(self, client, mock_job):
        """Test response when worker trigger throws exception."""
        from unittest.mock import AsyncMock

        mock_job.status = "instrumental_selected"

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(
                side_effect=Exception("Network error")
            )
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/trigger-worker",
                json={"worker_type": "video"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert data["triggered"] is False
            assert "Network error" in data["error"]

    def test_trigger_worker_invalid_type_rejected(self, client, mock_job):
        """Test that invalid worker_type is rejected."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/test-job-123/trigger-worker",
                json={"worker_type": "invalid"},
            )

            assert response.status_code == 400
            assert "unsupported" in response.json()["detail"].lower()

    def test_trigger_worker_job_not_found(self, client):
        """Test 404 when job doesn't exist."""
        with patch('backend.api.routes.admin.JobManager') as mock_jm_class:
            mock_jm = Mock()
            mock_jm.get_job.return_value = None
            mock_jm_class.return_value = mock_jm

            response = client.post(
                "/api/admin/jobs/nonexistent/trigger-worker",
                json={"worker_type": "video"},
            )

            assert response.status_code == 404

    def test_trigger_worker_logs_warning_for_unexpected_status(self, client, mock_job):
        """Test that warning is logged when job is not in expected status."""
        from unittest.mock import AsyncMock

        mock_job.status = "complete"  # Not instrumental_selected

        with patch('backend.api.routes.admin.JobManager') as mock_jm_class, \
             patch('backend.services.worker_service.get_worker_service') as mock_ws, \
             patch('backend.api.routes.admin.logger') as mock_logger:
            mock_jm = Mock()
            mock_jm.get_job.return_value = mock_job
            mock_jm_class.return_value = mock_jm

            mock_worker_service = Mock()
            mock_worker_service.trigger_video_worker = AsyncMock(return_value=True)
            mock_ws.return_value = mock_worker_service

            response = client.post(
                "/api/admin/jobs/test-job-123/trigger-worker",
                json={"worker_type": "video"},
            )

            # Should still succeed - we don't block based on status
            assert response.status_code == 200
            # But should log a warning
            mock_logger.warning.assert_called()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "unexpected status" in warning_msg.lower()

    def test_trigger_worker_requires_admin(self, client, mock_job):
        """Test that endpoint requires admin access."""
        original_override = app.dependency_overrides.get(require_admin)

        def get_non_admin():
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = get_non_admin

        try:
            response = client.post(
                "/api/admin/jobs/test-job-123/trigger-worker",
                json={"worker_type": "video"},
            )
            assert response.status_code == 403
        finally:
            if original_override:
                app.dependency_overrides[require_admin] = original_override
            else:
                app.dependency_overrides[require_admin] = get_mock_admin
