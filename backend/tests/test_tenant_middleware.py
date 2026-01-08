"""
Unit tests for TenantMiddleware.

Tests tenant detection from headers, query params, and subdomains,
as well as request state attachment.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import os

from starlette.requests import Request
from starlette.datastructures import Headers
from starlette.responses import Response

from backend.middleware.tenant import (
    TenantMiddleware,
    get_tenant_from_request,
    get_tenant_config_from_request,
    NON_TENANT_SUBDOMAINS,
)
from backend.models.tenant import TenantConfig, TenantFeatures


# Sample tenant config for mocking
SAMPLE_CONFIG = TenantConfig(
    id="vocalstar",
    name="Vocal Star",
    subdomain="vocalstar.nomadkaraoke.com",
    is_active=True,
    features=TenantFeatures(audio_search=False),
)

INACTIVE_CONFIG = TenantConfig(
    id="inactive",
    name="Inactive",
    subdomain="inactive.nomadkaraoke.com",
    is_active=False,
)


class MockRequest:
    """Mock Request object for testing."""

    def __init__(self, headers=None, query_params=None):
        self.headers = headers or {}
        self.query_params = query_params or {}
        self.url = MagicMock()
        self.url.path = "/api/test"
        self.state = MagicMock()


class TestTenantMiddleware:
    """Tests for TenantMiddleware class."""

    @pytest.fixture
    def middleware(self):
        """Create middleware instance."""
        app = MagicMock()
        return TenantMiddleware(app)

    @pytest.fixture
    def mock_tenant_service(self):
        """Create mock tenant service."""
        with patch("backend.middleware.tenant.get_tenant_service") as mock:
            service = MagicMock()
            mock.return_value = service
            yield service

    # Tests for _extract_tenant_from_host()
    def test_extract_tenant_from_host_standard_subdomain(self, middleware, mock_tenant_service):
        """Test extracting tenant from vocalstar.nomadkaraoke.com."""
        mock_tenant_service.tenant_exists.return_value = True

        tenant_id = middleware._extract_tenant_from_host("vocalstar.nomadkaraoke.com")

        assert tenant_id == "vocalstar"

    def test_extract_tenant_from_host_gen_subdomain(self, middleware, mock_tenant_service):
        """Test extracting tenant from vocalstar.gen.nomadkaraoke.com."""
        mock_tenant_service.tenant_exists.return_value = True

        tenant_id = middleware._extract_tenant_from_host("vocalstar.gen.nomadkaraoke.com")

        assert tenant_id == "vocalstar"

    def test_extract_tenant_from_host_with_port(self, middleware, mock_tenant_service):
        """Test extracting tenant when host includes port."""
        mock_tenant_service.tenant_exists.return_value = True

        tenant_id = middleware._extract_tenant_from_host("vocalstar.nomadkaraoke.com:443")

        assert tenant_id == "vocalstar"

    def test_extract_tenant_from_host_case_insensitive(self, middleware, mock_tenant_service):
        """Test host parsing is case insensitive."""
        mock_tenant_service.tenant_exists.return_value = True

        tenant_id = middleware._extract_tenant_from_host("VOCALSTAR.NomadKaraoke.COM")

        assert tenant_id == "vocalstar"

    def test_extract_tenant_from_host_empty(self, middleware):
        """Test returns None for empty host."""
        tenant_id = middleware._extract_tenant_from_host("")

        assert tenant_id is None

    def test_extract_tenant_from_host_localhost(self, middleware):
        """Test returns None for localhost."""
        tenant_id = middleware._extract_tenant_from_host("localhost:3000")

        assert tenant_id is None

    def test_extract_tenant_from_host_non_nomad_domain(self, middleware):
        """Test returns None for non-nomadkaraoke.com domains."""
        tenant_id = middleware._extract_tenant_from_host("example.com")

        assert tenant_id is None

    def test_extract_tenant_from_host_base_domain(self, middleware):
        """Test returns None for nomadkaraoke.com without subdomain."""
        tenant_id = middleware._extract_tenant_from_host("nomadkaraoke.com")

        assert tenant_id is None

    @pytest.mark.parametrize("subdomain", NON_TENANT_SUBDOMAINS)
    def test_extract_tenant_from_host_non_tenant_subdomains(self, middleware, subdomain):
        """Test returns None for known non-tenant subdomains."""
        host = f"{subdomain}.nomadkaraoke.com"

        tenant_id = middleware._extract_tenant_from_host(host)

        assert tenant_id is None

    def test_extract_tenant_from_host_tenant_not_exists(self, middleware, mock_tenant_service):
        """Test returns None when tenant doesn't exist in GCS."""
        mock_tenant_service.tenant_exists.return_value = False

        tenant_id = middleware._extract_tenant_from_host("nonexistent.nomadkaraoke.com")

        assert tenant_id is None

    # Tests for _extract_tenant_id()
    def test_extract_tenant_id_from_header(self, middleware):
        """Test X-Tenant-ID header takes priority."""
        request = MockRequest(
            headers={"X-Tenant-ID": "vocalstar", "Host": "other.nomadkaraoke.com"},
            query_params={"tenant": "different"},
        )

        tenant_id = middleware._extract_tenant_id(request)

        assert tenant_id == "vocalstar"

    def test_extract_tenant_id_header_normalized(self, middleware):
        """Test header value is normalized (lowercased, stripped)."""
        request = MockRequest(headers={"X-Tenant-ID": "  VOCALSTAR  "})

        tenant_id = middleware._extract_tenant_id(request)

        assert tenant_id == "vocalstar"

    @patch.dict(os.environ, {"ENV": ""})
    def test_extract_tenant_id_from_query_param_dev(self, middleware):
        """Test query param works in non-production."""
        request = MockRequest(query_params={"tenant": "vocalstar"})

        tenant_id = middleware._extract_tenant_id(request)

        assert tenant_id == "vocalstar"

    @patch.dict(os.environ, {"ENV": "production"})
    def test_extract_tenant_id_query_param_disabled_in_prod(self, middleware, mock_tenant_service):
        """Test query param is ignored in production."""
        # Need to reimport to pick up new env value
        import importlib
        import backend.middleware.tenant as tenant_module

        # Save original value
        original_is_prod = tenant_module.IS_PRODUCTION

        # Temporarily set to True for this test
        tenant_module.IS_PRODUCTION = True

        try:
            mock_tenant_service.tenant_exists.return_value = False
            request = MockRequest(
                query_params={"tenant": "vocalstar"},
                headers={"Host": "gen.nomadkaraoke.com"},
            )

            tenant_id = middleware._extract_tenant_id(request)

            # Should not use query param, and gen subdomain is non-tenant
            assert tenant_id is None
        finally:
            # Restore
            tenant_module.IS_PRODUCTION = original_is_prod

    def test_extract_tenant_id_from_host(self, middleware, mock_tenant_service):
        """Test falls back to host header subdomain detection."""
        mock_tenant_service.tenant_exists.return_value = True
        request = MockRequest(headers={"Host": "vocalstar.nomadkaraoke.com"})

        tenant_id = middleware._extract_tenant_id(request)

        assert tenant_id == "vocalstar"

    def test_extract_tenant_id_no_tenant(self, middleware, mock_tenant_service):
        """Test returns None when no tenant detected."""
        mock_tenant_service.tenant_exists.return_value = False
        request = MockRequest(headers={"Host": "gen.nomadkaraoke.com"})

        tenant_id = middleware._extract_tenant_id(request)

        assert tenant_id is None

    # Tests for dispatch()
    @pytest.mark.asyncio
    async def test_dispatch_attaches_tenant_to_state(self, middleware, mock_tenant_service):
        """Test middleware attaches tenant info to request.state."""
        mock_tenant_service.get_tenant_config.return_value = SAMPLE_CONFIG
        mock_tenant_service.tenant_exists.return_value = True

        request = MockRequest(headers={"X-Tenant-ID": "vocalstar"})
        response = Response(content="OK")
        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        assert request.state.tenant_id == "vocalstar"
        assert request.state.tenant_config == SAMPLE_CONFIG

    @pytest.mark.asyncio
    async def test_dispatch_adds_header_to_response(self, middleware, mock_tenant_service):
        """Test middleware adds X-Tenant-ID header to response."""
        mock_tenant_service.get_tenant_config.return_value = SAMPLE_CONFIG
        mock_tenant_service.tenant_exists.return_value = True

        request = MockRequest(headers={"X-Tenant-ID": "vocalstar"})
        response = Response(content="OK")
        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        assert result.headers.get("X-Tenant-ID") == "vocalstar"

    @pytest.mark.asyncio
    async def test_dispatch_no_tenant(self, middleware, mock_tenant_service):
        """Test middleware handles no tenant gracefully."""
        mock_tenant_service.tenant_exists.return_value = False

        request = MockRequest(headers={"Host": "gen.nomadkaraoke.com"})
        response = Response(content="OK")
        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        assert request.state.tenant_id is None
        assert request.state.tenant_config is None
        assert "X-Tenant-ID" not in result.headers

    @pytest.mark.asyncio
    async def test_dispatch_inactive_tenant(self, middleware, mock_tenant_service):
        """Test middleware treats inactive tenant as no tenant."""
        mock_tenant_service.get_tenant_config.return_value = INACTIVE_CONFIG

        request = MockRequest(headers={"X-Tenant-ID": "inactive"})
        response = Response(content="OK")
        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        # Inactive tenant should be treated as default
        assert request.state.tenant_id is None
        assert request.state.tenant_config is None

    @pytest.mark.asyncio
    async def test_dispatch_calls_next(self, middleware, mock_tenant_service):
        """Test middleware calls the next handler."""
        mock_tenant_service.tenant_exists.return_value = False

        request = MockRequest(headers={"Host": "gen.nomadkaraoke.com"})
        response = Response(content="OK")
        call_next = AsyncMock(return_value=response)

        await middleware.dispatch(request, call_next)

        call_next.assert_called_once_with(request)


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_tenant_from_request(self):
        """Test get_tenant_from_request helper."""
        request = MagicMock()
        request.state.tenant_id = "vocalstar"

        assert get_tenant_from_request(request) == "vocalstar"

    def test_get_tenant_from_request_none(self):
        """Test get_tenant_from_request when no tenant."""
        request = MagicMock()
        del request.state.tenant_id  # Simulate missing attribute

        assert get_tenant_from_request(request) is None

    def test_get_tenant_config_from_request(self):
        """Test get_tenant_config_from_request helper."""
        request = MagicMock()
        request.state.tenant_config = SAMPLE_CONFIG

        config = get_tenant_config_from_request(request)

        assert config == SAMPLE_CONFIG
        assert config.features.audio_search is False

    def test_get_tenant_config_from_request_none(self):
        """Test get_tenant_config_from_request when no config."""
        request = MagicMock()
        del request.state.tenant_config

        assert get_tenant_config_from_request(request) is None


class TestNonTenantSubdomains:
    """Tests for NON_TENANT_SUBDOMAINS constant."""

    def test_gen_is_non_tenant(self):
        """Test 'gen' is in non-tenant subdomains."""
        assert "gen" in NON_TENANT_SUBDOMAINS

    def test_api_is_non_tenant(self):
        """Test 'api' is in non-tenant subdomains."""
        assert "api" in NON_TENANT_SUBDOMAINS

    def test_www_is_non_tenant(self):
        """Test 'www' is in non-tenant subdomains."""
        assert "www" in NON_TENANT_SUBDOMAINS

    def test_admin_is_non_tenant(self):
        """Test 'admin' is in non-tenant subdomains."""
        assert "admin" in NON_TENANT_SUBDOMAINS
