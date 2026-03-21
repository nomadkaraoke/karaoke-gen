"""
Divebar Lookup API Cloud Function

Provides search and cross-reference endpoints for the KJ Controller:

  POST /  {"action": "search", "query": "bohemian rhapsody", "limit": 50}
    → Search Divebar catalog by artist/title

  POST /  {"action": "lookup", "kn_ids": [123, 456]}
    → Bulk lookup which KN songs have Divebar versions

  POST /  {"action": "xref_rebuild"}
    → Rebuild the cross-reference index (KN ↔ Divebar)

  POST /  {"action": "download_url", "file_id": "abc123"}
    → Generate a signed Google Drive download URL

Environment variables:
  GCP_PROJECT_ID: GCP project ID
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import functions_framework
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "nomadkaraoke")
DATASET = "karaoke_decide"


def _json_response(data: dict, status: int = 200):
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    return json.dumps(data), status, headers


def _search_divebar(query: str, limit: int = 50) -> list[dict]:
    """Search the Divebar catalog in BigQuery by artist/title."""
    client = bigquery.Client(project=GCP_PROJECT_ID)

    # Normalize query for matching
    normalized = query.lower().strip()

    # Use LIKE for simple substring matching (BigQuery doesn't have FTS5)
    # For better search, consider using CONTAINS or SEARCH functions
    sql = f"""
        SELECT
            file_id,
            brand,
            brand_code,
            artist,
            title,
            filename,
            format,
            file_size,
            drive_path,
            gcs_path
        FROM `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog`
        WHERE
            LOWER(CONCAT(COALESCE(artist, ''), ' ', COALESCE(title, '')))
            LIKE @query_pattern
        ORDER BY
            CASE WHEN artist IS NOT NULL AND title IS NOT NULL THEN 0 ELSE 1 END,
            brand,
            title
        LIMIT @limit
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("query_pattern", "STRING", f"%{normalized}%"),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )

    results = []
    for row in client.query(sql, job_config=job_config).result():
        results.append({
            "file_id": row.file_id,
            "brand": row.brand,
            "brand_code": row.brand_code,
            "artist": row.artist,
            "title": row.title,
            "filename": row.filename,
            "format": row.format,
            "file_size": row.file_size,
            "drive_path": row.drive_path,
            "in_gcs": row.gcs_path is not None,
        })

    return results


