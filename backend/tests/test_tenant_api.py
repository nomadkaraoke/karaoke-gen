"""
Unit tests for tenant API routes.

Tests the /api/tenant/config endpoints.

Note: We need to patch get_tenant_service in both the routes module AND the
middleware module since the middleware runs first and also calls get_tenant_service.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.models.tenant import (
    TenantConfig,
    TenantBranding,
    TenantFeatures,
    TenantDefaults,
    TenantAuth,
    TenantPublicConfig,
)


# Sample tenant config for mocking
SAMPLE_VOCALSTAR_CONFIG = TenantConfig(
    id="vocalstar",
    name="Vocal Star",
    subdomain="vocalstar.nomadkaraoke.com",
    is_active=True,
    branding=TenantBranding(
        logo_url="https://example.com/logo.png",
        primary_color="#ffff00",
        secondary_color="#006CF9",
        site_title="Vocal Star Karaoke Generator",
    ),
    features=TenantFeatures(
        audio_search=False,
        youtube_url=False,
        youtube_upload=False,
        dropbox_upload=False,
        gdrive_upload=False,
        theme_selection=False,
    ),
    defaults=TenantDefaults(
        theme_id="vocalstar",
        locked_theme="vocalstar",
        distribution_mode="download_only",
    ),
    auth=TenantAuth(
        allowed_email_domains=["vocal-star.com", "vocalstarmusic.com"],
        require_email_domain=True,
        sender_email="vocalstar@nomadkaraoke.com",
    ),
)

INACTIVE_CONFIG = TenantConfig(
    id="inactive",
    name="Inactive Tenant",
    subdomain="inactive.nomadkaraoke.com",
    is_active=False,
)


class TestTenantConfigEndpoint:
    """Tests for GET /api/tenant/config endpoint."""

    @pytest.fixture
    def mock_tenant_service(self):
        """Mock the tenant service in both routes and middleware."""
        service = MagicMock()
        # Patch in both places: routes AND middleware
        with patch("backend.api.routes.tenant.get_tenant_service", return_value=service), \
             patch("backend.middleware.tenant.get_tenant_service", return_value=service):
            yield service

    @pytest.fixture
    def client(self, mock_tenant_service):
        """Create test client with mocked tenant service."""
        from backend.main import app
        return TestClient(app)

    def test_get_config_default(self, client, mock_tenant_service):
        """Test returns default config when no tenant detected."""
        mock_tenant_service.tenant_exists.return_value = False

        response = client.get("/api/tenant/config")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant"] is None
        assert data["is_default"] is True

    def test_get_config_with_query_param(self, client, mock_tenant_service):
        """Test tenant detection via query parameter."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get("/api/tenant/config?tenant=vocalstar")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant"]["id"] == "vocalstar"
        assert data["tenant"]["name"] == "Vocal Star"
        assert data["is_default"] is False

    def test_get_config_with_header(self, client, mock_tenant_service):
        """Test tenant detection via X-Tenant-ID header."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get(
            "/api/tenant/config",
            headers={"X-Tenant-ID": "vocalstar"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tenant"]["id"] == "vocalstar"
        assert data["is_default"] is False

    def test_get_config_query_param_takes_priority(self, client, mock_tenant_service):
        """Test query param takes priority over header."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get(
            "/api/tenant/config?tenant=vocalstar",
            headers={"X-Tenant-ID": "other"},
        )

        assert response.status_code == 200
        # Should use vocalstar from query param
        mock_tenant_service.get_public_config.assert_called_with("vocalstar")

    def test_get_config_tenant_not_found(self, client, mock_tenant_service):
        """Test returns default when tenant not found."""
        mock_tenant_service.get_public_config.return_value = None

        response = client.get("/api/tenant/config?tenant=nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant"] is None
        assert data["is_default"] is True

    def test_get_config_inactive_tenant(self, client, mock_tenant_service):
        """Test returns default when tenant is inactive."""
        inactive_public = TenantPublicConfig.from_config(INACTIVE_CONFIG)
        mock_tenant_service.get_public_config.return_value = inactive_public

        response = client.get("/api/tenant/config?tenant=inactive")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant"] is None
        assert data["is_default"] is True

    def test_get_config_includes_branding(self, client, mock_tenant_service):
        """Test response includes full branding config."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get("/api/tenant/config?tenant=vocalstar")

        assert response.status_code == 200
        data = response.json()
        branding = data["tenant"]["branding"]
        assert branding["primary_color"] == "#ffff00"
        assert branding["secondary_color"] == "#006CF9"
        assert branding["site_title"] == "Vocal Star Karaoke Generator"

    def test_get_config_includes_features(self, client, mock_tenant_service):
        """Test response includes feature flags."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get("/api/tenant/config?tenant=vocalstar")

        assert response.status_code == 200
        data = response.json()
        features = data["tenant"]["features"]
        assert features["audio_search"] is False
        assert features["file_upload"] is True
        assert features["youtube_upload"] is False

    def test_get_config_includes_defaults(self, client, mock_tenant_service):
        """Test response includes default settings."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get("/api/tenant/config?tenant=vocalstar")

        assert response.status_code == 200
        data = response.json()
        defaults = data["tenant"]["defaults"]
        assert defaults["theme_id"] == "vocalstar"
        assert defaults["locked_theme"] == "vocalstar"
        assert defaults["distribution_mode"] == "download_only"

    def test_get_config_includes_allowed_domains(self, client, mock_tenant_service):
        """Test response includes allowed email domains."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get("/api/tenant/config?tenant=vocalstar")

        assert response.status_code == 200
        data = response.json()
        assert "vocal-star.com" in data["tenant"]["allowed_email_domains"]
        assert "vocalstarmusic.com" in data["tenant"]["allowed_email_domains"]


