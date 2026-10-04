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
        # Never publicly readable: every consumer is an authenticated service
        # account and browsers only ever get V4 signed URLs. (An out-of-band
        # `allUsers` objectViewer grant existed 2025-12-22 → 2026-09-28 to host a
        # debug page; it exposed every customer file AND made GCS edge-cache
        # objects for 1h, so backend reads of overwritten configs went stale.)
        public_access_prevention="enforced",
        cors=[
            storage.BucketCorArgs(
                # Any origin: tenant portals get new *.nomadkaraoke.com
                # subdomains at runtime (admin tenant console), and a per-tenant
                # list here can't keep up. Safe because the bucket is private —
                # a cross-origin request only succeeds with a signed URL or an
                # origin-bound resumable session, which are the auth.
                origins=["*"],
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
            # Noncurrent (overwritten OR deleted) versions are kept 7 days (was
            # 30 until the 2026-10-03 storage-retention work): long enough to undo
            # a bad overwrite/purge, short enough not to double-bill.
            # No num_newer_versions condition: a DELETED object's last version has
            # no newer version, so the old `num_newer_versions=1` rule kept every
            # deleted file's bytes forever (and would have made the storage-
            # retention purge free nothing).
            storage.BucketLifecycleRuleArgs(
                action=storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=storage.BucketLifecycleRuleConditionArgs(
                    with_state="ARCHIVED",
                    days_since_noncurrent_time=7,
                ),
            ),
        ],
    )

    return bucket
