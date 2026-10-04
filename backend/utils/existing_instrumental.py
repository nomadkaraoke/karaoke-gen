"""
Resolve a job's user-supplied ("existing") instrumental to a path that exists.

``existing_instrumental_gcs_path`` was recorded under ``uploads/{job_id}/...``,
which the bucket lifecycle deletes after 7 days — so any re-render, regenerate
or edit of an older bring-your-own-instrumental job lost its instrumental. The
video pipeline stages a copy at the job root (``jobs/{id}/custom_instrumental.<ext>``,
see video_worker_orchestrator._run_encoding), which is kept forever.

New uploads are persisted to that root path at upload time (file_upload); for
older jobs this resolver falls back to the staged copy and (best-effort)
repoints the job at it, so the fix migrates jobs lazily as they're used.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

STAGED_STEMS = ("custom_instrumental", "existing_instrumental")


def persistent_instrumental_path(job_id: str, source_path: str) -> str:
    """The job-root path a user instrumental is kept at (same name the encoder globs)."""
    ext = os.path.splitext(source_path or "")[1].lower() or ".mp3"
    return f"jobs/{job_id}/custom_instrumental{ext}"


def _staged_copy(job_id: str, storage) -> Optional[str]:
    try:
        names = storage.list_files(f"jobs/{job_id}/")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[job:{job_id}] Couldn't list job files for staged instrumental: {e}")
        return None
    prefix = f"jobs/{job_id}/"
    for stem in STAGED_STEMS:
        for name in sorted(names):
            rel = name[len(prefix):]
            if "/" not in rel and os.path.splitext(rel)[0] == stem:
                return name
    return None


def resolve_existing_instrumental(job, storage, job_manager=None, log=None) -> Optional[str]:
    """Return a GCS path for the job's existing instrumental that actually exists.

    - the recorded path, if it exists;
    - else the staged job-root copy (and repoint the job at it, best-effort);
    - else the recorded path unchanged (the caller fails loudly as before).
    """
    recorded = getattr(job, "existing_instrumental_gcs_path", None)
    if not recorded:
        return recorded
    job_id = job.job_id
    try:
        if storage.file_exists(recorded):
            return recorded
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[job:{job_id}] Couldn't check existing instrumental {recorded}: {e}")
        return recorded

    staged = _staged_copy(job_id, storage)
    if not staged:
        logger.error(f"[job:{job_id}] Existing instrumental {recorded} is gone and no staged copy exists")
        return recorded
    message = f"Existing instrumental {recorded} expired; using staged copy {staged}"
    logger.warning(f"[job:{job_id}] {message}")
    if log is not None:
        log.warning(message)
    if job_manager is not None:
        try:
            job_manager.update_job(job_id, {"existing_instrumental_gcs_path": staged})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[job:{job_id}] Couldn't repoint existing_instrumental_gcs_path: {e}")
    return staged
