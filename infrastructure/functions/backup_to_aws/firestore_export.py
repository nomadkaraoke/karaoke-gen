"""Firestore export to GCS staging bucket."""

import logging
from google.cloud import firestore_admin_v1, storage

logger = logging.getLogger(__name__)


def _clear_prior_exports(staging_bucket: str, keep_date: str) -> None:
    """Delete prior firestore/ exports in staging except the keep_date one.

    The Firestore export runs nightly to provide a fresh daily local (GCS)
    restore point, but is only uploaded to S3 weekly (to cut egress). On
    non-weekly days the export is held back from S3 and left in staging, so
    without this cleanup a week of exports would pile up and the weekly upload
    would ship all of them at once — defeating the saving. Keeping only the
    latest export means the weekly upload ships exactly one.
    """
    keep_prefix = f"firestore/{keep_date}"
    try:
        client = storage.Client()
        bucket = client.bucket(staging_bucket)
        for blob in bucket.list_blobs(prefix="firestore/"):
            if not blob.name.startswith(keep_prefix):
                try:
                    blob.delete()
                except Exception as e:
                    logger.warning(f"Failed to delete stale firestore export {blob.name}: {e}")
    except Exception as e:
        # Best-effort cleanup — the export already succeeded, so never fail on it.
        logger.warning(f"Failed to list/clear prior firestore exports: {e}")


def export_firestore(project: str, staging_bucket: str, date_str: str) -> str:
    """Export all Firestore collections to GCS.

    Args:
        project: GCP project ID.
        staging_bucket: GCS staging bucket name.
        date_str: ISO date string (YYYY-MM-DD) for the export folder.

    Returns:
        Summary string of the export.
    """
    client = firestore_admin_v1.FirestoreAdminClient()
    database_name = f"projects/{project}/databases/(default)"
    output_uri = f"gs://{staging_bucket}/firestore/{date_str}"

    logger.info(f"Starting Firestore export to {output_uri}")

    operation = client.export_documents(
        request={
            "name": database_name,
            "output_uri_prefix": output_uri,
        }
    )

    result = operation.result(timeout=1800)  # 30 min timeout

    # Keep only this export in staging so the weekly S3 upload ships exactly one.
    _clear_prior_exports(staging_bucket, date_str)

    logger.info(f"Firestore export complete: {output_uri}")
    return f"Exported to {output_uri} ({date_str})"
