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

  POST /  {"action": "refresh", "token": "..."}
    → On-demand "refresh now": force-run the divebar scheduler jobs
      (Drive→BigQuery index, Drive→GCS file sync, xref rebuild) so a track
      just published to the Nomad Drive shows up without waiting for the
      nightly 2/3/6 AM runs. Token-gated (shared bearer) since the endpoint
      is otherwise public.

Environment variables:
  GCP_PROJECT_ID: GCP project ID
  GCP_REGION: region the divebar scheduler jobs live in (for `refresh`)
  DIVEBAR_REFRESH_SECRET_ID: Secret Manager secret holding the `refresh` bearer
    token (read at runtime, so the function deploys before the secret has a
    value — it just returns 403 until one is added)
"""

import hmac
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
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")
DATASET = "karaoke_decide"
REFRESH_SECRET_ID = os.environ.get("DIVEBAR_REFRESH_SECRET_ID", "divebar-refresh-token")

# The `refresh` action force-runs ONLY the index job. The index function chains the
# index-dependent jobs (file-sync VM + xref rebuild) itself, on completion — see
# divebar_mirror._trigger_downstream_jobs. Firing all three here concurrently used to
# race: the sync VM finished before the index had the newly-published rows, so a
# just-published track was indexed but not byte-synced to GCS until the next nightly
# run. Chaining from the index's actual completion removes that race.
REFRESH_SCHEDULER_JOBS = [
    # Refresh-only mirror trigger: its request body sets chain_downstream, so the
    # index chains the sync-VM + xref itself on completion. (divebar-mirror-daily,
    # the nightly cron, omits the flag and leaves the standalone nightly sync/xref
    # schedules alone — so refreshing never double-runs the nightly pipeline.)
    "divebar-mirror-refresh",
]


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


def _norm_sql(col: str) -> str:
    """A BigQuery expression replicating divebar_mirror's normalize_for_search.

    Both the KaraokeNerds side and the Divebar side of the xref join are passed
    through this so the comparison is symmetric. Previously the join compared the
    KN side (only LOWER+TRIM) against the Divebar side's fully-normalized columns
    (diacritics / leading "the " / punctuation stripped), so most rows could never
    match. Steps mirror normalize_for_search: strip diacritics (NFD + drop combining
    marks), lower/trim, strip a trailing karaoke tag, strip a leading "the ", drop
    non-word chars (keeping unicode letters/digits/underscore), collapse whitespace.
    """
    return (
        "TRIM(REGEXP_REPLACE("
        "REGEXP_REPLACE("
        "REGEXP_REPLACE("
        "REGEXP_REPLACE("
        "LOWER(TRIM(REGEXP_REPLACE(NORMALIZE(COALESCE(" + col + ", ''), NFD), r'\\p{Mn}', ''))),"
        r" r'\s*[\(\[]\s*(?:[^)\]]*karaoke[^)\]]*|(?:instrumental|backing track|no vocals?|kj version|with vocals?|demo)[^)\]]*)[\)\]]\s*$', ''),"
        r" r'^the ', ''),"
        r" r'[^\p{L}\p{N}_\s]', ''),"
        r" r'\s+', ' '))"
    )


def _rebuild_xref() -> dict:
    """Rebuild the KN ↔ Divebar cross-reference index by exact normalized match.

    Both sides are normalized through the identical `_norm_sql` expression, so a
    KN song links to a Divebar file only when their artist AND title agree after
    normalization. (The previous "brand_match" branch matched on brand_code +
    artist only — with no title constraint it was a Cartesian product that linked
    every KN song by an artist/brand to every Divebar file by the same
    artist/brand, i.e. wrong-song links. Constraining it by title would make it a
    strict subset of the exact match, so it was removed.)
    """
    client = bigquery.Client(project=GCP_PROJECT_ID)
    start = time.time()

    kn_artist, kn_title = _norm_sql("kn.Artist"), _norm_sql("kn.Title")
    db_artist, db_title = _norm_sql("db.artist"), _norm_sql("db.title")

    exact_sql = f"""
        CREATE OR REPLACE TABLE `{GCP_PROJECT_ID}.{DATASET}.kn_divebar_xref` AS

        -- Exact match on symmetrically-normalized artist + title (high confidence)
        SELECT DISTINCT
            kn.Id AS kn_id,
            db.file_id AS divebar_file_id,
            'exact' AS match_type,
            0.95 AS confidence,
            CURRENT_TIMESTAMP() AS matched_at
        FROM `{GCP_PROJECT_ID}.{DATASET}.karaokenerds_raw` kn
        JOIN `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog` db
            ON {kn_artist} = {db_artist}
            AND {kn_title} = {db_title}
        WHERE {db_artist} != ''
            AND {db_title} != ''
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


