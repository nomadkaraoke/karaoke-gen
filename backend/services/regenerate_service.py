"""
Regenerate a completed job's GCS outputs on demand (storage retention).

The storage-retention job (backend/services/storage_retention.py) purges the big
regenerable files of jobs completed >30 days ago — every final except the 720p,
the rendered lyrics video, previews and (where the input audio is kept) the
separated stems. This service brings them back:

1. claim the job atomically: COMPLETE -> LYRICS_COMPLETE with
   ``state_data.regen_restore_status = "review_complete"`` (the mechanism shared
   with the admin/theme re-render and the private->public visibility flow) and
   the ``state_data.regenerate`` marker;
2. trigger the screens worker. If the job's stems were purged
   (``stems_purged_at``), the screens worker first re-runs audio separation from
   the job's input (``input_media_gcs_path``, i.e. ``input/edited.flac`` for
   audio-edited jobs) — see ``backend/workers/screens_worker.py`` stems gate and
   the audio worker's restore mode;
3. screens -> render -> video worker, using the job's EXISTING reviewed lyrics,
   instrumental selection (custom/uploaded instrumentals are never purged) and
   style snapshot.

Unlike the admin re-render this is GCS-ONLY: the video worker skips the whole
distribution stage (no YouTube/Dropbox/Google Drive changes, no new brand code,
no Discord post) while the marker is active, and keeps the job's published links.
The customer is emailed/pushed on completion unless ``notify_customer`` is False.

``after="change_to_private"`` chains a public->private visibility change once
the finals are back (redistribution needs the full finals set).

Like the admin re-render the marker records the job's ``review_token`` at claim,
so a later trip through review retires it; a FAILED regenerate keeps it, so
``POST /api/jobs/{id}/retry`` (or another regenerate) resumes it.
"""
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore
from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion

from backend.models.job import JobStatus
from backend.services.firestore_service import log_to_job
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.theme_rerender_service import (
    RerenderConflictError,
    RerenderError,
    claim_for_rerender,
    delete_regenerated_artifacts,
)

logger = logging.getLogger(__name__)

REGENERATE_MARKER = "regenerate"
_LOG_SOURCE = "regenerate"
RATE_LIMIT_COLLECTION = "regenerate_rate_limits"
AFTER_CHANGE_TO_PRIVATE = "change_to_private"
_ALLOWED_AFTER = {None, AFTER_CHANGE_TO_PRIVATE}

# Finals the UI / redistribution need; any missing => renders must be regenerated.
_REQUIRED_FINAL_KEYS = ("lossy_4k_mp4", "lossy_720p_mp4")


# --- Marker helpers ------------------------------------------------------------

def raw_regenerate_marker(job) -> Dict[str, Any]:
    state_data = getattr(job, "state_data", None)
    marker = state_data.get(REGENERATE_MARKER) if isinstance(state_data, dict) else None
    return marker if isinstance(marker, dict) else {}


def active_regenerate(job) -> Dict[str, Any]:
    """The marker if it belongs to the job's current run (same review_token), else ``{}``."""
    marker = raw_regenerate_marker(job)
    if marker and "review_token" in marker:
        if marker["review_token"] != getattr(job, "review_token", None):
            return {}
    return marker


def clear_regenerate_update(job) -> Dict[str, Any]:
    if raw_regenerate_marker(job):
        return {f"state_data.{REGENERATE_MARKER}": DELETE_FIELD}
    return {}


def regenerate_brand_code(job) -> Optional[str]:
    return active_regenerate(job).get("brand_code") or None


def regenerate_suppresses_notifications(job) -> bool:
    marker = active_regenerate(job)
    return bool(marker) and not marker.get("notify_customer", True)


def is_gcs_only_run(job) -> bool:
    """True while a regenerate is rebuilding this job's GCS outputs (no distribution)."""
    return bool(active_regenerate(job))


def renders_missing(job) -> bool:
    """True if the job's big finals are gone (purged, or never recorded)."""
    if getattr(job, "renders_purged_at", None):
        return True
    finals = (getattr(job, "file_urls", None) or {}).get("finals") or {}
    if not isinstance(finals, dict):
        return True
    return not all(finals.get(key) for key in _REQUIRED_FINAL_KEYS)


def stems_need_restore(job) -> bool:
    """True if the job's separated stems were purged and must be re-separated first.

    Bring-your-own-instrumental and finalise-only jobs never had separated
    stems to restore.
    """
    if getattr(job, "existing_instrumental_gcs_path", None):
        return False
    if getattr(job, "finalise_only", False):
        return False
    return bool(getattr(job, "stems_purged_at", None))


