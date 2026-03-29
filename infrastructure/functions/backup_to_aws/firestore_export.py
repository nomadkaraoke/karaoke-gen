"""Firestore export to GCS staging bucket."""

import logging
from google.cloud import firestore_admin_v1

logger = logging.getLogger(__name__)


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

    logger.info(f"Firestore export complete: {output_uri}")
    return f"Exported to {output_uri} ({date_str})"
