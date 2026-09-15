"""Upload files from GCS staging bucket to AWS S3."""

import json
import logging

import boto3
from google.cloud import storage, secretmanager

logger = logging.getLogger(__name__)

# Upload order matters: the function has a hard request timeout, and the multi-GB
# ``gcs/job-files/`` prefix (regenerable video finals) can consume the whole budget.
# ``list_blobs()`` returns objects in lexicographic order, which sorts
# ``gcs/job-files/`` ahead of ``git-repos/`` and ``secrets/`` — so a mid-run timeout
# starved the small, irreplaceable backups (DR incident 2026-09: secrets + git repos
# went stale for days while GCS job-files stayed fresh). Upload the small,
# irreplaceable prefixes first so a timeout can only ever cost the large regenerable
# finals, never code history or secrets.
_SMALL_FIRST_PREFIXES = ("secrets/", "git-repos/", "gcs/kn-data/", "firestore/")
_LARGE_LAST_PREFIXES = ("gcs/job-files/",)


def _upload_priority(name: str) -> int:
    """Sort key for staging blobs: lower uploads first. Small/irreplaceable
    prefixes first, unknown prefixes next, large regenerable finals last."""
    for i, prefix in enumerate(_SMALL_FIRST_PREFIXES):
        if name.startswith(prefix):
            return i
    if any(name.startswith(prefix) for prefix in _LARGE_LAST_PREFIXES):
        return len(_SMALL_FIRST_PREFIXES) + 1
    return len(_SMALL_FIRST_PREFIXES)


def get_aws_credentials(project: str) -> dict:
    """Retrieve AWS credentials from GCP Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/aws-backup-credentials/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return json.loads(response.payload.data.decode("utf-8"))


def upload_staging_to_s3(
    staging_bucket: str,
    s3_bucket: str,
    project: str = "nomadkaraoke",
    exclude_prefixes: list | None = None,
) -> str:
    """Upload all objects in staging bucket to S3 using streaming.

    Walks the staging bucket and uploads each object to the corresponding
    S3 key path. Skips marker files (.*) and any object whose name starts with
    one of ``exclude_prefixes`` (used to hold the nightly Firestore export back
    from S3 on non-weekly days — it stays in GCS staging as a daily local
    restore point and is uploaded only on the weekly run).

    Excluded objects are left in staging (not deleted), so they remain a local
    backup until the GCS lifecycle policy or the next weekly upload removes them.

    Objects are uploaded in priority order (see ``_upload_priority``): small,
    irreplaceable prefixes (secrets, git repos) first and the large, regenerable
    ``gcs/job-files/`` finals last, so a mid-run timeout can only ever cost the
    replaceable video finals.
    """
    exclude_prefixes = exclude_prefixes or []
    aws_creds = get_aws_credentials(project)
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=aws_creds["access_key_id"],
        aws_secret_access_key=aws_creds["secret_access_key"],
        region_name=aws_creds.get("region", "us-east-1"),
    )

    gcs_client = storage.Client()
    bucket = gcs_client.bucket(staging_bucket)

    uploaded = 0
    errors = 0

    blobs = sorted(bucket.list_blobs(), key=lambda b: (_upload_priority(b.name), b.name))
    for blob in blobs:
        if blob.name.startswith(".") or "/.last_sync" in blob.name:
            continue

        if any(blob.name.startswith(p) for p in exclude_prefixes):
            continue

        try:
            with blob.open("rb") as gcs_file:
                s3_client.upload_fileobj(
                    Fileobj=gcs_file,
                    Bucket=s3_bucket,
                    Key=blob.name,
                )
            uploaded += 1
            blob.delete()

        except Exception as e:
            logger.error(f"Failed to upload {blob.name}: {e}")
            errors += 1

    summary = f"Uploaded {uploaded} files to s3://{s3_bucket}"
    if errors:
        summary += f" ({errors} errors)"
    logger.info(summary)
    return summary
