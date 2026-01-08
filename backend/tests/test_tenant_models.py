"""
Unit tests for tenant data models.

Tests the TenantConfig, TenantBranding, TenantFeatures, TenantDefaults,
TenantAuth, and TenantPublicConfig models.
"""

import pytest
from datetime import datetime

from backend.models.tenant import (
    TenantConfig,
    TenantBranding,
    TenantFeatures,
    TenantDefaults,
    TenantAuth,
    TenantPublicConfig,
    TenantConfigResponse,
)


class TestTenantBranding:
    """Tests for TenantBranding model."""

    def test_default_values(self):
        """Test that TenantBranding has sensible defaults."""
        branding = TenantBranding()

        assert branding.logo_url is None
        assert branding.logo_height == 40
        assert branding.primary_color == "#ff5bb8"
        assert branding.secondary_color == "#8b5cf6"
        assert branding.accent_color is None
        assert branding.background_color is None
        assert branding.favicon_url is None
        assert branding.site_title == "Karaoke Generator"
        assert branding.tagline is None

    def test_custom_values(self):
        """Test TenantBranding with custom values."""
        branding = TenantBranding(
            logo_url="https://example.com/logo.png",
            logo_height=60,
            primary_color="#ffff00",
            secondary_color="#0000ff",
            accent_color="#ff0000",
            background_color="#000000",
            favicon_url="https://example.com/favicon.ico",
            site_title="Custom Karaoke",
            tagline="Make music magic",
        )

        assert branding.logo_url == "https://example.com/logo.png"
        assert branding.logo_height == 60
        assert branding.primary_color == "#ffff00"
        assert branding.site_title == "Custom Karaoke"


class TestTenantFeatures:
    """Tests for TenantFeatures model."""

    def test_default_features_enabled(self):
        """Test that most features are enabled by default."""
        features = TenantFeatures()

        # Input methods
        assert features.audio_search is True
        assert features.file_upload is True
        assert features.youtube_url is True

        # Distribution
        assert features.youtube_upload is True
        assert features.dropbox_upload is True
        assert features.gdrive_upload is True

        # Customization
        assert features.theme_selection is True
        assert features.color_overrides is True

        # Output formats
        assert features.enable_cdg is True
        assert features.enable_4k is True

        # Admin is disabled by default
        assert features.admin_access is False

    def test_restricted_features(self):
        """Test a restricted feature set like Vocal Star would have."""
        features = TenantFeatures(
            audio_search=False,
            youtube_url=False,
            youtube_upload=False,
            dropbox_upload=False,
            gdrive_upload=False,
            theme_selection=False,
            color_overrides=False,
        )

        assert features.audio_search is False
        assert features.file_upload is True  # Still allowed
        assert features.youtube_url is False
        assert features.youtube_upload is False


class TestTenantDefaults:
    """Tests for TenantDefaults model."""

    def test_default_values(self):
        """Test TenantDefaults has sensible defaults."""
        defaults = TenantDefaults()

        assert defaults.theme_id is None
        assert defaults.locked_theme is None
        assert defaults.distribution_mode == "all"
        assert defaults.brand_prefix is None
        assert defaults.youtube_description_template is None

    def test_locked_theme(self):
        """Test locked_theme setting."""
        defaults = TenantDefaults(
            theme_id="vocalstar",
            locked_theme="vocalstar",
            distribution_mode="download_only",
            brand_prefix="VSTAR",
        )

        assert defaults.locked_theme == "vocalstar"
        assert defaults.distribution_mode == "download_only"
        assert defaults.brand_prefix == "VSTAR"


class TestTenantAuth:
    """Tests for TenantAuth model."""

    def test_default_values(self):
        """Test TenantAuth defaults."""
        auth = TenantAuth()

        assert auth.allowed_email_domains == []
        assert auth.require_email_domain is True
        assert auth.fixed_token_ids == []
        assert auth.sender_email is None

    def test_restricted_domains(self):
        """Test restricted email domains configuration."""
        auth = TenantAuth(
            allowed_email_domains=["vocal-star.com", "vocalstarmusic.com"],
            require_email_domain=True,
            sender_email="vocalstar@nomadkaraoke.com",
        )

        assert "vocal-star.com" in auth.allowed_email_domains
        assert auth.require_email_domain is True
        assert auth.sender_email == "vocalstar@nomadkaraoke.com"


