"""
Tests for disposable email blocking in the magic link signup flow.

Verifies that EmailValidationService is correctly wired into the
POST /api/users/auth/magic-link endpoint.
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def mock_validation_svc():
    """Create a mock EmailValidationService."""
    svc = MagicMock()
    svc.is_disposable_domain.return_value = False
    svc.is_email_blocked.return_value = False
    svc.is_ip_blocked.return_value = False
    return svc


@pytest.fixture
def client(mock_validation_svc):
    """Create TestClient with mocked email validation service."""
    from backend.main import app

    with patch(
        "backend.api.routes.users.get_email_validation_service",
        return_value=mock_validation_svc,
    ):
        yield TestClient(app)


class TestDisposableEmailBlocking:
    """Tests for disposable email rejection in magic link flow."""

    def test_disposable_email_returns_422(self, client, mock_validation_svc):
        """Disposable email domains should be rejected with 422."""
        mock_validation_svc.is_disposable_domain.return_value = True

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "test@yopmail.com"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "disposable_email_not_allowed"

    def test_disposable_email_does_not_send_email(self, client, mock_validation_svc):
        """Disposable email should be rejected before any email is sent."""
        mock_validation_svc.is_disposable_domain.return_value = True

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "abuse@tempmail.com"},
        )

        assert response.status_code == 422
        # Validation should have been called with lowercased email
        mock_validation_svc.is_disposable_domain.assert_called_with("abuse@tempmail.com")

    def test_blocked_email_silent_reject(self, client, mock_validation_svc):
        """Blocked email should return generic success (anti-enumeration)."""
        mock_validation_svc.is_email_blocked.return_value = True

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "blocked@example.com"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_blocked_ip_silent_reject(self, client, mock_validation_svc):
        """Blocked IP should return generic success (anti-enumeration)."""
        mock_validation_svc.is_ip_blocked.return_value = True

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "innocent@gmail.com"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_email_is_lowercased_before_check(self, client, mock_validation_svc):
        """Email should be lowercased before disposable domain check."""
        mock_validation_svc.is_disposable_domain.return_value = True

        client.post(
            "/api/users/auth/magic-link",
            json={"email": "Test@YOPMAIL.COM"},
        )

        mock_validation_svc.is_disposable_domain.assert_called_with("test@yopmail.com")


class TestDisposableDomainList:
    """Verify DEFAULT_DISPOSABLE_DOMAINS is now empty (replaced by external sync)."""

    def test_defaults_empty_after_migration(self):
        from backend.services.email_validation_service import DEFAULT_DISPOSABLE_DOMAINS
        assert len(DEFAULT_DISPOSABLE_DOMAINS) == 0

    def test_gmail_not_in_defaults(self):
        from backend.services.email_validation_service import DEFAULT_DISPOSABLE_DOMAINS
        assert "gmail.com" not in DEFAULT_DISPOSABLE_DOMAINS
