"""
KaraokeNerds Data Sync Cloud Function

Fetches daily exports from the KaraokeNerds API and loads them into BigQuery
and GCS. Replaces the legacy pipeline in projectbread-karaokay.

Two modes (controlled by request body):
  mode=full      — Fetch full song catalog, store to GCS + refresh BigQuery, then
                   export the kjbox song-identification index (see below)
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

# kjbox song-identification index: every song a singer might mean (popular Spotify
# tracks + every KaraokeNerds song), exported daily after the KN refresh and
# downloaded by the NomadPC's nomad-catalog-sync. Design: kjbox
# docs/SONG-IDENTIFICATION.md. Each run writes a dated folder + a manifest
# (song-id/latest.json) naming its shards, so the device never mixes runs.
SONG_ID_PREFIX = "song-id"
SONG_ID_KEEP_RUNS = 3
SONG_ID_SQL = r"""
WITH sp AS (
  SELECT normalized_artist na, normalized_title nt,
         ARRAY_AGG(STRUCT(artist_name AS a, track_name AS t) ORDER BY popularity DESC LIMIT 1)[OFFSET(0)] best,
         MAX(popularity) p
  FROM `{project}.{dataset}.spotify_tracks_normalized`
  GROUP BY na, nt),
kn AS (
  SELECT TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(NORMALIZE(Artist, NFD)), r'\p{{M}}', ''), r'[^a-z0-9]+', ' ')) na,
         TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(NORMALIZE(Title, NFD)), r'\p{{M}}', ''), r'[^a-z0-9]+', ' ')) nt,
         ANY_VALUE(Artist) a, ANY_VALUE(Title) t
  FROM `{project}.{dataset}.karaokenerds_raw`
  WHERE Artist IS NOT NULL AND Title IS NOT NULL
  GROUP BY na, nt)
SELECT COALESCE(sp.best.a, kn.a) artist, COALESCE(sp.best.t, kn.t) title,
       sp.p popularity, IF(kn.na IS NOT NULL, 1, 0) karaoke
FROM sp FULL OUTER JOIN kn USING (na, nt)
"""

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


def _export_song_id_index() -> dict:
    """EXPORT DATA the song-identification index to GCS + write the manifest.

    Server-side export (no rows pass through this function). Columns:
    artist, title, popularity (blank = karaoke-only row), karaoke (0/1); TSV,
    gzipped, no header, sharded by BigQuery.
    """
    run = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = f"{SONG_ID_PREFIX}/{run}"
    client = bigquery.Client(project=GCP_PROJECT_ID)
    select = SONG_ID_SQL.format(project=GCP_PROJECT_ID, dataset=DATASET_ID)
    export = (
        f"EXPORT DATA OPTIONS(uri='gs://{GCS_BUCKET}/{folder}/songs-*.tsv.gz', format='CSV', "
        "field_delimiter='\t', header=false, compression='GZIP', overwrite=true) AS " + select
    )
    client.query(export).result()

    gcs = storage.Client(project=GCP_PROJECT_ID)
    bucket = gcs.bucket(GCS_BUCKET)
    shards = sorted(b.name for b in gcs.list_blobs(GCS_BUCKET, prefix=f"{folder}/"))
    if not shards:
        raise RuntimeError(f"song-id export wrote no files under {folder}/")
    manifest = {"run": run, "shards": [f"gs://{GCS_BUCKET}/{name}" for name in shards],
                "columns": ["artist", "title", "popularity", "karaoke"]}
    bucket.blob(f"{SONG_ID_PREFIX}/latest.json").upload_from_string(
        json.dumps(manifest), content_type="application/json")

    # Prune old runs (keep a few so a device mid-download never loses its shards).
    runs = sorted({b.name.split("/")[1] for b in gcs.list_blobs(GCS_BUCKET, prefix=f"{SONG_ID_PREFIX}/")
                   if b.name.count("/") >= 2})
    for old in runs[:-SONG_ID_KEEP_RUNS]:
        for b in gcs.list_blobs(GCS_BUCKET, prefix=f"{SONG_ID_PREFIX}/{old}/"):
            b.delete()
    logger.info("song-id index exported: %s (%d shards)", folder, len(shards))
    return {"run": run, "shards": len(shards)}


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

        # Step 4 (full mode): refresh the kjbox song-identification index from the
        # just-loaded KN table. Its failure must not fail the KN sync itself.
        song_id = None
        if mode == "full":
            try:
                song_id = _export_song_id_index()
            except Exception as e:  # noqa: BLE001
                logger.exception("song-id index export failed")
                song_id = {"error": str(e)}

        duration = time.time() - start

        result = {
            "status": "ok",
            "mode": mode,
            "records_fetched": record_count,
            "rows_loaded": rows_loaded,
            "gcs_path": f"gs://{GCS_BUCKET}/{gcs_path}",
            "song_id_index": song_id,
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
