#!/usr/bin/env python3
"""
Backfill missed referral earnings caused by the TZ-aware comparison bug in
record_earning() (fixed in this PR).

Scans stripe_payments for credit purchases by users with referred_by_code set,
and creates a ReferralEarning record + bumps stats.purchases / stats.total_earned_cents
on the referral link, for any session that doesn't already have an earning.

Idempotent: safe to re-run. Existing earnings (matched by stripe_session_id) are skipped.

Usage:
    python scripts/backfill_referral_earnings.py [--apply]

Defaults to dry-run. Pass --apply to actually write changes.

Requirements:
    GOOGLE_CLOUD_PROJECT=nomadkaraoke (or default credentials project)
"""
import argparse
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.cloud import firestore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

REFERRAL_LINKS_COLLECTION = "referral_links"
REFERRAL_EARNINGS_COLLECTION = "referral_earnings"
USERS_COLLECTION = "gen_users"
PAYMENTS_COLLECTION = "stripe_payments"


def main():
    parser = argparse.ArgumentParser(description="Backfill missed referral earnings")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes (default: dry-run)")
    args = parser.parse_args()

    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")
    db = firestore.Client()

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info(f"=== Referral earnings backfill ({mode}) ===")

    referred_users = list(
        db.collection(USERS_COLLECTION).where("referred_by_code", "!=", None).stream()
    )
    logger.info(f"Found {len(referred_users)} referred users")

    link_cache: dict[str, dict] = {}

    def get_link(code: str):
        if code not in link_cache:
            doc = db.collection(REFERRAL_LINKS_COLLECTION).document(code).get()
            link_cache[code] = doc.to_dict() if doc.exists else None
        return link_cache[code]

    existing_earnings_by_session: dict[str, str] = {}
    for e in db.collection(REFERRAL_EARNINGS_COLLECTION).stream():
        d = e.to_dict()
        sid = d.get("stripe_session_id")
        if sid:
            existing_earnings_by_session[sid] = e.id

    logger.info(f"Found {len(existing_earnings_by_session)} existing earnings")

    stats_to_apply: dict[str, dict] = {}
    earnings_to_create: list[dict] = []
    skipped_already = 0
    skipped_outside_window = 0
    skipped_before_attribution = 0
    skipped_no_link = 0
    skipped_zero_earning = 0
    skipped_unsuccessful = 0
    skipped_not_credit_purchase = 0
    skipped_refunded = 0

    for user_doc in referred_users:
        user_data = user_doc.to_dict()
        email = user_data.get("email") or user_doc.id
        referral_code = user_data.get("referred_by_code")
        referred_at = user_data.get("referred_at")
        if not referral_code or not referred_at:
            continue

        link = get_link(referral_code)
        if not link:
            logger.warning(f"User {email} references missing link {referral_code}")
            continue

        earning_duration_days = link.get("earning_duration_days", 365)
        kickback_percent = link.get("kickback_percent", 20)
        owner_email = link.get("owner_email")

        earning_expires = referred_at + timedelta(days=earning_duration_days)

        payments = list(
            db.collection(PAYMENTS_COLLECTION)
              .where("customer_email", "==", email.lower())
              .stream()
        )

        for p in payments:
            pdata = p.to_dict()
            sid = pdata.get("session_id") or p.id

            if pdata.get("order_type") != "credit_purchase":
                skipped_not_credit_purchase += 1
                continue
            if pdata.get("status") != "succeeded":
                skipped_unsuccessful += 1
                continue
            if pdata.get("refund_amount", 0) > 0:
                skipped_refunded += 1
                continue
            if sid in existing_earnings_by_session:
                skipped_already += 1
                continue

            payment_time = pdata.get("created_at")
            if payment_time is not None:
                cmp_expires = earning_expires
                cmp_referred = referred_at
                cmp_payment = payment_time
                # Normalize all three to share tzinfo (Firestore returns aware,
                # naive datetimes show up if a script wrote them).
                tz = cmp_payment.tzinfo or cmp_expires.tzinfo or cmp_referred.tzinfo
                if tz is not None:
                    if cmp_expires.tzinfo is None:
                        cmp_expires = cmp_expires.replace(tzinfo=tz)
                    if cmp_referred.tzinfo is None:
                        cmp_referred = cmp_referred.replace(tzinfo=tz)
                    if cmp_payment.tzinfo is None:
                        cmp_payment = cmp_payment.replace(tzinfo=tz)
                # Earnings only count for purchases made AFTER attribution
                # — not retroactively for purchases predating the referral.
                if cmp_payment < cmp_referred:
                    skipped_before_attribution += 1
                    continue
                if cmp_payment > cmp_expires:
                    skipped_outside_window += 1
                    continue

            amount_total = pdata.get("amount_total", 0) or 0
            earning_amount = int(amount_total * kickback_percent / 100)
            if earning_amount <= 0:
                skipped_zero_earning += 1
                continue

            earning_id = str(uuid.uuid4())
            now_iso = datetime.now(timezone.utc).isoformat()
            earning_doc = {
                "id": earning_id,
                "referrer_email": owner_email,
                "referred_email": email.lower(),
                "referral_code": referral_code,
                "stripe_session_id": sid,
                "purchase_amount_cents": amount_total,
                "earning_amount_cents": earning_amount,
                "status": "pending",
                "created_at": now_iso,
                "paid_out_at": None,
                "stripe_transfer_id": None,
                "payout_id": None,
            }
            earnings_to_create.append(earning_doc)
            agg = stats_to_apply.setdefault(referral_code, {"purchases": 0, "total_earned_cents": 0})
            agg["purchases"] += 1
            agg["total_earned_cents"] += earning_amount

            logger.info(
                f"  + {email} | code={referral_code} | session={sid} | "
                f"spent=${amount_total/100:.2f} | earning=${earning_amount/100:.2f}"
            )

    logger.info("")
    logger.info("=== Summary ===")
    logger.info(f"  Earnings to create: {len(earnings_to_create)}")
    logger.info(f"  Skipped (already recorded): {skipped_already}")
    logger.info(f"  Skipped (not credit_purchase): {skipped_not_credit_purchase}")
    logger.info(f"  Skipped (status != succeeded): {skipped_unsuccessful}")
    logger.info(f"  Skipped (refunded): {skipped_refunded}")
    logger.info(f"  Skipped (before attribution): {skipped_before_attribution}")
    logger.info(f"  Skipped (outside earning window): {skipped_outside_window}")
    logger.info(f"  Skipped (earning <= 0): {skipped_zero_earning}")
    logger.info("")
    logger.info("=== Stats updates per link ===")
    for code, agg in sorted(stats_to_apply.items()):
        logger.info(
            f"  {code}: +{agg['purchases']} purchases, +${agg['total_earned_cents']/100:.2f}"
        )

    if not args.apply:
        logger.info("")
        logger.info("Dry-run complete. Pass --apply to write changes.")
        return

    logger.info("")
    logger.info("=== Applying changes ===")
    for earning in earnings_to_create:
        db.collection(REFERRAL_EARNINGS_COLLECTION).document(earning["id"]).set(earning)

    for code, agg in stats_to_apply.items():
        db.collection(REFERRAL_LINKS_COLLECTION).document(code).update({
            "stats.purchases": firestore.Increment(agg["purchases"]),
            "stats.total_earned_cents": firestore.Increment(agg["total_earned_cents"]),
        })

    logger.info(f"Wrote {len(earnings_to_create)} earnings and updated {len(stats_to_apply)} links")


if __name__ == "__main__":
    main()
