"""
Keep a tenant's tracks in step with its theme after a theme edit.

A job snapshots its theme at creation (``jobs/{id}/style/style_params.json`` +
``style_assets``) and renders from that copy, so a theme edit used to reach only
jobs created afterwards — tracks already in progress (and Edits of finished
tracks) still came out in the old look.

On every theme save, tracks that haven't started rendering get the new theme:

* their style snapshot is replaced, so anything rendered from now on (lyrics
  video, screens not yet generated) uses it;
* if their title/end screens may already exist, ``state_data.theme_screens_stale``
  is set. When the render worker picks the job up it diverts once through the
  screens worker (LYRICS_COMPLETE + ``regen_restore_status=review_complete``,
  the same bounce the theme re-render uses), which regenerates the screens and
  re-triggers the render. Doing this at render time — rather than right away —
  never disturbs a user who is in the middle of reviewing.

Finished tracks can't be refreshed without re-rendering, so they are reported
as "outdated" (theme saved after their snapshot) for the UI to offer a
re-render (``outdated_jobs`` / ``rerender_outdated``).
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion

from backend.models.job import JobStatus
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService

logger = logging.getLogger(__name__)

THEME_SCREENS_STALE_KEY = "theme_screens_stale"

# Not yet rendering: swapping the style snapshot is safe. RENDER_PENDING_CAPACITY
# re-enters the render worker from scratch on its auto-retry. REVIEW_COMPLETE is
# excluded — the render worker may already be loading the old style.
_PRE_SCREENS_STATUSES = frozenset({
    JobStatus.PENDING, JobStatus.SEARCHING_AUDIO, JobStatus.AWAITING_AUDIO_SELECTION,
    JobStatus.DOWNLOADING_AUDIO, JobStatus.DOWNLOAD_PENDING_RETRY, JobStatus.DOWNLOADING,
    JobStatus.AWAITING_AUDIO_EDIT, JobStatus.IN_AUDIO_EDIT, JobStatus.AUDIO_EDIT_COMPLETE,
    JobStatus.AWAITING_DURATION_CONFIRM, JobStatus.SEPARATING_STAGE1, JobStatus.SEPARATING_STAGE2,
    JobStatus.AUDIO_COMPLETE, JobStatus.TRANSCRIBING, JobStatus.CORRECTING,
    JobStatus.LYRICS_COMPLETE,
})
# Screens may already exist (or are being made from the old snapshot right now).
_SCREENS_MADE_STATUSES = frozenset({
    JobStatus.GENERATING_SCREENS, JobStatus.APPLYING_PADDING, JobStatus.AWAITING_REVIEW,
    JobStatus.IN_REVIEW, JobStatus.RENDER_PENDING_CAPACITY,
})
REFRESHABLE_STATUSES = _PRE_SCREENS_STATUSES | _SCREENS_MADE_STATUSES


def _status_value(status) -> str:
    return status.value if hasattr(status, "value") else str(status)


def _as_utc(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def theme_updated_at(theme_id: str, storage: Optional[StorageService] = None) -> Optional[datetime]:
    """When the theme's style was last saved (its style_params.json write time)."""
    from backend.services.theme_service import THEMES_PREFIX
    storage = storage or StorageService()
    try:
        blob = storage.bucket.get_blob(f"{THEMES_PREFIX}/{theme_id}/style_params.json")
    except Exception as e:
        logger.warning(f"Couldn't read theme '{theme_id}' timestamp: {e}")
        return None
    return _as_utc(blob.updated) if blob is not None else None


def job_theme_applied_at(job) -> Optional[datetime]:
    """When the job's style snapshot was taken (creation unless re-snapshotted)."""
    return _as_utc(getattr(job, "theme_applied_at", None)) or _as_utc(getattr(job, "created_at", None))


