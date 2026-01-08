"""
Tenant detection middleware for white-label B2B portals.

This middleware extracts the tenant ID from incoming requests and attaches
the tenant configuration to the request state for use by downstream handlers.

Tenant detection priority:
1. X-Tenant-ID header (explicitly set by frontend)
2. `tenant` query parameter (for development/testing)
3. Host header subdomain detection (production flow)

The middleware is non-blocking - if no tenant is detected, the request
proceeds as a default Nomad Karaoke request (tenant_id = None).
"""

import logging
import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.services.tenant_service import get_tenant_service
from backend.models.tenant import TenantConfig

logger = logging.getLogger(__name__)

# Only allow query param tenant override in non-production environments
IS_PRODUCTION = os.environ.get("ENV", "").lower() == "production" or \
                os.environ.get("ENVIRONMENT", "").lower() == "production"


# Known non-tenant subdomains that should be treated as default Nomad Karaoke
NON_TENANT_SUBDOMAINS = {"gen", "api", "www", "buy", "admin", "app", "beta"}


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware that detects tenant from request and attaches config to state.

    After this middleware runs, routes can access:
    - request.state.tenant_id: str or None
    - request.state.tenant_config: TenantConfig or None

    Usage in route handlers:
    ```python
    @router.get("/something")
    async def handler(request: Request):
        tenant_id = getattr(request.state, "tenant_id", None)
        tenant_config = getattr(request.state, "tenant_config", None)
    ```
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Extract tenant ID from request
        tenant_id = self._extract_tenant_id(request)

        # Load tenant config if tenant detected
        tenant_config: Optional[TenantConfig] = None
        if tenant_id:
            tenant_service = get_tenant_service()
            tenant_config = tenant_service.get_tenant_config(tenant_id)

            if tenant_config and not tenant_config.is_active:
                # Tenant exists but is inactive - treat as default
                logger.warning(f"Inactive tenant requested: {tenant_id}")
                tenant_id = None
                tenant_config = None

        # Attach to request state
        request.state.tenant_id = tenant_id
        request.state.tenant_config = tenant_config

        # Log tenant detection for debugging
        if tenant_id:
            logger.debug(f"Request tenant: {tenant_id} (path: {request.url.path})")

        # Process request
        response = await call_next(request)

        # Add tenant ID to response headers for debugging
        if tenant_id:
            response.headers["X-Tenant-ID"] = tenant_id

        return response

    def _extract_tenant_id(self, request: Request) -> Optional[str]:
        """
        Extract tenant ID from request using priority-based detection.

        Returns:
            Tenant ID if detected, None for default Nomad Karaoke
        """
        # Priority 1: X-Tenant-ID header (explicitly set by frontend)
        header_tenant = request.headers.get("X-Tenant-ID")
        if header_tenant:
            return header_tenant.lower().strip()

        # Priority 2: Query parameter (for development/testing only)
        # Disabled in production to prevent tenant spoofing
        if not IS_PRODUCTION:
            query_tenant = request.query_params.get("tenant")
            if query_tenant:
                return query_tenant.lower().strip()

        # Priority 3: Host header subdomain detection
        host = request.headers.get("Host", "")
        return self._extract_tenant_from_host(host)

    def _extract_tenant_from_host(self, host: str) -> Optional[str]:
        """
        Extract tenant ID from Host header subdomain.

        Patterns handled:
        - vocalstar.nomadkaraoke.com -> vocalstar
        - vocalstar.gen.nomadkaraoke.com -> vocalstar
        - localhost:3000 -> None (local dev)
        - gen.nomadkaraoke.com -> None (main app)
        - api.nomadkaraoke.com -> None (API)

        Returns:
            Tenant ID if subdomain matches tenant, None otherwise
        """
        if not host:
            return None

        # Normalize
        host_lower = host.lower()

        # Remove port if present
        if ":" in host_lower:
            host_lower = host_lower.split(":")[0]

        # Check if this is a nomadkaraoke.com domain
        if "nomadkaraoke.com" not in host_lower:
            return None

        # Split into parts
        parts = host_lower.split(".")

        # Need at least 3 parts: subdomain.nomadkaraoke.com
        if len(parts) < 3:
            return None

        # First part is the potential tenant ID
        potential_tenant = parts[0]

        # Skip known non-tenant subdomains
        if potential_tenant in NON_TENANT_SUBDOMAINS:
            return None

        # Verify tenant exists in GCS
        tenant_service = get_tenant_service()
        if tenant_service.tenant_exists(potential_tenant):
            return potential_tenant

        return None


def get_tenant_from_request(request: Request) -> Optional[str]:
    """
    Helper function to get tenant ID from request state.

    Usage in route handlers:
    ```python
    from backend.middleware.tenant import get_tenant_from_request

    @router.get("/something")
    async def handler(request: Request):
        tenant_id = get_tenant_from_request(request)
    ```
    """
    return getattr(request.state, "tenant_id", None)


def get_tenant_config_from_request(request: Request) -> Optional[TenantConfig]:
    """
    Helper function to get tenant config from request state.

    Usage in route handlers:
    ```python
    from backend.middleware.tenant import get_tenant_config_from_request

    @router.get("/something")
    async def handler(request: Request):
        config = get_tenant_config_from_request(request)
        if config and not config.features.audio_search:
            raise HTTPException(403, "Audio search not available")
    ```
    """
    return getattr(request.state, "tenant_config", None)
