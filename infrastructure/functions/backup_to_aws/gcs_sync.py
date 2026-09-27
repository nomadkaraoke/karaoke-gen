"""Sync new/changed GCS objects to the staging bucket."""

import logging
from datetime import datetime, timezone
from google.cloud import storage

logger = logging.getLogger(__name__)

DEFAULT_SYNC_PREFIXES = ["jobs/", "tenants/", "themes/"]


def sync_gcs_to_staging(
    source_bucket: str,
    staging_bucket: str,
    staging_prefix: str,
    max_objects: int = 5000,
    sync_prefixes: list[str] | None = None,
) -> str:
    """Copy new/changed objects from source to staging bucket.

    Uses each object's ``time_created`` (the creation time of its current
    generation — an overwrite creates a new generation) to detect new/changed
    content since the last sync. The staging bucket stores a marker file with
    the last sync timestamp.

    Deliberately NOT ``updated``: that is bumped by any *metadata* change,
    including Autoclass storage-class transitions (enabled on the job-files
    bucket 2026-09-26). Keying on it would re-copy every object each time it
    cooled to Nearline — and the copy's read would pull it straight back to
    Standard, defeating Autoclass.

    Args:
        source_bucket: Source GCS bucket name.
        staging_bucket: Staging GCS bucket name.
        staging_prefix: Prefix in staging bucket for synced files.
        max_objects: Max objects to sync per run (safety limit).

    Returns:
        Summary string.
    """
    client = storage.Client()
    src = client.bucket(source_bucket)
    dst = client.bucket(staging_bucket)

    marker_blob = dst.blob(f"{staging_prefix}.last_sync")
    last_sync_dt = None
    if marker_blob.exists():
        last_sync_str = marker_blob.download_as_text().strip()
        last_sync_dt = datetime.fromisoformat(last_sync_str)
        logger.info(f"Last sync: {last_sync_str}")

    copied = 0
    latest_updated_dt = last_sync_dt
    prefixes = sync_prefixes if sync_prefixes is not None else DEFAULT_SYNC_PREFIXES

    for prefix in prefixes:
        blobs = src.list_blobs(prefix=prefix)
        for blob in blobs:
            created = blob.time_created
            if not created:
                continue

            if last_sync_dt and created <= last_sync_dt:
                continue

            dst_path = f"{staging_prefix}{blob.name}"
            src.copy_blob(blob, dst, dst_path)
            copied += 1

            if latest_updated_dt is None or created > latest_updated_dt:
                latest_updated_dt = created

            if copied >= max_objects:
                logger.warning(f"Hit max_objects limit ({max_objects})")
                break

        if copied >= max_objects:
            break

    if latest_updated_dt:
        marker_blob.upload_from_string(latest_updated_dt.isoformat())

    logger.info(f"Synced {copied} objects from {source_bucket}")
    return f"Synced {copied} objects"
