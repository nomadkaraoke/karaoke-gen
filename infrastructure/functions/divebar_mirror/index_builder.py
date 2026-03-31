"""
Build and upload the Divebar catalog index to BigQuery.

Takes the flat file listing from drive_client and the parsed metadata
from filename_parser, and loads it into a BigQuery table.

Uses a staging table + MERGE to preserve gcs_path values set by the
file sync script (divebar_file_sync.py).
"""

import logging
from datetime import datetime, timezone

from google.cloud import bigquery

from filename_parser import parse_filename, should_index_file, normalize_for_search

logger = logging.getLogger(__name__)

# BigQuery table reference
DATASET_ID = "karaoke_decide"
TABLE_ID = "divebar_catalog"
STAGING_TABLE_ID = "divebar_catalog_staging"

# Schema for the staging table (no gcs_path — that lives only in the main table)
STAGING_SCHEMA = [
    bigquery.SchemaField("file_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("brand", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("brand_code", "STRING"),
    bigquery.SchemaField("artist", "STRING"),
    bigquery.SchemaField("title", "STRING"),
    bigquery.SchemaField("disc_id", "STRING"),
    bigquery.SchemaField("filename", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("format", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("subfolder", "STRING"),
    bigquery.SchemaField("file_size", "INT64"),
    bigquery.SchemaField("drive_path", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("drive_md5", "STRING"),
    bigquery.SchemaField("artist_normalized", "STRING"),
    bigquery.SchemaField("title_normalized", "STRING"),
    bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
]


def build_rows(drive_files: list[dict]) -> list[dict]:
    """Convert drive file listings into BigQuery rows."""
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    skipped = 0

    for f in drive_files:
        filename = f["name"]
        if not should_index_file(filename):
            skipped += 1
            continue

        parsed = parse_filename(filename, brand_folder=f.get("brand", ""))

        row = {
            "file_id": f["id"],
            "brand": f.get("brand", "Unknown"),
            "brand_code": parsed.get("brand_code"),
            "artist": parsed.get("artist"),
            "title": parsed.get("title"),
            "disc_id": parsed.get("disc_id"),
            "filename": filename,
            "format": parsed.get("format", "unknown"),
            "subfolder": f.get("subfolder", ""),
            "file_size": int(f["size"]) if f.get("size") else None,
            "drive_path": f.get("path", filename),
            "drive_md5": f.get("md5Checksum"),
            "artist_normalized": normalize_for_search(parsed.get("artist", "")),
            "title_normalized": normalize_for_search(parsed.get("title", "")),
            "synced_at": now,
        }
        rows.append(row)

    logger.info("Built %d index rows (%d files skipped)", len(rows), skipped)
    return rows


def load_to_bigquery(project_id: str, rows: list[dict]) -> int:
    """
    Load rows into the BigQuery divebar_catalog table.

    Uses a staging table + MERGE to update the catalog without losing
    gcs_path values set by the file sync script. Steps:
      1. WRITE_TRUNCATE the staging table with fresh Drive data
      2. MERGE staging into main table:
         - UPDATE existing rows (by file_id) with fresh metadata
         - INSERT new rows
         - DELETE rows no longer in Drive
      gcs_path is preserved on updates and defaults to NULL on inserts.

    Returns the number of rows in the main table after merge.
    """
    if not rows:
        logger.warning("No rows to load")
        return 0

    client = bigquery.Client(project=project_id)
    staging_ref = f"{project_id}.{DATASET_ID}.{STAGING_TABLE_ID}"
    main_ref = f"{project_id}.{DATASET_ID}.{TABLE_ID}"

    # Step 1: Load fresh data into staging table (TRUNCATE is fine here)
    job_config = bigquery.LoadJobConfig(
        schema=STAGING_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )

    load_job = client.load_table_from_json(rows, staging_ref, job_config=job_config)
    load_job.result()
    logger.info("Loaded %d rows to staging table", load_job.output_rows)

    # Step 2: MERGE staging into main table, preserving gcs_path
    merge_sql = f"""
        MERGE `{main_ref}` AS main
        USING `{staging_ref}` AS staging
        ON main.file_id = staging.file_id

        WHEN MATCHED THEN UPDATE SET
            brand = staging.brand,
            brand_code = staging.brand_code,
            artist = staging.artist,
            title = staging.title,
            disc_id = staging.disc_id,
            filename = staging.filename,
            format = staging.format,
            subfolder = staging.subfolder,
            file_size = staging.file_size,
            drive_path = staging.drive_path,
            drive_md5 = staging.drive_md5,
            artist_normalized = staging.artist_normalized,
            title_normalized = staging.title_normalized,
            synced_at = staging.synced_at

        WHEN NOT MATCHED BY TARGET THEN INSERT (
            file_id, brand, brand_code, artist, title, disc_id,
            filename, format, subfolder, file_size, drive_path,
            drive_md5, artist_normalized, title_normalized, synced_at,
            gcs_path
        ) VALUES (
            staging.file_id, staging.brand, staging.brand_code,
            staging.artist, staging.title, staging.disc_id,
            staging.filename, staging.format, staging.subfolder,
            staging.file_size, staging.drive_path, staging.drive_md5,
            staging.artist_normalized, staging.title_normalized,
            staging.synced_at, NULL
        )

        WHEN NOT MATCHED BY SOURCE THEN DELETE
    """

    logger.info("Merging staging into main table (preserving gcs_path)...")
    merge_job = client.query(merge_sql)
    merge_job.result()

    # Get final row count
    count_result = list(client.query(
        f"SELECT COUNT(*) as cnt FROM `{main_ref}`"
    ).result())
    final_count = count_result[0].cnt if count_result else 0

    logger.info("Merge complete. Main table has %d rows", final_count)
    return final_count
