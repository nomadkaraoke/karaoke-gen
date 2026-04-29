"""
Tests for the resend-from-token recovery endpoint.

When a magic link is clicked after expiry/use, the verify page calls
POST /api/users/auth/resend-from-token to email a fresh sign-in link to
the address the original token was issued to. The token in the URL is a
32-byte cryptographic secret, so resending to the email recorded in the
token doc adds no new attack surface.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import sys
from fastapi.testclient import TestClient

# Mock Firestore before importing modules that use it (mirrors test_anti_abuse.py)
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.firestore_v1', MagicMock())

from backend.models.user import MagicLinkToken


@pytest.fixture
def mock_validation_svc():
    svc = MagicMock()
    svc.is_disposable_domain.return_value = False
    svc.is_email_blocked.return_value = False
    svc.is_ip_blocked.return_value = False
    return svc


def _make_token_doc(exists: bool, data: dict | None = None) -> MagicMock:
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data or {}
    return doc


@pytest.fixture
def mock_user_svc():
    svc = MagicMock()
    svc.create_magic_link.return_value = MagicLinkToken(
        token="fresh-token",
        email="user@example.com",
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )

    # Default: token doc exists with a known email
    svc.db = MagicMock()
    svc.db.collection.return_value.document.return_value.get.return_value = _make_token_doc(
        exists=True,
        data={
            "email": "user@example.com",
            "tenant_id": None,
            "referral_code": None,
            "device_fingerprint": None,
        },
    )
    return svc


@pytest.fixture
def mock_email_svc():
    svc = MagicMock()
    svc.is_configured.return_value = True
    svc.send_magic_link.return_value = True
    return svc


@pytest.fixture
def client(mock_validation_svc, mock_user_svc, mock_email_svc):
    from backend.main import app
    from backend.services.user_service import get_user_service
    from backend.services.email_service import get_email_service

    app.dependency_overrides[get_user_service] = lambda: mock_user_svc
    app.dependency_overrides[get_email_service] = lambda: mock_email_svc

    with patch(
        "backend.api.routes.users.get_email_validation_service",
        return_value=mock_validation_svc,
    ):
        yield TestClient(app)

    app.dependency_overrides.clear()


class TestResendFromToken:
    def test_token_exists_resends_to_original_email(
        self, client, mock_user_svc, mock_email_svc
    ):
        """When the token doc is found, mint and send a fresh magic link."""
        response = client.post(
            "/api/users/auth/resend-from-token",
            json={"token": "expired-original-token"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "sent"
        assert body["masked_email"] == "us***@ex***.com"

        mock_user_svc.create_magic_link.assert_called_once()
        called_kwargs = mock_user_svc.create_magic_link.call_args.kwargs
        # First positional arg is the email
        assert mock_user_svc.create_magic_link.call_args.args[0] == "user@example.com"

        mock_email_svc.send_magic_link.assert_called_once()
        send_args = mock_email_svc.send_magic_link.call_args
        assert send_args.args[0] == "user@example.com"
        assert send_args.args[1] == "fresh-token"

    def test_token_missing_returns_no_token(
        self, client, mock_user_svc, mock_email_svc
    ):
        """When the token doc is absent, return no_token and skip sending."""
        mock_user_svc.db.collection.return_value.document.return_value.get.return_value = (
            _make_token_doc(exists=False)
        )

        response = client.post(
            "/api/users/auth/resend-from-token",
            json={"token": "never-issued"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "no_token"
        assert body["masked_email"] is None

        mock_user_svc.create_magic_link.assert_not_called()
        mock_email_svc.send_magic_link.assert_not_called()

    def test_token_doc_without_email_returns_no_token(
        self, client, mock_user_svc, mock_email_svc
    ):
        """A doc that exists but has no email is treated as no_token (defensive)."""
        mock_user_svc.db.collection.return_value.document.return_value.get.return_value = (
            _make_token_doc(exists=True, data={"email": None})
        )

        response = client.post(
            "/api/users/auth/resend-from-token",
            json={"token": "weird-doc"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "no_token"
        mock_email_svc.send_magic_link.assert_not_called()

    def test_empty_token_rejected(self, client):
        """Empty / whitespace tokens are rejected before any Firestore lookup."""
        response = client.post(
            "/api/users/auth/resend-from-token",
            json={"token": "   "},
        )
        assert response.status_code == 422

    def test_used_token_still_resends(
        self, client, mock_user_svc, mock_email_svc
    ):
        """Already-used tokens should still trigger a resend — the user clearly
        owned the original email, and a fresh link doesn't reuse the old one."""
        mock_user_svc.db.collection.return_value.document.return_value.get.return_value = (
            _make_token_doc(
                exists=True,
                data={
                    "email": "user@example.com",
                    "used": True,
                    "used_at": datetime.utcnow().isoformat(),
                },
            )
        )

        response = client.post(
            "/api/users/auth/resend-from-token",
            json={"token": "used-token"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "sent"
        mock_email_svc.send_magic_link.assert_called_once()

    def test_email_service_not_configured_returns_503(
        self, client, mock_email_svc
    ):
        """If email is not configured, surface a 503 like send_magic_link does."""
        mock_email_svc.is_configured.return_value = False

        response = client.post(
            "/api/users/auth/resend-from-token",
            json={"token": "any-token"},
        )

        assert response.status_code == 503

    def test_cooldown_window_suppresses_repeat_resends(
        self, client, mock_user_svc, mock_email_svc
    ):
        """A second resend within the cooldown window does not re-send."""
        recent = datetime.utcnow() - timedelta(seconds=10)
        mock_user_svc.db.collection.return_value.document.return_value.get.return_value = (
            _make_token_doc(
                exists=True,
                data={
                    "email": "user@example.com",
                    "last_resend_at": recent,
                },
            )
        )

        response = client.post(
            "/api/users/auth/resend-from-token",
            json={"token": "recently-resent-token"},
        )

        assert response.status_code == 200
        body = response.json()
        # Still report sent so the UI shows the success state to the user
        # who already received an email a moment ago.
        assert body["status"] == "sent"
        assert body["masked_email"] == "us***@ex***.com"
        # But no new email was actually sent.
        mock_email_svc.send_magic_link.assert_not_called()
        mock_user_svc.create_magic_link.assert_not_called()

    def test_cooldown_expired_allows_resend(
        self, client, mock_user_svc, mock_email_svc
    ):
        """Once the cooldown elapses, a fresh resend goes through."""
        old = datetime.utcnow() - timedelta(minutes=5)
        mock_user_svc.db.collection.return_value.document.return_value.get.return_value = (
            _make_token_doc(
                exists=True,
                data={
                    "email": "user@example.com",
                    "last_resend_at": old,
                },
            )
        )

        response = client.post(
            "/api/users/auth/resend-from-token",
            json={"token": "old-resend-token"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "sent"
        mock_email_svc.send_magic_link.assert_called_once()

    def test_tenant_context_carried_forward(
        self, client, mock_user_svc, mock_email_svc
    ):
        """The original token's tenant_id is preserved when minting the new link."""
        mock_user_svc.db.collection.return_value.document.return_value.get.return_value = (
            _make_token_doc(
                exists=True,
                data={
                    "email": "user@example.com",
                    "tenant_id": "vocalstar",
                    "referral_code": "ref-abc",
                    "device_fingerprint": "fp-xyz",
                },
            )
        )

        with patch(
            "backend.services.tenant_service.get_tenant_service"
        ) as mock_get_tenant:
            mock_get_tenant.return_value.get_tenant_config.return_value = None

            response = client.post(
                "/api/users/auth/resend-from-token",
                json={"token": "tenant-token"},
            )

        assert response.status_code == 200
        kwargs = mock_user_svc.create_magic_link.call_args.kwargs
        assert kwargs["tenant_id"] == "vocalstar"
        assert kwargs["referral_code"] == "ref-abc"
        assert kwargs["device_fingerprint"] == "fp-xyz"
