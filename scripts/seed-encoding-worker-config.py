#!/usr/bin/env python3
"""
Seed the Firestore config/encoding-worker document.

Run once during migration to initialize the blue-green config.
Uses the Pulumi-assigned IPs for VMs A and B.

Usage:
    python scripts/seed-encoding-worker-config.py <ip-a> <ip-b>

Requires:
    - GOOGLE_CLOUD_PROJECT=nomadkaraoke (or gcloud default project)
    - Firestore write access
"""

import sys
from datetime import datetime, timezone
from google.cloud import firestore

PROJECT_ID = "nomadkaraoke"


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/seed-encoding-worker-config.py <ip-a> <ip-b>")
        print("  ip-a: Static IP assigned to encoding-worker-a")
        print("  ip-b: Static IP assigned to encoding-worker-b")
        sys.exit(1)

    ip_a, ip_b = sys.argv[1], sys.argv[2]

    db = firestore.Client(project=PROJECT_ID)
    now = datetime.now(timezone.utc).isoformat()

    config = {
        "primary_vm": "encoding-worker-a",
        "primary_ip": ip_a,
        "primary_version": "unknown",
        "primary_deployed_at": now,
        "secondary_vm": "encoding-worker-b",
        "secondary_ip": ip_b,
        "secondary_version": None,
        "secondary_deployed_at": None,
        "last_swap_at": None,
        "last_activity_at": now,
        "deploy_in_progress": False,
        "deploy_in_progress_since": None,
    }

    doc_ref = db.collection("config").document("encoding-worker")

    # Check if already exists
    if doc_ref.get().exists:
        print("WARNING: config/encoding-worker already exists!")
        print("Current value:", doc_ref.get().to_dict())
        response = input("Overwrite? (y/N): ")
        if response.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    doc_ref.set(config)
    print(f"Seeded config/encoding-worker:")
    print(f"  Primary: encoding-worker-a ({ip_a})")
    print(f"  Secondary: encoding-worker-b ({ip_b})")
    print(f"  Timestamp: {now}")


if __name__ == "__main__":
    main()
