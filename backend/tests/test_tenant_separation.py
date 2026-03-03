"""
Tests for whitelabel tenant separation (magic link redirect fix, frontend URL, verify response,
tenant-scoped tokens).
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.models.tenant import TenantConfig, TenantAuth, TenantBranding
from backend.models.user import VerifyMagicLinkResponse, UserPublic
from backend.services.auth_service import AuthResult, AuthService, UserType


class TestTenantConfigGetFrontendUrl:
    """Tests for TenantConfig.get_frontend_url()."""

    def test_returns_https_url_with_subdomain(self):
        config = TenantConfig(
            id="vocalstar",
            name="Vocal Star",
            subdomain="vocalstar.nomadkaraoke.com",
        )
        assert config.get_frontend_url() == "https://vocalstar.nomadkaraoke.com"

    def test_different_tenant(self):
        config = TenantConfig(
            id="singa",
            name="Singa",
            subdomain="singa.nomadkaraoke.com",
        )
        assert config.get_frontend_url() == "https://singa.nomadkaraoke.com"


class TestSendMagicLinkTenantParams:
    """Tests for EmailService.send_magic_link() with tenant parameters."""

    @pytest.fixture
    def email_service(self):
        """Create an EmailService with a mock provider."""
        from backend.services.email_service import EmailService
        service = EmailService()
        service.provider = MagicMock()
        service.provider.send_email = MagicMock(return_value=True)
        return service

    def test_default_uses_nomad_karaoke_branding(self, email_service):
        """Without tenant params, email uses default Nomad Karaoke branding."""
        email_service.send_magic_link("user@test.com", "abc123")

        call_args = email_service.provider.send_email.call_args
        subject = call_args[0][1]
        html_content = call_args[0][2]
        text_content = call_args[0][3]

        assert subject == "Sign in to Nomad Karaoke"
        assert "Nomad Karaoke" in html_content
        assert "Nomad Karaoke" in text_content
        # Default URL should be used
        assert f"{email_service.frontend_url}/auth/verify" in html_content

    def test_tenant_url_used_in_magic_link(self, email_service):
        """When tenant_frontend_url is provided, magic link points to tenant domain."""
        email_service.send_magic_link(
            "user@vocal-star.com",
            "abc123",
            tenant_frontend_url="https://vocalstar.nomadkaraoke.com",
        )

        call_args = email_service.provider.send_email.call_args
        html_content = call_args[0][2]
        text_content = call_args[0][3]

        assert "https://vocalstar.nomadkaraoke.com/auth/verify?token=abc123" in html_content
        assert "https://vocalstar.nomadkaraoke.com/auth/verify?token=abc123" in text_content
        # Should NOT contain default URL
        assert "gen.nomadkaraoke.com" not in html_content

    def test_tenant_name_used_in_subject_and_body(self, email_service):
        """When tenant_name is provided, email uses tenant brand name."""
        email_service.send_magic_link(
            "user@vocal-star.com",
            "abc123",
            tenant_name="Vocal Star",
        )

        call_args = email_service.provider.send_email.call_args
        subject = call_args[0][1]
        html_content = call_args[0][2]
        text_content = call_args[0][3]

        assert subject == "Sign in to Vocal Star"
        assert "Vocal Star" in html_content
        assert "Vocal Star" in text_content

    def test_tenant_name_html_escaped(self, email_service):
        """Tenant name is HTML-escaped to prevent XSS."""
        email_service.send_magic_link(
            "user@test.com",
            "abc123",
            tenant_name='<script>alert("xss")</script>',
        )

        call_args = email_service.provider.send_email.call_args
        html_content = call_args[0][2]

        # Should be escaped in HTML
        assert "<script>" not in html_content
        assert "&lt;script&gt;" in html_content

    def test_all_tenant_params_together(self, email_service):
        """Test sending magic link with all tenant parameters."""
        result = email_service.send_magic_link(
            "user@vocal-star.com",
            "token123",
            sender_email="vocalstar@nomadkaraoke.com",
            tenant_frontend_url="https://vocalstar.nomadkaraoke.com",
            tenant_name="Vocal Star",
        )

        assert result is True

        call_args = email_service.provider.send_email.call_args
        subject = call_args[0][1]
        html_content = call_args[0][2]

        assert subject == "Sign in to Vocal Star"
        assert "https://vocalstar.nomadkaraoke.com/auth/verify?token=token123" in html_content
        # Check sender email override was passed through
        assert call_args[1]["from_email_override"] == "vocalstar@nomadkaraoke.com"


class TestVerifyMagicLinkResponseTenantSubdomain:
    """Tests for tenant_subdomain field in VerifyMagicLinkResponse."""

    def test_response_without_tenant(self):
        """Verify response without tenant has null subdomain."""
        user = UserPublic(email="user@test.com", role="user", credits=2)
        response = VerifyMagicLinkResponse(
            status="success",
            session_token="sess_123",
            user=user,
            message="ok",
        )
        assert response.tenant_subdomain is None

    def test_response_with_tenant_subdomain(self):
        """Verify response includes tenant subdomain when provided."""
        user = UserPublic(email="user@vocal-star.com", role="user", credits=2)
        response = VerifyMagicLinkResponse(
            status="success",
            session_token="sess_123",
            user=user,
            message="ok",
            tenant_subdomain="vocalstar.nomadkaraoke.com",
        )
        assert response.tenant_subdomain == "vocalstar.nomadkaraoke.com"

    def test_response_serialization_includes_tenant_subdomain(self):
        """Verify response serializes tenant_subdomain correctly."""
        user = UserPublic(email="user@test.com", role="user", credits=2)
        response = VerifyMagicLinkResponse(
            status="success",
            session_token="sess_123",
            user=user,
            message="ok",
            tenant_subdomain="singa.nomadkaraoke.com",
        )
        data = response.model_dump()
        assert data["tenant_subdomain"] == "singa.nomadkaraoke.com"


class TestTenantScopedTokens:
    """Tests for tenant_id propagation from Firestore auth tokens to AuthResult."""

    @pytest.fixture
    def auth_service(self):
        """Create an AuthService with mocked Firestore."""
        service = AuthService()
        service.firestore = MagicMock()
        service.admin_tokens = set()
        return service

    def test_token_without_tenant_id(self, auth_service):
        """Auth token without tenant_id returns None tenant_id in result."""
        auth_service.firestore.get_token.return_value = {
            "type": "unlimited",
            "active": True,
            "user_email": "user@test.com",
        }
        result = auth_service.validate_token_full("test-token")
        assert result.is_valid is True
        assert result.tenant_id is None

    def test_token_with_tenant_id(self, auth_service):
        """Auth token with tenant_id propagates it to AuthResult."""
        auth_service.firestore.get_token.return_value = {
            "type": "unlimited",
            "active": True,
            "user_email": "admin@nomadkaraoke.com",
            "tenant_id": "vocalstar",
        }
        result = auth_service.validate_token_full("test-token")
        assert result.is_valid is True
        assert result.tenant_id == "vocalstar"
        assert result.is_admin is True

    def test_limited_token_with_tenant_id(self, auth_service):
        """Limited token with tenant_id propagates it."""
        auth_service.firestore.get_token.return_value = {
            "type": "limited",
            "active": True,
            "user_email": "user@test.com",
            "tenant_id": "singa",
            "max_uses": 10,
            "usage_count": 3,
        }
        result = auth_service.validate_token_full("test-token")
        assert result.is_valid is True
        assert result.tenant_id == "singa"
        assert result.remaining_uses == 7

    def test_api_key_with_tenant_id(self, auth_service):
        """API key token with tenant_id propagates it."""
        auth_service.firestore.get_token.return_value = {
            "type": "api_key",
            "active": True,
            "user_email": "api@vocal-star.com",
            "tenant_id": "vocalstar",
            "api_key_id": "ak_123",
        }
        result = auth_service.validate_token_full("test-token")
        assert result.is_valid is True
        assert result.tenant_id == "vocalstar"
        assert result.api_key_id == "ak_123"

    def test_admin_env_token_has_no_tenant_id(self, auth_service):
        """Admin tokens from env var should not have tenant scoping."""
        auth_service.admin_tokens = {"admin-secret-token"}
        result = auth_service.validate_token_full("admin-secret-token")
        assert result.is_valid is True
        assert result.is_admin is True
        assert result.tenant_id is None


class TestRequireAuthTenantOverride:
    """Tests for tenant_id override logic in require_auth dependency."""

    def _make_request(self, initial_tenant_id="hostname-tenant"):
        """Create a mock Request with request.state.tenant_id pre-set."""
        request = MagicMock()
        request.state.tenant_id = initial_tenant_id
        request.state.request_id = "req-123"
        request.headers.get.return_value = ""
        return request

    @pytest.mark.asyncio
    async def test_auth_result_with_tenant_id_overrides_request_state(self):
        """When auth_result has tenant_id, it overrides the hostname-based tenant_id."""
        from backend.api.dependencies import require_auth

        request = self._make_request(initial_tenant_id="hostname-tenant")

        mock_auth_result = AuthResult(
            is_valid=True,
            user_type=UserType.UNLIMITED,
            remaining_uses=-1,
            message="ok",
            tenant_id="token-tenant",
        )
        mock_auth_service = MagicMock()
        mock_auth_service.validate_token_full.return_value = mock_auth_result

        with patch("backend.api.dependencies.get_auth_service", return_value=mock_auth_service):
            result = await require_auth(
                request=request,
                auth_service=mock_auth_service,
                credentials=MagicMock(credentials="valid-token"),
                token=None,
            )

        assert result.tenant_id == "token-tenant"
        assert request.state.tenant_id == "token-tenant"

    @pytest.mark.asyncio
    async def test_auth_result_without_tenant_id_leaves_request_state_unchanged(self):
        """When auth_result has no tenant_id, request.state.tenant_id is not changed."""
        from backend.api.dependencies import require_auth

        request = self._make_request(initial_tenant_id="hostname-tenant")

        mock_auth_result = AuthResult(
            is_valid=True,
            user_type=UserType.UNLIMITED,
            remaining_uses=-1,
            message="ok",
            tenant_id=None,
        )
        mock_auth_service = MagicMock()
        mock_auth_service.validate_token_full.return_value = mock_auth_result

        with patch("backend.api.dependencies.get_auth_service", return_value=mock_auth_service):
            result = await require_auth(
                request=request,
                auth_service=mock_auth_service,
                credentials=MagicMock(credentials="valid-token"),
                token=None,
            )

        assert result.tenant_id is None
        assert request.state.tenant_id == "hostname-tenant"