# --- Validation ------------------------------------------------------------------

def _claimable_statuses(job) -> set:
    statuses = {JobStatus.COMPLETE.value}
    if active_regenerate(job):
        statuses.update({JobStatus.FAILED.value, JobStatus.CANCELLED.value})
    return statuses


def validate_regenerate(job, after: Optional[str] = None) -> Optional[str]:
    """Return a reason the job can't be regenerated, or None if it can."""
    from backend.services.storage_retention import PURGE_IN_PROGRESS_MESSAGE, purge_in_progress

    status = job.status.value if hasattr(job.status, "value") else job.status
    if status not in _claimable_statuses(job):
        return f"Only finished tracks can be regenerated (current status: {status})."
    if getattr(job, "outputs_deleted_at", None):
        return "This track's outputs were deleted, so it can't be regenerated."
    if getattr(job, "prep_only", False) or getattr(job, "finalise_only", False):
        return "This track type can't be regenerated."
    state_data = job.state_data or {}
    if state_data.get("visibility_change_in_progress") and after != AFTER_CHANGE_TO_PRIVATE:
        return "A visibility change is in progress for this track."
    from backend.services.admin_rerender_service import active_admin_rerender
    if active_admin_rerender(job) or state_data.get("theme_rerender"):
        return "This track is already being re-rendered."
    if purge_in_progress(job):
        return PURGE_IN_PROGRESS_MESSAGE
    if not state_data.get("instrumental_selection"):
        return "This track has no instrumental selection to regenerate with."
    if not ((job.file_urls or {}).get("lyrics") or {}).get("corrections"):
        return "This track has no reviewed lyrics to regenerate with."
    if not getattr(job, "input_media_gcs_path", None):
        return "This track's original audio is no longer available, so it can't be regenerated."
    if not getattr(job, "theme_id", None):
        return "This track has no video style to regenerate with."
    return None


# --- Rate limiting ------------------------------------------------------------------

def _recent(timestamps: List[str], now: datetime, window: timedelta) -> List[str]:
    out = []
    for ts in timestamps or []:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if now - dt < window:
            out.append(dt.isoformat())
    return out


def check_and_record_rate_limit(db, job, user_email: str, settings, now: Optional[datetime] = None) -> Optional[str]:
    """Customer limits: N regenerations per job and M per user in a rolling 24h.

    Returns a refusal message, or None (and records the request) if allowed.
    """
    from backend.config import get_settings

    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    window = timedelta(hours=24)

    job_recent = _recent((job.state_data or {}).get("regenerate_requests") or [], now, window)
    if len(job_recent) >= settings.regenerate_max_per_job_per_day:
        return "This track was regenerated recently. Please try again tomorrow."

    key = hashlib.sha256((user_email or "unknown").strip().lower().encode()).hexdigest()[:32]
    user_ref = db.collection(RATE_LIMIT_COLLECTION).document(key)

    @firestore.transactional
    def record(transaction):
        snap = user_ref.get(transaction=transaction)
        user_recent = _recent((snap.to_dict() or {}).get("requests") if snap.exists else [], now, window)
        if len(user_recent) >= settings.regenerate_max_per_user_per_day:
            return False
        transaction.set(user_ref, {"requests": user_recent + [now.isoformat()], "updated_at": now})
        return True

    if not record(db.transaction()):
        return "You've regenerated several tracks today. Please try again tomorrow."
    return None


# --- Service ---------------------------------------------------------------------

