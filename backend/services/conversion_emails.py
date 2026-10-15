"""
Free-to-paid conversion emails for gen consumers.

Funnel data (2026-10 business review): 44% of signups finish a free video but
only 6% ever pay, and buyers decide fast (median ~5h after signup). So:

- When a never-paid user's FIRST video completes, ``on_job_completed`` marks
  ``conversion_e1_due_at`` (now + CONVERSION_E1_DELAY_HOURS).
- The hourly stale-review cron calls ``process_conversion_emails``:
  E1 ("your next song" + CONVERSION_OFFER_PERCENT% off for CONVERSION_OFFER_HOURS)
  once due, which also grants the time-limited ``promo_discount_*`` that
  checkout applies automatically; E2 ("ends in N hours") once, near the end.
- Both are promotional, so they go out on Postmark's broadcast stream (Postmark
  adds the unsubscribe link and suppresses unsubscribed recipients).

Everything is gated by CONVERSION_EMAILS_ENABLED (off until Andrew approves the
copy). Due times older than STALE_DUE_HOURS are skipped, so enabling the flag
later never emails a backlog.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from backend.config import get_settings
from backend.utils.test_data import is_test_email

logger = logging.getLogger(__name__)

USERS_COLLECTION = "gen_users"
STALE_DUE_HOURS = 24
UTM = "utm_source=email&utm_medium=email"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Any) -> Optional[datetime]:
    """Firestore timestamps / ISO strings / naive UTC datetimes -> aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def active_promo(user: Any) -> Optional[Tuple[int, datetime]]:
    """(percent, expires_at) of the user's unexpired promo discount, else None."""
    percent = getattr(user, "promo_discount_percent", None)
    expires = _aware(getattr(user, "promo_discount_expires_at", None))
    if not percent or not expires or expires <= _now():
        return None
    return int(percent), expires


def promo_coupon_for_checkout(email: str, better_than_percent: int = 0) -> Optional[str]:
    """Stripe coupon id for the user's active promo if it beats ``better_than_percent``."""
    from backend.services.stripe_service import get_stripe_service
    from backend.services.user_service import get_user_service

    user = get_user_service().get_user(email)
    promo = active_promo(user) if user else None
    if not promo or promo[0] <= better_than_percent:
        return None
    return get_stripe_service().get_or_create_promo_coupon(promo[0])


def on_job_completed(job: Any, user_service: Any) -> bool:
    """Mark a never-paid consumer's first completed video for the E1 email.

    Called (best-effort) right after ``total_jobs_completed`` is incremented.
    Returns True when the user was marked.
    """
    if getattr(job, "made_for_you", False) or getattr(job, "tenant_id", None):
        return False
    email = getattr(job, "user_email", None)
    if not email or is_test_email(email):
        return False
    user = user_service.get_user(email)
    if not user or getattr(user, "tenant_id", None):
        return False
    if (user.total_spent or 0) > 0 or user.total_jobs_completed != 1:
        return False
    if user.conversion_e1_due_at or user.conversion_e1_sent_at:
        return False
    due = _now() + timedelta(hours=get_settings().conversion_e1_delay_hours)
    user_service.update_user(
        email,
        conversion_e1_due_at=due,
        first_video_artist=getattr(job, "artist", None),
        first_video_title=getattr(job, "title", None),
    )
    logger.info(f"Conversion E1 scheduled for {email.split('@')[-1]} user at {due.isoformat()}")
    return True


def _links(frontend_url: str, locale: str, campaign: str) -> Tuple[str, str]:
    from backend.i18n import get_locale_prefix

    base = f"{frontend_url}{get_locale_prefix(locale)}"
    utm = f"{UTM}&utm_campaign={campaign}"
    return f"{base}/app?buy=1&{utm}", f"{base}/order?{utm}"


def process_conversion_emails(db: Any = None, email_service: Any = None, user_service: Any = None) -> Dict[str, Any]:
    """Send due E1/E2 conversion emails. Safe to run every hour (idempotent per user)."""
    settings = get_settings()
    if not settings.conversion_emails_enabled:
        return {"status": "disabled", "e1_sent": 0, "e2_sent": 0, "errors": []}

    from backend.services.email_service import get_email_service
    from backend.services.user_service import get_user_service

    user_service = user_service or get_user_service()
    email_service = email_service or get_email_service()
    db = db or user_service.db
    now = _now()
    e1_sent = e2_sent = 0
    errors = []

    # --- E1: offer, once due ---
    for doc in db.collection(USERS_COLLECTION).where("conversion_e1_due_at", "<=", now).stream():
        data = doc.to_dict() or {}
        email = data.get("email") or doc.id
        try:
            due = _aware(data.get("conversion_e1_due_at"))
            if data.get("conversion_e1_sent_at") or not due:
                continue
            if (data.get("total_spent") or 0) > 0 or (now - due) > timedelta(hours=STALE_DUE_HOURS):
                # Bought already, or too late to be timely: never send
                user_service.update_user(email, conversion_e1_sent_at=now, conversion_e1_skipped=True)
                continue
            percent = settings.conversion_offer_percent
            expires = now + timedelta(hours=settings.conversion_offer_hours)
            # Mark first so a crash mid-send can't produce a duplicate next hour
            user_service.update_user(
                email,
                conversion_e1_sent_at=now,
                promo_discount_percent=percent,
                promo_discount_expires_at=expires,
            )
            locale = data.get("locale") or "en"
            buy_url, mfy_url = _links(email_service.frontend_url, locale, "conv_e1")
            email_service.send_conversion_offer(
                to_email=email,
                artist=data.get("first_video_artist"),
                title=data.get("first_video_title"),
                percent=percent,
                offer_hours=settings.conversion_offer_hours,
                buy_url=buy_url,
                made_for_you_url=mfy_url,
                locale=locale,
            )
            e1_sent += 1
        except Exception as e:
            logger.exception(f"Conversion E1 failed for a user: {e}")
            errors.append(str(e))

    # --- E2: offer ending soon, once ---
    window_end = now + timedelta(hours=settings.conversion_e2_hours_before_expiry)
    for doc in db.collection(USERS_COLLECTION).where("promo_discount_expires_at", ">", now).stream():
        data = doc.to_dict() or {}
        email = data.get("email") or doc.id
        try:
            expires = _aware(data.get("promo_discount_expires_at"))
            if not expires or expires > window_end or data.get("conversion_e2_sent_at"):
                continue
            if not data.get("conversion_e1_sent_at") or (data.get("total_spent") or 0) > 0:
                continue
            user_service.update_user(email, conversion_e2_sent_at=now)
            locale = data.get("locale") or "en"
            buy_url, _ = _links(email_service.frontend_url, locale, "conv_e2")
            hours_left = max(1, round((expires - now).total_seconds() / 3600))
            email_service.send_conversion_offer_reminder(
                to_email=email,
                percent=int(data.get("promo_discount_percent") or settings.conversion_offer_percent),
                hours_left=hours_left,
                buy_url=buy_url,
                locale=locale,
            )
            e2_sent += 1
        except Exception as e:
            logger.exception(f"Conversion E2 failed for a user: {e}")
            errors.append(str(e))

    logger.info(f"Conversion emails: {e1_sent} E1, {e2_sent} E2, {len(errors)} errors")
    return {"status": "completed", "e1_sent": e1_sent, "e2_sent": e2_sent, "errors": errors}
