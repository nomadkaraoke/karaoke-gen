"""
Unit tests for TenantService.

Tests config loading from GCS, caching behavior, subdomain resolution,
and email domain validation.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from backend.services.tenant_service import TenantService, get_tenant_service
from backend.models.tenant import TenantConfig, TenantPublicConfig


# Sample tenant config data that would come from GCS
SAMPLE_VOCALSTAR_CONFIG = {
    "id": "vocalstar",
    "name": "Vocal Star",
    "subdomain": "vocalstar.nomadkaraoke.com",
    "is_active": True,
    "branding": {
        "logo_url": "https://example.com/logo.png",
        "logo_height": 50,
        "primary_color": "#ffff00",
        "secondary_color": "#006CF9",
        "site_title": "Vocal Star Karaoke Generator",
    },
    "features": {
        "audio_search": False,
        "file_upload": True,
        "youtube_url": False,
        "youtube_upload": False,
        "dropbox_upload": False,
        "gdrive_upload": False,
        "theme_selection": False,
    },
    "defaults": {
        "theme_id": "vocalstar",
        "locked_theme": "vocalstar",
        "distribution_mode": "download_only",
    },
    "auth": {
        "allowed_email_domains": ["vocal-star.com", "vocalstarmusic.com"],
        "require_email_domain": True,
        "sender_email": "vocalstar@nomadkaraoke.com",
    },
}

SAMPLE_INACTIVE_CONFIG = {
    "id": "inactive",
    "name": "Inactive Tenant",
    "subdomain": "inactive.nomadkaraoke.com",
    "is_active": False,
    "branding": {},
    "features": {},
    "defaults": {},
    "auth": {},
}


class TestTenantService:
    """Tests for TenantService class."""

    @pytest.fixture
    def mock_storage(self):
        """Create a mock StorageService."""
        storage = MagicMock()
        storage.file_exists.return_value = False
        storage.download_json.return_value = {}
        storage.generate_signed_url.return_value = "https://signed-url.example.com"
        return storage

    @pytest.fixture
    def tenant_service(self, mock_storage):
        """Create a TenantService with mock storage."""
        return TenantService(storage=mock_storage)

    # Tests for get_tenant_config()
    def test_get_tenant_config_success(self, tenant_service, mock_storage):
        """Test successful config loading from GCS."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        config = tenant_service.get_tenant_config("vocalstar")

        assert config is not None
        assert config.id == "vocalstar"
        assert config.name == "Vocal Star"
        assert config.features.audio_search is False
        mock_storage.download_json.assert_called_once_with("tenants/vocalstar/config.json")

    def test_get_tenant_config_not_found(self, tenant_service, mock_storage):
        """Test returns None when tenant config doesn't exist."""
        mock_storage.file_exists.return_value = False

        config = tenant_service.get_tenant_config("nonexistent")

        assert config is None
        mock_storage.download_json.assert_not_called()

    def test_get_tenant_config_gcs_error(self, tenant_service, mock_storage):
        """Test handles GCS errors gracefully."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.side_effect = Exception("GCS error")

        config = tenant_service.get_tenant_config("vocalstar")

        assert config is None

    # Tests for caching behavior
    def test_config_is_cached(self, tenant_service, mock_storage):
        """Test that configs are cached after first load."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        # First call loads from GCS
        config1 = tenant_service.get_tenant_config("vocalstar")
        # Second call should use cache
        config2 = tenant_service.get_tenant_config("vocalstar")

        assert config1 is config2
        # download_json should only be called once
        assert mock_storage.download_json.call_count == 1

    def test_force_refresh_bypasses_cache(self, tenant_service, mock_storage):
        """Test that force_refresh reloads from GCS."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        # First call
        tenant_service.get_tenant_config("vocalstar")
        # Force refresh
        tenant_service.get_tenant_config("vocalstar", force_refresh=True)

        # download_json should be called twice
        assert mock_storage.download_json.call_count == 2

    def test_cache_expires_after_ttl(self, tenant_service, mock_storage):
        """Test that cache expires after TTL."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        # Load config
        tenant_service.get_tenant_config("vocalstar")

        # Simulate cache expiration by manipulating cache time
        tenant_service._cache_times["vocalstar"] = datetime.now() - timedelta(seconds=400)

        # Next call should reload
        tenant_service.get_tenant_config("vocalstar")

        assert mock_storage.download_json.call_count == 2

    # Tests for get_tenant_by_subdomain()
    def test_get_tenant_by_subdomain_cached(self, tenant_service, mock_storage):
        """Test subdomain lookup uses cache."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        # First load by ID to populate cache
        tenant_service.get_tenant_config("vocalstar")

        # Now lookup by subdomain should use cached mapping
        config = tenant_service.get_tenant_by_subdomain("vocalstar.nomadkaraoke.com")

        assert config is not None
        assert config.id == "vocalstar"

    def test_get_tenant_by_subdomain_fallback_resolution(self, tenant_service, mock_storage):
        """Test subdomain resolution when not in cache."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        # Lookup by subdomain without prior cache
        config = tenant_service.get_tenant_by_subdomain("vocalstar.nomadkaraoke.com")

        assert config is not None
        assert config.id == "vocalstar"

    def test_get_tenant_by_subdomain_not_found(self, tenant_service, mock_storage):
        """Test returns None for unknown subdomain."""
        mock_storage.file_exists.return_value = False

        config = tenant_service.get_tenant_by_subdomain("unknown.nomadkaraoke.com")

        assert config is None

    def test_get_tenant_by_subdomain_case_insensitive(self, tenant_service, mock_storage):
        """Test subdomain lookup is case insensitive."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        config = tenant_service.get_tenant_by_subdomain("VOCALSTAR.NomadKaraoke.COM")

        assert config is not None
        assert config.id == "vocalstar"

    # Tests for _resolve_subdomain_to_tenant_id()
    def test_resolve_subdomain_3_parts(self, tenant_service, mock_storage):
        """Test resolving tenant.nomadkaraoke.com pattern."""
        mock_storage.file_exists.return_value = True

        tenant_id = tenant_service._resolve_subdomain_to_tenant_id("vocalstar.nomadkaraoke.com")

        assert tenant_id == "vocalstar"

    def test_resolve_subdomain_4_parts(self, tenant_service, mock_storage):
        """Test resolving tenant.gen.nomadkaraoke.com pattern."""
        mock_storage.file_exists.return_value = True

        tenant_id = tenant_service._resolve_subdomain_to_tenant_id("vocalstar.gen.nomadkaraoke.com")

        assert tenant_id == "vocalstar"

    def test_resolve_subdomain_too_short(self, tenant_service, mock_storage):
        """Test returns None for subdomains with fewer than 3 parts."""
        tenant_id = tenant_service._resolve_subdomain_to_tenant_id("nomadkaraoke.com")

        assert tenant_id is None

    def test_resolve_subdomain_tenant_not_exists(self, tenant_service, mock_storage):
        """Test returns None when tenant config doesn't exist in GCS."""
        mock_storage.file_exists.return_value = False

        tenant_id = tenant_service._resolve_subdomain_to_tenant_id("nonexistent.nomadkaraoke.com")

        assert tenant_id is None

    # Tests for get_public_config()
    def test_get_public_config_success(self, tenant_service, mock_storage):
        """Test getting public config."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        public_config = tenant_service.get_public_config("vocalstar")

        assert public_config is not None
        assert isinstance(public_config, TenantPublicConfig)
        assert public_config.id == "vocalstar"

    def test_get_public_config_not_found(self, tenant_service, mock_storage):
        """Test returns None when tenant not found."""
        mock_storage.file_exists.return_value = False

        public_config = tenant_service.get_public_config("nonexistent")

        assert public_config is None

    def test_get_public_config_by_subdomain(self, tenant_service, mock_storage):
        """Test getting public config by subdomain."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        public_config = tenant_service.get_public_config_by_subdomain("vocalstar.nomadkaraoke.com")

        assert public_config is not None
        assert public_config.id == "vocalstar"

    # Tests for tenant_exists()
    def test_tenant_exists_true(self, tenant_service, mock_storage):
        """Test returns True when config exists."""
        mock_storage.file_exists.return_value = True

        assert tenant_service.tenant_exists("vocalstar") is True
        mock_storage.file_exists.assert_called_with("tenants/vocalstar/config.json")

    def test_tenant_exists_false(self, tenant_service, mock_storage):
        """Test returns False when config doesn't exist."""
        mock_storage.file_exists.return_value = False

        assert tenant_service.tenant_exists("nonexistent") is False

    # Tests for is_email_allowed_for_tenant()
    def test_is_email_allowed_for_tenant_valid(self, tenant_service, mock_storage):
        """Test email allowed for valid domain."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        assert tenant_service.is_email_allowed_for_tenant("vocalstar", "user@vocal-star.com") is True

    def test_is_email_allowed_for_tenant_invalid(self, tenant_service, mock_storage):
        """Test email not allowed for invalid domain."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        assert tenant_service.is_email_allowed_for_tenant("vocalstar", "user@gmail.com") is False

    def test_is_email_allowed_for_tenant_not_found(self, tenant_service, mock_storage):
        """Test returns False when tenant not found."""
        mock_storage.file_exists.return_value = False

        assert tenant_service.is_email_allowed_for_tenant("nonexistent", "user@any.com") is False

    # Tests for get_tenant_sender_email()
    def test_get_tenant_sender_email_custom(self, tenant_service, mock_storage):
        """Test returns custom sender email when configured."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        sender = tenant_service.get_tenant_sender_email("vocalstar")

        assert sender == "vocalstar@nomadkaraoke.com"

    def test_get_tenant_sender_email_default(self, tenant_service, mock_storage):
        """Test returns default sender when tenant not found."""
        mock_storage.file_exists.return_value = False

        sender = tenant_service.get_tenant_sender_email("nonexistent")

        # Should match DEFAULT_SENDER_EMAIL constant (consistent with EmailService)
        assert sender == "gen@nomadkaraoke.com"

    # Tests for invalidate_cache()
    def test_invalidate_cache_specific_tenant(self, tenant_service, mock_storage):
        """Test invalidating cache for specific tenant."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        # Load into cache
        tenant_service.get_tenant_config("vocalstar")
        assert "vocalstar" in tenant_service._config_cache

        # Invalidate
        tenant_service.invalidate_cache("vocalstar")

        assert "vocalstar" not in tenant_service._config_cache
        assert "vocalstar" not in tenant_service._cache_times

    def test_invalidate_cache_specific_tenant_clears_subdomain_map(self, tenant_service, mock_storage):
        """Test invalidating specific tenant also clears its subdomain map entry."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        # Load into cache (this also populates subdomain map)
        tenant_service.get_tenant_config("vocalstar")
        assert "vocalstar.nomadkaraoke.com" in tenant_service._subdomain_map
        assert tenant_service._subdomain_map["vocalstar.nomadkaraoke.com"] == "vocalstar"

        # Invalidate specific tenant
        tenant_service.invalidate_cache("vocalstar")

        # Subdomain map entry should also be removed
        assert "vocalstar.nomadkaraoke.com" not in tenant_service._subdomain_map

    def test_invalidate_cache_all(self, tenant_service, mock_storage):
        """Test invalidating all caches."""
        mock_storage.file_exists.return_value = True
        mock_storage.download_json.return_value = SAMPLE_VOCALSTAR_CONFIG

        # Load into cache
        tenant_service.get_tenant_config("vocalstar")

        # Invalidate all
        tenant_service.invalidate_cache()

        assert len(tenant_service._config_cache) == 0
        assert len(tenant_service._cache_times) == 0
        assert len(tenant_service._subdomain_map) == 0

    # Tests for get_asset_url()
    def test_get_asset_url_success(self, tenant_service, mock_storage):
        """Test getting signed URL for existing asset."""
        mock_storage.file_exists.return_value = True

        url = tenant_service.get_asset_url("vocalstar", "logo.png")

        assert url == "https://signed-url.example.com"
        mock_storage.file_exists.assert_called_with("tenants/vocalstar/logo.png")
        mock_storage.generate_signed_url.assert_called_with("tenants/vocalstar/logo.png", expiration_minutes=60)

    def test_get_asset_url_not_found(self, tenant_service, mock_storage):
        """Test returns None when asset doesn't exist."""
        mock_storage.file_exists.return_value = False

        url = tenant_service.get_asset_url("vocalstar", "missing.png")

        assert url is None
        mock_storage.generate_signed_url.assert_not_called()


class TestGetTenantServiceSingleton:
    """Tests for the get_tenant_service() singleton function."""

    def test_returns_same_instance(self):
        """Test that get_tenant_service returns singleton."""
        # Reset the singleton
        import backend.services.tenant_service as module
        module._tenant_service = None

        with patch.object(TenantService, '__init__', return_value=None):
            service1 = get_tenant_service()
            service2 = get_tenant_service()

            assert service1 is service2

    def test_thread_safe_initialization(self):
        """Test that singleton initialization is thread-safe."""
        import threading
        import backend.services.tenant_service as module

        # Reset the singleton
        module._tenant_service = None

        instances = []

        def get_instance():
            with patch.object(TenantService, '__init__', return_value=None):
                instances.append(get_tenant_service())

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All instances should be the same
        assert all(i is instances[0] for i in instances)
