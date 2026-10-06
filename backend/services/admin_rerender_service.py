"""
Admin re-render: rebuild ANY finished job end to end without a review step.

Use case: a renderer fix (e.g. RTL/Hebrew lyrics, v0.255.0) needs videos that
were already delivered regenerated. Unlike the tenant "Re-render with current
theme" (theme_rerender_service), this:

- works for every job (consumer or tenant, public or private);
- keeps the job's EXISTING style snapshot (``jobs/{id}/style/...``,
  ``style_params_gcs_path``, ``style_assets``) — no theme re-snapshot;
- deletes the published outputs (YouTube video, Google Drive files, Dropbox
  folder) up front, like Edit — but only for destinations the re-render will
  re-publish to (see ``plan_republish``); others are left in place with a
  warning. The brand code is KEPT (never recycled): the video worker
  re-publishes under the same code via ``rerender_brand_code``. YouTube gets a
  new upload, so the YouTube URL changes;
- doesn't email/push the customer (or Discord / community voters) on
  completion unless the admin opted in (``notify_customer``) — see
  ``suppress_customer_notifications``.

Mechanism (shared with the theme re-render and the private->public visibility
flow): atomically move the job COMPLETE -> LYRICS_COMPLETE with
``state_data.regen_restore_status = "review_complete"`` and trigger the screens
worker. The screens worker regenerates the screens, restores REVIEW_COMPLETE and
triggers the render worker, which renders from the existing
corrections_updated.json; the video worker then encodes, distributes and
completes using ``state_data.instrumental_selection``.

``state_data.admin_rerender`` is the in-progress marker. The video worker clears
it on success; a FAILED admin re-render keeps it, which lets an admin
``POST /api/jobs/{id}/retry`` (or another admin re-render) resume it. The marker
only applies to its own run: it records the job's ``review_token`` at claim
(a new token is minted whenever the job goes back through review), and the
admin reset/restart, Edit, visibility and delete-outputs flows clear it.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import firestore
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
    RerenderConflictError,
    RerenderError,
    delete_regenerated_artifacts,
)

logger = logging.getLogger(__name__)

ADMIN_RERENDER_MARKER = "admin_rerender"
_LOG_SOURCE = "admin-rerender"

# destination -> state_data keys that reference its published output
_DESTINATION_KEYS = {
    "youtube": ("youtube_url", "youtube_video_id", "youtube_upload_queued"),
    "dropbox": ("dropbox_link",),
    "gdrive": ("gdrive_files",),
}
# state_data link a destination's output is recorded under (kept_outputs keys)
_DESTINATION_LINK = {"youtube": "youtube_url", "dropbox": "dropbox_link", "gdrive": "gdrive_files"}


# --- Marker helpers ------------------------------------------------------------

def raw_admin_rerender_marker(job) -> Dict[str, Any]:
    """The stored marker, regardless of whether it belongs to the current run."""
    state_data = getattr(job, "state_data", None)
    marker = state_data.get(ADMIN_RERENDER_MARKER) if isinstance(state_data, dict) else None
    return marker if isinstance(marker, dict) else {}


def active_admin_rerender(job) -> Dict[str, Any]:
    """The marker if it belongs to the job's current run, else ``{}``.

    A job that has since gone back through review (admin reset, Edit, ...) has a
    new ``review_token``, so a stale marker from a failed admin re-render no
    longer suppresses notifications or forces the brand code.
    """
    marker = raw_admin_rerender_marker(job)
    if marker and "review_token" in marker:
        if marker["review_token"] != getattr(job, "review_token", None):
            return {}
    return marker


def admin_rerender_blocks_upload(job_data: Dict[str, Any]) -> bool:
    """True if a queued YouTube upload must not start for this job (raw job doc).

    An ACTIVE (review_token-scoped) admin re-render on a job that isn't complete
    is rebuilding (or failed while rebuilding) the finals. Used inside the
    queue's claim transaction, which reads the job doc.
    """
    from types import SimpleNamespace

    view = SimpleNamespace(
        state_data=job_data.get("state_data") or {},
        review_token=job_data.get("review_token"),
    )
    return bool(active_admin_rerender(view)) and job_data.get("status") != JobStatus.COMPLETE.value


UPLOAD_IN_PROGRESS_MESSAGE = (
    "A YouTube upload for this track is in progress — try again in a few minutes."
)


def claim_with_youtube_queue(
    db, job_id: str, update: Dict[str, Any], allowed_statuses: set,
    cancel_deferred_upload: bool, keep_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically claim the job for an admin re-render, reading its YouTube queue doc too.

    - job status not claimable → RerenderConflictError (409);
    - a deferred upload is ``processing`` → RerenderConflictError (409): an
      upload of the old finals must never overlap the re-render;
    - a ``queued``/``failed`` entry is cancelled in the same transaction when
      the re-render will re-publish YouTube, otherwise left in place (warning).

    The queue's ``mark_processing`` transaction reads the job doc and refuses
    while an admin re-render is active, so the two can never interleave.
    Returns the queue outcome, e.g. ``{"status": "cancelled", "previous_status": "queued"}``.
    """
    from backend.services.youtube_upload_queue_service import (
        PACIFIC_TZ,
        YOUTUBE_UPLOAD_QUEUE_COLLECTION,
    )

    from backend.config import get_settings

    # Same collection the YouTube queue's mark_processing transaction reads, so
    # the two claims always contend on the same job document.
    job_ref = db.collection(get_settings().firestore_collection).document(job_id)
    queue_ref = db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION).document(job_id)
    marker_key = f"state_data.{ADMIN_RERENDER_MARKER}"

    @firestore.transactional
    def claim(transaction):
        snapshot = job_ref.get(transaction=transaction)
        queue_snapshot = queue_ref.get(transaction=transaction)
        status = (snapshot.to_dict() or {}).get("status") if snapshot.exists else None
        if status not in allowed_statuses:
            return "not_claimable", None
        queue_status = (queue_snapshot.to_dict() or {}).get("status") if queue_snapshot.exists else None
        if queue_status == "processing":
            return "uploading", None

        payload = dict(update)  # fresh copy per transaction attempt
        if queue_status in ("queued", "failed"):
            if cancel_deferred_upload:
                transaction.update(queue_ref, {
                    "status": "cancelled",
                    "last_error": "Cancelled: admin re-render will re-upload",
                    "updated_at": datetime.now(PACIFIC_TZ),
                })
                outcome = {"status": "cancelled", "previous_status": queue_status}
            else:
                warning = f"pending deferred YouTube upload left queued (YouTube not re-published: {keep_reason})"
                marker = dict(payload[marker_key])
                marker["warnings"] = [*marker.get("warnings", []), warning]
                payload[marker_key] = marker
                outcome = {"status": "kept", "previous_status": queue_status, "reason": keep_reason,
                           "warning": warning}
        else:
            outcome = {"status": "skipped",
                       "reason": "no queue entry" if queue_status is None else f"entry is {queue_status}"}
        transaction.update(job_ref, payload)
        return "ok", outcome

    result, outcome = claim(db.transaction())
    if result == "uploading":
        raise RerenderConflictError(UPLOAD_IN_PROGRESS_MESSAGE)
    if result != "ok":
        raise RerenderConflictError("This track is already being re-rendered or is no longer finished.")
    return outcome


