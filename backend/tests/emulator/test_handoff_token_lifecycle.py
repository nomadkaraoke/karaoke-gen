"""Emulator tests for mobile handoff tokens (POST /api/users/auth/handoff-token).

The handoff URL carries a short-lived magic-link token. These run the real
UserService against the Firestore emulator, because single use is enforced by
the verify transaction: a second open (or an open after the TTL) must fail, and
the read-only status probe the verify page runs first must report it dead so
the page shows its normal "link expired, sign in" state.
Run with: scripts/run-emulator-tests.sh
"""
import uuid
from datetime import timedelta

import pytest

from backend.api.routes.login_code import HANDOFF_TOKEN_TTL_SECONDS

from .conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh",
)


@pytest.fixture
def user_service():
    from backend.services.user_service import UserService

    return UserService()


def _mint(user_service):
    email = f"handoff-{uuid.uuid4().hex[:8]}@example.com"
    token = user_service.create_admin_login_token(
        email, expiry_seconds=HANDOFF_TOKEN_TTL_SECONDS, purpose="job_review:job1234",
    ).token
    return email, token


def test_handoff_ttl_is_two_minutes_or_less():
    assert 0 < HANDOFF_TOKEN_TTL_SECONDS <= 120


def test_handoff_token_single_use(user_service):
    email, token = _mint(user_service)
    assert user_service.get_magic_link_status(token) == "valid"

    ok, user, _ = user_service.verify_magic_link(token)
    assert ok and user.email == email

    ok, user, message = user_service.verify_magic_link(token)
    assert not ok and user is None and "already been used" in message
    assert user_service.get_magic_link_status(token) == "used"


def test_handoff_token_expires(user_service, monkeypatch):
    from backend.services import user_service as us

    _, token = _mint(user_service)
    real_datetime = us.datetime

    class Later(real_datetime):
        @classmethod
        def utcnow(cls):
            return real_datetime.utcnow() + timedelta(seconds=HANDOFF_TOKEN_TTL_SECONDS + 1)

    monkeypatch.setattr(us, "datetime", Later)
    assert user_service.get_magic_link_status(token) == "expired"
    ok, user, message = user_service.verify_magic_link(token)
    assert not ok and user is None and "expired" in message


def test_handoff_token_verifies_to_review_redirect():
    """End to end through the API: mint → GET /auth/verify → review redirect."""
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services.user_service import UserService

    email, token = _mint(UserService())
    client = TestClient(app)
    resp = client.get("/api/users/auth/verify", params={"token": token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["redirect_path"] == "/app/jobs#/job1234/review"
    assert client.get("/api/users/auth/verify", params={"token": token}).status_code == 401
