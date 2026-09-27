"""
Cloud Storage resources.

Manages the GCS bucket for uploads, outputs, and temporary files.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import storage

from config import PROJECT_ID


def create_bucket() -> storage.Bucket:
    """
    Create the main GCS bucket with lifecycle rules.

    Returns:
        storage.Bucket: The created bucket resource.
    """
    bucket = storage.Bucket(
        "karaoke-storage",
        name=f"karaoke-gen-storage-{PROJECT_ID}",
        location="US-CENTRAL1",
        force_destroy=False,  # Prevent accidental deletion
        uniform_bucket_level_access=True,
        cors=[
            storage.BucketCorArgs(
                origins=[
                    "https://gen.nomadkaraoke.com",
                    "https://vocalstar.nomadkaraoke.com",
                    "https://singa.nomadkaraoke.com",
                    "http://localhost:3000",
                ],
                # PUT: direct-to-GCS uploads. GET/HEAD: the lyrics-review vocals
                # waveform fetch()es the signed stem URL and reads its bytes via
                # decodeAudioData — unlike <audio> playback, that's a cross-origin
                # read and needs CORS. Without GET the browser blocks it with
                # "Access-Control-Allow-Origin missing".
                methods=["GET", "HEAD", "PUT"],
                response_headers=["Content-Type"],
                max_age_seconds=3600,
            ),
        ],
        versioning=storage.BucketVersioningArgs(enabled=True),
        # Autoclass (2026-09-26 GCP cost cut): objects not read for 30 days move
        # to Nearline automatically and back to Standard on the next read, with
        # no retrieval or early-deletion fees (operations bill at Standard
        # rates). Terminal class NEARLINE (not ARCHIVE) because customers
        # re-download old job outputs. Incompatible with lifecycle
        # SetStorageClass rules — only Delete rules may live below.
        autoclass=storage.BucketAutoclassArgs(
            enabled=True,
            terminal_storage_class="NEARLINE",
        ),
        soft_delete_policy=storage.BucketSoftDeletePolicyArgs(retention_duration_seconds=604800),
        lifecycle_rules=[
            storage.BucketLifecycleRuleArgs(
                action=storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=storage.BucketLifecycleRuleConditionArgs(
                    age=7,
                    matches_prefixes=["temp/", "uploads/"]
                ),
            ),
            storage.BucketLifecycleRuleArgs(
                action=storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=storage.BucketLifecycleRuleConditionArgs(
                    num_newer_versions=1,
                    days_since_noncurrent_time=30,
                ),
            ),
        ],
    )

    return bucket