def clear_admin_rerender_update(job) -> Dict[str, Any]:
    """Firestore update that drops the marker (empty if there is none).

    Also drops a storage-retention regenerate marker: every caller (admin
    reset/restart, Edit, visibility change, delete outputs) starts a new run.
    """
    update: Dict[str, Any] = {}
    if raw_admin_rerender_marker(job):
        update[f"state_data.{ADMIN_RERENDER_MARKER}"] = DELETE_FIELD
    from backend.services.regenerate_service import clear_regenerate_update
    update.update(clear_regenerate_update(job))
    return update


def suppress_customer_notifications(job) -> bool:
    """True if this run's customer-facing notifications must be skipped.

    Only an active admin re-render the admin didn't opt into announcing
    suppresses them; normal jobs and a single tenant theme re-render notify.
    """
    marker = active_admin_rerender(job)
    if marker:
        return not marker.get("notify_customer", False)
    # A bulk "re-render all outdated tracks" theme re-render is started quietly.
    theme_marker = (getattr(job, "state_data", None) or {}).get("theme_rerender") or {}
    if theme_marker.get("notify_customer") is False:
        return True
    # A storage-retention regenerate notifies unless it was started quietly.
    from backend.services.regenerate_service import regenerate_suppresses_notifications
    return regenerate_suppresses_notifications(job)