def _get_full_stats() -> dict:
    """Get comprehensive Divebar mirror statistics."""
    client = bigquery.Client(project=GCP_PROJECT_ID)

    sql = f"""
        WITH catalog_stats AS (
            SELECT
                COUNT(*) as total_files,
                COUNT(DISTINCT brand) as total_brands,
                COUNTIF(gcs_path LIKE 'gs://%') as gcs_synced,
                COUNTIF(gcs_path IS NULL) as gcs_pending,
                COUNTIF(gcs_path IS NOT NULL AND gcs_path NOT LIKE 'gs://%') as gcs_unavailable,
                COUNTIF(artist IS NOT NULL AND title IS NOT NULL) as with_metadata,
                ROUND(SUM(file_size) / 1024/1024/1024, 1) as total_gb,
                ROUND(SUM(CASE WHEN gcs_path LIKE 'gs://%' THEN file_size ELSE 0 END) / 1024/1024/1024, 1) as gcs_synced_gb,
                ROUND(SUM(CASE WHEN gcs_path IS NULL THEN file_size ELSE 0 END) / 1024/1024/1024, 1) as gcs_pending_gb,
                ROUND(SUM(CASE WHEN gcs_path IS NOT NULL AND gcs_path NOT LIKE 'gs://%' THEN file_size ELSE 0 END) / 1024/1024/1024, 1) as gcs_unavailable_gb,
                MAX(synced_at) as last_index_sync
            FROM `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog`
        ),
        format_stats AS (
            SELECT format, COUNT(*) as count,
                ROUND(SUM(file_size) / 1024/1024/1024, 1) as gb,
                COUNTIF(gcs_path LIKE 'gs://%') as in_gcs
            FROM `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog`
            GROUP BY format
            ORDER BY count DESC
        ),
        xref_stats AS (
            SELECT
                COUNT(*) as total_matches,
                COUNT(DISTINCT kn_id) as unique_kn_songs,
                COUNT(DISTINCT divebar_file_id) as unique_divebar_files,
                MAX(matched_at) as last_xref_rebuild
            FROM `{GCP_PROJECT_ID}.{DATASET}.kn_divebar_xref`
        ),
        kn_stats AS (
            SELECT
                (SELECT COUNT(*) FROM `{GCP_PROJECT_ID}.{DATASET}.karaokenerds_raw`) as kn_songs,
                (SELECT COUNT(*) FROM `{GCP_PROJECT_ID}.{DATASET}.karaokenerds_community`) as kn_community
        )
        SELECT
            c.*, x.total_matches, x.unique_kn_songs, x.unique_divebar_files,
            x.last_xref_rebuild, k.kn_songs, k.kn_community
        FROM catalog_stats c, xref_stats x, kn_stats k
    """

    row = list(client.query(sql).result())[0]

    # Get format breakdown
    fmt_sql = f"""
        SELECT format, COUNT(*) as count,
            ROUND(SUM(file_size) / 1024/1024/1024, 1) as gb,
            COUNTIF(gcs_path LIKE 'gs://%') as in_gcs
        FROM `{GCP_PROJECT_ID}.{DATASET}.divebar_catalog`
        GROUP BY format ORDER BY count DESC
    """
    formats = {}
    for fmt_row in client.query(fmt_sql).result():
        formats[fmt_row.format] = {
            "count": fmt_row.count,
            "gb": fmt_row.gb,
            "in_gcs": fmt_row.in_gcs,
        }

    # Files that can actually be mirrored (exclude permanently-unavailable ones).
    syncable = row.total_files - row.gcs_unavailable

    return {
        "catalog": {
            "total_files": row.total_files,
            "total_brands": row.total_brands,
            "with_metadata": row.with_metadata,
            "total_gb": row.total_gb,
            "last_index_sync": row.last_index_sync.isoformat() if row.last_index_sync else None,
        },
        "gcs_mirror": {
            "synced": row.gcs_synced,
            "pending": row.gcs_pending,
            # Files that can never be mirrored: no longer on Drive (404/410) or
            # too large to buffer. Marked with a sentinel gcs_path by the sync VM.
            "unavailable": row.gcs_unavailable,
            "syncable_total": syncable,
            "synced_gb": row.gcs_synced_gb,
            "pending_gb": row.gcs_pending_gb,
            "unavailable_gb": row.gcs_unavailable_gb,
            # Percent of *syncable* files mirrored. Reaches 100% once every file
            # that can be synced is in GCS — unavailable files are excluded from
            # the denominator so a healthy, fully-caught-up mirror reads 100%
            # (green) instead of being pinned below by permanently-dead links.
            "percent": round(row.gcs_synced / syncable * 100, 1) if syncable else 0,
        },
        "formats": formats,
        "cross_reference": {
            "total_matches": row.total_matches,
            "unique_kn_songs": row.unique_kn_songs,
            "unique_divebar_files": row.unique_divebar_files,
            "last_rebuild": row.last_xref_rebuild.isoformat() if row.last_xref_rebuild else None,
        },
        "karaoke_nerds": {
            "songs": row.kn_songs,
            "community_tracks": row.kn_community,
        },
    }