class RegenerateService:
    def __init__(
        self,
        job_manager: Optional[JobManager] = None,
        storage: Optional[StorageService] = None,
    ):
        self.job_manager = job_manager or JobManager()
        self.storage = storage or StorageService()

    async def start(
        self,
        job,
        requested_by: str,
        source: str = "customer",
        notify_customer: bool = True,
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Claim the job and kick off the GCS-only regenerate.

        Raises RerenderError (400/500/503) or RerenderConflictError (409).
        Returns ``{"needs_stems": bool, "brand_code": ...}``.
        """
        if after not in _ALLOWED_AFTER:
            raise RerenderError(f"Unknown follow-up action: {after}")
        reason = validate_regenerate(job, after=after)
        if reason:
            raise RerenderError(reason)

        job_id = job.job_id
        input_path = getattr(job, "input_media_gcs_path", None)
        if not await asyncio.to_thread(self.storage.file_exists, input_path):
            raise RerenderError(
                "This track's original audio is no longer available, so it can't be regenerated."
            )
        update, context = self._build_claim(job, requested_by, source, notify_customer, after)
        await asyncio.to_thread(
            claim_for_rerender, self.job_manager.firestore.db, job_id, update, _claimable_statuses(job),
        )

        try:
            await asyncio.to_thread(self._after_claim, job, context)
            from backend.services.worker_service import get_worker_service
            triggered = await get_worker_service().trigger_screens_worker(job_id)
        except Exception as e:
            logger.exception(f"[job:{job_id}] Regenerate failed after claim")
            await asyncio.to_thread(self._fail_run, job_id, f"Regenerate failed to start: {e}", "start_error")
            raise RerenderError(
                "The regeneration failed to start. Please try again.", status_code=500,
            ) from e
        if not triggered:
            await asyncio.to_thread(
                self._fail_run, job_id, "Regenerate couldn't start the screens worker.", "screens_trigger_failed",
            )
            raise RerenderError("Couldn't start the regeneration. Please try again.", status_code=503)

        await asyncio.to_thread(
            log_to_job, job_id, _LOG_SOURCE, "INFO",
            f"Regenerate started by {requested_by} ({source}); stems restore first: {context['needs_stems']}",
        )
        return {"needs_stems": context["needs_stems"], "brand_code": context["brand_code"]}

    def _build_claim(self, job, requested_by, source, notify_customer, after):
        state_data = job.state_data or {}
        previous = active_regenerate(job)
        brand_code = state_data.get("brand_code") or previous.get("brand_code")
        needs_stems = stems_need_restore(job)
        now = datetime.now(timezone.utc)
        message = (
            f"Regenerate requested by {requested_by} ({source}): rebuilding GCS outputs only "
            f"(no YouTube/Dropbox/Google Drive changes){'; re-separating stems first' if needs_stems else ''}"
        )
        # Only customer-triggered runs count towards the customer's daily limit.
        requests = _recent(state_data.get("regenerate_requests") or [], now, timedelta(hours=24))
        if source == "customer":
            requests = requests + [now.isoformat()]
        update: Dict[str, Any] = {
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
            "state_data.stems_restore": DELETE_FIELD,
            "state_data.regenerate_requests": requests,
            f"state_data.{REGENERATE_MARKER}": {
                "requested_by": requested_by,
                "requested_at": now.isoformat(),
                "source": source,
                "notify_customer": bool(notify_customer),
                "brand_code": brand_code,
                "after": after,
                "needs_stems": needs_stems,
                "review_token": getattr(job, "review_token", None),
            },
            # Stale screens/lyrics video are deleted below; drop their file_urls.
            "file_urls.screens": DELETE_FIELD,
            "file_urls.videos.with_vocals": DELETE_FIELD,
            "updated_at": now,
            "timeline": ArrayUnion([{
                "status": JobStatus.LYRICS_COMPLETE.value,
                "timestamp": now.isoformat(),
                "message": message,
                "metadata": {
                    "action": "regenerate_initiated",
                    "initiated_by": requested_by,
                    "source": source,
                    "needs_stems": needs_stems,
                    "after": after,
                },
            }]),
        }
        if after == AFTER_CHANGE_TO_PRIVATE:
            update["state_data.visibility_change_in_progress"] = True
        return update, {"brand_code": brand_code, "needs_stems": needs_stems, "message": message}

    def _after_claim(self, job, context) -> None:
        log_to_job(job.job_id, _LOG_SOURCE, "INFO", context["message"])
        delete_regenerated_artifacts(self.storage, job.job_id)

    def _fail_run(self, job_id: str, error: str, reason: str) -> None:
        logger.error(f"[job:{job_id}] {error}")
        try:
            self.job_manager.update_job(job_id, {
                "status": JobStatus.FAILED.value,
                "error_message": error,
                "error_details": {"stage": "regenerate", "reason": reason},
                "state_data.regen_restore_status": DELETE_FIELD,
            })
            log_to_job(job_id, _LOG_SOURCE, "ERROR", error)
        except Exception:
            logger.exception(f"[job:{job_id}] Failed to mark regenerate as failed")


__all__ = [
    "REGENERATE_MARKER",
    "RegenerateService",
    "RerenderConflictError",
    "RerenderError",
    "active_regenerate",
    "check_and_record_rate_limit",
    "clear_regenerate_update",
    "is_gcs_only_run",
    "regenerate_brand_code",
    "regenerate_suppresses_notifications",
    "renders_missing",
    "stems_need_restore",
    "validate_regenerate",
]
