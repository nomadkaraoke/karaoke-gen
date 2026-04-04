"""
Tests for whitelabel tenant separation (magic link redirect fix, frontend URL, verify response,
tenant-scoped tokens, push notification isolation).
"""

import pytest
from unittest.mock import MagicMock, Mock, patch, AsyncMock

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
        assert f"{email_service.frontend_url}/en/auth/verify" in html_content

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

        assert "https://vocalstar.nomadkaraoke.com/en/auth/verify?token=abc123" in html_content
        assert "https://vocalstar.nomadkaraoke.com/en/auth/verify?token=abc123" in text_content
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
        assert "https://vocalstar.nomadkaraoke.com/en/auth/verify?token=token123" in html_content
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
    async def test_auth_result_with_tenant_id_loads_tenant_config(self):
        """When auth_result has tenant_id and tenant_config is missing, it loads the config."""
        from backend.api.dependencies import require_auth

        request = self._make_request(initial_tenant_id=None)
        request.state.tenant_config = None

        mock_auth_result = AuthResult(
            is_valid=True,
            user_type=UserType.UNLIMITED,
            remaining_uses=-1,
            message="ok",
            tenant_id="vocalstar",
        )
        mock_auth_service = MagicMock()
        mock_auth_service.validate_token_full.return_value = mock_auth_result

        mock_tenant_config = TenantConfig(
            id="vocalstar",
            name="Vocal Star",
            subdomain="vocalstar.nomadkaraoke.com",
            is_active=True,
        )
        mock_tenant_service = MagicMock()
        mock_tenant_service.get_tenant_config.return_value = mock_tenant_config

        with patch("backend.api.dependencies.get_auth_service", return_value=mock_auth_service), \
             patch("backend.services.tenant_service.get_tenant_service", return_value=mock_tenant_service):
            result = await require_auth(
                request=request,
                auth_service=mock_auth_service,
                credentials=MagicMock(credentials="valid-token"),
                token=None,
            )

        assert request.state.tenant_id == "vocalstar"
        assert request.state.tenant_config == mock_tenant_config
        mock_tenant_service.get_tenant_config.assert_called_once_with("vocalstar")

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


class TestPushNotificationTenantIsolation:
    """Tests that push notifications respect tenant boundaries end-to-end.

    These tests verify the full flow from job_manager -> push_notification_service,
    ensuring that tenant_id is correctly propagated and used for filtering.
    """

    @pytest.fixture
    def push_service(self):
        """Create PushNotificationService with mocked dependencies."""
        mock_settings = Mock()
        mock_settings.enable_push_notifications = True
        mock_settings.max_push_subscriptions_per_user = 5
        mock_settings.vapid_subject = "mailto:test@example.com"
        mock_settings.get_secret = Mock(side_effect=lambda x: {
            "vapid-public-key": "test-public-key",
            "vapid-private-key": "test-private-key"
        }.get(x))

        mock_db = Mock()

        with patch('backend.services.push_notification_service.get_settings', return_value=mock_settings):
            from backend.services.push_notification_service import PushNotificationService
            service = PushNotificationService(db=mock_db)
            return service

    def _setup_multi_tenant_user(self, push_service):
        """Set up a user with subscriptions across multiple tenants."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [
                {
                    "endpoint": "https://push.example.com/consumer",
                    "keys": {"p256dh": "key", "auth": "auth"},
                    "tenant_id": None,
                },
                {
                    "endpoint": "https://push.example.com/vocalstar",
                    "keys": {"p256dh": "key", "auth": "auth"},
                    "tenant_id": "vocalstar",
                },
                {
                    "endpoint": "https://push.example.com/singa",
                    "keys": {"p256dh": "key", "auth": "auth"},
                    "tenant_id": "singa",
                },
            ]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

    @pytest.mark.asyncio
    async def test_job_manager_passes_tenant_id_in_job_dict(self):
        """JobManager._send_push_notification includes tenant_id in job dict."""
        from backend.services.job_manager import JobManager

        job = Mock()
        job.job_id = "job-123"
        job.user_email = "user@test.com"
        job.artist = "Artist"
        job.title = "Song"
        job.tenant_id = "vocalstar"

        captured_job_dict = {}

        async def capture_completion(job_dict):
            captured_job_dict.update(job_dict)
            return 1

        with patch('backend.services.push_notification_service.get_push_notification_service') as mock_get:
            mock_service = Mock()
            mock_service.is_enabled.return_value = True
            mock_service.send_completion_notification = AsyncMock(side_effect=capture_completion)
            mock_get.return_value = mock_service

            manager = JobManager.__new__(JobManager)
            manager._send_push_notification(job, "complete")

            # Give the async task a moment to execute
            import asyncio
            await asyncio.sleep(0.1)

            assert captured_job_dict.get("tenant_id") == "vocalstar"

    @pytest.mark.asyncio
    async def test_consumer_job_never_notifies_tenant_subscriptions(self, push_service):
        """A consumer portal job must never trigger notifications on tenant subscriptions."""
        self._setup_multi_tenant_user(push_service)

        with patch('backend.services.push_notification_service.webpush') as mock_webpush:
            job = {
                "job_id": "consumer-job-1",
                "user_email": "user@test.com",
                "artist": "Artist",
                "title": "Song",
                # No tenant_id = consumer job
            }

            await push_service.send_completion_notification(job)

            # Should only send to consumer endpoint
            assert mock_webpush.call_count == 1
            sent_endpoint = mock_webpush.call_args[1]["subscription_info"]["endpoint"]
            assert sent_endpoint == "https://push.example.com/consumer"

    @pytest.mark.asyncio
    async def test_tenant_job_never_notifies_consumer_subscriptions(self, push_service):
        """A tenant job must never trigger notifications on consumer subscriptions."""
        self._setup_multi_tenant_user(push_service)

        with patch('backend.services.push_notification_service.webpush') as mock_webpush:
            job = {
                "job_id": "vocalstar-job-1",
                "user_email": "user@test.com",
                "artist": "Artist",
                "title": "Song",
                "tenant_id": "vocalstar",
            }

            await push_service.send_completion_notification(job)

            # Should only send to vocalstar endpoint
            assert mock_webpush.call_count == 1
            sent_endpoint = mock_webpush.call_args[1]["subscription_info"]["endpoint"]
            assert sent_endpoint == "https://push.example.com/vocalstar"

    @pytest.mark.asyncio
    async def test_tenant_a_never_notifies_tenant_b(self, push_service):
        """Tenant A job must never trigger notifications on Tenant B subscriptions."""
        self._setup_multi_tenant_user(push_service)

        with patch('backend.services.push_notification_service.webpush') as mock_webpush:
            job = {
                "job_id": "singa-job-1",
                "user_email": "user@test.com",
                "artist": "Artist",
                "title": "Song",
                "tenant_id": "singa",
            }

            await push_service.send_blocking_notification(job, "lyrics")

            # Should only send to singa endpoint, never vocalstar or consumer
            assert mock_webpush.call_count == 1
            sent_endpoint = mock_webpush.call_args[1]["subscription_info"]["endpoint"]
            assert sent_endpoint == "https://push.example.com/singa"