def _get_expected_token() -> str:
    """Read the refresh bearer token from Secret Manager (latest version).

    Read at call time rather than injected as an env var so the function can be
    deployed before the secret has any version — until one is added this returns
    "" and the gate below rejects every request. Returns "" on any access error
    (missing secret/version, no permission) so failures fail closed.
    """
    from google.cloud import secretmanager

    name = f"projects/{GCP_PROJECT_ID}/secrets/{REFRESH_SECRET_ID}/versions/latest"
    try:
        client = secretmanager.SecretManagerServiceClient()
        resp = client.access_secret_version(name=name)
        return resp.payload.data.decode("utf-8").strip()
    except Exception as e:
        logger.warning("Could not read refresh token secret: %s", e)
        return ""


def _refresh(token: str) -> dict:
    """Force-run the divebar pipeline scheduler jobs on demand.

    Validates the shared bearer token (from Secret Manager), then triggers each
    job in REFRESH_SCHEDULER_JOBS via the Cloud Scheduler API. Jobs run
    asynchronously under their own identities; this returns as soon as they're
    queued.

    Returns {"triggered": [...]} on success. Raises PermissionError on a
    bad/missing token.
    """
    expected = _get_expected_token()
    # Constant-time compare; also reject when the token isn't configured so a
    # function deployed before the secret has a value can't be bypassed.
    if not expected or not token or not hmac.compare_digest(str(token), expected):
        raise PermissionError("invalid or missing refresh token")

    from google.cloud import scheduler_v1

    client = scheduler_v1.CloudSchedulerClient()
    triggered, failed = [], []
    for job in REFRESH_SCHEDULER_JOBS:
        job_path = f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/jobs/{job}"
        try:
            client.run_job(name=job_path)
            triggered.append(job)
        except Exception as e:
            # Best-effort: one job failing (e.g. already running) shouldn't
            # block the others. Surface it so callers can see partial success.
            logger.warning("Failed to run scheduler job %s: %s", job, e)
            failed.append({"job": job, "error": str(e)})

    return {
        "triggered": triggered,
        "failed": failed,
        # The mirror/sync jobs run async (index ~minutes, GCS sync up to ~1h);
        # the catalog reflects a newly-published track once the mirror index
        # completes. xref fully reflects new tracks after the next mirror run.
        "note": "Jobs queued. New tracks appear after the mirror index completes (~minutes).",
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

        elif action == "stats":
            stats = _get_full_stats()
            return _json_response({"status": "ok", **stats})

        elif action == "refresh":
            try:
                result = _refresh(body.get("token", ""))
            except PermissionError:
                # Don't leak whether the token exists; uniform 403.
                return _json_response({"status": "error", "message": "forbidden"}, 403)
            return _json_response({"status": "ok", **result})

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
