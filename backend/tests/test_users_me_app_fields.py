"""Tests for the mobile-app fields on GET /api/users/me (app_user_id, capabilities, push_prefs)."""
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.firestore_v1', MagicMock())

from backend.main import app
from backend.models.user import ExpoPushToken, User
from backend.services.auth_service import AuthResult, UserType
from backend.services.user_service import UserService, get_user_service

EMAIL = "singer@example.com"


class FakeUserService:
    """Mints like the real service: once, then returns the stored id."""

    def __init__(self, user):
        self.user = user

    def get_or_create_user(self, email, **kwargs):
        return self.user

    def ensure_app_user_id(self, email):
        if not self.user.app_user_id:
            self.user.app_user_id = str(uuid.uuid4())
        return self.user.app_user_id


@pytest.fixture
def user():
    return User(email=EMAIL)


@pytest.fixture
def client(user):
    svc = FakeUserService(user)
    app.dependency_overrides[get_user_service] = lambda: svc
    auth = MagicMock()
    auth.validate_token_full.return_value = AuthResult(
        is_valid=True, user_type=UserType.UNLIMITED, remaining_uses=-1,
        message="ok", user_email=EMAIL, is_admin=False,
    )
    with patch("backend.services.auth_service.get_auth_service", return_value=auth):
        yield TestClient(app)
    app.dependency_overrides.pop(get_user_service, None)


def _me(client):
    r = client.get("/api/users/me", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200, r.text
    return r.json()


def test_me_mints_app_user_id_once(client):
    first = _me(client)["app_user_id"]
    second = _me(client)["app_user_id"]
    assert first == second
    assert uuid.UUID(first).version == 4


@pytest.mark.parametrize("enabled", [False, True])
def test_me_capabilities_reflect_flag(client, enabled):
    with patch("backend.api.routes.users.get_settings") as gs:
        gs.return_value.account_deletion_enabled = enabled
        assert _me(client)["capabilities"] == {"account_deletion": enabled}


def test_me_push_prefs_default_true(client, user):
    assert _me(client)["push_prefs"] == {"ready": True, "review": True, "problems": True}
    user.push_prefs = {"review": False, "bogus": False}
    assert _me(client)["push_prefs"] == {"ready": True, "review": False, "problems": True}


def test_expo_push_token_model_validates_platform():
    t = ExpoPushToken(token="ExponentPushToken[x]", platform="ios", device_id="d1")
    assert t.last_used_at is None
    with pytest.raises(ValueError):
        ExpoPushToken(token="t", platform="web", device_id="d")


class _Snap:
    def __init__(self, data):
        self.exists = data is not None
        self._d = data

    def to_dict(self):
        return self._d


def _service(stored):
    svc = UserService.__new__(UserService)
    svc.db = MagicMock()
    ref = MagicMock()
    ref.get.return_value = _Snap(stored)
    txn = MagicMock()
    svc.db.transaction.return_value = txn
    svc.get_user_doc_ref = lambda email: ref
    return svc, ref, txn


def test_ensure_app_user_id_sets_when_absent_and_keeps_when_present():
    with patch("backend.services.user_service.firestore.transactional", lambda f: f):
        svc, ref, txn = _service({"email": EMAIL})
        minted = svc.ensure_app_user_id(EMAIL)
        assert uuid.UUID(minted).version == 4
        txn.set.assert_called_once_with(ref, {"app_user_id": minted}, merge=True)

        svc, ref, txn = _service({"email": EMAIL, "app_user_id": "existing-id"})
        assert svc.ensure_app_user_id(EMAIL) == "existing-id"
        txn.set.assert_not_called()
