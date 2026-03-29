"""BigQuery table export to GCS staging bucket as Parquet."""

import logging
from google.cloud import bigquery

logger = logging.getLogger(__name__)

DATASET = "karaoke_decide"

WEEKLY_TABLES = [
    "karaokenerds_raw",
    "karaokenerds_community",
    "divebar_catalog",
    "kn_divebar_xref",
]

MONTHLY_TABLES = [
    "mb_artists",
    "mb_recordings",
    "mb_artist_tags",
    "mb_recording_isrc",
    "mbid_spotify_mapping",
    "mb_artists_normalized",
    "karaoke_recording_links",
    "mlhd_artist_similarity",
]


def export_bigquery_tables(
    project: str,
    staging_bucket: str,
    date_str: str,
    day_of_week: int,
    day_of_month: int,
) -> str:
    """Export BigQuery tables to GCS as Parquet based on schedule.

    Args:
        project: GCP project ID.
        staging_bucket: GCS staging bucket name.
        date_str: ISO date string for folder naming.
        day_of_week: 0=Monday, 6=Sunday.
        day_of_month: 1-31.

    Returns:
        Summary string.
    """
    tables_to_export = []

    if day_of_week == 6:  # Sunday
        tables_to_export.extend((t, "daily-refresh") for t in WEEKLY_TABLES)

    if day_of_month == 1:  # 1st of month
        tables_to_export.extend((t, "musicbrainz") for t in MONTHLY_TABLES)

    if not tables_to_export:
        logger.info("No BigQuery exports scheduled for today")
        return "Skipped — no exports scheduled"

    client = bigquery.Client(project=project)
    exported = []

    for table_name, prefix in tables_to_export:
        source = f"{project}.{DATASET}.{table_name}"
        dest_uri = f"gs://{staging_bucket}/bigquery/{prefix}/{date_str}/{table_name}/*.parquet"

        logger.info(f"Exporting {source} to {dest_uri}")

        job_config = bigquery.ExtractJobConfig(
            destination_format=bigquery.DestinationFormat.PARQUET,
        )

        job = client.extract_table(
            source=source,
            destination_uris=[dest_uri],
            job_config=job_config,
        )
        job.result(timeout=600)

        exported.append(table_name)
        logger.info(f"Exported {table_name}")

    return f"Exported {len(exported)} tables: {', '.join(exported)}"