def admin_rerender_brand_code(job) -> Optional[str]:
    return active_admin_rerender(job).get("brand_code") or None


def admin_rerender_kept_outputs(job) -> Dict[str, Any]:
    """Published outputs left in place (not re-published) by the active re-render."""
    return active_admin_rerender(job).get("kept_outputs") or {}


# --- Validation / planning -----------------------------------------------------

def _claimable_statuses(job) -> set:
    """COMPLETE, plus FAILED/CANCELLED when that was an admin re-render (resume)."""
    statuses = {JobStatus.COMPLETE.value}
    if active_admin_rerender(job):
        statuses.update({JobStatus.FAILED.value, JobStatus.CANCELLED.value})
    return statuses


def validate_admin_rerender(job) -> Optional[str]:
    """Return a reason the job can't be re-rendered, or None if it can."""
    if job.status not in _claimable_statuses(job):
        return f"Only completed jobs can be re-rendered (current status: {job.status})."
    if getattr(job, "outputs_deleted_at", None):
        return "This job's outputs were deleted, so it can't be re-rendered."
    from backend.services.storage_retention import PURGE_IN_PROGRESS_MESSAGE, purge_in_progress
    if purge_in_progress(job):
        return PURGE_IN_PROGRESS_MESSAGE
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


def plan_republish(job) -> Dict[str, Tuple[bool, Optional[str]]]:
    """Which destinations the re-render's distribution step will publish to.

    Mirrors the video worker: YouTube needs ``enable_youtube_upload`` on a
    non-private job (effective distribution) and configured YouTube credentials;
    Dropbox needs an effective ``dropbox_path`` and ``brand_prefix``; Google
    Drive needs an effective ``gdrive_folder_id``. Returns
    ``{destination: (will_republish, reason_if_not)}``. Does blocking I/O
    (YouTube credential lookup) — call off the event loop.
    """
    from backend.services.job_defaults_service import get_effective_distribution_for_job

    dist = get_effective_distribution_for_job(job)
    plan: Dict[str, Tuple[bool, Optional[str]]] = {}

    if getattr(job, "is_private", False) or not dist.enable_youtube_upload:
        plan["youtube"] = (False, "YouTube upload is disabled for this job")
    else:
        try:
            from backend.services.youtube_service import get_youtube_service
            configured = get_youtube_service().is_configured
            reason = "YouTube credentials are not configured"
        except Exception as e:
            # Can't plan → don't delete or cancel anything YouTube; warn instead.
            logger.warning(f"[job:{job.job_id}] YouTube credential check failed: {e}")
            configured = False
            reason = f"couldn't verify YouTube credentials ({e})"
        plan["youtube"] = (True, None) if configured else (False, reason)

    if dist.dropbox_path and dist.brand_prefix:
        plan["dropbox"] = (True, None)
    else:
        plan["dropbox"] = (False, "Dropbox upload is not configured for this job")

    if dist.gdrive_folder_id:
        plan["gdrive"] = (True, None)
    else:
        plan["gdrive"] = (False, "Google Drive upload is not configured for this job")
    return plan


def _has_output(state_data: dict, destination: str) -> bool:
    return bool(state_data.get(_DESTINATION_LINK[destination]))


# --- Service ---------------------------------------------------------------------

