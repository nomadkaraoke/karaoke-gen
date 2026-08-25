"""Tests for POST /api/tenant/bulk/analyze (auth + feature gate + cap)."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.api.dependencies import require_auth
from backend.services.auth_service import AuthResult, UserType


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    async def override_auth():
        return AuthResult(
            is_valid=True,
            user_type=UserType.UNLIMITED,
            remaining_uses=-1,
            message="Valid",
            user_email="op@vocalstar.com",
            is_admin=False,
        )

    app.dependency_overrides[require_auth] = override_auth
    yield
    app.dependency_overrides.clear()


def _tenant(bulk_upload=True):
    return SimpleNamespace(
        id="vocalstar", features=SimpleNamespace(bulk_upload=bulk_upload)
    )


CLEAN = [
    "S1100-1 Eddy Grant - I Don't Wanna Dance Guide.mp3",
    "S1100-2 Eddy Grant - I Don't Wanna Dance BV.mp3",
    "cover.png",
]


def test_403_when_not_a_tenant(client):
    with patch(
        "backend.api.routes.tenant_bulk.get_tenant_config_from_request",
        return_value=None,
    ):
        r = client.post("/api/tenant/bulk/analyze", json={"filenames": CLEAN})
    assert r.status_code == 403


def test_403_when_feature_disabled(client):
    with patch(
        "backend.api.routes.tenant_bulk.get_tenant_config_from_request",
        return_value=_tenant(bulk_upload=False),
    ):
        r = client.post("/api/tenant/bulk/analyze", json={"filenames": CLEAN})
    assert r.status_code == 403


def test_400_when_empty(client):
    with patch(
        "backend.api.routes.tenant_bulk.get_tenant_config_from_request",
        return_value=_tenant(),
    ):
        r = client.post("/api/tenant/bulk/analyze", json={"filenames": []})
    assert r.status_code == 400


def test_400_when_over_cap(client):
    too_many = [f"S{i}-1 A - T Guide.mp3" for i in range(101)]
    with patch(
        "backend.api.routes.tenant_bulk.get_tenant_config_from_request",
        return_value=_tenant(),
    ):
        r = client.post("/api/tenant/bulk/analyze", json={"filenames": too_many})
    assert r.status_code == 400
    assert "at most 100" in r.json()["detail"]


def test_happy_path_returns_rows(client):
    # Clean batch has no leftovers, so the LLM generate is never invoked.
    with patch(
        "backend.api.routes.tenant_bulk.get_tenant_config_from_request",
        return_value=_tenant(),
    ):
        r = client.post("/api/tenant/bulk/analyze", json={"filenames": CLEAN})
    assert r.status_code == 200
    data = r.json()
    assert len(data["rows"]) == 1
    assert data["rows"][0]["artist"] == "Eddy Grant"
    assert {i["filename"] for i in data["ignored"]} == {"cover.png"}
