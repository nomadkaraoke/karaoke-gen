"""
Tenant self-service theme editor API (``/api/tenant/theme``).

Any user allowed on a tenant's portal can view, preview and save that tenant's
theme. Tenant context comes from the request (subdomain or the client-controlled
``X-Tenant-ID`` header), so every endpoint re-checks membership against the
tenant's allowlist (admins always pass) — a header alone grants nothing.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, constr

from backend.api.dependencies import require_auth
from backend.middleware.tenant import get_tenant_config_from_request
from backend.models.tenant import TenantConfig
from backend.services.auth_service import AuthResult
from backend.services.tenant_admin_service import TenantValidationError
from backend.services.tenant_theme_service import (
    ThemeNotEditableError,
    ThemeNotFoundError,
    get_theme_for_editor,
    prepare_preview_styles,
    save_tenant_theme,
    store_uploaded_asset,
)
from backend.services.theme_preview_service import ThemePreviewError, render_theme_preview
from backend.services.tenant_admin_service import _theme_id_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenant/theme", tags=["tenant-theme"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# Previews run ffmpeg/PIL on the API instance — bound concurrency like the review
# preview path so a burst of edits can't starve request handling.
_PREVIEW_SEMAPHORE = asyncio.Semaphore(2)
_PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="theme-preview")
PREVIEW_TIMEOUT_S = 45


def require_tenant_member(
    request: Request, auth_result: AuthResult = Depends(require_auth)
) -> TenantConfig:
    """The request's tenant, iff the authenticated user is allowed on it."""
    config = get_tenant_config_from_request(request)
    if config is None or not config.is_active:
        raise HTTPException(status_code=404, detail="Theme editing is only available on a tenant portal.")
    if auth_result.tenant_id and auth_result.tenant_id != config.id:
        raise HTTPException(status_code=403, detail="Your sign-in belongs to a different portal.")
    if not auth_result.user_email or not config.is_email_allowed(auth_result.user_email):
        raise HTTPException(status_code=403, detail="You don't have access to this portal's theme.")
    return config


def _raise_http(exc: TenantValidationError) -> None:
    if isinstance(exc, ThemeNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ThemeNotEditableError):
        raise HTTPException(status_code=403, detail=str(exc))
    raise HTTPException(status_code=400, detail=str(exc))


class ThemeResponse(BaseModel):
    theme_id: str
    style_params: Dict[str, Any]
    images: List[str]
    fonts: List[str]


class PreviewSample(BaseModel):
    artist: str = Field("Artist Name", max_length=80)
    title: str = Field("Song Title", max_length=80)
    lyrics: Optional[List[constr(max_length=120)]] = Field(None, max_length=4)


class PreviewRequest(BaseModel):
    style_params: Dict[str, Any]
    sample: PreviewSample = Field(default_factory=PreviewSample)


class PreviewResponse(BaseModel):
    title_card: str  # data:image/jpeg;base64,...
    karaoke_frame: str


class SaveRequest(BaseModel):
    style_params: Dict[str, Any]


class AssetResponse(BaseModel):
    name: str


@router.get("", response_model=ThemeResponse)
def get_theme(config: TenantConfig = Depends(require_tenant_member)):
    try:
        return ThemeResponse(**get_theme_for_editor(config))
    except TenantValidationError as exc:
        _raise_http(exc)


@router.post("/assets", response_model=AssetResponse)
async def upload_asset(file: UploadFile = File(...), config: TenantConfig = Depends(require_tenant_member)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="The file is too large (max 15 MB).")
    try:
        name = await run_in_threadpool(store_uploaded_asset, config, file.filename or "asset", data)
    except TenantValidationError as exc:
        _raise_http(exc)
    return AssetResponse(name=name)


@router.post("/preview", response_model=PreviewResponse)
async def preview_theme(body: PreviewRequest, config: TenantConfig = Depends(require_tenant_member)):
    try:
        styles = await run_in_threadpool(prepare_preview_styles, config, body.style_params)
    except TenantValidationError as exc:
        _raise_http(exc)

    loop = asyncio.get_running_loop()
    async with _PREVIEW_SEMAPHORE:
        try:
            images = await asyncio.wait_for(
                loop.run_in_executor(
                    _PREVIEW_EXECUTOR,
                    lambda: render_theme_preview(
                        _theme_id_for(config),
                        styles,
                        artist=body.sample.artist,
                        title=body.sample.title,
                        lyrics=body.sample.lyrics,
                    ),
                ),
                timeout=PREVIEW_TIMEOUT_S,
            )
        except ThemePreviewError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="The preview took too long to render. Try again.")
    return PreviewResponse(**images.as_data_urls())


@router.put("", response_model=ThemeResponse)
def save_theme(body: SaveRequest, config: TenantConfig = Depends(require_tenant_member)):
    try:
        save_tenant_theme(config, body.style_params)
        return ThemeResponse(**get_theme_for_editor(config))
    except TenantValidationError as exc:
        _raise_http(exc)
