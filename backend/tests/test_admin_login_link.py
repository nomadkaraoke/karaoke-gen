"""Tests for the admin mint-login-link endpoint and purpose->redirect mapping."""
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin import router
from backend.api.dependencies import require_admin
from backend.api.routes.users import _magic_link_redirect_path
from backend.services.user_service import get_user_service


app = FastAPI()
app.include_router(router, prefix="/api")


def _mock_admin():
    from backend.api.dependencies import AuthResult, UserType
    return AuthResult(is_valid=True, user_type=UserType.ADMIN, remaining_uses=999,
                      message="ok", user_email="admin@example.com", is_admin=True)


app.dependency_overrides[require_admin] = _mock_admin


@pytest.fixture
def client():
    mock_us = Mock()
    tok = Mock()
    tok.email = "user@example.com"
    tok.token = "tok123"
    tok.expires_at = datetime.utcnow() + timedelta(hours=168)
    mock_us.create_admin_login_token.return_value = tok
    app.dependency_overrides[get_user_service] = lambda: mock_us
    yield TestClient(app), mock_us
    app.dependency_overrides.pop(get_user_service, None)


class TestMintLoginLink:
    def test_mints_link_with_purpose(self, client):
        c, mock_us = client
        resp = c.post("/api/admin/users/User@Example.com/login-link",
                      json={"expiry_hours": 100, "purpose": "job_review:abc12345"})
        assert resp.status_code == 200
        data = resp.json()
        assert "auth/verify?token=tok123" in data["url"]
        assert data["purpose"] == "job_review:abc12345"
        mock_us.create_admin_login_token.assert_called_once_with(
            email="user@example.com", expiry_hours=100, purpose="job_review:abc12345")


class TestMagicLinkRedirectPath:
    def test_board(self):
        assert _magic_link_redirect_path("requests_board") == "/requests"

    def test_job_review(self):
        assert _magic_link_redirect_path("job_review:abc12345") == "/app/jobs#/abc12345/review"

    def test_malformed_job_id_rejected(self):
        assert _magic_link_redirect_path("job_review:../evil") is None
        assert _magic_link_redirect_path("job_review:") is None

    def test_none_and_unknown(self):
        assert _magic_link_redirect_path(None) is None
        assert _magic_link_redirect_path("something_else") is None
