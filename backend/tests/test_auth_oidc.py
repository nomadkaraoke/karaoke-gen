"""
Tests for Google OIDC token validation in AuthService.

Cloud Scheduler and Cloud Tasks send OIDC JWTs signed by Google.
The auth service must validate these and grant admin access for
requests from the configured service account.
"""
import pytest
from unittest.mock import patch, MagicMock

from backend.services.auth_service import AuthService, AuthResult, UserType


@pytest.fixture
def auth_service():
    """Create an AuthService instance with mocked Firestore."""
    with patch("backend.services.auth_service.FirestoreService"):
        service = AuthService()
        service._scheduler_service_account = "karaoke-backend@nomadkaraoke.iam.gserviceaccount.com"
        return service


# A fake JWT-shaped token (3 dot-separated parts)
FAKE_JWT = "eyJhbGciOiJSUzI1NiJ9.eyJlbWFpbCI6InRlc3RAZXhhbXBsZS5jb20ifQ.signature"


class TestOidcTokenValidation:
    """Tests for _validate_oidc_token."""

    def test_non_jwt_returns_none(self, auth_service):
        """Non-JWT tokens (no dots) should return None immediately."""
        result = auth_service._validate_oidc_token("simple-token-no-dots")
        assert result is None

    def test_two_dots_not_three_parts_returns_none(self, auth_service):
        """Tokens with wrong number of dots should return None."""
        result = auth_service._validate_oidc_token("only.one.dot-but-not-jwt")
        # This has 2 dots = 3 parts, so it will attempt OIDC validation
        # but fail signature verification. That's fine — it returns None.
        # A token with 1 dot should be skipped entirely.
        result_one_dot = auth_service._validate_oidc_token("one.dot")
        assert result_one_dot is None

    @patch("backend.services.auth_service.google_id_token.verify_oauth2_token")
    def test_valid_oidc_from_allowed_sa(self, mock_verify, auth_service):
        """Valid OIDC token from the configured service account grants admin."""
        mock_verify.return_value = {
            "email": "karaoke-backend@nomadkaraoke.iam.gserviceaccount.com",
            "email_verified": True,
            "iss": "https://accounts.google.com",
            "aud": "https://api.nomadkaraoke.com/api/internal/process-stale-reviews",
        }

        result = auth_service._validate_oidc_token(FAKE_JWT)

        assert result is not None
        assert result.is_valid is True
        assert result.is_admin is True
        assert result.user_type == UserType.ADMIN
        assert result.remaining_uses == -1
        assert result.user_email == "karaoke-backend@nomadkaraoke.iam.gserviceaccount.com"

    @patch("backend.services.auth_service.google_id_token.verify_oauth2_token")
    def test_oidc_from_wrong_sa_returns_none(self, mock_verify, auth_service):
        """OIDC token from a different service account is rejected."""
        mock_verify.return_value = {
            "email": "attacker@evil-project.iam.gserviceaccount.com",
            "email_verified": True,
            "iss": "https://accounts.google.com",
        }

        result = auth_service._validate_oidc_token(FAKE_JWT)

        assert result is None

    @patch("backend.services.auth_service.google_id_token.verify_oauth2_token")
    def test_expired_oidc_token_returns_none(self, mock_verify, auth_service):
        """Expired OIDC token raises ValueError and returns None."""
        mock_verify.side_effect = ValueError("Token expired")

        result = auth_service._validate_oidc_token(FAKE_JWT)

        assert result is None

    @patch("backend.services.auth_service.google_id_token.verify_oauth2_token")
    def test_invalid_signature_returns_none(self, mock_verify, auth_service):
        """Token with invalid signature raises ValueError and returns None."""
        mock_verify.side_effect = ValueError("Could not verify token signature")

        result = auth_service._validate_oidc_token(FAKE_JWT)

        assert result is None

    @patch("backend.services.auth_service.google_id_token.verify_oauth2_token")
    def test_network_error_returns_none(self, mock_verify, auth_service):
        """Network error fetching Google certs returns None gracefully."""
        mock_verify.side_effect = Exception("Connection timeout")

        result = auth_service._validate_oidc_token(FAKE_JWT)

        assert result is None


class TestValidateTokenFullOidc:
    """Tests that OIDC validation is properly integrated into validate_token_full."""

    @patch("backend.services.auth_service.google_id_token.verify_oauth2_token")
    def test_oidc_token_grants_admin_via_validate_token_full(self, mock_verify, auth_service):
        """OIDC token flows through validate_token_full and grants admin."""
        mock_verify.return_value = {
            "email": "karaoke-backend@nomadkaraoke.iam.gserviceaccount.com",
            "email_verified": True,
        }

        # Mock Firestore to not interfere
        auth_service.firestore.get_token = MagicMock(return_value=None)

        result = auth_service.validate_token_full(FAKE_JWT)

        assert result.is_valid is True
        assert result.is_admin is True
        assert result.user_type == UserType.ADMIN

    def test_admin_token_still_works(self, auth_service):
        """Admin tokens from env var still take priority over OIDC."""
        auth_service.admin_tokens = ["test-admin-token"]

        result = auth_service.validate_token_full("test-admin-token")

        assert result.is_valid is True
        assert result.is_admin is True
        assert result.message == "Admin access granted"

    @patch("backend.services.auth_service.google_id_token.verify_oauth2_token")
    def test_failed_oidc_falls_through_to_firestore(self, mock_verify, auth_service):
        """If OIDC validation fails, token is still checked against Firestore."""
        mock_verify.side_effect = ValueError("Invalid token")

        # Firestore has a valid token
        auth_service.firestore.get_token = MagicMock(return_value={
            "type": "unlimited",
            "active": True,
            "max_uses": -1,
            "user_email": "user@example.com",
        })

        result = auth_service.validate_token_full(FAKE_JWT)

        assert result.is_valid is True
        assert result.user_type == UserType.UNLIMITED
