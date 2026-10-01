"""
Admin re-render: rebuild ANY finished job end to end without a review step.

Use case: a renderer fix (e.g. RTL/Hebrew lyrics, v0.255.0) needs videos that
were already delivered regenerated. Unlike the tenant "Re-render with current
theme" (theme_rerender_service), this:

- works for every job (consumer or tenant, public or private);
- keeps the job's EXISTING style snapshot (``jobs/{id}/style/...``,
  ``style_params_gcs_path``, ``style_assets``) — no theme re-snapshot;
- deletes the published outputs (YouTube video, Google Drive files, Dropbox
  folder) up front, like Edit, but KEEPS the brand code (it isn't recycled):
  the video worker re-publishes under the same code via ``rerender_brand_code``.
  YouTube gets a new upload, so the YouTube URL changes;
- doesn't email/push the customer on completion unless the admin opted in
  (``notify_customer``) — see ``suppress_customer_notifications``.

Mechanism (shared with the theme re-render and the private->public visibility
flow): atomically move the job COMPLETE -> LYRICS_COMPLETE with
``state_data.regen_restore_status = "review_complete"`` and trigger the screens
worker. The screens worker regenerates the screens, restores REVIEW_COMPLETE and
triggers the render worker, which renders from the existing
corrections_updated.json; the video worker then encodes, distributes and
completes using ``state_data.instrumental_selection``.

``state_data.admin_rerender`` is the in-progress marker. The video worker
clears it on success; a FAILED admin re-render keeps it, which lets
``POST /api/jobs/{id}/retry`` (or another admin re-render) resume it.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion

from backend.models.job import JobStatus
from backend.services.firestore_service import log_to_job
from backend.services.job_manager import JobManager
from backend.services.published_outputs_cleanup import (
    delete_dropbox_folder,
    delete_gdrive_files,
    delete_youtube_video,
    snapshot_published_outputs,
)
from backend.services.storage_service import StorageService
from backend.services.theme_rerender_service import (
    RerenderError,
    claim_for_rerender,
    delete_regenerated_artifacts,
)

logger = logging.getLogger(__name__)

ADMIN_RERENDER_MARKER = "admin_rerender"
_LOG_SOURCE = "admin-rerender"


def _marker(job) -> Dict[str, Any]:
    state_data = getattr(job, "state_data", None)
    marker = state_data.get(ADMIN_RERENDER_MARKER) if isinstance(state_data, dict) else None
    return marker if isinstance(marker, dict) else {}


def _claimable_statuses(job) -> set:
    """COMPLETE, plus FAILED when the failure was an admin re-render (retry)."""
    statuses = {JobStatus.COMPLETE.value}
    if _marker(job):
        statuses.add(JobStatus.FAILED.value)
    return statuses


def validate_admin_rerender(job) -> Optional[str]:
    """Return a reason the job can't be re-rendered, or None if it can."""
    if job.status not in _claimable_statuses(job):
        return f"Only completed jobs can be re-rendered (current status: {job.status})."
    if getattr(job, "outputs_deleted_at", None):
        return "This job's outputs were deleted, so it can't be re-rendered."
    if getattr(job, "prep_only", False) or getattr(job, "finalise_only", False):
        return "Prep-only / finalise-only jobs can't be re-rendered."
    state_data = job.state_data or {}
    if state_data.get("visibility_change_in_progress"):
        return "A visibility change is in progress for this job."
    if not state_data.get("instrumental_selection"):
        return "This job has no instrumental selection to re-render with."
    if not ((job.file_urls or {}).get("lyrics") or {}).get("corrections"):
        return "This job has no reviewed lyrics to re-render with."
    return None


def suppress_customer_notifications(job) -> bool:
    """True if the job's completion email/push must be skipped.

    Only an admin re-render the admin didn't opt into notifying suppresses
    them; normal jobs and the tenant theme re-render always notify.
    """
    marker = _marker(job)
    return bool(marker) and not marker.get("notify_customer", False)


