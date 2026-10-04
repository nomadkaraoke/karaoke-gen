"""
Restore a job's separated stems after storage retention purged them.

The storage-retention job deletes the separated stems of old jobs (they're
regenerable: re-run separation on the kept input audio) and stamps
``stems_purged_at``. Every flow that rebuilds a completed job goes through the
screens worker — regenerate, admin re-render, theme re-render, private->public
visibility change, Edit — so the screens worker gates on this module: if the
job's stems were purged it starts the audio-separation Cloud Run Job in RESTORE
mode instead of generating screens, and the audio worker re-triggers the screens
worker once the stems are back. Review (Edit flow) and the encoder then find the
stems exactly where they always were.

``state_data.stems_restore = {status: running|failed, attempts, started_at}``
tracks the restore; ``attempts`` bounds automatic re-runs.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from google.cloud.firestore_v1 import DELETE_FIELD

logger = logging.getLogger(__name__)

STEMS_RESTORE_KEY = "stems_restore"
MAX_AUTO_ATTEMPTS = 3
STALE_RUNNING_SECONDS = 60 * 60

NOT_NEEDED = "not_needed"
STARTED = "started"
IN_PROGRESS = "in_progress"
FAILED = "failed"


def restore_marker(job) -> dict:
    marker = (getattr(job, "state_data", None) or {}).get(STEMS_RESTORE_KEY)
    return marker if isinstance(marker, dict) else {}


def _age_seconds(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        started = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds()


def is_restore_run(job) -> bool:
    """True when the audio worker was started to restore purged stems.

    Keyed on the marker existing (any status), not just ``running``: when a
    restore attempt fails, Cloud Run retries the task after the worker already
    marked it failed — that retry must still run in restore mode.
    """
    from backend.services.regenerate_service import stems_need_restore
    return stems_need_restore(job) and bool(restore_marker(job))


async def maybe_start_stems_restore(job, job_manager, job_log=None) -> str:
    """Screens-worker gate. Returns NOT_NEEDED, STARTED, IN_PROGRESS or FAILED."""
    from backend.services.regenerate_service import stems_need_restore

    if not stems_need_restore(job):
        return NOT_NEEDED
    job_id = job.job_id
    marker = restore_marker(job)
    if marker.get("status") == "running":
        age = _age_seconds(marker.get("started_at"))
        if age is not None and age < STALE_RUNNING_SECONDS:
            logger.info(f"[job:{job_id}] Stems restore already running; skipping duplicate screens dispatch")
            return IN_PROGRESS
    attempts = int(marker.get("attempts") or 0)
    if attempts >= MAX_AUTO_ATTEMPTS:
        message = (
            "Couldn't restore this track's audio stems (archived to save space) after "
            f"{attempts} attempts. Retry the job to try again."
        )
        job_manager.mark_job_failed(
            job_id=job_id, error_message=message,
            error_details={"stage": "stems_restore", "attempts": attempts},
        )
        return FAILED

    now = datetime.now(timezone.utc).isoformat()
    job_manager.update_job(job_id, {
        f"state_data.{STEMS_RESTORE_KEY}": {
            "status": "running", "attempts": attempts + 1, "started_at": now,
        },
        "message": "Restoring audio stems",
        "updated_at": datetime.now(timezone.utc),
    })
    if job_log is not None:
        job_log.info("Stems were archived by storage retention: re-running audio separation first")
    from backend.services.worker_service import get_worker_service
    triggered = await get_worker_service().trigger_audio_worker(job_id)
    if not triggered:
        job_manager.update_job(job_id, {f"state_data.{STEMS_RESTORE_KEY}.status": "failed"})
        job_manager.mark_job_failed(
            job_id=job_id,
            error_message="Couldn't start audio separation to restore this track's stems. Retry the job.",
            error_details={"stage": "stems_restore", "reason": "audio_trigger_failed"},
        )
        return FAILED
    logger.info(f"[job:{job_id}] Stems restore started (attempt {attempts + 1})")
    return STARTED


async def complete_stems_restore(job_id: str, job_manager) -> bool:
    """Audio worker (restore mode) finished: clear the markers and resume screens."""
    update = {
        "stems_purged_at": None,
        f"state_data.{STEMS_RESTORE_KEY}": DELETE_FIELD,
    }
    # A Cloud Run task retry succeeded after an earlier attempt marked the job
    # FAILED: put it back where the restore started so screens can continue.
    job = job_manager.get_job(job_id)
    status = getattr(getattr(job, "status", None), "value", getattr(job, "status", None))
    if status == "failed" and ((getattr(job, "error_details", None) or {}).get("stage")
                               in ("audio_separation", "download_audio", "stems_restore")):
        update.update({"status": "lyrics_complete", "error_message": None, "error_details": None})
    job_manager.update_job(job_id, update)
    from backend.services.worker_service import get_worker_service
    triggered = await get_worker_service().trigger_screens_worker(job_id)
    if not triggered:
        logger.error(f"[job:{job_id}] Stems restored but the screens worker couldn't be triggered")
    return triggered


def restore_stalled(job, now: Optional[datetime] = None) -> bool:
    """A ``running`` restore whose audio job died without reporting back."""
    marker = restore_marker(job)
    if marker.get("status") != "running":
        return False
    age = _age_seconds(marker.get("started_at"))
    return age is not None and age > STALE_RUNNING_SECONDS


def mark_restore_failed(job_id: str, job_manager) -> None:
    try:
        job_manager.update_job(job_id, {f"state_data.{STEMS_RESTORE_KEY}.status": "failed"})
    except Exception:  # noqa: BLE001
        logger.exception(f"[job:{job_id}] Couldn't mark stems restore failed")
