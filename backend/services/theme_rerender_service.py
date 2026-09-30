"""
Re-render a completed tenant job with the tenant's CURRENT theme.

Tenant users can edit their theme at any time, but a job snapshots the theme's
style at creation (``jobs/{id}/style/style_params.json`` + ``style_assets``), so
existing videos keep the old look. This service re-snapshots the current theme
onto the job and re-runs everything downstream of review — title/end screens,
the lyrics video, final encodes and CDG/TXT packages — reusing the job's
reviewed lyrics (corrections_updated.json) and instrumental selection, so the
user never goes back through review.

Mechanism (same as VisibilityChangeService.change_to_public): move the job to
LYRICS_COMPLETE with ``state_data.regen_restore_status = "review_complete"`` and
trigger the screens worker. The screens worker regenerates the screens with the
new style, restores REVIEW_COMPLETE and triggers the render worker, which renders
from the existing corrections; the video worker then re-encodes and completes.

Scope is deliberately narrow: tenant jobs with no external distribution
(YouTube/Dropbox/GDrive) — re-rendering a distributed job would need the
delete/redistribute dance the visibility flow does, which tenants don't use.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore
from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion

from backend.models.job import JobStatus
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# Screens + lyrics video are regenerated; stale copies must go so the encoder
# can't reuse them (it prefers an existing screens/*.mov over the new PNG).
_REGENERATED_ARTIFACTS = [
    "screens/title.mov",
    "screens/title.jpg",
    "screens/title.png",
    "screens/end.mov",
    "screens/end.jpg",
    "screens/end.png",
    "videos/with_vocals.mkv",
    "videos/with_vocals.mov",
]


class RerenderError(Exception):
    """Re-render request rejected; ``status_code`` maps to the HTTP response."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class RerenderConflictError(RerenderError):
    def __init__(self, message: str):
        super().__init__(message, status_code=409)


def validate_rerender(job) -> Optional[str]:
    """Return a reason the job can't be re-rendered, or None if it can."""
    if not getattr(job, "tenant_id", None):
        return "Re-rendering with the current theme is only available for portal tracks."
    if job.status != JobStatus.COMPLETE.value:
        return f"Only finished tracks can be re-rendered (current status: {job.status})."
    if getattr(job, "outputs_deleted_at", None):
        return "This track's outputs were deleted, so it can't be re-rendered."
    if not getattr(job, "theme_id", None):
        return "This track wasn't made from a theme, so there's nothing to re-apply."
    if getattr(job, "prep_only", False) or getattr(job, "finalise_only", False):
        return "This track type can't be re-rendered."
    state_data = job.state_data or {}
    if not state_data.get("instrumental_selection"):
        return "This track has no instrumental selection to re-render with."
    if not ((job.file_urls or {}).get("lyrics") or {}).get("corrections"):
        return "This track has no reviewed lyrics to re-render with."
    if state_data.get("youtube_url") or state_data.get("dropbox_link") or state_data.get("gdrive_files"):
        return "This track was published to YouTube/Dropbox/Drive and can't be re-rendered here."
    return None


class ThemeRerenderService:
    def __init__(
        self,
        job_manager: Optional[JobManager] = None,
        storage: Optional[StorageService] = None,
    ):
        self.job_manager = job_manager or JobManager()
        self.storage = storage or StorageService()

    async def start(self, job, theme_id: str, requested_by: str) -> None:
        """Re-snapshot ``theme_id`` onto the job and kick off the re-render.

        Raises RerenderConflictError if the job stopped being COMPLETE (e.g. a
        double-click already started a re-render).
        """
        job_id = job.job_id
        reason = validate_rerender(job)
        if reason:
            raise RerenderError(reason)

        # Snapshot the current theme onto the job's style folder. Done before the
        # claim so a failure here leaves the job untouched; overwriting the style
        # copy of a still-complete job is harmless (its finals are already made).
        from backend.api.routes.file_upload import _prepare_theme_for_job
        style_params_path, style_assets, _ = _prepare_theme_for_job(
            job_id, theme_id, getattr(job, "color_overrides", None) or None
        )
        if not style_params_path:
            raise RerenderError(f"Theme '{theme_id}' has no style to apply.", status_code=500)

        now = datetime.now(timezone.utc)
        update = {
            "status": JobStatus.LYRICS_COMPLETE.value,
            "progress": 50,
            "theme_id": theme_id,
            "style_params_gcs_path": style_params_path,
            "style_assets": style_assets,
            "error_message": None,
            "error_details": None,
            "state_data.regen_restore_status": "review_complete",
            "state_data.audio_complete": True,
            "state_data.lyrics_complete": True,
            "state_data.screens_progress": DELETE_FIELD,
            "state_data.render_progress": DELETE_FIELD,
            "state_data.video_progress": DELETE_FIELD,
            "state_data.encoding_progress": DELETE_FIELD,
            "state_data.theme_rerender": {
                "requested_by": requested_by,
                "requested_at": now.isoformat(),
                "theme_id": theme_id,
            },
            # The old screens are deleted below. Drop their file_urls so nothing
            # (e.g. a later retry) reuses them.
            "file_urls.screens": DELETE_FIELD,
            "file_urls.videos.with_vocals": DELETE_FIELD,
            "updated_at": now,
            "timeline": ArrayUnion([{
                "status": JobStatus.LYRICS_COMPLETE.value,
                "timestamp": now.isoformat(),
                "message": f"Re-render with current theme '{theme_id}' requested by {requested_by}",
            }]),
        }
        self._claim(job_id, update)

        for rel in _REGENERATED_ARTIFACTS:
            try:
                self.storage.delete_file(f"jobs/{job_id}/{rel}", ignore_missing=True)
            except Exception as e:  # best-effort, like the visibility flow
                logger.warning(f"[job:{job_id}] Re-render: failed to delete {rel}: {e}")

        from backend.services.worker_service import get_worker_service
        triggered = await get_worker_service().trigger_screens_worker(job_id)
        if not triggered:
            logger.error(f"[job:{job_id}] Re-render: failed to trigger screens worker, restoring complete")
            self.job_manager.update_job(job_id, {
                "status": JobStatus.COMPLETE.value,
                "progress": 100,
                "state_data.regen_restore_status": DELETE_FIELD,
                "state_data.theme_rerender": DELETE_FIELD,
            })
            raise RerenderError("Couldn't start the re-render. Please try again.", status_code=503)

        logger.info(f"[job:{job_id}] Re-render with theme '{theme_id}' started by {requested_by}")

    def _claim(self, job_id: str, update: dict) -> None:
        """Atomically apply ``update`` iff the job is still COMPLETE."""
        db = self.job_manager.firestore.db
        job_ref = db.collection("jobs").document(job_id)

        @firestore.transactional
        def claim(transaction):
            snapshot = job_ref.get(transaction=transaction)
            status = (snapshot.to_dict() or {}).get("status") if snapshot.exists else None
            if status != JobStatus.COMPLETE.value:
                return False
            transaction.update(job_ref, update)
            return True

        if not claim(db.transaction()):
            raise RerenderConflictError("This track is already being re-rendered or is no longer finished.")
