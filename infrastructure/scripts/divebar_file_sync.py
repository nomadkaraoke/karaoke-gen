#!/usr/bin/env python3
"""
Divebar File Sync — Download files from Google Drive to GCS.

Reads the BigQuery divebar_catalog index and downloads all files that don't
yet exist in GCS. Progress is tracked by updating a `gcs_path` column in
BigQuery after each successful upload.

Usage:
    # Sync all files (initial bulk or incremental)
    python divebar_file_sync.py

    # Sync with concurrency (faster but uses more Drive API quota)
    python divebar_file_sync.py --workers 4

    # Dry run (show what would be downloaded)
    python divebar_file_sync.py --dry-run

    # Sync only specific brand
    python divebar_file_sync.py --brand "WTF Karaoke Videos"

    # Limit number of files (useful for testing)
    python divebar_file_sync.py --limit 100

Requirements:
    pip install google-api-python-client google-cloud-storage google-cloud-bigquery

Environment:
    GOOGLE_CLOUD_PROJECT (default: nomadkaraoke)
    GCS_BUCKET (default: nomadkaraoke-divebar-files)
"""

import argparse
import io
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.auth import default
from google.cloud import bigquery, storage
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "nomadkaraoke-divebar-files")
DATASET = "karaoke_decide"
TABLE = "divebar_catalog"

# Files larger than this are skipped (would OOM the sync VM)
MAX_FILE_SIZE = 3 * 1024 * 1024 * 1024  # 3 GB


_thread_local = threading.local()


def get_drive_service():
    """Get a thread-local Google Drive API service.

    googleapiclient is NOT thread-safe — sharing a single service object
    across threads causes SSL errors, bad file descriptors, and heap
    corruption. Each thread gets its own instance.
    """
    svc = getattr(_thread_local, "drive_service", None)
    if svc is None:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
        svc = build("drive", "v3", credentials=credentials)
        _thread_local.drive_service = svc
    return svc


def get_unsynced_files(bq_client, brand=None, limit=None):
    """Get files from BigQuery that haven't been synced to GCS yet."""
    where = "WHERE gcs_path IS NULL"
    if brand:
        where += f" AND brand = @brand"

    query = f"""
        SELECT file_id, brand, filename, drive_path, file_size, drive_md5
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        {where}
        ORDER BY file_size ASC
    """
    if limit:
        query += f" LIMIT {limit}"

    params = []
    if brand:
        params.append(bigquery.ScalarQueryParameter("brand", "STRING", brand))

    job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
    rows = list(bq_client.query(query, job_config=job_config).result())
    return rows


def get_sync_stats(bq_client):
    """Get current sync progress."""
    query = f"""
        SELECT
            COUNT(*) as total,
            COUNTIF(gcs_path LIKE 'gs://%') as synced,
            COUNTIF(gcs_path IS NULL) as pending,
            COUNTIF(gcs_path IS NOT NULL AND gcs_path NOT LIKE 'gs://%') as unavailable,
            ROUND(SUM(CASE WHEN gcs_path LIKE 'gs://%' THEN file_size ELSE 0 END) / 1024/1024/1024, 1) as synced_gb,
            ROUND(SUM(CASE WHEN gcs_path IS NULL THEN file_size ELSE 0 END) / 1024/1024/1024, 1) as pending_gb
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
    """
    row = list(bq_client.query(query).result())[0]
    return {
        "total": row.total,
        "synced": row.synced,
        "pending": row.pending,
        # Sentinel gcs_path values (missing:*/skipped:*) — non-null but not in GCS.
        "unavailable": row.unavailable,
        "synced_gb": row.synced_gb,
        "pending_gb": row.pending_gb,
    }


# Transient download failures (network blips, Drive 5xx) are retried within the
# run so a single sync reliably drains the worklist to zero instead of leaving a
# small tail that lingers across nights. A 404/410 is permanent and never retried.
DOWNLOAD_MAX_ATTEMPTS = 3
DOWNLOAD_BACKOFF_BASE = 2  # seconds between attempts: 2, then 4


