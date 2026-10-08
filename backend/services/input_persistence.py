"""
Keep user-uploaded inputs forever: copy ``uploads/{job_id}/**`` into ``jobs/{job_id}/input/``.

Uploads land under ``uploads/{job_id}/`` (signed-URL / multipart / downloader
destinations). That prefix has a bucket lifecycle delete, and user inputs (the
mix, a bring-your-own instrumental and its conformed copy, a lyrics file, style
uploads) are irreplaceable: re-renders, regenerations after the storage-retention
purge and late reviews all need them again. Losing them made tenant re-renders
fail (2026-10-08: every randy-vild job left in review for >7 days had lost its
mix and instrumental).

``persist_job_inputs`` server-side copies every object under ``uploads/{job_id}/``
to ``jobs/{job_id}/input/<same relative path>`` (``input/`` is KEEP-forever in
``storage_retention``) and repoints every job field that referenced the
``uploads/`` copy. It is idempotent and cheap when there's nothing new, so each
worker calls it on entry: inputs uploaded later in a job's life (conformed
instrumentals, style uploads, review-time uploads) are caught by the next worker.
"""
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

UPLOADS_PREFIX = "uploads/"


def uploads_prefix(job_id: str) -> str:
    return f"{UPLOADS_PREFIX}{job_id}/"


def persisted_path(job_id: str, path: str) -> Optional[str]:
    """``uploads/{job_id}/a/b.wav`` -> ``jobs/{job_id}/input/a/b.wav`` (None if not this job's upload)."""
    prefix = uploads_prefix(job_id)
    if not isinstance(path, str) or not path.startswith(prefix) or path == prefix:
        return None
    return f"jobs/{job_id}/input/{path[len(prefix):]}"


def _rewrites(value: Any, mapping: Dict[str, str], path: str, out: Dict[str, str]) -> None:
    """Collect dotted Firestore field paths whose string value is a mapped upload path."""
    if isinstance(value, str):
        if value in mapping:
            out[path] = mapping[value]
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and "." not in key and "`" not in key:
                _rewrites(child, mapping, f"{path}.{key}" if path else key, out)


# Job fields that can hold an input path (top-level, plus everything under these maps).
_SCALAR_FIELDS = (
    "input_media_gcs_path", "existing_instrumental_gcs_path", "lyrics_file_gcs_path", "style_params_gcs_path",
)
_MAP_FIELDS = ("file_urls", "state_data", "style_assets")


def persist_job_inputs(job, storage, job_manager) -> Tuple[int, Dict[str, str]]:
    """Copy this job's uploads into ``jobs/{id}/input/`` and repoint the job.

    Returns ``(objects_copied, field_updates)``. Raises on storage/Firestore errors.
    """
    job_id = job.job_id
    copied = 0
    mapping: Dict[str, str] = {}
    for blob in storage.bucket.list_blobs(prefix=uploads_prefix(job_id)):
        dest = persisted_path(job_id, blob.name)
        if not dest:
            continue
        mapping[blob.name] = dest
        existing = storage.bucket.get_blob(dest)
        if existing is not None and existing.md5_hash == blob.md5_hash and existing.size == blob.size:
            continue
        storage.bucket.copy_blob(blob, storage.bucket, dest)
        copied += 1

    updates: Dict[str, str] = {}
    for field in _SCALAR_FIELDS:
        value = getattr(job, field, None)
        if value in mapping:
            updates[field] = mapping[value]
    for field in _MAP_FIELDS:
        _rewrites(getattr(job, field, None) or {}, mapping, field, updates)

    if updates:
        job_manager.update_job(job_id, dict(updates))
        # Keep the in-memory job consistent for the caller.
        for field in _SCALAR_FIELDS:
            if field in updates:
                setattr(job, field, updates[field])
    if copied or updates:
        logger.info(f"Job {job_id}: persisted {copied} upload(s) to jobs/{job_id}/input/; repointed {sorted(updates)}")
    return copied, updates


def ensure_job_inputs_persisted(job, storage, job_manager, worker: str = "") -> None:
    """Worker-entry wrapper: never fails the job, but logs an ERROR (alerts) on failure.

    The ``uploads/`` lifecycle gives months to retry, and every later worker retries.
    """
    try:
        persist_job_inputs(job, storage, job_manager)
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Job {getattr(job, 'job_id', '?')}: failed to persist uploaded inputs to jobs/ "
            f"({worker or 'worker'}): {e}"
        )
