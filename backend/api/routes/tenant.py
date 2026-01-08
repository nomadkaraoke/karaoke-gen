"""
Tenant API routes for white-label portal configuration.

These endpoints are public (no auth required) since the frontend needs
to fetch tenant branding before the user logs in.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request

from backend.models.tenant import TenantConfigResponse, TenantPublicConfig
from backend.services.tenant_service import get_tenant_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenant", tags=["tenant"])


@router.get("/config", response_model=TenantConfigResponse)
async def get_tenant_config(
    request: Request,
    tenant: Optional[str] = Query(
        None,
        description="Tenant ID override (for development). If not provided, detected from subdomain.",
    ),
):
    """
    Get tenant configuration for the current portal.

    This endpoint detects the tenant from:
    1. Query parameter `tenant` (for development/testing)
    2. X-Tenant-ID header (set by frontend)
    3. Host header subdomain

    Returns the public tenant configuration (branding, features, defaults)
    or indicates this is the default Nomad Karaoke portal.
    """
    tenant_service = get_tenant_service()

    # Priority 1: Query parameter (for dev/testing)
    tenant_id = tenant

    # Priority 2: X-Tenant-ID header
    if not tenant_id:
        tenant_id = request.headers.get("X-Tenant-ID")

    # Priority 3: Detect from Host header
    if not tenant_id:
        host = request.headers.get("Host", "")
        # Check if this is a tenant subdomain
        # Pattern: {tenant}.nomadkaraoke.com or {tenant}.gen.nomadkaraoke.com
        if host and "nomadkaraoke.com" in host.lower():
            parts = host.lower().split(".")
            # Skip known non-tenant subdomains
            if parts[0] not in ["gen", "api", "www", "buy", "admin"]:
                potential_tenant = parts[0]
                if tenant_service.tenant_exists(potential_tenant):
                    tenant_id = potential_tenant

    # If no tenant detected, return default config
    if not tenant_id:
        logger.debug("No tenant detected, returning default config")
        return TenantConfigResponse(tenant=None, is_default=True)

    # Load tenant config
    public_config = tenant_service.get_public_config(tenant_id)

    if not public_config:
        logger.warning(f"Tenant not found: {tenant_id}")
        return TenantConfigResponse(tenant=None, is_default=True)

    if not public_config.is_active:
        logger.warning(f"Tenant is inactive: {tenant_id}")
        return TenantConfigResponse(tenant=None, is_default=True)

    logger.info(f"Returning config for tenant: {tenant_id}")
    return TenantConfigResponse(tenant=public_config, is_default=False)


@router.get("/config/{tenant_id}", response_model=TenantConfigResponse)
async def get_tenant_config_by_id(tenant_id: str):
    """
    Get tenant configuration by explicit tenant ID.

    This is useful for admin tools or debugging.
    """
    tenant_service = get_tenant_service()
    public_config = tenant_service.get_public_config(tenant_id)

    if not public_config:
        return TenantConfigResponse(tenant=None, is_default=True)

    # Check if tenant is active
    if not public_config.is_active:
        logger.warning(f"Tenant is inactive: {tenant_id}")
        return TenantConfigResponse(tenant=None, is_default=True)

    return TenantConfigResponse(tenant=public_config, is_default=False)


@router.get("/asset/{tenant_id}/{asset_name}")
async def get_tenant_asset(tenant_id: str, asset_name: str):
    """
    Get a signed URL for a tenant asset (logo, favicon, etc.).

    This redirects to the signed GCS URL for the asset.
    """
    from fastapi.responses import RedirectResponse

    tenant_service = get_tenant_service()
    url = tenant_service.get_asset_url(tenant_id, asset_name)

    if not url:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Asset not found")

    return RedirectResponse(url=url)