class AdminRerenderService:
    def __init__(
        self,
        job_manager: Optional[JobManager] = None,
        storage: Optional[StorageService] = None,
    ):
        self.job_manager = job_manager or JobManager()
        self.storage = storage or StorageService()

    async def start(self, job, requested_by: str, notify_customer: bool = False) -> Dict[str, Any]:
        """Claim the job, delete its re-publishable outputs and kick off the re-render.

        Blocking Firestore/GCS/YouTube/Dropbox/GDrive work runs in worker
        threads. Any failure after the claim marks the job FAILED with the
        marker kept (so it can be retried) and raises RerenderError.

        Returns ``{"brand_code", "previous_outputs", "cleanup_results", "warnings"}``.
        Raises RerenderError (400/500/503) or RerenderConflictError (409).
        """
        reason = validate_admin_rerender(job)
        if reason:
            raise RerenderError(reason)

        plan = await asyncio.to_thread(plan_republish, job)
        update, context = self._build_claim(job, requested_by, notify_customer, plan)
        queue_outcome = await asyncio.to_thread(
            claim_with_youtube_queue, self.job_manager.firestore.db, job.job_id, update,
            _claimable_statuses(job), plan["youtube"][0], plan["youtube"][1],
        )
        context["youtube_queue"] = queue_outcome
        if queue_outcome.get("warning"):
            context["warnings"].append(queue_outcome["warning"])

        job_id = job.job_id
        try:
            cleanup_results = await asyncio.to_thread(self._after_claim, job, context, plan)
            from backend.services.worker_service import get_worker_service
            triggered = await get_worker_service().trigger_screens_worker(job_id)
        except Exception as e:
            logger.exception(f"[job:{job_id}] Admin re-render failed after claim")
            await asyncio.to_thread(self._fail_run, job_id, f"Admin re-render failed to start: {e}", "start_error")
            raise RerenderError(
                f"The re-render failed to start ({e}). The job was marked failed; retry it to continue.",
                status_code=500,
            ) from e

        if not triggered:
            await asyncio.to_thread(
                self._fail_run, job_id,
                "Admin re-render couldn't start the screens worker. Retry the job to continue.",
                "screens_trigger_failed",
            )
            raise RerenderError("Couldn't start the re-render. Please retry the job.", status_code=503)

        await asyncio.to_thread(log_to_job, job_id, _LOG_SOURCE, "INFO",
                                "Re-render started (screens worker triggered)",
                                {"cleanup_results": cleanup_results})
        return {
            "brand_code": context["brand_code"],
            "previous_outputs": context["previous_outputs"],
            "cleanup_results": cleanup_results,
            "warnings": context["warnings"],
        }

    def _build_claim(self, job, requested_by: str, notify_customer: bool, plan) -> Tuple[dict, dict]:
        state_data = job.state_data or {}
        # Only the ACTIVE (same-run) marker carries over: a stale one from an
        # earlier run must not leak its brand code / history into this run.
        previous_marker = active_admin_rerender(job)
        # On a retry the re-published outputs were already removed (and cleared
        # from state_data) by the first attempt — keep that attempt's record.
        previous_outputs = {
            **(previous_marker.get("previous_outputs") or {}),
            **snapshot_published_outputs(state_data),
        }
        brand_code = state_data.get("brand_code") or previous_marker.get("brand_code")
        is_retry = bool(previous_marker)

        warnings: List[str] = []
        kept_outputs: Dict[str, Any] = {}
        for destination, (republish, why) in plan.items():
            if not republish and _has_output(state_data, destination):
                link_key = _DESTINATION_LINK[destination]
                kept_outputs[link_key] = state_data[link_key]
                warnings.append(f"{destination} output left in place (not re-published): {why}")
        republish_youtube = plan["youtube"][0]

        now = datetime.now(timezone.utc)
        message = (
            f"Admin re-render requested by {requested_by} "
            f"(existing style, no review; brand code {brand_code or 'n/a'} kept; "
            f"customer notification {'on' if notify_customer else 'off'})"
        )
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
            "state_data.distribution_warnings": DELETE_FIELD,
            f"state_data.{ADMIN_RERENDER_MARKER}": {
                "requested_by": requested_by,
                "requested_at": now.isoformat(),
                "notify_customer": bool(notify_customer),
                "brand_code": brand_code,
                "previous_outputs": previous_outputs,
                "kept_outputs": kept_outputs,
                "warnings": warnings,
                "republish_youtube": republish_youtube,
                # Run identity: a later trip through review mints a new token,
                # which retires this marker (see active_admin_rerender).
                "review_token": getattr(job, "review_token", None),
            },
            # Old screens are deleted after the claim; drop their file_urls so
            # nothing (e.g. a later retry) reuses them.
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
                    "kept_outputs": kept_outputs,
                    "warnings": warnings,
                    "retry": is_retry,
                },
            }]),
        }
        # Drop the links of outputs about to be deleted and re-published (dead
        # links otherwise). brand_code stays — it's reused.
        for destination, (republish, _) in plan.items():
            if republish:
                for key in _DESTINATION_KEYS[destination]:
                    update[f"state_data.{key}"] = DELETE_FIELD
        # youtube_upload_queued is only cleared (and the deferred upload
        # cancelled) when YouTube will be re-published — it's in
        # _DESTINATION_KEYS["youtube"] above.

        context = {
            "message": message,
            "brand_code": brand_code,
            "previous_outputs": previous_outputs,
            "warnings": warnings,
            "notify_customer": bool(notify_customer),
            "is_retry": is_retry,
            "republish_youtube": republish_youtube,
        }
        return update, context

    def _after_claim(self, job, context: dict, plan) -> Dict[str, Any]:
        """Blocking post-claim work: audit log, stale artifacts, published outputs."""
        job_id = job.job_id
        logger.info(
            f"[job:{job_id}] Admin re-render claimed: brand_code={context['brand_code']} "
            f"notify_customer={context['notify_customer']} retry={context['is_retry']} "
            f"previous_outputs={sorted(context['previous_outputs'])} warnings={context['warnings']}"
        )
        log_to_job(job_id, _LOG_SOURCE, "INFO", context["message"], {
            "previous_outputs": context["previous_outputs"],
            "notify_customer": context["notify_customer"],
            "retry": context["is_retry"],
        })
        for warning in context["warnings"]:
            log_to_job(job_id, _LOG_SOURCE, "WARNING", warning)

        delete_regenerated_artifacts(self.storage, job_id)
        return self._delete_published_outputs(
            job, job.state_data or {}, context["brand_code"], plan, context.get("youtube_queue")
        )

    def _fail_run(self, job_id: str, error: str, reason: str) -> None:
        """Mark the run FAILED, keeping the marker so it can be retried."""
        logger.error(f"[job:{job_id}] {error}")
        try:
            self.job_manager.update_job(job_id, {
                "status": JobStatus.FAILED.value,
                "error_message": error,
                "error_details": {"stage": "admin_rerender", "reason": reason},
                "state_data.regen_restore_status": DELETE_FIELD,
            })
            log_to_job(job_id, _LOG_SOURCE, "ERROR", error)
        except Exception:
            logger.exception(f"[job:{job_id}] Failed to mark admin re-render as failed")

    def _delete_published_outputs(
        self, job, state_data: dict, brand_code: Optional[str], plan,
        youtube_queue: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Delete outputs the re-render will re-publish; keep the rest. Best-effort.

        Failures don't abort the re-render: the re-publish targets the same
        names (same brand code), and the server-side YouTube upload and GDrive
        upload both replace a same-named leftover, while Dropbox uploads overwrite.
        """
        job_id = job.job_id
        from backend.services.job_defaults_service import get_effective_distribution_for_job

        def kept(destination):
            return {"status": "kept", "reason": plan[destination][1]}

        results: Dict[str, Any] = {}
        results["youtube"] = (
            delete_youtube_video(job_id, state_data.get("youtube_url"))
            if plan["youtube"][0] else kept("youtube")
        )
        # Private jobs publish to the private Dropbox path, not job.dropbox_path.
        results["dropbox"] = (
            delete_dropbox_folder(
                job_id, get_effective_distribution_for_job(job).dropbox_path,
                brand_code, job.artist, job.title,
            )
            if plan["dropbox"][0] else kept("dropbox")
        )
        # Same brand code is re-published, so the kjbox GCS mirror copy is
        # overwritten in place rather than removed.
        results["gdrive"] = (
            delete_gdrive_files(job_id, state_data.get("gdrive_files"), brand_code, cleanup_mirror=False)
            if plan["gdrive"][0] else kept("gdrive")
        )
        # Handled atomically by the claim transaction (claim_with_youtube_queue).
        results["youtube_queue"] = youtube_queue or {"status": "skipped", "reason": "no queue entry"}
        results["brand_code"] = {"status": "kept", "code": brand_code}

        for service in ("youtube", "dropbox", "gdrive", "youtube_queue"):
            status = results[service].get("status")
            level = "WARNING" if status in ("failed", "partial", "error", "kept", "processing") else "INFO"
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
