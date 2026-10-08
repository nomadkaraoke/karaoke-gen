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
from backend.services.theme_change_service import outdated_jobs, refresh_inflight_jobs
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
    # data:image/jpeg;base64,... — null for a screen the theme leaves out of videos.
    title_card: Optional[str] = None
    karaoke_frame: str
    end_screen: Optional[str] = None


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
    # Read in chunks and stop as soon as the limit is exceeded, so an oversized
    # upload never gets materialised in memory in full.
    chunks, size = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="The file is too large (max 15 MB).")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")
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
    await _PREVIEW_SEMAPHORE.acquire()
    future = loop.run_in_executor(
        _PREVIEW_EXECUTOR,
        lambda: render_theme_preview(
            _theme_id_for(config),
            styles,
            artist=body.sample.artist,
            title=body.sample.title,
            lyrics=body.sample.lyrics,
        ),
    )
    # The slot is released only when the render really finishes — a timed-out
    # render keeps running in its thread, so releasing early would let a burst
    # queue unbounded work behind it.
    future.add_done_callback(lambda _f: _PREVIEW_SEMAPHORE.release())
    try:
        images = await asyncio.wait_for(asyncio.shield(future), timeout=PREVIEW_TIMEOUT_S)
    except ThemePreviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="The preview took too long to render. Try again.")
    return PreviewResponse(**images.as_data_urls())


class SaveResponse(ThemeResponse):
    # In-progress tracks switched to the new theme by this save.
    refreshed_jobs: int = 0
    # The caller's finished tracks made with an older theme (re-render to update).
    outdated_job_ids: List[str] = Field(default_factory=list)


class OutdatedJobsResponse(BaseModel):
    theme_updated_at: Optional[str] = None
    job_ids: List[str]


class RerenderOutdatedResponse(BaseModel):
    started: List[str]
    failed: Dict[str, str]
    # Outdated tracks beyond this call's cap (ask again to start them).
    remaining: List[str] = Field(default_factory=list)


# Tracks started per bulk call: bounds the request time and the burst of
# render/encode work it queues. The UI offers the remainder again.
MAX_BULK_RERENDER = 20


def _owner_scope(auth_result: AuthResult) -> Optional[str]:
    """Admins act on all the portal's tracks; members only on their own."""
    return None if auth_result.is_admin else auth_result.user_email


@router.put("", response_model=SaveResponse)
def save_theme(
    body: SaveRequest,
    config: TenantConfig = Depends(require_tenant_member),
    auth_result: AuthResult = Depends(require_auth),
):
    try:
        save_tenant_theme(config, body.style_params)
        theme = get_theme_for_editor(config)
    except TenantValidationError as exc:
        _raise_http(exc)
    theme_id = _theme_id_for(config)
    # Never fail the save over the follow-up work: the theme is already stored.
    refreshed = 0
    try:
        refreshed = refresh_inflight_jobs(config.id, theme_id)["updated"]
    except Exception:
        logger.exception(f"Tenant '{config.id}': refreshing in-progress tracks after theme save failed")
    outdated: List[str] = []
    try:
        outdated = outdated_jobs(config.id, theme_id, _owner_scope(auth_result))["job_ids"]
    except Exception:
        logger.exception(f"Tenant '{config.id}': listing outdated tracks after theme save failed")
    return SaveResponse(**theme, refreshed_jobs=refreshed, outdated_job_ids=outdated)


@router.get("/outdated-jobs", response_model=OutdatedJobsResponse)
def get_outdated_jobs(
    config: TenantConfig = Depends(require_tenant_member),
    auth_result: AuthResult = Depends(require_auth),
):
    """Finished tracks made with an older version of the theme (re-render to update)."""
    return OutdatedJobsResponse(**outdated_jobs(config.id, _theme_id_for(config), _owner_scope(auth_result)))


@router.post("/rerender-outdated", response_model=RerenderOutdatedResponse)
async def rerender_outdated(
    config: TenantConfig = Depends(require_tenant_member),
    auth_result: AuthResult = Depends(require_auth),
):
    """Re-render every outdated finished track with the current theme.

    Quiet: no per-track "your video is ready" email/push (one per track would
    flood the inbox); the portal shows progress on each track.
    """
    from backend.services.job_manager import JobManager
    from backend.services.theme_rerender_service import RerenderError, ThemeRerenderService

    theme_id = _theme_id_for(config)
    job_ids = (await run_in_threadpool(
        outdated_jobs, config.id, theme_id, _owner_scope(auth_result)
    ))["job_ids"]
    batch, remaining = job_ids[:MAX_BULK_RERENDER], job_ids[MAX_BULK_RERENDER:]
    job_manager = JobManager()
    service = ThemeRerenderService(job_manager)
    started: List[str] = []
    failed: Dict[str, str] = {}
    for job_id in batch:
        job = await run_in_threadpool(job_manager.get_job, job_id)
        if job is None:
            continue
        try:
            await service.start(
                job, theme_id=theme_id,
                requested_by=auth_result.user_email or "unknown",
                notify_customer=False,
            )
            started.append(job_id)
        except RerenderError as e:
            failed[job_id] = str(e)
        except Exception as e:
            logger.exception(f"[job:{job_id}] Bulk theme re-render failed to start")
            failed[job_id] = "Couldn't start the re-render."
    logger.info(
        f"Tenant '{config.id}': bulk re-render started {len(started)}, failed {len(failed)}, "
        f"remaining {len(remaining)}"
    )
    return RerenderOutdatedResponse(started=started, failed=failed, remaining=remaining)