def _http_status(err):
    """Best-effort HTTP status from a googleapiclient HttpError (or None)."""
    status = getattr(err, "status_code", None)
    if status is None:
        status = getattr(getattr(err, "resp", None), "status", None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def download_and_upload(drive_service, gcs_bucket, file_id, gcs_path,
                        expected_size=None, max_attempts=DOWNLOAD_MAX_ATTEMPTS):
    """Download a file from Drive and upload to GCS.

    Returns ``(ok, actual_size, gone)``. ``gone`` is True when Drive reports the
    file no longer exists (HTTP 404/410) — a permanent failure the caller records
    so it stops counting as "pending" and isn't retried every run; it is returned
    immediately without retrying. Any other failure (5xx, network, timeout) is
    transient: retried up to ``max_attempts`` with exponential backoff, and if
    still failing returns ``gone=False`` (left NULL to retry on the next run).
    """
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            # Download from Drive
            request = drive_service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

            buffer.seek(0)
            actual_size = buffer.getbuffer().nbytes

            # Upload to GCS
            blob = gcs_bucket.blob(gcs_path)
            blob.upload_from_file(buffer, timeout=300)

            return True, actual_size, False

        except HttpError as e:
            if _http_status(e) in (404, 410):
                # Permanent — file no longer exists on Drive. Do not retry.
                logger.error("Gone %s: %s", gcs_path, e)
                return False, 0, True
            last_err = e
        except Exception as e:
            last_err = e

        # Transient failure — back off and retry (except after the last attempt).
        if attempt < max_attempts:
            time.sleep(DOWNLOAD_BACKOFF_BASE ** attempt)

    logger.error("Failed %s after %d attempts: %s", gcs_path, max_attempts, last_err)
    return False, 0, False


def update_gcs_path(bq_client, file_id, gcs_path):
    """Update the gcs_path in BigQuery to mark a file as synced."""
    query = f"""
        UPDATE `{PROJECT_ID}.{DATASET}.{TABLE}`
        SET gcs_path = @gcs_path
        WHERE file_id = @file_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("gcs_path", "STRING", gcs_path),
            bigquery.ScalarQueryParameter("file_id", "STRING", file_id),
        ]
    )
    bq_client.query(query, job_config=job_config).result()


def sync_file(gcs_bucket, bq_client, row):
    """Sync a single file from Drive to GCS.

    Uses a thread-local Drive service (googleapiclient is not thread-safe).
    Checks if the file already exists in GCS before downloading from Drive.
    If it exists, just updates gcs_path in BigQuery (no re-download needed).
    """
    file_id = row.file_id
    drive_path = row.drive_path
    gcs_path = f"files/{drive_path}"
    full_gcs_path = f"gs://{GCS_BUCKET}/{gcs_path}"
    file_size = row.file_size or 0
    size_mb = file_size / 1024 / 1024

    # Skip files too large to buffer in memory (would OOM the VM). Marked
    # unsyncable so they don't count as pending or get retried every run.
    if file_size > MAX_FILE_SIZE:
        update_gcs_path(bq_client, file_id, "skipped:too_large")
        logger.warning("  ⏭ %s skipped (%.0f MB > 3 GB limit)", drive_path, size_mb)
        return True, 0, True

    # Check if file already exists in GCS (avoids re-downloading)
    blob = gcs_bucket.blob(gcs_path)
    if blob.exists():
        update_gcs_path(bq_client, file_id, full_gcs_path)
        logger.info("  ⏭ %s already in GCS, updated gcs_path", drive_path)
        return True, 0, False

    logger.info("Downloading %s (%.1f MB)...", drive_path, size_mb)
    start = time.time()

    drive_service = get_drive_service()
    ok, actual_size, gone = download_and_upload(
        drive_service, gcs_bucket, file_id, gcs_path, row.file_size
    )

    if ok:
        duration = time.time() - start
        speed = actual_size / 1024 / 1024 / duration if duration > 0 else 0
        update_gcs_path(bq_client, file_id, full_gcs_path)
        logger.info(
            "  ✓ %s (%.1f MB in %.1fs, %.1f MB/s)",
            drive_path, actual_size / 1024 / 1024, duration, speed,
        )
        return True, actual_size, False
    elif gone:
        # File no longer exists in Drive (deleted/moved since it was indexed).
        # Mark unsyncable so it stops counting as pending and isn't retried
        # every night; a genuinely new upload gets a fresh file_id + row.
        update_gcs_path(bq_client, file_id, "missing:drive_404")
        logger.warning("  ✗ %s unavailable on Drive (404/410) — marked unsyncable", drive_path)
        return False, 0, True
    else:
        # Transient failure (network, 5xx, timeout) — leave gcs_path NULL to retry.
        return False, 0, False


def main():
    parser = argparse.ArgumentParser(description="Sync Divebar files from Google Drive to GCS")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent download workers (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded")
    parser.add_argument("--brand", help="Only sync files from this brand")
    parser.add_argument("--limit", type=int, help="Maximum files to sync")
    args = parser.parse_args()

    bq_client = bigquery.Client(project=PROJECT_ID)
    gcs_client = storage.Client(project=PROJECT_ID)

    # Show current progress
    stats = get_sync_stats(bq_client)
    logger.info(
        "Sync status: %d/%d files synced (%.1f/%.1f GB), %d pending (%.1f GB), %d unavailable",
        stats["synced"], stats["total"], stats["synced_gb"],
        stats["synced_gb"] + stats["pending_gb"],
        stats["pending"], stats["pending_gb"], stats["unavailable"],
    )

    # Get unsynced files
    files = get_unsynced_files(bq_client, brand=args.brand, limit=args.limit)
    if not files:
        logger.info("All files are synced!")
        return

    total_size = sum(r.file_size or 0 for r in files)
    logger.info(
        "Will sync %d files (%.1f GB)%s",
        len(files), total_size / 1024 / 1024 / 1024,
        " [DRY RUN]" if args.dry_run else "",
    )

    if args.dry_run:
        for row in files[:20]:
            logger.info("  Would download: %s (%.1f MB)", row.drive_path, (row.file_size or 0) / 1024 / 1024)
        if len(files) > 20:
            logger.info("  ... and %d more", len(files) - 20)
        return

    gcs_bucket = gcs_client.bucket(GCS_BUCKET)

    # Warm up the main thread's Drive service (also validates credentials early)
    get_drive_service()

    downloaded = 0
    recovered = 0
    failed = 0
    unavailable = 0
    bytes_synced = 0
    overall_start = time.time()

    def _tally(ok, size, is_unavailable=False):
        nonlocal downloaded, recovered, failed, unavailable, bytes_synced
        if is_unavailable:
            # Permanently unsyncable (Drive 404/410 or too-large) — recorded in
            # BigQuery via a sentinel gcs_path, so not a retry-worthy failure.
            unavailable += 1
        elif ok:
            if size > 0:
                downloaded += 1
                bytes_synced += size
            else:
                recovered += 1
        else:
            failed += 1

    def _log_progress():
        elapsed = time.time() - overall_start
        logger.info(
            "Progress: %d downloaded, %d recovered, %d unavailable, %d failed (of %d), %.1f GB, %.0f s",
            downloaded, recovered, unavailable, failed, len(files),
            bytes_synced / 1024 / 1024 / 1024, elapsed,
        )

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(sync_file, gcs_bucket, bq_client, row): row
                for row in files
            }
            for future in as_completed(futures):
                _tally(*future.result())
                if (downloaded + recovered + failed + unavailable) % 50 == 0:
                    _log_progress()
    else:
        for i, row in enumerate(files):
            _tally(*sync_file(gcs_bucket, bq_client, row))
            if (i + 1) % 50 == 0:
                _log_progress()

    elapsed = time.time() - overall_start
    logger.info(
        "Done: %d downloaded, %d recovered from GCS, %d unavailable (marked), %d failed, %.1f GB in %.0f s",
        downloaded, recovered, unavailable, failed, bytes_synced / 1024 / 1024 / 1024, elapsed,
    )


if __name__ == "__main__":
    main()
