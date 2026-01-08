"""
Tenant service for managing white-label B2B portal configurations.

Tenant configs are stored in GCS at tenants/{tenant_id}/config.json.
The service provides:
- Tenant config loading with caching
- Subdomain to tenant ID resolution
- Tenant validation

Similar to ThemeService, configs are cached for 5 minutes to reduce GCS reads.
"""

import logging
import threading
from datetime import datetime
from typing import Dict, Optional

from backend.models.tenant import TenantConfig, TenantPublicConfig
from backend.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# GCS paths for tenant configs
TENANTS_PREFIX = "tenants"

# Default sender email (shared with EmailService for consistency)
DEFAULT_SENDER_EMAIL = "gen@nomadkaraoke.com"


class TenantService:
    """Service for managing tenant configurations from GCS."""

    def __init__(self, storage: Optional[StorageService] = None):
        """
        Initialize the tenant service.

        Args:
            storage: StorageService instance (creates new one if not provided)
        """
        self.storage = storage or StorageService()
        self._config_cache: Dict[str, TenantConfig] = {}
        self._cache_times: Dict[str, datetime] = {}
        self._subdomain_map: Dict[str, str] = {}  # subdomain -> tenant_id
        self._subdomain_map_time: Optional[datetime] = None
        self.CACHE_TTL_SECONDS = 300  # 5 minute cache

    def _get_config_path(self, tenant_id: str) -> str:
        """Get the GCS path for a tenant's config file."""
        return f"{TENANTS_PREFIX}/{tenant_id}/config.json"

    def _is_cache_valid(self, tenant_id: str) -> bool:
        """Check if the config cache for a tenant is still valid."""
        if tenant_id not in self._config_cache or tenant_id not in self._cache_times:
            return False
        age = datetime.now() - self._cache_times[tenant_id]
        return age.total_seconds() < self.CACHE_TTL_SECONDS

    def _is_subdomain_map_valid(self) -> bool:
        """Check if the subdomain map cache is still valid."""
        if not self._subdomain_map or self._subdomain_map_time is None:
            return False
        age = datetime.now() - self._subdomain_map_time
        return age.total_seconds() < self.CACHE_TTL_SECONDS

    def get_tenant_config(
        self, tenant_id: str, force_refresh: bool = False
    ) -> Optional[TenantConfig]:
        """
        Load tenant configuration from GCS with caching.

        Args:
            tenant_id: The tenant identifier
            force_refresh: Force reload from GCS even if cache is valid

        Returns:
            TenantConfig if found, None otherwise
        """
        if not force_refresh and self._is_cache_valid(tenant_id):
            return self._config_cache[tenant_id]

        try:
            config_path = self._get_config_path(tenant_id)
            if not self.storage.file_exists(config_path):
                logger.debug(f"Tenant config not found: {tenant_id}")
                return None

            data = self.storage.download_json(config_path)
            config = TenantConfig(**data)

            # Update cache
            self._config_cache[tenant_id] = config
            self._cache_times[tenant_id] = datetime.now()

            # Update subdomain map and its timestamp
            self._subdomain_map[config.subdomain.lower()] = tenant_id
            self._subdomain_map_time = datetime.now()

            logger.info(f"Loaded tenant config: {tenant_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to load tenant config {tenant_id}: {e}")
            return None

    def get_tenant_by_subdomain(self, subdomain: str) -> Optional[TenantConfig]:
        """
        Get tenant config by subdomain.

        Args:
            subdomain: Full subdomain (e.g., 'vocalstar.nomadkaraoke.com')

        Returns:
            TenantConfig if found, None otherwise
        """
        subdomain_lower = subdomain.lower()

        # Check cache first
        if subdomain_lower in self._subdomain_map:
            tenant_id = self._subdomain_map[subdomain_lower]
            return self.get_tenant_config(tenant_id)

        # Try to find tenant by listing all configs
        # This is a fallback for when we don't have the subdomain cached
        tenant_id = self._resolve_subdomain_to_tenant_id(subdomain_lower)
        if tenant_id:
            return self.get_tenant_config(tenant_id)

        return None

    def _resolve_subdomain_to_tenant_id(self, subdomain: str) -> Optional[str]:
        """
        Resolve a subdomain to tenant ID by checking GCS.

        This extracts the tenant ID from the subdomain pattern:
        - 'vocalstar.nomadkaraoke.com' -> 'vocalstar'
        - 'vocalstar.gen.nomadkaraoke.com' -> 'vocalstar'
        """
        # Extract potential tenant ID from subdomain
        # Pattern: {tenant}.nomadkaraoke.com or {tenant}.gen.nomadkaraoke.com
        parts = subdomain.split(".")

        if len(parts) >= 3:
            # First part is likely the tenant ID
            potential_tenant_id = parts[0]

            # Check if config exists
            config_path = self._get_config_path(potential_tenant_id)
            if self.storage.file_exists(config_path):
                return potential_tenant_id

        return None

    def get_public_config(self, tenant_id: str) -> Optional[TenantPublicConfig]:
        """
        Get the public (frontend-safe) tenant configuration.

        Args:
            tenant_id: The tenant identifier

        Returns:
            TenantPublicConfig if found, None otherwise
        """
        config = self.get_tenant_config(tenant_id)
        if config:
            return TenantPublicConfig.from_config(config)
        return None

    def get_public_config_by_subdomain(
        self, subdomain: str
    ) -> Optional[TenantPublicConfig]:
        """
        Get the public tenant configuration by subdomain.

        Args:
            subdomain: Full subdomain

        Returns:
            TenantPublicConfig if found, None otherwise
        """
        config = self.get_tenant_by_subdomain(subdomain)
        if config:
            return TenantPublicConfig.from_config(config)
        return None

    def tenant_exists(self, tenant_id: str) -> bool:
        """
        Check if a tenant exists.

        Args:
            tenant_id: The tenant identifier

        Returns:
            True if tenant config exists, False otherwise
        """
        config_path = self._get_config_path(tenant_id)
        return self.storage.file_exists(config_path)

    def is_email_allowed_for_tenant(self, tenant_id: str, email: str) -> bool:
        """
        Check if an email is allowed for a specific tenant.

        Args:
            tenant_id: The tenant identifier
            email: Email address to check

        Returns:
            True if email is allowed, False otherwise
        """
        config = self.get_tenant_config(tenant_id)
        if not config:
            return False
        return config.is_email_allowed(email)

    def get_tenant_sender_email(self, tenant_id: str) -> str:
        """
        Get the email sender address for a tenant.

        Args:
            tenant_id: The tenant identifier

        Returns:
            Sender email address
        """
        config = self.get_tenant_config(tenant_id)
        if config:
            return config.get_sender_email()
        # Default fallback (consistent with EmailService)
        return DEFAULT_SENDER_EMAIL

    def invalidate_cache(self, tenant_id: Optional[str] = None) -> None:
        """
        Invalidate tenant config cache.

        Args:
            tenant_id: Specific tenant to invalidate, or None for all
        """
        if tenant_id:
            self._config_cache.pop(tenant_id, None)
            self._cache_times.pop(tenant_id, None)
            # Also remove any subdomain map entries pointing to this tenant
            subdomains_to_remove = [
                subdomain
                for subdomain, tid in self._subdomain_map.items()
                if tid == tenant_id
            ]
            for subdomain in subdomains_to_remove:
                self._subdomain_map.pop(subdomain, None)
            logger.info(f"Tenant config cache invalidated: {tenant_id}")
        else:
            self._config_cache.clear()
            self._cache_times.clear()
            self._subdomain_map.clear()
            self._subdomain_map_time = None
            logger.info("All tenant config caches invalidated")

    def get_asset_url(self, tenant_id: str, asset_name: str) -> Optional[str]:
        """
        Get a signed URL for a tenant asset (logo, favicon, etc.).

        Args:
            tenant_id: The tenant identifier
            asset_name: Asset filename (e.g., 'logo.png')

        Returns:
            Signed URL if asset exists, None otherwise
        """
        asset_path = f"{TENANTS_PREFIX}/{tenant_id}/{asset_name}"
        if self.storage.file_exists(asset_path):
            return self.storage.generate_signed_url(asset_path, expiration_minutes=60)
        return None


# Singleton instance with thread-safe initialization
_tenant_service: Optional[TenantService] = None
_tenant_service_lock = threading.Lock()


def get_tenant_service() -> TenantService:
    """Get or create the singleton TenantService instance (thread-safe)."""
    global _tenant_service
    if _tenant_service is None:
        with _tenant_service_lock:
            # Double-check after acquiring lock
            if _tenant_service is None:
                _tenant_service = TenantService()
    return _tenant_service
