#!/usr/bin/env python3
"""
Fetch and display job data from Firestore.

Usage:
    python scripts/get_job.py <job_id>
    python scripts/get_job.py ffb0b8fa

Requires:
    - google-cloud-firestore
    - GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
"""
import sys
import json
from google.cloud import firestore


def get_job(job_id: str) -> dict | None:
    """Fetch job document from Firestore."""
    db = firestore.Client(project='nomadkaraoke')
    doc = db.collection('jobs').document(job_id).get()

    if doc.exists:
        return doc.to_dict()
    # Deleted jobs are archived (doc + archived_logs) instead of lost.
    archived = db.collection('jobs_archive').document(job_id).get()
    if archived.exists:
        print(f"(job {job_id} was deleted; showing jobs_archive copy)", file=sys.stderr)
        return archived.to_dict()
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python get_job.py <job_id> [--field <field_name>]")
        print("\nExamples:")
        print("  python get_job.py ffb0b8fa                  # Full job data")
        print("  python get_job.py ffb0b8fa --field status   # Just status")
        print("  python get_job.py ffb0b8fa --field timeline # Just timeline")
        sys.exit(1)

    job_id = sys.argv[1]
    field_filter = None

    if len(sys.argv) >= 4 and sys.argv[2] == "--field":
        field_filter = sys.argv[3]

    data = get_job(job_id)

    if data is None:
        print(f"Job {job_id} not found", file=sys.stderr)
        sys.exit(1)

    if field_filter:
        if field_filter in data:
            output = data[field_filter]
        else:
            print(f"Field '{field_filter}' not found. Available fields:", file=sys.stderr)
            print(", ".join(sorted(data.keys())), file=sys.stderr)
            sys.exit(1)
    else:
        output = data

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
