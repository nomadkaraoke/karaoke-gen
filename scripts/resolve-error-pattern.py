#!/usr/bin/env python3
"""
Resolve an error pattern by marking it as fixed.

Usage:
    python scripts/resolve-error-pattern.py <pattern_prefix> --pr 42 --note "Fixed timeout"
    python scripts/resolve-error-pattern.py <pattern_prefix> --note "Added retry logic"

Requires:
    - google-cloud-firestore
    - GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
"""
import argparse
import sys
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore


def resolve_error_pattern(
    pattern_prefix: str,
    note: str,
    pr: Optional[int] = None,
    repo: str = "nomadkaraoke/karaoke-gen",
    project: str = "nomadkaraoke",
) -> bool:
    """
    Find an error pattern by ID prefix and mark it as fixed.

    Args:
        pattern_prefix: Prefix of the pattern document ID.
        note: Resolution note explaining how it was fixed.
        pr: Optional PR number that fixed the issue.
        repo: GitHub repo slug used to build the PR URL.
        project: GCP project ID.

    Returns:
        True if the pattern was resolved, False otherwise.
    """
    try:
        db = firestore.Client(project=project)
        collection = db.collection("error_patterns")
    except Exception as exc:
        print(f"Error: Could not connect to Firestore (project={project}): {exc}", file=sys.stderr)
        return False

    # Stream all docs and find those whose ID starts with the prefix
    matches = []
    try:
        for doc in collection.stream():
            if doc.id.startswith(pattern_prefix):
                matches.append(doc)
    except Exception as exc:
        print(f"Error: Could not query error_patterns: {exc}", file=sys.stderr)
        return False

    if len(matches) == 0:
        print(f"Error: No pattern found with ID prefix '{pattern_prefix}'", file=sys.stderr)
        return False

    if len(matches) > 1:
        print(
            f"Ambiguous prefix '{pattern_prefix}' — matches {len(matches)} pattern(s). "
            "Please provide a longer prefix.",
            file=sys.stderr,
        )
        print("\nFirst 5 matches:", file=sys.stderr)
        for doc in matches[:5]:
            data = doc.to_dict()
            service = data.get("service", "")
            message = str(data.get("error_message", data.get("message", "")))[:60]
            print(f"  {doc.id}  [{service}]  {message}", file=sys.stderr)
        return False

    doc = matches[0]
    data = doc.to_dict()
    resolved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    fixed_by: dict = {
        "resolved_at": resolved_at,
        "note": note,
    }

    if pr is not None:
        pr_url = f"https://github.com/{repo}/pull/{pr}"
        fixed_by["pr_url"] = pr_url
        fixed_by["pr_display"] = f"#{pr}"

    update_payload = {
        "status": "fixed",
        "fixed_by": fixed_by,
    }

    try:
        doc.reference.update(update_payload)
    except Exception as exc:
        print(f"Error: Could not update pattern {doc.id}: {exc}", file=sys.stderr)
        return False

    # Confirmation output
    service = data.get("service", "")
    message = str(data.get("error_message", data.get("message", "")))[:80]
    print(f"Resolved pattern: {doc.id}")
    print(f"  Service:  {service}")
    print(f"  Message:  {message}")
    print(f"  Status:   fixed")
    print(f"  Note:     {note}")
    if pr is not None:
        print(f"  PR:       {fixed_by['pr_url']}")
    print(f"  Resolved: {resolved_at}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Resolve an error pattern by marking it as fixed"
    )
    parser.add_argument(
        "pattern_prefix",
        help="Prefix of the error pattern document ID.",
    )
    parser.add_argument(
        "--note",
        required=True,
        help="Resolution note (e.g. 'Fixed timeout in encoding worker').",
    )
    parser.add_argument(
        "--pr",
        type=int,
        help="PR number that fixed the issue (optional).",
    )
    parser.add_argument(
        "--repo",
        default="nomadkaraoke/karaoke-gen",
        help="GitHub repo slug for PR URL (default: nomadkaraoke/karaoke-gen).",
    )
    parser.add_argument(
        "--project",
        default="nomadkaraoke",
        help="GCP project ID (default: nomadkaraoke).",
    )
    args = parser.parse_args()

    success = resolve_error_pattern(
        pattern_prefix=args.pattern_prefix,
        note=args.note,
        pr=args.pr,
        repo=args.repo,
        project=args.project,
    )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
