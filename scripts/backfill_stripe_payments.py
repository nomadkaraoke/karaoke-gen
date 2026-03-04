#!/usr/bin/env python3
"""
Backfill stripe_payments collection from processed_stripe_sessions.

For each processed session, retrieves the expanded Checkout Session from Stripe
and stores enriched payment data in the stripe_payments collection.

Usage:
    python scripts/backfill_stripe_payments.py [--dry-run] [--limit N]

Requirements:
    - STRIPE_SECRET_KEY environment variable
    - GOOGLE_CLOUD_PROJECT=nomadkaraoke
"""
import argparse
import logging
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import stripe
from google.cloud import firestore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Backfill stripe_payments from processed_stripe_sessions")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to Firestore")
    parser.add_argument("--limit", type=int, default=0, help="Max sessions to process (0 = all)")
    args = parser.parse_args()

    # Verify Stripe key
    secret_key = os.getenv("STRIPE_SECRET_KEY")
    if not secret_key:
        logger.error("STRIPE_SECRET_KEY not set")
        sys.exit(1)
    stripe.api_key = secret_key

    # Init Firestore
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")
    db = firestore.Client()

    # Read all processed sessions
    sessions_ref = db.collection("processed_stripe_sessions")
    payments_ref = db.collection("stripe_payments")

    logger.info("Reading processed_stripe_sessions...")
    docs = list(sessions_ref.stream())
    logger.info(f"Found {len(docs)} processed sessions")

    if args.limit > 0:
        docs = docs[:args.limit]
        logger.info(f"Processing first {args.limit} sessions")

    processed = 0
    skipped = 0
    errors = 0

    for doc in docs:
        session_id = doc.id
        data = doc.to_dict()

        # Check if already in stripe_payments
        existing = payments_ref.document(session_id).get()
        if existing.exists:
            skipped += 1
            continue

        try:
            # Rate limiting: 50 requests/sec
            time.sleep(0.02)

            # Fetch expanded session from Stripe
            session = stripe.checkout.Session.retrieve(
                session_id,
                expand=["payment_intent.latest_charge.balance_transaction"],
            )

            if args.dry_run:
                logger.info(
                    f"[DRY RUN] Would store payment for {session_id}: "
                    f"${session.get('amount_total', 0) / 100:.2f} "
                    f"from {data.get('email', 'unknown')}"
                )
                processed += 1
                continue

            # Use the admin service to store
            # Import here to avoid import issues when running standalone
            from backend.services.stripe_admin_service import StripeAdminService

            service = StripeAdminService()
            success = service.store_payment(dict(session))

            if success:
                processed += 1
                if processed % 10 == 0:
                    logger.info(f"Progress: {processed} stored, {skipped} skipped, {errors} errors")
            else:
                errors += 1
                logger.warning(f"Failed to store payment for {session_id}")

        except stripe.error.InvalidRequestError as e:
            if "No such checkout.session" in str(e):
                logger.warning(f"Session {session_id} no longer exists in Stripe (expired?)")
                skipped += 1
            else:
                errors += 1
                logger.error(f"Stripe error for {session_id}: {e}")

        except Exception as e:
            errors += 1
            logger.error(f"Error processing {session_id}: {e}")

    logger.info(
        f"Backfill complete: {processed} stored, {skipped} skipped, {errors} errors"
    )


if __name__ == "__main__":
    main()
