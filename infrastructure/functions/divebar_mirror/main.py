"""
Divebar Mirror Cloud Function

Lists all karaoke files in the diveBar Karaoke Google Drive shared folder,
parses filenames into structured metadata, and loads the index into BigQuery.

Triggered daily via Cloud Scheduler. Also provides a search/lookup API
for the KJ Controller.

Environment variables:
  DIVEBAR_FOLDER_ID: Google Drive folder ID for the diveBar root folder
  GCP_PROJECT_ID: GCP project ID (for BigQuery)
"""

import json
import logging
import os
import time

import functions_framework

from drive_client import get_drive_service, list_divebar_recursive
from filename_parser import should_index_file
from index_builder import build_rows, load_to_bigquery

# Configure structured logging for Cloud Functions
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Configuration
DIVEBAR_FOLDER_ID = os.environ.get(
    "DIVEBAR_FOLDER_ID", "1zxnSZcE03gzy0YVGOdnTrEIi8It_3Wu8"
)
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "nomadkaraoke")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")

# Jobs that depend on a fresh index. Triggered here, at the index build's ACTUAL
# completion, rather than concurrently by the caller — this fixes the race where the
# file-sync VM ran before the index had the newly-published rows (so a manual
# "Refresh catalog" click reliably syncs just-published tracks). Order within the
# list is not significant: xref reads the catalog (now fresh), the sync VM reads the
# catalog for pending files (now fresh); neither depends on the other.
DOWNSTREAM_SCHEDULER_JOBS = [
    "divebar-sync-vm-daily",       # Drive -> GCS file byte-sync
    "divebar-xref-rebuild-daily",  # KN <-> Divebar cross-reference rebuild
]


def _wants_downstream_chain(request) -> bool:
    """True only when the caller explicitly asked to chain the index-dependent jobs.

    The manual "Refresh catalog" path triggers a dedicated scheduler job whose body
    sets ``chain_downstream``. The nightly mirror cron does NOT set it, so the nightly
    pipeline keeps its own staggered sync/xref schedules and we never double-run them.
    """
    try:
        body = request.get_json(silent=True) or {}
        if isinstance(body, dict) and body.get("chain_downstream"):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return str(request.args.get("chain_downstream", "")).lower() in ("1", "true", "yes")
    except Exception:  # noqa: BLE001
        return False


def _trigger_downstream_jobs():
    """Force-run the index-dependent scheduler jobs (file-sync VM + xref rebuild).
    Best-effort: a failure here is logged and reported but never fails the index
    build itself."""
    from google.cloud import scheduler_v1

    client = scheduler_v1.CloudSchedulerClient()
    triggered, failed = [], []
    for job in DOWNSTREAM_SCHEDULER_JOBS:
        job_path = f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/jobs/{job}"
        try:
            client.run_job(name=job_path)
            triggered.append(job)
        except Exception as e:  # noqa: BLE001 - never fail the index on this
            logger.warning("Failed to trigger downstream job %s: %s", job, e)
            failed.append({"job": job, "error": str(e)})
    return {"triggered": triggered, "failed": failed}


def _json_response(data: dict, status: int = 200):
    """Return a JSON response tuple."""
    return json.dumps(data), status, {"Content-Type": "application/json"}


@functions_framework.http
def sync_divebar_index(request):
    """
    HTTP Cloud Function entry point.

    POST /  — Full sync: list Drive, parse filenames, load to BigQuery
    GET /?action=stats  — Return index stats without syncing

    Returns JSON with sync results.
    """
    logger.info("Divebar mirror function invoked")

    # Handle stats request
    if request.method == "GET":
        action = request.args.get("action", "")
        if action == "stats":
            return _get_stats()

    try:
        start = time.time()

        # Step 1: List all files from Google Drive
        logger.info("Step 1: Listing files from Drive folder %s", DIVEBAR_FOLDER_ID)
        service = get_drive_service()
        drive_files = list_divebar_recursive(service, DIVEBAR_FOLDER_ID)
        list_duration = time.time() - start
        logger.info("Listed %d total items in %.1fs", len(drive_files), list_duration)

        # Step 2: Filter to indexable files and build rows
        logger.info("Step 2: Parsing filenames and building index rows")
        indexable = [f for f in drive_files if should_index_file(f["name"])]
        rows = build_rows(drive_files)

        # Step 3: Load to BigQuery
        logger.info("Step 3: Loading %d rows to BigQuery", len(rows))
        rows_loaded = load_to_bigquery(GCP_PROJECT_ID, rows)
        total_duration = time.time() - start

        # Compute stats
        brands = set()
        formats = {}
        for f in indexable:
            brands.add(f.get("brand", "Unknown"))
            fmt = f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else "unknown"
            formats[fmt] = formats.get(fmt, 0) + 1

        result = {
            "status": "ok",
            "total_files_listed": len(drive_files),
            "indexable_files": len(indexable),
            "rows_loaded": rows_loaded,
            "brands": len(brands),
            "formats": formats,
            "list_duration_s": round(list_duration, 1),
            "total_duration_s": round(total_duration, 1),
        }

        # Chain the index-dependent jobs (file-sync VM + xref) ONLY when the caller
        # asked for it — the manual "Refresh catalog" path. Firing here, at the index's
        # actual completion (which outlasts the caller's HTTP timeout), is what makes a
        # refresh reliably sync just-published files instead of racing the sync VM ahead
        # of the index. The nightly mirror cron doesn't set the flag, so it leaves the
        # standalone nightly sync/xref schedules to run once — no double-run.
        if _wants_downstream_chain(request):
            result["downstream"] = _trigger_downstream_jobs()

        logger.info("Sync complete: %s", json.dumps(result))
        return _json_response(result)

    except Exception as e:
        logger.exception("Divebar mirror sync failed")
        return _json_response({"status": "error", "message": str(e)}, 500)


def _get_stats():
    """Return current index stats from BigQuery."""
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=GCP_PROJECT_ID)
        query = """
            SELECT
                COUNT(*) as total_files,
                COUNT(DISTINCT brand) as brands,
                COUNTIF(artist IS NOT NULL) as files_with_artist,
                COUNTIF(title IS NOT NULL) as files_with_title,
                MAX(synced_at) as last_synced
            FROM `{project}.karaoke_decide.divebar_catalog`
        """.format(project=GCP_PROJECT_ID)
        result = list(client.query(query).result())
        if result:
            row = result[0]
            return _json_response({
                "status": "ok",
                "total_files": row.total_files,
                "brands": row.brands,
                "files_with_artist": row.files_with_artist,
                "files_with_title": row.files_with_title,
                "last_synced": row.last_synced.isoformat() if row.last_synced else None,
            })
        return _json_response({"status": "ok", "total_files": 0})
    except Exception as e:
        logger.exception("Failed to get stats")
        return _json_response({"status": "error", "message": str(e)}, 500)


# For local testing
if __name__ == "__main__":
    import sys

    class MockRequest:
        method = "POST"
        args = {}

    print("Testing Divebar mirror sync...")
    result = sync_divebar_index(MockRequest())
    print(f"\nResult: {result[0]}")
