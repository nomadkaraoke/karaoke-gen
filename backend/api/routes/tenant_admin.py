"""
Admin API routes for provisioning white-label tenants.

These endpoints let an admin mint a tenant (branded portal + locked theme) from
the UI, replacing the hand-run ``scripts/setup-*-tenant.py`` recipe. Once created,
the tenant can be driven immediately via the admin preview path
``?preview_tenant=<id>`` — including the tenant bulk-upload flow (folder of tracks,
auto-paired mixed/instrumental, resumable uploads, locked theme on every job).
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ValidationError

from backend.api.dependencies import require_admin
from backend.models.tenant import TenantPublicConfig
from backend.models.theme import ColorOverrides
from backend.services.auth_service import AuthResult
from backend.services.tenant_admin_service import (
    IMAGE_CONTENT_TYPES,
    TenantConflictError,
    TenantNotFoundError,
    TenantValidationError,
    create_tenant,
    get_default_style_params,
    get_tenant_detail,
    list_tenants,
    update_tenant,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/tenants", tags=["admin", "tenant"])

# Public app origin used to build the admin-preview link.
APP_ORIGIN = "https://gen.nomadkaraoke.com"

# Reject oversized image uploads (backgrounds/logo).
MAX_IMAGE_BYTES = 15 * 1024 * 1024


class TenantSummary(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    subdomain: Optional[str] = None
    is_active: bool = True
    locked_theme: Optional[str] = None
    dropbox_path: Optional[str] = None
    created_at: Optional[str] = None


class TenantListResponse(BaseModel):
    tenants: List[TenantSummary]


class TenantCreateResponse(BaseModel):
    tenant: TenantPublicConfig
    preview_url: str
    subdomain_url: str


class TenantDetailResponse(BaseModel):
    tenant: Dict[str, Any]
    theme_id: str
    style_params: Dict[str, Any]
    assets: List[str]
    preview_url: str


class TenantTemplateResponse(BaseModel):
    style_params: Dict[str, Any]


class TenantUpdateResponse(BaseModel):
    tenant: TenantPublicConfig


def _clean(value: Optional[str]) -> Optional[str]:
    """Normalize empty/whitespace form values to None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _preview_url(tenant_id: str) -> str:
    return f"{APP_ORIGIN}/en/app?preview_tenant={tenant_id}"


def _parse_json_field(raw: Optional[str], label: str) -> Optional[Any]:
    """Parse an optional JSON string form field, mapping errors to 400."""
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"{label}: invalid JSON ({exc}).")


