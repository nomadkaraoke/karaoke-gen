#!/usr/bin/env python3
"""
Seed the local GCS emulator (fake-gcs-server) with the minimum data the
backend needs to create jobs: the bucket itself plus a default theme.

Run after starting the emulators (scripts/start-emulators.sh); it is invoked
automatically by scripts/run-backend-local.sh --with-emulators. Idempotent —
safe to run repeatedly.

Usage:
    STORAGE_EMULATOR_HOST=http://127.0.0.1:4443 python scripts/seed-local-data.py

Environment:
    STORAGE_EMULATOR_HOST  Required — refuses to run against real GCS.
    GCS_BUCKET_NAME        Bucket to create/seed (default: test-bucket)
    GOOGLE_CLOUD_PROJECT   Project for the emulator client (default: test-project)
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_THEME_ID = "local-default"


def main() -> int:
    emulator_host = os.getenv("STORAGE_EMULATOR_HOST")
    if not emulator_host:
        print("ERROR: STORAGE_EMULATOR_HOST is not set. This script only seeds the")
        print("local GCS emulator — it refuses to touch real GCS buckets.")
        return 1

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "test-project")
    bucket_name = os.getenv("GCS_BUCKET_NAME", "test-bucket")

    from google.cloud import storage

    client = storage.Client(project=project)

    bucket = client.bucket(bucket_name)
    if not bucket.exists():
        client.create_bucket(bucket_name)
        print(f"Created bucket {bucket_name}")
    else:
        print(f"Bucket {bucket_name} already exists")

    # Theme registry with one default theme (jobs can't be created without one)
    registry = {
        "version": 1,
        "themes": [
            {
                "id": DEFAULT_THEME_ID,
                "name": "Local Default",
                "description": "Default theme seeded for local development",
                "is_default": True,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
    }
    bucket.blob("themes/_metadata.json").upload_from_string(
        json.dumps(registry, indent=2), content_type="application/json"
    )
    print("Seeded themes/_metadata.json")

    from karaoke_gen.style_loader import get_default_style_params

    style_params = get_default_style_params()
    bucket.blob(f"themes/{DEFAULT_THEME_ID}/style_params.json").upload_from_string(
        json.dumps(style_params, indent=2), content_type="application/json"
    )
    print(f"Seeded themes/{DEFAULT_THEME_ID}/style_params.json")

    print("\nLocal seed data ready — the backend can now create jobs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