class TestTenantConfigByIdEndpoint:
    """Tests for GET /api/tenant/config/{tenant_id} endpoint."""

    @pytest.fixture
    def mock_tenant_service(self):
        """Mock the tenant service in both routes and middleware."""
        service = MagicMock()
        with patch("backend.api.routes.tenant.get_tenant_service", return_value=service), \
             patch("backend.middleware.tenant.get_tenant_service", return_value=service):
            yield service

    @pytest.fixture
    def client(self, mock_tenant_service):
        """Create test client with mocked tenant service."""
        from backend.main import app
        return TestClient(app)

    def test_get_config_by_id_success(self, client, mock_tenant_service):
        """Test getting config by explicit ID."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get("/api/tenant/config/vocalstar")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant"]["id"] == "vocalstar"
        assert data["is_default"] is False

    def test_get_config_by_id_not_found(self, client, mock_tenant_service):
        """Test returns default when tenant ID not found."""
        mock_tenant_service.get_public_config.return_value = None

        response = client.get("/api/tenant/config/nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant"] is None
        assert data["is_default"] is True

    def test_get_config_by_id_inactive(self, client, mock_tenant_service):
        """Test returns default when tenant is inactive."""
        inactive_public = TenantPublicConfig.from_config(INACTIVE_CONFIG)
        mock_tenant_service.get_public_config.return_value = inactive_public

        response = client.get("/api/tenant/config/inactive")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant"] is None
        assert data["is_default"] is True


class TestTenantAssetEndpoint:
    """Tests for GET /api/tenant/asset/{tenant_id}/{asset_name} endpoint."""

    @pytest.fixture
    def mock_tenant_service(self):
        """Mock the tenant service in both routes and middleware."""
        service = MagicMock()
        with patch("backend.api.routes.tenant.get_tenant_service", return_value=service), \
             patch("backend.middleware.tenant.get_tenant_service", return_value=service):
            yield service

    @pytest.fixture
    def client(self, mock_tenant_service):
        """Create test client with mocked tenant service."""
        from backend.main import app
        return TestClient(app)

    def test_get_asset_redirects(self, client, mock_tenant_service):
        """Test asset endpoint redirects to signed URL."""
        mock_tenant_service.get_asset_url.return_value = "https://storage.googleapis.com/signed-url"

        response = client.get(
            "/api/tenant/asset/vocalstar/logo.png",
            follow_redirects=False,
        )

        # Should redirect (307 Temporary Redirect is FastAPI default)
        assert response.status_code == 307
        assert "storage.googleapis.com" in response.headers["location"]

    def test_get_asset_not_found(self, client, mock_tenant_service):
        """Test returns 404 when asset not found."""
        mock_tenant_service.get_asset_url.return_value = None

        response = client.get("/api/tenant/asset/vocalstar/missing.png")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestTenantConfigResponseSchema:
    """Tests for response schema validation."""

    @pytest.fixture
    def mock_tenant_service(self):
        """Mock the tenant service in both routes and middleware."""
        service = MagicMock()
        with patch("backend.api.routes.tenant.get_tenant_service", return_value=service), \
             patch("backend.middleware.tenant.get_tenant_service", return_value=service):
            yield service

    @pytest.fixture
    def client(self, mock_tenant_service):
        """Create test client with mocked tenant service."""
        from backend.main import app
        return TestClient(app)

    def test_response_has_required_fields(self, client, mock_tenant_service):
        """Test response always has required fields."""
        mock_tenant_service.tenant_exists.return_value = False

        response = client.get("/api/tenant/config")

        assert response.status_code == 200
        data = response.json()
        assert "tenant" in data
        assert "is_default" in data

    def test_tenant_config_excludes_sensitive_fields(self, client, mock_tenant_service):
        """Test public config doesn't include sensitive auth data."""
        public_config = TenantPublicConfig.from_config(SAMPLE_VOCALSTAR_CONFIG)
        mock_tenant_service.get_public_config.return_value = public_config

        response = client.get("/api/tenant/config?tenant=vocalstar")

        assert response.status_code == 200
        data = response.json()
        tenant = data["tenant"]

        # Should NOT have full auth object
        assert "auth" not in tenant
        # Should NOT have sensitive defaults
        assert tenant["defaults"].get("brand_prefix") is None
        # Should have allowed_email_domains at top level
        assert "allowed_email_domains" in tenant
