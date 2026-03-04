#!/usr/bin/env python3
"""Fix backfilled stripe_payments created_at dates.

The initial backfill stored datetime.utcnow() instead of the original
Stripe session creation timestamp. This script fetches the real `created`
timestamp from each Stripe session and updates the Firestore documents.

Usage:
    STRIPE_SECRET_KEY=... GOOGLE_CLOUD_PROJECT=nomadkaraoke python scripts/fix_backfill_dates.py [--dry-run]
"""

import os
import sys
import time
import stripe
from datetime import datetime, timezone
from google.cloud import firestore

def main():
    dry_run = "--dry-run" in sys.argv

    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe.api_key:
        print("ERROR: STRIPE_SECRET_KEY not set")
        sys.exit(1)

    db = firestore.Client()
    collection = db.collection("stripe_payments")

    # Get all payment docs
    docs = list(collection.stream())
    print(f"Found {len(docs)} payment documents")

    updated = 0
    skipped = 0
    errors = 0

    for doc in docs:
        data = doc.to_dict()
        session_id = data.get("session_id", doc.id)

        try:
            # Fetch the Stripe session to get the real created timestamp
            session = stripe.checkout.Session.retrieve(session_id)
            real_created = datetime.fromtimestamp(session.created, tz=timezone.utc)

            current_created = data.get("created_at")
            # Check if dates differ by more than 1 hour (backfill vs real)
            if current_created:
                if hasattr(current_created, 'timestamp'):
                    diff = abs(current_created.timestamp() - session.created)
                else:
                    diff = 999999  # force update if not a datetime

                if diff < 3600:
                    skipped += 1
                    continue

            if dry_run:
                print(f"  [DRY RUN] {session_id}: would update created_at to {real_created}")
                updated += 1
            else:
                collection.document(doc.id).update({"created_at": real_created})
                print(f"  Updated {session_id}: created_at -> {real_created}")
                updated += 1

            # Rate limit
            time.sleep(0.1)

        except stripe.error.InvalidRequestError as e:
            if "No such checkout.session" in str(e):
                skipped += 1
            else:
                print(f"  ERROR {session_id}: {e}")
                errors += 1
        except Exception as e:
            print(f"  ERROR {session_id}: {e}")
            errors += 1

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Done: {updated} updated, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