class TestTenantConfig:
    """Tests for TenantConfig model."""

    @pytest.fixture
    def basic_config(self):
        """Create a basic tenant config for testing."""
        return TenantConfig(
            id="vocalstar",
            name="Vocal Star",
            subdomain="vocalstar.nomadkaraoke.com",
        )

    @pytest.fixture
    def full_config(self):
        """Create a fully configured tenant."""
        return TenantConfig(
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

    def test_required_fields(self):
        """Test that id, name, and subdomain are required."""
        config = TenantConfig(
            id="test",
            name="Test Tenant",
            subdomain="test.nomadkaraoke.com",
        )

        assert config.id == "test"
        assert config.name == "Test Tenant"
        assert config.subdomain == "test.nomadkaraoke.com"

    def test_default_is_active(self, basic_config):
        """Test that tenants are active by default."""
        assert basic_config.is_active is True

    def test_nested_models_have_defaults(self, basic_config):
        """Test that nested models are created with defaults."""
        assert basic_config.branding is not None
        assert basic_config.features is not None
        assert basic_config.defaults is not None
        assert basic_config.auth is not None

    # Tests for get_sender_email()
    def test_get_sender_email_custom(self, full_config):
        """Test get_sender_email returns custom sender when configured."""
        assert full_config.get_sender_email() == "vocalstar@nomadkaraoke.com"

    def test_get_sender_email_default_pattern(self, basic_config):
        """Test get_sender_email returns default pattern when not configured."""
        # basic_config has no custom sender_email
        assert basic_config.get_sender_email() == "vocalstar@nomadkaraoke.com"

    def test_get_sender_email_different_tenant(self):
        """Test sender email follows tenant ID pattern."""
        config = TenantConfig(
            id="customtenant",
            name="Custom",
            subdomain="custom.nomadkaraoke.com",
        )
        assert config.get_sender_email() == "customtenant@nomadkaraoke.com"

    # Tests for is_email_allowed()
    def test_is_email_allowed_matching_domain(self, full_config):
        """Test email is allowed when domain matches."""
        assert full_config.is_email_allowed("user@vocal-star.com") is True
        assert full_config.is_email_allowed("user@vocalstarmusic.com") is True

    def test_is_email_allowed_non_matching_domain(self, full_config):
        """Test email is rejected when domain doesn't match and require_email_domain=True."""
        assert full_config.is_email_allowed("user@gmail.com") is False
        assert full_config.is_email_allowed("user@other.com") is False

    def test_is_email_allowed_case_insensitive(self, full_config):
        """Test email domain matching is case insensitive."""
        assert full_config.is_email_allowed("User@VOCAL-STAR.COM") is True
        assert full_config.is_email_allowed("User@VocalStarMusic.com") is True

    def test_is_email_allowed_no_domain_restrictions(self, basic_config):
        """Test any email allowed when no domain restrictions."""
        # basic_config has empty allowed_email_domains
        assert basic_config.is_email_allowed("anyone@gmail.com") is True
        assert basic_config.is_email_allowed("user@anything.com") is True

    def test_is_email_allowed_require_domain_false(self):
        """Test non-matching emails allowed when require_email_domain=False."""
        config = TenantConfig(
            id="flexible",
            name="Flexible Tenant",
            subdomain="flexible.nomadkaraoke.com",
            auth=TenantAuth(
                allowed_email_domains=["preferred.com"],
                require_email_domain=False,  # Don't require matching
            ),
        )

        # Matching domain still works
        assert config.is_email_allowed("user@preferred.com") is True
        # Non-matching also allowed since require_email_domain=False
        assert config.is_email_allowed("user@other.com") is True

    def test_is_email_allowed_partial_domain_no_match(self, full_config):
        """Test partial domain matches don't work (must be exact suffix)."""
        # "star.com" should not match "vocal-star.com"
        assert full_config.is_email_allowed("user@star.com") is False
        # Subdomain of allowed domain should work
        assert full_config.is_email_allowed("user@sub.vocal-star.com") is False


class TestTenantPublicConfig:
    """Tests for TenantPublicConfig model."""

    @pytest.fixture
    def full_config(self):
        """Create a fully configured tenant for conversion testing."""
        return TenantConfig(
            id="vocalstar",
            name="Vocal Star",
            subdomain="vocalstar.nomadkaraoke.com",
            is_active=True,
            branding=TenantBranding(
                logo_url="https://example.com/logo.png",
                primary_color="#ffff00",
            ),
            features=TenantFeatures(
                audio_search=False,
                admin_access=True,  # This should be included in public config
            ),
            defaults=TenantDefaults(
                theme_id="vocalstar",
                locked_theme="vocalstar",
                distribution_mode="download_only",
                brand_prefix="VSTAR",  # This should NOT be in public config
                youtube_description_template="Custom template",  # This should NOT be in public config
            ),
            auth=TenantAuth(
                allowed_email_domains=["vocal-star.com"],
                require_email_domain=True,
                fixed_token_ids=["secret-token-123"],  # This should NOT be in public config
                sender_email="vocalstar@nomadkaraoke.com",  # This should NOT be in public config
            ),
        )

    def test_from_config_basic_fields(self, full_config):
        """Test that basic fields are copied correctly."""
        public = TenantPublicConfig.from_config(full_config)

        assert public.id == "vocalstar"
        assert public.name == "Vocal Star"
        assert public.subdomain == "vocalstar.nomadkaraoke.com"
        assert public.is_active is True

    def test_from_config_branding_included(self, full_config):
        """Test that branding is fully included."""
        public = TenantPublicConfig.from_config(full_config)

        assert public.branding.logo_url == "https://example.com/logo.png"
        assert public.branding.primary_color == "#ffff00"

    def test_from_config_features_included(self, full_config):
        """Test that features are fully included."""
        public = TenantPublicConfig.from_config(full_config)

        assert public.features.audio_search is False
        assert public.features.admin_access is True

    def test_from_config_allowed_email_domains_included(self, full_config):
        """Test that allowed_email_domains is included for frontend validation."""
        public = TenantPublicConfig.from_config(full_config)

        assert "vocal-star.com" in public.allowed_email_domains

    def test_from_config_sensitive_auth_excluded(self, full_config):
        """Test that sensitive auth fields are not in public config."""
        public = TenantPublicConfig.from_config(full_config)

        # TenantPublicConfig only has allowed_email_domains from auth
        # It should NOT have fixed_token_ids or sender_email
        assert not hasattr(public, 'auth')
        # allowed_email_domains is a top-level field
        assert hasattr(public, 'allowed_email_domains')

    def test_from_config_defaults_partially_included(self, full_config):
        """Test that only safe defaults are included."""
        public = TenantPublicConfig.from_config(full_config)

        # These should be included
        assert public.defaults.theme_id == "vocalstar"
        assert public.defaults.locked_theme == "vocalstar"
        assert public.defaults.distribution_mode == "download_only"

        # brand_prefix and youtube_description_template should NOT be included
        # The from_config method creates a new TenantDefaults without these
        assert public.defaults.brand_prefix is None
        assert public.defaults.youtube_description_template is None


class TestTenantConfigResponse:
    """Tests for TenantConfigResponse model."""

    def test_default_response(self):
        """Test default response indicates no tenant."""
        response = TenantConfigResponse()

        assert response.tenant is None
        assert response.is_default is True

    def test_tenant_response(self):
        """Test response with tenant config."""
        public_config = TenantPublicConfig(
            id="vocalstar",
            name="Vocal Star",
            subdomain="vocalstar.nomadkaraoke.com",
            is_active=True,
            branding=TenantBranding(),
            features=TenantFeatures(),
            defaults=TenantDefaults(),
        )

        response = TenantConfigResponse(tenant=public_config, is_default=False)

        assert response.tenant is not None
        assert response.tenant.id == "vocalstar"
        assert response.is_default is False