def _parse_domains(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = raw.replace("\n", ",").replace(" ", ",").split(",")
    return [p.strip().lower() for p in parts if p.strip()]


async def _read_image(upload: Optional[UploadFile], label: str) -> Optional[Tuple[bytes, str]]:
    """Read+validate an uploaded image, returning (bytes, ext) or None."""
    if upload is None or not upload.filename:
        return None
    ext = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    if ext not in IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: unsupported image type '.{ext}'. Use PNG, JPG, GIF, or WEBP.",
        )
    data = await upload.read()
    if not data:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: image is too large ({len(data) // (1024 * 1024)}MB). Max 15MB.",
        )
    return data, ext


# Assets replaceable in the manage flow: images plus fonts.
ASSET_EXTENSIONS = set(IMAGE_CONTENT_TYPES) | {"ttf", "otf", "woff", "woff2"}


async def _read_asset(upload: Optional[UploadFile], label: str) -> Optional[Tuple[bytes, str]]:
    """Read+validate an uploaded theme asset (image or font)."""
    if upload is None or not upload.filename:
        return None
    ext = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    if ext not in ASSET_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: unsupported asset type '.{ext}'. Use an image or font file.",
        )
    data = await upload.read()
    if not data:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: file is too large ({len(data) // (1024 * 1024)}MB). Max 15MB.",
        )
    return data, ext


@router.get("", response_model=TenantListResponse)
async def admin_list_tenants(auth_data: AuthResult = Depends(require_admin)):
    """List all white-label tenants."""
    return TenantListResponse(tenants=[TenantSummary(**t) for t in list_tenants()])


@router.get("/_template", response_model=TenantTemplateResponse)
async def admin_tenant_theme_template(auth_data: AuthResult = Depends(require_admin)):
    """Return the default Nomad theme's full style_params as an editing starting point."""
    try:
        return TenantTemplateResponse(style_params=get_default_style_params())
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{tenant_id}", response_model=TenantDetailResponse)
async def admin_get_tenant(tenant_id: str, auth_data: AuthResult = Depends(require_admin)):
    """Return a tenant's full config, theme style_params, and asset list for editing."""
    try:
        detail = get_tenant_detail(tenant_id)
    except TenantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return TenantDetailResponse(preview_url=_preview_url(tenant_id), **detail)


@router.put("/{tenant_id}", response_model=TenantUpdateResponse)
async def admin_update_tenant(
    tenant_id: str,
    auth_data: AuthResult = Depends(require_admin),
    config: Optional[str] = Form(None),
    style_params: Optional[str] = Form(None),
    logo: Optional[UploadFile] = File(None),
    assets: Optional[List[UploadFile]] = File(None),
):
    """
    Update a tenant: merge config fields (JSON), replace the full theme
    style_params (JSON), and/or add/replace theme assets (uploaded files keyed
    by their filename). Re-render jobs to apply an updated theme.
    """
    config_updates = _parse_json_field(config, "config")
    if config_updates is not None and not isinstance(config_updates, dict):
        raise HTTPException(status_code=400, detail="config must be a JSON object.")
    style = _parse_json_field(style_params, "style_params")

    asset_map: Dict[str, Tuple[bytes, str]] = {}
    for upload in assets or []:
        asset = await _read_asset(upload, f"asset '{upload.filename}'")
        if asset and upload.filename:
            asset_map[upload.filename] = asset
    logo_image = await _read_image(logo, "logo")

    try:
        updated = update_tenant(
            tenant_id,
            config_updates=config_updates,
            style_params=style,
            assets=asset_map or None,
            logo=logo_image,
        )
    except TenantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TenantValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to update tenant")
        raise HTTPException(status_code=500, detail=f"Failed to update tenant: {exc}")

    admin_id = auth_data.user_email or "admin:unknown"
    logger.info(f"Admin {admin_id} updated tenant '{tenant_id}'")
    return TenantUpdateResponse(tenant=TenantPublicConfig.from_config(updated))


@router.post("", response_model=TenantCreateResponse, status_code=201)
async def admin_create_tenant(
    auth_data: AuthResult = Depends(require_admin),
    name: str = Form(...),
    tenant_id: Optional[str] = Form(None),
    subdomain: Optional[str] = Form(None),
    allowed_email_domains: Optional[str] = Form(None),
    artist_color: Optional[str] = Form(None),
    title_color: Optional[str] = Form(None),
    sung_lyrics_color: Optional[str] = Form(None),
    unsung_lyrics_color: Optional[str] = Form(None),
    tagline: Optional[str] = Form(None),
    distribution_mode: str = Form("download_only"),
    dropbox_path: Optional[str] = Form(None),
    brand_prefix: Optional[str] = Form(None),
    style_params: Optional[str] = Form(None),
    karaoke_background: Optional[UploadFile] = File(None),
    intro_background: Optional[UploadFile] = File(None),
    end_background: Optional[UploadFile] = File(None),
    logo: Optional[UploadFile] = File(None),
):
    """Provision a new white-label tenant (theme + config) from admin form input."""
    try:
        colors = ColorOverrides(
            artist_color=_clean(artist_color),
            title_color=_clean(title_color),
            sung_lyrics_color=_clean(sung_lyrics_color),
            unsung_lyrics_color=_clean(unsung_lyrics_color),
        )
    except ValidationError:
        raise HTTPException(
            status_code=400, detail="Colors must be hex values like #ff5bb8."
        )

    backgrounds = {}
    for field, upload in (
        ("karaoke_background", karaoke_background),
        ("intro_background", intro_background),
        ("end_background", end_background),
    ):
        image = await _read_image(upload, field)
        if image:
            backgrounds[field] = image
    logo_image = await _read_image(logo, "logo")

    if distribution_mode not in ("all", "download_only", "cloud_only"):
        raise HTTPException(status_code=400, detail="Invalid distribution_mode.")

    style_override = _parse_json_field(style_params, "style_params")
    if style_override is not None and not isinstance(style_override, dict):
        raise HTTPException(status_code=400, detail="style_params must be a JSON object.")

    try:
        config = create_tenant(
            name=name,
            tenant_id=_clean(tenant_id),
            subdomain=_clean(subdomain),
            allowed_email_domains=_parse_domains(allowed_email_domains),
            colors=colors,
            style_params_override=style_override,
            tagline=_clean(tagline),
            distribution_mode=distribution_mode,
            dropbox_path=_clean(dropbox_path),
            brand_prefix=_clean(brand_prefix),
            backgrounds=backgrounds,
            logo=logo_image,
        )
    except TenantConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except TenantValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to create tenant")
        raise HTTPException(status_code=500, detail=f"Failed to create tenant: {exc}")

    admin_id = auth_data.user_email or "admin:unknown"
    logger.info(f"Admin {admin_id} created tenant '{config.id}'")

    return TenantCreateResponse(
        tenant=TenantPublicConfig.from_config(config),
        preview_url=_preview_url(config.id),
        subdomain_url=config.get_frontend_url(),
    )
