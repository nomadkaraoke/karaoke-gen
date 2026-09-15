"""Regression: admin endpoints that log the caller must not 500.

`require_admin` returns an `AuthResult` object (not a tuple). Three admin endpoints
logged `auth_data[0]`, which raised `TypeError: 'AuthResult' object is not
subscriptable` and returned 500 (prod: POST /jobs/{id}/reset-worker-state on
2026-09-15). These endpoints had no test coverage, so the bug shipped. This test
locks in the fix (`auth_data.user_email`).
"""
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin import router
from backend.api.dependencies import require_admin, AuthResult, UserType

app = FastAPI()
app.include_router(router, prefix="/api")


def _mock_admin():
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=999,
        message="Admin authenticated",
        user_email="admin@example.com",
        is_admin=True,
    )


app.dependency_overrides[require_admin] = _mock_admin


@pytest.fixture
def client():
    return TestClient(app)


def test_reset_worker_state_does_not_500(client):
    """POST /jobs/{id}/reset-worker-state must succeed — previously raised
    TypeError on `auth_data[0]` because require_admin returns an AuthResult."""
    mock_jm = Mock()
    mock_jm.get_job.return_value = Mock()  # truthy job

    with patch("backend.services.job_manager.JobManager", return_value=mock_jm):
        resp = client.post("/api/admin/jobs/test-123/reset-worker-state")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"
    # video/render/screens progress states reset
    assert mock_jm.update_state_data.call_count == 3


def test_clear_all_flacfetch_cache_does_not_500(client):
    """DELETE /cache must succeed — same `auth_data[0]` bug on the admin log line."""
    mock_client = Mock()

    async def _clear():
        return 7

    mock_client.clear_all_cache = _clear

    with patch("backend.api.routes.admin.get_flacfetch_client", return_value=mock_client):
        resp = client.delete("/api/admin/cache")

    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_count"] == 7
