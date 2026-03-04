#!/usr/bin/env python3
"""
Investigate user activity across all data sources.

Queries Firestore for:
- User profile and credit balance
- Credit transaction history
- All jobs created by the user
- Session history (active and revoked)
- Magic link usage

Usage:
    python scripts/investigate_user.py user@example.com
    python scripts/investigate_user.py user@example.com --days 30
    python scripts/investigate_user.py user@example.com --json
    python scripts/investigate_user.py user@example.com --include-revoked

Requires:
    - google-cloud-firestore
    - GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter


def investigate_user(
    email: str,
    days: int = 7,
    include_revoked: bool = False,
    output_json: bool = False
) -> Dict[str, Any]:
    """
    Pull all activity for a user from Firestore.

    Args:
        email: User's email address
        days: Number of days to look back for magic links
        include_revoked: Include revoked sessions
        output_json: Output as JSON instead of human-readable

    Returns:
        Dictionary with all user data
    """
    project_id = os.environ.get('GOOGLE_CLOUD_PROJECT', 'nomadkaraoke')
    db = firestore.Client(project=project_id)
    email = email.lower()

    results = {
        "email": email,
        "investigated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "lookback_days": days,
    }

    # 1. User profile
    user_doc = db.collection("users").document(email).get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        results["user"] = {
            "email": user_data.get("email"),
            "role": user_data.get("role"),
            "credits": user_data.get("credits"),
            "created_at": _format_datetime(user_data.get("created_at")),
            "updated_at": _format_datetime(user_data.get("updated_at")),
            "last_login_at": _format_datetime(user_data.get("last_login_at")),
            "total_jobs_created": user_data.get("total_jobs_created", 0),
            "total_jobs_completed": user_data.get("total_jobs_completed", 0),
            "is_active": user_data.get("is_active", True),
            "email_verified": user_data.get("email_verified", False),
            "display_name": user_data.get("display_name"),
        }
        # Credit transactions (last 20)
        results["credit_transactions"] = user_data.get("credit_transactions", [])[-20:]
    else:
        results["user"] = None
        results["credit_transactions"] = []

    # 2. Jobs
    jobs_query = db.collection("jobs").where(
        filter=FieldFilter("user_email", "==", email)
    ).order_by("created_at", direction=firestore.Query.DESCENDING).limit(50)

    results["jobs"] = []
    for doc in jobs_query.stream():
        job = doc.to_dict()
        results["jobs"].append({
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "created_at": _format_datetime(job.get("created_at")),
            "updated_at": _format_datetime(job.get("updated_at")),
            "artist": job.get("artist"),
            "title": job.get("title"),
            "url": job.get("url"),
            "error_message": job.get("error_message"),
        })

    # 3. Sessions
    sessions_query = db.collection("sessions").where(
        filter=FieldFilter("user_email", "==", email)
    )
    if not include_revoked:
        sessions_query = sessions_query.where(filter=FieldFilter("is_active", "==", True))

    sessions_query = sessions_query.order_by(
        "created_at", direction=firestore.Query.DESCENDING
    ).limit(20)

    results["sessions"] = []
    for doc in sessions_query.stream():
        session = doc.to_dict()
        results["sessions"].append({
            "token_prefix": doc.id[:12] + "..." if doc.id else None,
            "created_at": _format_datetime(session.get("created_at")),
            "expires_at": _format_datetime(session.get("expires_at")),
            "last_activity_at": _format_datetime(session.get("last_activity_at")),
            "is_active": session.get("is_active"),
            "ip_address": session.get("ip_address"),
            "user_agent": _truncate(session.get("user_agent"), 80),
        })

    # 4. Magic links (recent)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        magic_query = db.collection("magic_links").where(
            filter=FieldFilter("email", "==", email)
        ).order_by("created_at", direction=firestore.Query.DESCENDING).limit(20)

        results["magic_links"] = []
        for doc in magic_query.stream():
            ml = doc.to_dict()
            created_at = ml.get("created_at")
            # Filter by date in Python since compound queries may need indexes
            if created_at and hasattr(created_at, 'timestamp'):
                if created_at.timestamp() < cutoff.timestamp():
                    continue
            results["magic_links"].append({
                "created_at": _format_datetime(ml.get("created_at")),
                "expires_at": _format_datetime(ml.get("expires_at")),
                "used": ml.get("used"),
                "used_at": _format_datetime(ml.get("used_at")) if ml.get("used_at") else None,
                "ip_address": ml.get("ip_address"),
            })
    except Exception as e:
        results["magic_links"] = []
        results["magic_links_error"] = str(e)

    # Output
    if output_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        _print_human_readable(results)

    return results


def _format_datetime(dt) -> Optional[str]:
    """Format datetime for display."""
    if dt is None:
        return None
    if hasattr(dt, 'isoformat'):
        return dt.isoformat()
    return str(dt)


def _truncate(s: Optional[str], max_len: int) -> Optional[str]:
    """Truncate string to max length."""
    if s is None:
        return None
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."


def _print_human_readable(results: Dict[str, Any]):
    """Print results in human-readable format."""
    print(f"\n{'='*70}")
    print(f"USER INVESTIGATION: {results['email']}")
    print(f"{'='*70}")
    print(f"Investigated at: {results['investigated_at']}")
    print()

    # User profile
    user = results.get("user")
    if user:
        print("USER PROFILE:")
        print(f"  Email:           {user['email']}")
        print(f"  Role:            {user['role']}")
        print(f"  Credits:         {user['credits']}")
        print(f"  Created:         {user['created_at']}")
        print(f"  Last Login:      {user['last_login_at']}")
        print(f"  Jobs Created:    {user['total_jobs_created']}")
        print(f"  Jobs Completed:  {user['total_jobs_completed']}")
        print(f"  Active:          {user['is_active']}")
        print(f"  Email Verified:  {user['email_verified']}")
        if user.get('display_name'):
            print(f"  Display Name:    {user['display_name']}")
    else:
        print("USER NOT FOUND - No user profile exists for this email")
    print()

    # Jobs
    jobs = results.get("jobs", [])
    print(f"JOBS ({len(jobs)} found):")
    if jobs:
        for job in jobs[:15]:
            status_indicator = {
                "pending": "[PEND]",
                "complete": "[DONE]",
                "failed": "[FAIL]",
                "cancelled": "[CANC]",
                "awaiting_review": "[WAIT]",
                "in_review": "[REVW]",
            }.get(job['status'], f"[{job['status'][:4].upper()}]")

            error_suffix = ""
            if job.get('error_message'):
                error_suffix = f" - ERROR: {_truncate(job['error_message'], 40)}"

            print(f"  {status_indicator} {job['job_id'][:8]} | {job['artist']} - {job['title']}{error_suffix}")
            print(f"           Created: {job['created_at']}")
        if len(jobs) > 15:
            print(f"  ... and {len(jobs) - 15} more jobs")
    else:
        print("  No jobs found")
    print()

    # Sessions
    sessions = results.get("sessions", [])
    print(f"SESSIONS ({len(sessions)} found):")
    if sessions:
        for session in sessions[:10]:
            status = "ACTIVE" if session['is_active'] else "REVOKED"
            print(f"  [{status}] Token: {session['token_prefix']}")
            print(f"           Created: {session['created_at']}, Expires: {session['expires_at']}")
            print(f"           Last Activity: {session['last_activity_at']}")
            print(f"           IP: {session['ip_address']}")
            if session.get('user_agent'):
                print(f"           UA: {session['user_agent']}")
            print()
        if len(sessions) > 10:
            print(f"  ... and {len(sessions) - 10} more sessions")
    else:
        print("  No sessions found")
    print()

    # Credit transactions
    transactions = results.get("credit_transactions", [])
    print(f"CREDIT TRANSACTIONS (last {len(transactions)}):")
    if transactions:
        for txn in transactions[-10:]:
            amount = txn.get('amount', 0)
            sign = "+" if amount > 0 else ""
            reason = txn.get('reason', 'unknown')
            created_at = txn.get('created_at', 'unknown')
            job_id = txn.get('job_id', '')
            job_suffix = f" (job: {job_id[:8]})" if job_id else ""
            print(f"  {sign}{amount:>3} | {reason:<20} | {created_at}{job_suffix}")
    else:
        print("  No transactions found")
    print()

    # Magic links
    magic_links = results.get("magic_links", [])
    print(f"MAGIC LINKS (last {results['lookback_days']} days, {len(magic_links)} found):")
    if magic_links:
        for ml in magic_links[:5]:
            used_status = "USED" if ml['used'] else "unused"
            print(f"  [{used_status}] Created: {ml['created_at']}, IP: {ml['ip_address']}")
            if ml['used'] and ml.get('used_at'):
                print(f"           Used at: {ml['used_at']}")
    else:
        print("  No magic links found in this period")

    if results.get("magic_links_error"):
        print(f"  (Error querying magic links: {results['magic_links_error']})")

    print()
    print("=" * 70)
    print("TIP: Use --json for machine-readable output")
    print("TIP: Use --include-revoked to see all sessions including logged-out ones")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Investigate user activity across all data sources"
    )
    parser.add_argument("email", help="User email to investigate")
    parser.add_argument(
        "--days", type=int, default=7,
        help="Lookback days for magic links (default: 7)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--include-revoked", action="store_true",
        help="Include revoked/inactive sessions"
    )
    args = parser.parse_args()

    investigate_user(
        args.email,
        days=args.days,
        include_revoked=args.include_revoked,
        output_json=args.json
    )


if __name__ == "__main__":
    main()
