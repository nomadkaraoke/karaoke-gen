"""Tests for the admin regenerate-screens endpoint.

Regression coverage for the bug where a job in the `in_review` state (user has
opened the review UI but not yet submitted) returned 400 from
POST /api/admin/jobs/{job_id}/regenerate-screens because `in_review` was
missing from REGENERATE_SCREENS_ALLOWED_STATES.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.auth_service import AuthResult, UserType
from backend.api.routes.admin import REGENERATE_SCREENS_ALLOWED_STATES


@pytest.fixture
def admin_auth_result():
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=-1,
        message="Admin access granted",
        user_email="admin@nomadkaraoke.com",
        is_admin=True,
    )


def _make_job(status):
    """A job that satisfies every regenerate-screens precondition except (maybe) status."""
    job = MagicMock()
    job.status = status
    job.artist = "Lin-Manuel Miranda"
    job.title = "Alexander Hamilton"
    job.state_data = {
        "audio_progress": {"stage": "audio_complete"},
        "lyrics_progress": {"stage": "lyrics_complete"},
    }
    return job


@pytest.fixture
def client(admin_auth_result):
    """Test client with JobManager / StorageService / worker_service mocked.

    The job returned by get_job is configured per-test via
    mock_job_manager.get_job.return_value.
    """
    mock_creds = MagicMock()
    mock_creds.universe_domain = "googleapis.com"

    mock_job_manager = MagicMock()
    # firestore.db.collection(...).document(...).update(...) chain
    mock_job_manager.firestore.db.collection.return_value.document.return_value.update.return_value = None

    mock_storage = MagicMock()

    mock_worker_service = MagicMock()
    mock_worker_service.trigger_screens_worker = AsyncMock(return_value=True)

    with patch("backend.services.firestore_service.firestore"), \
         patch("backend.services.storage_service.storage"), \
         patch("google.auth.default", return_value=(mock_creds, "test-project")), \
         patch("backend.api.routes.admin.JobManager", return_value=mock_job_manager), \
         patch("backend.api.routes.admin.StorageService", return_value=mock_storage), \
         patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service):
        from backend.api.routes.admin import router
        from backend.api.dependencies import require_admin

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[require_admin] = lambda: admin_auth_result

        test_client = TestClient(app)
        test_client.mock_job_manager = mock_job_manager  # expose for per-test config
        yield test_client


class TestRegenerateScreensStateValidation:
    def test_in_review_is_allowed(self):
        """Regression: in_review must be an allowed state."""
        assert "in_review" in REGENERATE_SCREENS_ALLOWED_STATES

    def test_in_review_job_succeeds(self, client):
        """A job mid-review should regenerate screens instead of returning 400."""
        client.mock_job_manager.get_job.return_value = _make_job("in_review")

        response = client.post("/api/admin/jobs/8e9df2c1/regenerate-screens")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "success"
        assert data["worker_triggered"] is True

    def test_disallowed_state_returns_400(self, client):
        """A genuinely-invalid state (e.g. mid-rendering) still returns 400."""
        client.mock_job_manager.get_job.return_value = _make_job("rendering_video")

        response = client.post("/api/admin/jobs/8e9df2c1/regenerate-screens")

        assert response.status_code == 400
        assert "rendering_video" in response.json()["detail"]

    def test_missing_job_returns_404(self, client):
        client.mock_job_manager.get_job.return_value = None

        response = client.post("/api/admin/jobs/missing/regenerate-screens")

        assert response.status_code == 404
