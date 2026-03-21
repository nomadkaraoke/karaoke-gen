"""
Build and upload the Divebar catalog index to BigQuery.

Takes the flat file listing from drive_client and the parsed metadata
from filename_parser, and loads it into a BigQuery table.
"""

import logging
from datetime import datetime, timezone

from google.cloud import bigquery

from filename_parser import parse_filename, should_index_file, normalize_for_search

logger = logging.getLogger(__name__)

# BigQuery table reference
DATASET_ID = "karaoke_decide"
TABLE_ID = "divebar_catalog"

SCHEMA = [
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

    Uses WRITE_TRUNCATE to atomically replace the entire table contents.
    Returns the number of rows loaded.
    """
    if not rows:
        logger.warning("No rows to load")
        return 0

    client = bigquery.Client(project=project_id)
    table_ref = f"{project_id}.{DATASET_ID}.{TABLE_ID}"

    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )

    # BigQuery load from in-memory rows
    load_job = client.load_table_from_json(
        rows,
        table_ref,
        job_config=job_config,
    )

    # Wait for completion
    load_job.result()

    logger.info("Loaded %d rows to %s", load_job.output_rows, table_ref)
    return load_job.output_rows
