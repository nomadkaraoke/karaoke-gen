#!/usr/bin/env python3
"""
Query error patterns from Firestore.

Usage:
    python scripts/query-error-patterns.py                    # All active
    python scripts/query-error-patterns.py --status new       # Only new
    python scripts/query-error-patterns.py --service karaoke-backend
    python scripts/query-error-patterns.py --hours 24         # Last 24h
    python scripts/query-error-patterns.py --json             # JSON output

Requires:
    - google-cloud-firestore
    - GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter


ACTIVE_STATUSES = ["new", "acknowledged", "known"]


def query_error_patterns(
    status: Optional[str] = None,
    service: Optional[str] = None,
    hours: Optional[int] = None,
    output_json: bool = False,
    project: str = "nomadkaraoke",
) -> list[dict[str, Any]]:
    """
    Query error patterns from Firestore.

    Args:
        status: Filter by status (new/acknowledged/known/fixed). Default: active (new/acknowledged/known).
        service: Filter by service name.
        hours: Only return patterns last seen within this many hours.
        output_json: Print JSON output instead of a table.
        project: GCP project ID.

    Returns:
        List of pattern dicts.
    """
    db = firestore.Client(project=project)
    query = db.collection("error_patterns")

    # Status filter
    if status:
        query = query.where(filter=FieldFilter("status", "==", status))
    else:
        query = query.where(filter=FieldFilter("status", "in", ACTIVE_STATUSES))

    # Service filter
    if service:
        query = query.where(filter=FieldFilter("service", "==", service))

    # Fetch all matching docs (streaming)
    patterns = []
    for doc in query.stream():
        data = doc.to_dict()
        data["_id"] = doc.id
        patterns.append(data)

    # Hours filter (applied in Python — Firestore requires composite index for multi-field filters)
    if hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        filtered = []
        for p in patterns:
            last_seen = p.get("last_seen")
            if last_seen is not None:
                # Firestore Timestamps have a .timestamp() method
                ts = last_seen.timestamp() if hasattr(last_seen, "timestamp") else float(last_seen)
                if ts >= cutoff.timestamp():
                    filtered.append(p)
        patterns = filtered

    # Sort by last_seen descending (most recent first)
    def sort_key(p):
        ls = p.get("last_seen")
        if ls is None:
            return 0.0
        return ls.timestamp() if hasattr(ls, "timestamp") else float(ls)

    patterns.sort(key=sort_key, reverse=True)

    if output_json:
        print(json.dumps(patterns, indent=2, default=str))
    else:
        _print_table(patterns)

    return patterns


def _fmt_last_seen(value) -> str:
    """Return first 19 chars of ISO timestamp, or empty string."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()[:19]
    return str(value)[:19]


def _print_table(patterns: list[dict[str, Any]]) -> None:
    """Print patterns as a human-readable table."""
    if not patterns:
        print("No error patterns found.")
        return

    # Column widths
    col_status = 14
    col_service = 24
    col_count = 6
    col_last_seen = 19
    col_message = 50

    header = (
        f"{'Status':<{col_status}} "
        f"{'Service':<{col_service}} "
        f"{'Count':>{col_count}} "
        f"{'Last Seen':<{col_last_seen}} "
        f"{'Message':<{col_message}}"
    )
    separator = "-" * len(header)

    print(separator)
    print(header)
    print(separator)

    for p in patterns:
        status = str(p.get("status", ""))[:col_status]
        service = str(p.get("service", ""))[:col_service]
        count = p.get("total_count", p.get("occurrence_count", p.get("count", 0)))
        last_seen = _fmt_last_seen(p.get("last_seen"))
        message = str(p.get("normalized_message", p.get("error_message", p.get("message", ""))))[:col_message]

        print(
            f"{status:<{col_status}} "
            f"{service:<{col_service}} "
            f"{count:>{col_count}} "
            f"{last_seen:<{col_last_seen}} "
            f"{message:<{col_message}}"
        )

    print(separator)
    print(f"Total: {len(patterns)} pattern(s)")


def main():
    parser = argparse.ArgumentParser(
        description="Query error patterns from Firestore"
    )
    parser.add_argument(
        "--status",
        help="Filter by status (new/acknowledged/known/fixed). Default: active patterns.",
    )
    parser.add_argument(
        "--service",
        help="Filter by service name (e.g. karaoke-backend).",
    )
    parser.add_argument(
        "--hours",
        type=int,
        help="Only show patterns last seen within this many hours.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON.",
    )
    parser.add_argument(
        "--project",
        default="nomadkaraoke",
        help="GCP project ID (default: nomadkaraoke).",
    )
    args = parser.parse_args()

    query_error_patterns(
        status=args.status,
        service=args.service,
        hours=args.hours,
        output_json=args.output_json,
        project=args.project,
    )


if __name__ == "__main__":
    main()
