#!/usr/bin/env python3
"""List jobs currently in the lyrics-review stage, with ready-to-open review URLs.

Handy for reviewing UI changes locally against a real prod job: start the frontend
dev server (which proxies /api/* to the prod backend by default), log in as admin,
then open one of the printed URLs.

    make -C frontend dev        # or: cd frontend && npm run dev   (BACKEND_URL defaults to prod)
    python scripts/list-review-jobs.py

Reads Firestore directly (read-only ADC is sufficient). Prints hash-routed review URLs
pointing at the local dev server.

Usage:
    python scripts/list-review-jobs.py [--limit N] [--port 3000] [--host localhost]
"""

import argparse
import os

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

REVIEW_STATUSES = ["awaiting_review", "in_review"]


def _meta(job: dict, key: str) -> str:
    return job.get(key) or (job.get("metadata") or {}).get(key) or "?"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25, help="Max jobs to list (default 25)")
    parser.add_argument("--port", type=int, default=3000, help="Local dev server port (default 3000)")
    parser.add_argument("--host", default="localhost", help="Local dev server host (default localhost)")
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "nomadkaraoke"))
    args = parser.parse_args()

    db = firestore.Client(project=args.project)
    base = f"http://{args.host}:{args.port}/app/jobs#"

    total = 0
    for status in REVIEW_STATUSES:
        query = (
            db.collection("jobs")
            .where(filter=FieldFilter("status", "==", status))
            .limit(args.limit)
        )
        rows = list(query.stream())
        if not rows:
            continue
        print(f"\n== {status} ({len(rows)}) ==")
        for doc in rows:
            job = doc.to_dict() or {}
            total += 1
            artist = _meta(job, "artist")
            title = _meta(job, "title")
            print(f"  {doc.id}  {artist} - {title}")
            print(f"    {base}/{doc.id}/review")

    if total == 0:
        print("No jobs currently in review.")
    else:
        print(f"\n{total} job(s) in review. Open a URL above (log in as admin first).")


if __name__ == "__main__":
    main()