def snapshot_theme_update(job, theme_id: str) -> Dict:
    """Copy ``theme_id``'s current style onto the job; return the Firestore fields to set.

    Writes the job's style folder (GCS) immediately — callers apply the returned
    fields themselves (often inside a larger update).
    """
    from backend.api.routes.file_upload import _prepare_theme_for_job
    style_params_path, style_assets, _ = _prepare_theme_for_job(
        job.job_id, theme_id, getattr(job, "color_overrides", None) or None
    )
    if not style_params_path:
        raise ValueError(f"Theme '{theme_id}' has no style to apply.")
    return {
        "theme_id": theme_id,
        "style_params_gcs_path": style_params_path,
        "style_assets": style_assets,
        "theme_applied_at": datetime.now(timezone.utc),
    }


def refresh_inflight_jobs(tenant_id: str, theme_id: str, job_manager: Optional[JobManager] = None) -> Dict[str, int]:
    """Apply the just-saved theme to every tenant track that hasn't started rendering.

    Returns ``{"updated": n, "failed": m}``. Best-effort per job: one bad job
    never blocks the save or the other jobs.
    """
    job_manager = job_manager or JobManager()
    updated = failed = 0
    jobs = job_manager.list_jobs(tenant_id=tenant_id, limit=1000)
    for job in jobs:
        if job.status not in REFRESHABLE_STATUSES or getattr(job, "theme_id", None) != theme_id:
            continue
        try:
            update = snapshot_theme_update(job, theme_id)
            if job.status in _SCREENS_MADE_STATUSES:
                update[f"state_data.{THEME_SCREENS_STALE_KEY}"] = True
            update["timeline"] = ArrayUnion([{
                "status": _status_value(job.status),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": "Theme updated: this track will use the new look",
            }])
            if _update_if_status(job_manager, job.job_id, update, REFRESHABLE_STATUSES):
                updated += 1
        except Exception as e:
            failed += 1
            logger.error(f"[job:{job.job_id}] Theme refresh failed: {e}", exc_info=True)
    logger.info(f"Tenant '{tenant_id}' theme saved: refreshed {updated} in-progress tracks ({failed} failed)")
    return {"updated": updated, "failed": failed}


def _update_if_status(job_manager: JobManager, job_id: str, update: Dict, allowed) -> bool:
    """Apply ``update`` iff the job is still in one of ``allowed`` statuses (transactional)."""
    from backend.services.theme_rerender_service import RerenderConflictError, claim_for_rerender
    try:
        claim_for_rerender(job_manager.firestore.db, job_id, update, {_status_value(s) for s in allowed})
        return True
    except RerenderConflictError:
        logger.info(f"[job:{job_id}] Theme refresh skipped: job moved on")
        return False


