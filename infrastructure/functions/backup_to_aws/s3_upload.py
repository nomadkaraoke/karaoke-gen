"""Upload files from GCS staging bucket to AWS S3."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError
from google.cloud import storage, secretmanager

logger = logging.getLogger(__name__)

# Upload order matters: the function has a hard request timeout, and the multi-GB
# ``gcs/job-files/`` prefix (regenerable video finals) can consume the whole budget.
# We upload the small, irreplaceable prefixes first (in a barrier phase) so a
# mid-run timeout can only ever cost the large regenerable finals, never code
# history or secrets (DR incident 2026-09: secrets + git repos went stale for days
# while GCS job-files stayed fresh).
_SMALL_FIRST_PREFIXES = ("secrets/", "git-repos/", "gcs/kn-data/", "firestore/")
_LARGE_LAST_PREFIXES = ("gcs/job-files/",)

# Transfers stream GCS->S3 *through this function process*, so a sequential loop is
# capped at a single stream's throughput. Fan out across threads (boto3 low-level
# clients are thread-safe) so a night's delta transfers in minutes, not the whole
# 1800s budget. Per-file multipart concurrency is capped so peak memory stays well
# under the function's 4Gi: workers * max_concurrency * chunksize.
_MAX_UPLOAD_WORKERS = 8
_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=16 * 1024 * 1024,
    multipart_chunksize=16 * 1024 * 1024,
    max_concurrency=4,
)


def _upload_priority(name: str) -> int:
    """Sort key for staging blobs: lower uploads first. Small/irreplaceable
    prefixes first, unknown prefixes next, large regenerable finals last."""
    for i, prefix in enumerate(_SMALL_FIRST_PREFIXES):
        if name.startswith(prefix):
            return i
    if any(name.startswith(prefix) for prefix in _LARGE_LAST_PREFIXES):
        return len(_SMALL_FIRST_PREFIXES) + 1
    return len(_SMALL_FIRST_PREFIXES)


def _is_large_last(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _LARGE_LAST_PREFIXES)


def _content_key(blob):
    """A content identity for a blob (prefers crc32c, falls back to md5), or
    ``None`` if the object exposes no checksum."""
    return blob.crc32c or blob.md5_hash


def _dedupe_final_duplicates(blobs: list) -> tuple[list, int]:
    """Drop byte-identical duplicate finals before upload.

    The render pipeline writes every final under both a machine name
    (``lossless_4k_mp4.mp4``) and a human-friendly name
    (``Artist - Title (Final Karaoke Lossless 4k).mp4``). Both live in the same
    ``.../finals/`` directory and are byte-identical, so the backup was storing
    and transferring the largest, most-regenerable data twice.

    Within each ``finals/`` directory, keep one blob per distinct content
    checksum (the first by name, deterministically) and delete the duplicate
    twins from staging. Scoped to a single directory + matched on checksum, so a
    coincidental cross-job size collision can never cause a false drop. Returns
    the surviving blobs and the number of duplicates removed.
    """
    kept = []
    seen_by_dir: dict = {}
    dup_count = 0
    # Sort so the survivor (first-by-name) is chosen deterministically.
    for blob in sorted(blobs, key=lambda b: b.name):
        if "/finals/" not in blob.name:
            kept.append(blob)
            continue
        content = _content_key(blob)
        if content is None:
            kept.append(blob)
            continue
        directory = blob.name.rsplit("/", 1)[0]
        seen = seen_by_dir.setdefault(directory, set())
        if content in seen:
            blob.delete()
            dup_count += 1
            continue
        seen.add(content)
        kept.append(blob)
    return kept, dup_count


def _s3_object_matches(s3_client, s3_bucket: str, key: str, size) -> bool:
    """True if S3 already holds ``key`` with the same byte size. Lets the nightly
    skip anything already backed up — making it genuinely incremental and
    self-healing (a file left in staging by a prior timeout/error is not
    re-transferred once it lands in S3)."""
    if size is None:
        return False
    try:
        head = s3_client.head_object(Bucket=s3_bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return head.get("ContentLength") == size


def _transfer_blob(blob, s3_client, s3_bucket: str) -> str:
    """Idempotently move one staging blob to S3, deleting it from staging on
    success. Returns 'uploaded', 'skipped_exists', or 'error'."""
    try:
        if _s3_object_matches(s3_client, s3_bucket, blob.name, blob.size):
            blob.delete()
            return "skipped_exists"
        with blob.open("rb") as gcs_file:
            s3_client.upload_fileobj(
                Fileobj=gcs_file,
                Bucket=s3_bucket,
                Key=blob.name,
                Config=_TRANSFER_CONFIG,
            )
        blob.delete()
        return "uploaded"
    except Exception as e:
        logger.error(f"Failed to upload {blob.name}: {e}")
        return "error"


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
    """Upload staging-bucket objects to S3, incrementally and in parallel.

    Skips marker files (``.*``) and any object whose name starts with one of
    ``exclude_prefixes`` (used to hold the nightly Firestore export back from S3
    on non-weekly days — it stays in GCS staging as a daily local restore point
    and is uploaded only on the weekly run). Excluded objects are left in staging
    (not deleted), so they remain a local backup until the GCS lifecycle policy
    or the next weekly upload removes them.

    Efficiency:
      * **Deduped** — byte-identical duplicate finals are dropped so the largest
        data is stored/transferred once (see ``_dedupe_final_duplicates``).
      * **Incremental** — objects already in S3 (same key + size) are skipped, so
        only genuinely new/changed bytes cross the wire and a prior partial run
        self-heals (see ``_s3_object_matches``).
      * **Parallel** — transfers fan out across a thread pool instead of a single
        serialized stream.

    Ordering guarantee: small/irreplaceable prefixes upload to completion (a
    barrier) before the large ``gcs/job-files/`` finals begin, so a mid-run
    timeout can only cost the replaceable finals.
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

    candidates = [
        blob
        for blob in bucket.list_blobs()
        if not (blob.name.startswith(".") or "/.last_sync" in blob.name)
        and not any(blob.name.startswith(p) for p in exclude_prefixes)
    ]

    candidates, dup_count = _dedupe_final_duplicates(candidates)

    # Two phases: small/irreplaceable prefixes first (barrier), then large finals,
    # so a timeout can only ever cost the regenerable finals. Each phase runs in
    # parallel.
    critical = [b for b in candidates if not _is_large_last(b.name)]
    bulk = [b for b in candidates if _is_large_last(b.name)]

    counts = {"uploaded": 0, "skipped_exists": 0, "error": 0}

    def run_phase(phase_blobs):
        if not phase_blobs:
            return
        with ThreadPoolExecutor(max_workers=_MAX_UPLOAD_WORKERS) as pool:
            for result in pool.map(
                lambda b: _transfer_blob(b, s3_client, s3_bucket), phase_blobs
            ):
                counts[result] += 1

    run_phase(critical)
    run_phase(bulk)

    summary = (
        f"Uploaded {counts['uploaded']} files to s3://{s3_bucket} "
        f"(skipped {counts['skipped_exists']} already present, "
        f"{dup_count} duplicate finals)"
    )
    if counts["error"]:
        summary += f" ({counts['error']} errors)"
    logger.info(summary)
    return summary
