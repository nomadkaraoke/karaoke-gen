"""
KaraokeNerds Data Sync Cloud Function

Fetches daily exports from the KaraokeNerds API and loads them into BigQuery
and GCS. Replaces the legacy pipeline in projectbread-karaokay.

Two modes (controlled by request body):
  mode=full      — Fetch full song catalog, store to GCS + refresh BigQuery
  mode=community — Fetch community tracks (with YouTube URLs), store to GCS + refresh BigQuery

Environment variables:
  GCP_PROJECT_ID: GCP project ID
  KARAOKENERDS_API_KEY: API key for karaokenerds.com
  GCS_BUCKET: Bucket for raw JSON exports
"""

import gzip
import json
import logging
import os
import time
from datetime import datetime, timezone

import functions_framework
import requests
from google.cloud import bigquery, storage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "nomadkaraoke")
KARAOKENERDS_API_KEY = os.environ.get("KARAOKENERDS_API_KEY", "")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "nomadkaraoke-kn-data")
DATASET_ID = "karaoke_decide"

# API endpoints
SONGS_URL = "https://karaokenerds.com/Data/Songs"
COMMUNITY_URL = "https://karaokenerds.com/Data/Community"

# BigQuery schemas
SONGS_SCHEMA = [
    bigquery.SchemaField("Id", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("Artist", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("Title", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("Brands", "STRING", mode="REQUIRED"),
]

COMMUNITY_SCHEMA = [
    bigquery.SchemaField("Artist", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("Title", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("Brand", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("Watch", "STRING"),
    bigquery.SchemaField("Created", "STRING"),
    bigquery.SchemaField("Id", "INTEGER"),
]


def _json_response(data: dict, status: int = 200):
    return json.dumps(data), status, {"Content-Type": "application/json"}


def _fetch_kn_data(url: str) -> tuple[bytes, int]:
    """Fetch data from KaraokeNerds API. Returns (raw_bytes, record_count)."""
    logger.info("Fetching %s", url)
    resp = requests.get(
        url,
        params={"key": KARAOKENERDS_API_KEY},
        timeout=120,
        stream=True,
    )
    resp.raise_for_status()

    raw = resp.content
    data = json.loads(raw)

    # Community endpoint wraps in {"Items": [...]}
    if isinstance(data, dict) and "Items" in data:
        count = len(data["Items"])
    elif isinstance(data, list):
        count = len(data)
    else:
        count = 0

    logger.info("Fetched %d records (%.1f MB)", count, len(raw) / 1024 / 1024)
    return raw, count


def _store_to_gcs(raw_bytes: bytes, prefix: str) -> str:
    """Store raw JSON to GCS as gzipped file. Returns GCS path."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d-%H.%M.%S")

    gcs_client = storage.Client(project=GCP_PROJECT_ID)
    bucket = gcs_client.bucket(GCS_BUCKET)

    # Gzip the data
    compressed = gzip.compress(raw_bytes)
    logger.info("Compressed %d bytes → %d bytes", len(raw_bytes), len(compressed))

    # Upload date-stamped file
    dated_path = f"{prefix}/{prefix}-data-{date_str}.json.gz"
    blob = bucket.blob(dated_path)
    blob.upload_from_string(compressed, content_type="application/gzip")
    logger.info("Uploaded to gs://%s/%s", GCS_BUCKET, dated_path)

    # Upload latest pointer
    latest_path = f"{prefix}/{prefix}-data-latest.json.gz"
    latest_blob = bucket.blob(latest_path)
    latest_blob.upload_from_string(compressed, content_type="application/gzip")
    logger.info("Updated latest pointer: gs://%s/%s", GCS_BUCKET, latest_path)

    return dated_path


def _load_songs_to_bigquery(raw_bytes: bytes) -> int:
    """Parse Songs JSON and load to BigQuery karaokenerds_raw table."""
    data = json.loads(raw_bytes)
    if not isinstance(data, list):
        raise ValueError(f"Expected list, got {type(data).__name__}")

    client = bigquery.Client(project=GCP_PROJECT_ID)
    table_ref = f"{GCP_PROJECT_ID}.{DATASET_ID}.karaokenerds_raw"

    job_config = bigquery.LoadJobConfig(
        schema=SONGS_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )

    load_job = client.load_table_from_json(data, table_ref, job_config=job_config)
    load_job.result()

    logger.info("Loaded %d rows to %s", load_job.output_rows, table_ref)
    return load_job.output_rows


def _load_community_to_bigquery(raw_bytes: bytes) -> int:
    """Parse Community JSON and load to BigQuery karaokenerds_community table."""
    data = json.loads(raw_bytes)
    if isinstance(data, dict) and "Items" in data:
        items = data["Items"]
    else:
        raise ValueError(f"Expected dict with 'Items', got {type(data).__name__}")

    # Normalize Created field from .NET date format to ISO string
    for item in items:
        created = item.get("Created")
        if created and isinstance(created, str) and created.startswith("/Date("):
            try:
                ms = int(created.replace("/Date(", "").replace(")/", ""))
                item["Created"] = datetime.fromtimestamp(
                    ms / 1000, tz=timezone.utc
                ).isoformat()
            except (ValueError, OverflowError):
                item["Created"] = None

    client = bigquery.Client(project=GCP_PROJECT_ID)
    table_ref = f"{GCP_PROJECT_ID}.{DATASET_ID}.karaokenerds_community"

    job_config = bigquery.LoadJobConfig(
        schema=COMMUNITY_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )

    load_job = client.load_table_from_json(items, table_ref, job_config=job_config)
    load_job.result()

    logger.info("Loaded %d rows to %s", load_job.output_rows, table_ref)
    return load_job.output_rows


@functions_framework.http
def sync_kn_data(request):
    """
    HTTP Cloud Function entry point.

    Request body JSON: {"mode": "full"} or {"mode": "community"}
    """
    logger.info("KN data sync function invoked")

    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "full")

    if mode not in ("full", "community"):
        return _json_response({"status": "error", "message": f"Invalid mode: {mode}"}, 400)

    if not KARAOKENERDS_API_KEY:
        return _json_response({"status": "error", "message": "KARAOKENERDS_API_KEY not set"}, 500)

    try:
        start = time.time()

        if mode == "full":
            url = SONGS_URL
            prefix = "full"
        else:
            url = COMMUNITY_URL
            prefix = "community"

        # Step 1: Fetch from KN API
        raw_bytes, record_count = _fetch_kn_data(url)

        # Step 2: Store raw JSON to GCS
        gcs_path = _store_to_gcs(raw_bytes, prefix)

        # Step 3: Load to BigQuery
        if mode == "full":
            rows_loaded = _load_songs_to_bigquery(raw_bytes)
        else:
            rows_loaded = _load_community_to_bigquery(raw_bytes)

        duration = time.time() - start

        result = {
            "status": "ok",
            "mode": mode,
            "records_fetched": record_count,
            "rows_loaded": rows_loaded,
            "gcs_path": f"gs://{GCS_BUCKET}/{gcs_path}",
            "duration_s": round(duration, 1),
        }

        logger.info("Sync complete: %s", json.dumps(result))
        return _json_response(result)

    except Exception as e:
        logger.exception("KN data sync failed (mode=%s)", mode)
        return _json_response({"status": "error", "mode": mode, "message": str(e)}, 500)


# For local testing
if __name__ == "__main__":
    class MockRequest:
        def get_json(self, silent=False):
            return {"mode": "full"}

    print("Testing KN data sync (full)...")
    result = sync_kn_data(MockRequest())
    print(f"\nResult: {result[0]}")