async def divert_for_stale_screens(job, job_manager: JobManager, storage: Optional[StorageService] = None) -> bool:
    """Render-worker hook: regenerate theme-stale screens before rendering.

    Returns True when the job was handed to the screens worker (the render worker
    must stop; the screens worker re-triggers it). Returns False to render now.
    """
    state_data = job.state_data or {}
    if not state_data.get(THEME_SCREENS_STALE_KEY) or job.status != JobStatus.REVIEW_COMPLETE:
        return False

    from backend.services.theme_rerender_service import (
        RerenderConflictError,
        claim_for_rerender,
        delete_regenerated_artifacts,
    )

    job_id = job.job_id
    now = datetime.now(timezone.utc)
    update = {
        "status": JobStatus.LYRICS_COMPLETE.value,
        f"state_data.{THEME_SCREENS_STALE_KEY}": DELETE_FIELD,
        "state_data.regen_restore_status": JobStatus.REVIEW_COMPLETE.value,
        "state_data.audio_complete": True,
        "state_data.lyrics_complete": True,
        "state_data.screens_progress": DELETE_FIELD,
        "file_urls.screens": DELETE_FIELD,
        "updated_at": now,
        "timeline": ArrayUnion([{
            "status": JobStatus.LYRICS_COMPLETE.value,
            "timestamp": now.isoformat(),
            "message": "Regenerating title and end screens with the updated theme",
        }]),
    }
    try:
        claim_for_rerender(job_manager.firestore.db, job_id, update, {JobStatus.REVIEW_COMPLETE.value})
    except RerenderConflictError:
        # Another trigger already moved the job on (e.g. a duplicate dispatch diverted it).
        logger.info(f"[job:{job_id}] Stale-screens divert: job no longer review_complete, skipping render")
        return True

    # Old screens, and (for an Edit of a finished track) old finals MOVs the
    # encoder would otherwise splice back in.
    delete_regenerated_artifacts(storage or StorageService(), job_id)

    from backend.services.worker_service import get_worker_service
    if await get_worker_service().trigger_screens_worker(job_id):
        logger.info(f"[job:{job_id}] Theme changed since screens were made: regenerating before render")
        return True

    # Couldn't hand off: put the job back and render with fresh screens on the
    # next attempt (flag restored) rather than stranding it.
    logger.error(f"[job:{job_id}] Stale-screens divert: failed to trigger screens worker, restoring review_complete")
    job_manager.update_job(job_id, {
        "status": JobStatus.REVIEW_COMPLETE.value,
        "state_data.regen_restore_status": DELETE_FIELD,
        f"state_data.{THEME_SCREENS_STALE_KEY}": True,
    })
    raise RuntimeError("Couldn't regenerate screens for the updated theme")


def outdated_jobs(tenant_id: str, theme_id: str, user_email: Optional[str],
                  job_manager: Optional[JobManager] = None,
                  storage: Optional[StorageService] = None) -> Dict:
    """Finished tracks made with an older version of the theme that can be re-rendered.

    ``user_email`` scopes to the caller's own tracks (None = all, for admins).
    """
    from backend.services.theme_rerender_service import validate_rerender
    job_manager = job_manager or JobManager()
    updated_at = theme_updated_at(theme_id, storage)
    result = {"theme_updated_at": updated_at.isoformat() if updated_at else None, "job_ids": []}
    if updated_at is None:
        return result
    # Same (tenant_id, user_email) query shape as the portal's job list, so it
    # needs no extra composite index; status is filtered here.
    jobs = job_manager.list_jobs(tenant_id=tenant_id, user_email=user_email, limit=1000)
    for job in jobs:
        if job.status != JobStatus.COMPLETE or getattr(job, "theme_id", None) != theme_id:
            continue
        applied = job_theme_applied_at(job)
        if applied is None or applied >= updated_at:
            continue
        if validate_rerender(job) is None:
            result["job_ids"].append(job.job_id)
    return result


def edit_theme_update(job, screens_regenerating: bool) -> Dict:
    """Fields that make an Edit of a finished tenant track use the current theme.

    Empty for non-tenant jobs or when the track already has the current theme.
    ``screens_regenerating``: the Edit already regenerates the screens (metadata
    change), which will pick up the new snapshot — no stale flag needed.
    """
    tenant_id = getattr(job, "tenant_id", None)
    if not tenant_id or not getattr(job, "theme_id", None):
        return {}
    from backend.services.tenant_admin_service import _theme_id_for
    from backend.services.tenant_service import get_tenant_service
    config = get_tenant_service().get_tenant_config(tenant_id)
    if config is None:
        return {}
    theme_id = _theme_id_for(config)
    if theme_id != job.theme_id:
        return {}
    updated_at = theme_updated_at(theme_id)
    applied = job_theme_applied_at(job)
    if updated_at is None or (applied is not None and applied >= updated_at):
        return {}
    update = snapshot_theme_update(job, theme_id)
    if not screens_regenerating:
        update[f"state_data.{THEME_SCREENS_STALE_KEY}"] = True
    logger.info(f"[job:{job.job_id}] Edit: applying the current theme '{theme_id}'")
    return update