class AdminRerenderService:
    def __init__(
        self,
        job_manager: Optional[JobManager] = None,
        storage: Optional[StorageService] = None,
    ):
        self.job_manager = job_manager or JobManager()
        self.storage = storage or StorageService()

    async def start(self, job, requested_by: str, notify_customer: bool = False) -> Dict[str, Any]:
        """Claim the job, delete its published outputs and kick off the re-render.

        Returns ``{"brand_code", "previous_outputs", "cleanup_results"}``.
        Raises RerenderError (400/503) or RerenderConflictError (409).
        """
        job_id = job.job_id
        reason = validate_admin_rerender(job)
        if reason:
            raise RerenderError(reason)

        state_data = job.state_data or {}
        previous_marker = _marker(job)
        # On a retry the outputs were already removed (and cleared from
        # state_data) by the first attempt — keep that attempt's record.
        previous_outputs = {
            **(previous_marker.get("previous_outputs") or {}),
            **snapshot_published_outputs(state_data),
        }
        brand_code = state_data.get("brand_code") or previous_marker.get("brand_code")
        is_retry = bool(previous_marker)

        now = datetime.now(timezone.utc)
        message = (
            f"Admin re-render requested by {requested_by} "
            f"(existing style, no review; brand code {brand_code or 'n/a'} kept; "
            f"customer notification {'on' if notify_customer else 'off'})"
        )
        update = {
            "status": JobStatus.LYRICS_COMPLETE.value,
            "progress": 50,
            "error_message": None,
            "error_details": None,
            "state_data.regen_restore_status": "review_complete",
            "state_data.audio_complete": True,
            "state_data.lyrics_complete": True,
            "state_data.screens_progress": DELETE_FIELD,
            "state_data.render_progress": DELETE_FIELD,
            "state_data.video_progress": DELETE_FIELD,
            "state_data.encoding_progress": DELETE_FIELD,
            # The published outputs are deleted below — drop the dead links so
            # the dashboard doesn't show them. brand_code stays (it's reused).
            "state_data.youtube_url": DELETE_FIELD,
            "state_data.youtube_video_id": DELETE_FIELD,
            "state_data.youtube_upload_queued": DELETE_FIELD,
            "state_data.dropbox_link": DELETE_FIELD,
            "state_data.gdrive_files": DELETE_FIELD,
            "state_data.distribution_warnings": DELETE_FIELD,
            f"state_data.{ADMIN_RERENDER_MARKER}": {
                "requested_by": requested_by,
                "requested_at": now.isoformat(),
                "notify_customer": bool(notify_customer),
                "brand_code": brand_code,
                "previous_outputs": previous_outputs,
            },
            # Old screens are deleted below; drop their file_urls so nothing
            # (e.g. a later retry) reuses them.
            "file_urls.screens": DELETE_FIELD,
            "file_urls.videos.with_vocals": DELETE_FIELD,
            "updated_at": now,
            "timeline": ArrayUnion([{
                "status": JobStatus.LYRICS_COMPLETE.value,
                "timestamp": now.isoformat(),
                "message": message,
                "metadata": {
                    "action": "admin_rerender_initiated",
                    "initiated_by": requested_by,
                    "notify_customer": bool(notify_customer),
                    "brand_code": brand_code,
                    "previous_outputs": previous_outputs,
                    "retry": is_retry,
                },
            }]),
        }
        claim_for_rerender(self.job_manager.firestore.db, job_id, update, _claimable_statuses(job))

        logger.info(
            f"[job:{job_id}] Admin re-render claimed by {requested_by}: brand_code={brand_code} "
            f"notify_customer={notify_customer} retry={is_retry} previous_outputs={sorted(previous_outputs)}"
        )
        log_to_job(job_id, _LOG_SOURCE, "INFO", message, {
            "previous_outputs": previous_outputs,
            "notify_customer": bool(notify_customer),
            "retry": is_retry,
        })

        delete_regenerated_artifacts(self.storage, job_id)
        cleanup_results = self._delete_published_outputs(job, state_data, brand_code)

        from backend.services.worker_service import get_worker_service
        triggered = await get_worker_service().trigger_screens_worker(job_id)
        if not triggered:
            # The published outputs may already be gone, so don't pretend the job
            # is still complete: fail it with the marker kept, so /retry (or
            # another admin re-render) picks the re-render up again.
            error = "Admin re-render couldn't start the screens worker. Retry the job to continue."
            logger.error(f"[job:{job_id}] {error}")
            self.job_manager.update_job(job_id, {
                "status": JobStatus.FAILED.value,
                "error_message": error,
                "error_details": {"stage": "admin_rerender", "reason": "screens_trigger_failed"},
                "state_data.regen_restore_status": DELETE_FIELD,
            })
            log_to_job(job_id, _LOG_SOURCE, "ERROR", error)
            raise RerenderError("Couldn't start the re-render. Please retry the job.", status_code=503)

        log_to_job(job_id, _LOG_SOURCE, "INFO", "Re-render started (screens worker triggered)", {
            "cleanup_results": cleanup_results,
        })
        return {
            "brand_code": brand_code,
            "previous_outputs": previous_outputs,
            "cleanup_results": cleanup_results,
        }

    def _delete_published_outputs(self, job, state_data: dict, brand_code: Optional[str]) -> Dict[str, Any]:
        """Delete YouTube/Dropbox/GDrive outputs; best-effort, the brand code is kept.

        Failures don't abort the re-render: the re-publish targets the same
        names (same brand code), and the server-side YouTube upload and GDrive
        upload both replace a same-named leftover, while Dropbox uploads overwrite.
        """
        job_id = job.job_id
        from backend.services.job_defaults_service import get_effective_distribution_for_job

        # Private jobs publish to the private Dropbox path, not job.dropbox_path.
        dropbox_path = get_effective_distribution_for_job(job).dropbox_path
        results = {
            "youtube": delete_youtube_video(job_id, state_data.get("youtube_url")),
            "dropbox": delete_dropbox_folder(job_id, dropbox_path, brand_code, job.artist, job.title),
            # Same brand code is re-published, so the kjbox GCS mirror copy is
            # overwritten in place rather than removed.
            "gdrive": delete_gdrive_files(
                job_id, state_data.get("gdrive_files"), brand_code, cleanup_mirror=False
            ),
            "brand_code": {"status": "kept", "code": brand_code},
        }
        for service in ("youtube", "dropbox", "gdrive"):
            status = results[service].get("status")
            level = "WARNING" if status in ("failed", "partial", "error") else "INFO"
            log_to_job(job_id, _LOG_SOURCE, level, f"{service} cleanup: {status}", results[service])
        logger.info(
            f"[job:{job_id}] Admin re-render cleanup: "
            + ", ".join(f"{k}={v.get('status')}" for k, v in results.items())
        )
        try:
            now = datetime.now(timezone.utc)
            self.job_manager.update_job(job_id, {
                f"state_data.{ADMIN_RERENDER_MARKER}.cleanup_results": results,
                "timeline": ArrayUnion([{
                    "status": JobStatus.LYRICS_COMPLETE.value,
                    "timestamp": now.isoformat(),
                    "message": "Admin re-render: previous published outputs removed",
                    "metadata": {"action": "admin_rerender_cleanup", "cleanup_results": results},
                }]),
            })
        except Exception as e:  # audit only — never block the re-render
            logger.warning(f"[job:{job_id}] Failed to record admin re-render cleanup results: {e}")
        return results
