"""
Tests for POST /api/jobs/estimate — URL duration probe.

Covers:
- duration 905s → 200, duration_seconds=905, credits=2, blocked=False
- duration 4000s → blocked=True
- probe failure (duration None) → duration_seconds=None, source="unknown", credits=1, blocked=False

`check_availability` is patched at its import location in backend.api.routes.jobs.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _ensure_auth_overrides():
    """Ensure auth dependency overrides are set for every test in this file."""
    from backend.api.dependencies import require_auth, require_admin
    from backend.main import app
    from backend.services.auth_service import UserType, AuthResult

    async def mock_require_auth():
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            remaining_uses=999,
            message="Test admin token",
            is_admin=True,
            user_email="test@example.com",
        )

    app.dependency_overrides[require_auth] = mock_require_auth
    app.dependency_overrides[require_admin] = mock_require_auth
    yield
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {
        "Authorization": "Bearer test-admin-token",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_estimate_returns_credits(client, auth_headers):
    """905s of audio → 2 credits (ceil(905/600)=2), not blocked."""
    mock_svc = AsyncMock()
    mock_svc.check_availability = AsyncMock(
        return_value={"available": True, "duration": 905}
    )

    with patch(
        "backend.api.routes.jobs.get_youtube_download_service",
        return_value=mock_svc,
    ):
        resp = client.post(
            "/api/jobs/estimate",
            json={"url": "https://youtu.be/x"},
            headers=auth_headers,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["duration_seconds"] == 905
    assert body["credits"] == 2
    assert body["blocked"] is False
    assert body["source"] == "youtube_metadata"


def test_estimate_blocks_over_60min(client, auth_headers):
    """4000s (>3600s) → blocked=True."""
    mock_svc = AsyncMock()
    mock_svc.check_availability = AsyncMock(
        return_value={"available": True, "duration": 4000}
    )

    with patch(
        "backend.api.routes.jobs.get_youtube_download_service",
        return_value=mock_svc,
    ):
        resp = client.post(
            "/api/jobs/estimate",
            json={"url": "https://youtu.be/x"},
            headers=auth_headers,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["blocked"] is True
    assert body["duration_seconds"] == 4000


def test_estimate_probe_failure_returns_unknown(client, auth_headers):
    """When probe returns None duration → unknown source, 1 credit hold, not blocked."""
    mock_svc = AsyncMock()
    mock_svc.check_availability = AsyncMock(
        return_value={"available": True, "duration": None}
    )

    with patch(
        "backend.api.routes.jobs.get_youtube_download_service",
        return_value=mock_svc,
    ):
        resp = client.post(
            "/api/jobs/estimate",
            json={"url": "https://youtu.be/x"},
            headers=auth_headers,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["duration_seconds"] is None
    assert body["source"] == "unknown"
    assert body["credits"] == 1
    assert body["blocked"] is False
