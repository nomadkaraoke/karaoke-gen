"""Tests for board sign-in plumbing: the convert-to-gen credit claim + purpose field."""
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend.api.routes import users as users_routes
from backend.models.user import SendMagicLinkRequest
from backend.services.auth_service import AuthResult, UserType
from backend.services.user_service import get_user_service


@pytest.fixture
def app():
    # Fixture-time import — see note in test_requests_board_routes.py (earlier modules
    # reload backend.main / dependencies, so a module-level capture would go stale).
    from backend.main import app as _app
    return _app


@pytest.fixture
def client(app):
    # Override the require_auth object the users router actually uses (reload-proof).
    async def _mock_auth():
        return AuthResult(
            is_valid=True, user_type=UserType.LIMITED, remaining_uses=999,
            message="test", is_admin=False, user_email="test@example.com",
        )
    app.dependency_overrides[users_routes.require_auth] = _mock_auth
    c = TestClient(app)
    yield c
    app.dependency_overrides.pop(users_routes.require_auth, None)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}


def test_send_magic_link_request_accepts_purpose():
    req = SendMagicLinkRequest(email="a@b.com", purpose="requests_board")
    assert req.purpose == "requests_board"
    # Backwards compatible: purpose is optional
    assert SendMagicLinkRequest(email="a@b.com").purpose is None


def _override_user_service(app, svc):
    app.dependency_overrides[get_user_service] = lambda: svc


def test_claim_welcome_credit_grants(client, app, auth_headers):
    svc = MagicMock()
    svc.grant_welcome_credits_if_eligible.return_value = (True, "granted")
    svc.get_user.return_value = MagicMock(credits=1)
    svc.NEW_USER_FREE_CREDITS = 1
    _override_user_service(app, svc)
    try:
        resp = client.post("/api/users/claim-welcome-credit", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "granted", "credits": 1, "credits_granted": 1}
        svc.grant_welcome_credits_if_eligible.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_user_service, None)


def test_claim_welcome_credit_idempotent_already_granted(client, app, auth_headers):
    svc = MagicMock()
    svc.grant_welcome_credits_if_eligible.return_value = (False, "already_granted")
    svc.get_user.return_value = MagicMock(credits=1)
    svc.NEW_USER_FREE_CREDITS = 1
    _override_user_service(app, svc)
    try:
        resp = client.post("/api/users/claim-welcome-credit", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "already_granted"
        assert data["credits_granted"] == 0
    finally:
        app.dependency_overrides.pop(get_user_service, None)