def _lookup_kn_ids(kn_ids: list[int]) -> dict[int, list[dict]]:
    """Look up which KN songs have Divebar versions via the cross-reference table."""
    if not kn_ids:
        return {}

    client = bigquery.Client(project=GCP_PROJECT_ID)

    sql = f"""
        SELECT
            x.kn_id,
            x.match_type,
            x.confidence,
            d.file_id,
            d.brand,
            d.format,
            d.file_size,
            d.drive_path,
            d.artist,
            d.title
        FROM `{GCP_PROJECT_ID}.{DATASET}.kn_divebar_xref` x
        JOIN `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog` d
            ON x.divebar_file_id = d.file_id
        WHERE x.kn_id IN UNNEST(@kn_ids)
            AND x.confidence >= 0.80
        ORDER BY x.kn_id, x.confidence DESC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("kn_ids", "INT64", kn_ids),
        ]
    )

    result = {}
    for row in client.query(sql, job_config=job_config).result():
        kn_id = row.kn_id
        if kn_id not in result:
            result[kn_id] = []
        result[kn_id].append({
            "file_id": row.file_id,
            "brand": row.brand,
            "format": row.format,
            "file_size": row.file_size,
            "drive_path": row.drive_path,
            "artist": row.artist,
            "title": row.title,
            "match_type": row.match_type,
            "confidence": row.confidence,
        })

    return result


def _rebuild_xref() -> dict:
    """Rebuild the KN ↔ Divebar cross-reference index using exact + fuzzy matching."""
    client = bigquery.Client(project=GCP_PROJECT_ID)
    start = time.time()

    # Step 1: Exact match on normalized artist + title
    exact_sql = f"""
        CREATE OR REPLACE TABLE `{GCP_PROJECT_ID}.{DATASET}.kn_divebar_xref` AS

        -- Exact normalized match (high confidence)
        SELECT DISTINCT
            kn.Id AS kn_id,
            db.file_id AS divebar_file_id,
            'exact' AS match_type,
            0.95 AS confidence,
            CURRENT_TIMESTAMP() AS matched_at
        FROM `{GCP_PROJECT_ID}.{DATASET}.karaokenerds_raw` kn
        JOIN `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog` db
            ON LOWER(TRIM(kn.Artist)) = db.artist_normalized
            AND LOWER(TRIM(kn.Title)) = db.title_normalized
        WHERE db.artist_normalized IS NOT NULL
            AND db.artist_normalized != ''
            AND db.title_normalized IS NOT NULL
            AND db.title_normalized != ''

        UNION ALL

        -- Community brand match: KN community track brand matches Divebar folder brand code
        SELECT DISTINCT
            kn.Id AS kn_id,
            db.file_id AS divebar_file_id,
            'brand_match' AS match_type,
            0.90 AS confidence,
            CURRENT_TIMESTAMP() AS matched_at
        FROM `{GCP_PROJECT_ID}.{DATASET}.karaokenerds_community` c
        JOIN `{GCP_PROJECT_ID}.{DATASET}.karaokenerds_raw` kn
            ON LOWER(TRIM(c.Artist)) = LOWER(TRIM(kn.Artist))
            AND LOWER(TRIM(c.Title)) = LOWER(TRIM(kn.Title))
        JOIN `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog` db
            ON LOWER(c.Brand) = LOWER(COALESCE(db.brand_code, ''))
            AND LOWER(TRIM(c.Artist)) = db.artist_normalized
        WHERE c.Brand IS NOT NULL
            AND c.Brand != ''
            AND db.artist_normalized IS NOT NULL
            AND db.artist_normalized != ''
            -- Exclude exact matches (already captured above)
            AND NOT EXISTS (
                SELECT 1 FROM `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog` db2
                WHERE LOWER(TRIM(kn.Artist)) = db2.artist_normalized
                AND LOWER(TRIM(kn.Title)) = db2.title_normalized
                AND db2.file_id = db.file_id
            )
    """

    logger.info("Rebuilding cross-reference index...")
    query_job = client.query(exact_sql)
    query_job.result()

    # Get stats
    stats_sql = f"""
        SELECT
            COUNT(*) as total_matches,
            COUNT(DISTINCT kn_id) as unique_kn_songs,
            COUNT(DISTINCT divebar_file_id) as unique_divebar_files,
            COUNTIF(match_type = 'exact') as exact_matches,
            COUNTIF(match_type = 'brand_match') as brand_matches
        FROM `{GCP_PROJECT_ID}.{DATASET}.kn_divebar_xref`
    """
    stats_rows = list(client.query(stats_sql).result())
    stats = stats_rows[0] if stats_rows else None

    duration = time.time() - start

    result = {
        "total_matches": stats.total_matches if stats else 0,
        "unique_kn_songs": stats.unique_kn_songs if stats else 0,
        "unique_divebar_files": stats.unique_divebar_files if stats else 0,
        "exact_matches": stats.exact_matches if stats else 0,
        "brand_matches": stats.brand_matches if stats else 0,
        "duration_s": round(duration, 1),
    }

    logger.info("Cross-reference rebuilt: %s", json.dumps(result))
    return result


GCS_BUCKET = os.environ.get("GCS_BUCKET", "nomadkaraoke-divebar-files")
_SIGNED_URL_EXPIRY_MINUTES = 60


def _get_download_url(file_id: str) -> dict:
    """Get a download URL for a Divebar file.

    Prefers a signed GCS URL (fast, reliable) if the file has been synced.
    Falls back to a direct Google Drive URL for files not yet in GCS.

    Returns dict with 'url' and 'source' ('gcs' or 'drive').
    """
    # Check if file has been synced to GCS
    client = bigquery.Client(project=GCP_PROJECT_ID)
    query = f"""
        SELECT gcs_path
        FROM `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog`
        WHERE file_id = @file_id
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("file_id", "STRING", file_id),
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())

    if rows and rows[0].gcs_path:
        # File is in GCS — return public GCS URL
        # The bucket has public read access (allUsers objectViewer)
        # since these are community karaoke files from a public Google Drive
        gcs_path = rows[0].gcs_path
        path = gcs_path.replace(f"gs://{GCS_BUCKET}/", "")
        # URL-encode the path for GCS public URL
        from urllib.parse import quote
        encoded_path = quote(path, safe="/")
        public_url = f"https://storage.googleapis.com/{GCS_BUCKET}/{encoded_path}"
        return {"url": public_url, "source": "gcs"}

    # Fallback: direct Google Drive download URL
    return {
        "url": f"https://drive.google.com/uc?export=download&id={file_id}",
        "source": "drive",
    }


@functions_framework.http
def divebar_lookup(request):
    """HTTP Cloud Function entry point."""
    # Handle CORS preflight
    if request.method == "OPTIONS":
        return "", 204, {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

    body = request.get_json(silent=True) or {}
    action = body.get("action", "search")

    try:
        if action == "search":
            query = body.get("query", "").strip()
            if not query:
                return _json_response({"status": "error", "message": "query required"}, 400)
            limit = min(body.get("limit", 50), 200)
            results = _search_divebar(query, limit)
            return _json_response({"status": "ok", "results": results, "count": len(results)})

        elif action == "lookup":
            kn_ids = body.get("kn_ids", [])
            if not kn_ids or not isinstance(kn_ids, list):
                return _json_response({"status": "error", "message": "kn_ids list required"}, 400)
            # Limit batch size
            kn_ids = [int(i) for i in kn_ids[:500]]
            matches = _lookup_kn_ids(kn_ids)
            # Convert int keys to strings for JSON
            return _json_response({
                "status": "ok",
                "matches": {str(k): v for k, v in matches.items()},
            })

        elif action == "xref_rebuild":
            stats = _rebuild_xref()
            return _json_response({"status": "ok", **stats})

        elif action == "download_url":
            file_id = body.get("file_id", "").strip()
            if not file_id:
                return _json_response({"status": "error", "message": "file_id required"}, 400)
            result = _get_download_url(file_id)
            return _json_response({
                "status": "ok",
                "download_url": result["url"],
                "source": result["source"],
            })

        else:
            return _json_response({"status": "error", "message": f"Unknown action: {action}"}, 400)

    except Exception as e:
        logger.exception("Divebar lookup error (action=%s)", action)
        return _json_response({"status": "error", "message": str(e)}, 500)


# For local testing
if __name__ == "__main__":
    class MockRequest:
        method = "POST"
        def get_json(self, silent=False):
            return {"action": "search", "query": "bohemian rhapsody"}

    print("Testing Divebar lookup...")
    result = divebar_lookup(MockRequest())
    print(f"\nResult: {result[0]}")
