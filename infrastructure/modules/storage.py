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
                methods=["PUT"],
                response_headers=["Content-Type"],
                max_age_seconds=3600,
            ),
        ],
        versioning=storage.BucketVersioningArgs(enabled=True),
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
