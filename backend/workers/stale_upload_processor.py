"""
Stale upload processor.

The signed-URL upload flow creates the job (and charges the credit) BEFORE the
browser PUTs the audio straight to GCS; the job stays PENDING with
``state_data.awaiting_upload`` until the client calls ``uploads-complete``.
If the tab is closed or the upload fails, nothing ever advances the job — it
sits at "Waiting for upload" forever with the credit spent.

This sweep cancels such jobs once their signed upload URLs have long expired
(the URLs are valid for 60 min), which refunds the credit via ``cancel_job``.

Called from the hourly stale-review scheduler endpoint.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from backend.models.job import JobStatus
from backend.services.firestore_service import FirestoreService
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService


logger = logging.getLogger(__name__)

# Signed upload URLs expire after 60 minutes, but GCS only checks expiry when the
# PUT starts — a big file on a slow uplink can still be streaming hours later, and
# a single-shot PUT isn't visible until it finishes. 6h leaves plenty of margin.
STALE_UPLOAD_HOURS = 6
# Some but not all files landed (e.g. mix uploaded, instrumental didn't) and
# uploads-complete was never called: give a human a day, then cancel + refund.
STALE_PARTIAL_UPLOAD_HOURS = 24

CANCEL_REASON = "Audio upload never finished. Please submit the song again."


def process_stale_uploads() -> Dict[str, Any]:
    """
    Cancel (and refund) PENDING jobs whose browser upload never completed.

    Skips tenant bulk jobs (``state_data.batch_id``): they use resumable sessions
    that can be resumed for days via the re-pick recovery flow.
    Jobs with some objects under ``uploads/{job_id}/`` get a longer grace period
    (``STALE_PARTIAL_UPLOAD_HOURS``) before being cancelled.
    """
    firestore = FirestoreService()
    job_manager = JobManager()
    storage = StorageService()

    cancelled = 0
    skipped_has_files = 0
    errors = []

    try:
        pending_jobs = firestore.list_jobs(status=JobStatus.PENDING, limit=500)
    except Exception as e:
        logger.error(f"Failed to query pending jobs: {e}")
        return {"status": "error", "cancelled": 0, "errors": [str(e)]}

    now = datetime.now(timezone.utc)

    for job in pending_jobs:
        try:
            state_data = job.state_data or {}
            if not state_data.get('awaiting_upload'):
                continue
            if state_data.get('batch_id'):
                continue

            created_at = job.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            hours_elapsed = (now - created_at).total_seconds() / 3600
            if hours_elapsed < STALE_UPLOAD_HOURS:
                continue

            if hours_elapsed < STALE_PARTIAL_UPLOAD_HOURS and storage.list_files(f"uploads/{job.job_id}/"):
                skipped_has_files += 1
                logger.warning(
                    f"Job {job.job_id}: awaiting_upload for {hours_elapsed:.1f}h with some files "
                    f"under uploads/ — uploads-complete never called; cancelling at "
                    f"{STALE_PARTIAL_UPLOAD_HOURS}h"
                )
                continue

            logger.info(
                f"Job {job.job_id}: upload never completed after {hours_elapsed:.1f}h, "
                f"cancelling with refund"
            )
            if job_manager.cancel_job(job.job_id, reason=CANCEL_REASON):
                cancelled += 1
            else:
                logger.warning(f"Job {job.job_id}: cancel_job returned False")
        except Exception as e:
            error_msg = f"Job {getattr(job, 'job_id', '?')}: stale upload processing failed: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    return {
        "status": "completed",
        "cancelled": cancelled,
        "skipped_has_files": skipped_has_files,
        "errors": errors,
    }
