"""
Referral service for link management, earnings tracking, and payouts.

Handles:
- Referral link creation (generated and vanity codes)
- Link lookup and validation
- Click tracking
- Admin listing
"""
import logging
import re
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, Tuple

from google.cloud import firestore

from backend.models.referral import (
    ReferralLink,
    ReferralLinkStats,
    ReferralEarning,
    ReferralPayout,
)


logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

REFERRAL_LINKS_COLLECTION = "referral_links"
REFERRAL_EARNINGS_COLLECTION = "referral_earnings"
REFERRAL_PAYOUTS_COLLECTION = "referral_payouts"

RESERVED_CODES = frozenset({
    "admin", "app", "api", "r", "pricing", "order", "payment",
    "login", "auth", "webhook", "webhooks", "internal", "health",
    "static", "assets", "public", "private",
})

PAYOUT_THRESHOLD_CENTS = 2000

VANITY_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{1,28}[a-z0-9]$")

# Characters for generated codes
_CODE_ALPHABET = string.ascii_lowercase + string.digits


# ============================================================================
# Service
# ============================================================================

class ReferralService:
    """Service for referral link management and earnings."""

    def __init__(self, db=None, stripe_service=None):
        if db is None:
            db = firestore.Client()
        self.db = db
        self.stripe_service = stripe_service

    # ========================================================================
    # Code Generation & Validation
    # ========================================================================

    def _generate_code(self) -> str:
        """Generate an 8-character lowercase alphanumeric referral code."""
        return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))

    def _validate_vanity_code(self, code: str) -> Tuple[bool, str]:
        """Validate a vanity code against reserved words and pattern rules.

        Returns (is_valid, message).
        """
        if code.lower() in RESERVED_CODES:
            return False, f"'{code}' is a reserved code"
        if not VANITY_CODE_PATTERN.match(code.lower()):
            return False, (
                "Code must be 3-30 characters, start/end with alphanumeric, "
                "and contain only lowercase letters, digits, and hyphens"
            )
        return True, "Valid"

    # ========================================================================
    # Link CRUD
    # ========================================================================

    def get_link_by_code(self, code: str) -> Optional[ReferralLink]:
        """Get a referral link by its code. Returns None if not found or disabled."""
        doc = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        if not data.get("enabled", True):
            return None
        return ReferralLink(**data)

    def get_or_create_link(self, owner_email: str) -> ReferralLink:
        """Get existing referral link for owner, or create a new one.

        Retries up to 10 times if generated code collides with existing doc.
        """
        # Check for existing link
        existing = (
            self.db.collection(REFERRAL_LINKS_COLLECTION)
            .where("owner_email", "==", owner_email)
            .limit(1)
            .stream()
        )
        for doc in existing:
            return ReferralLink(**doc.to_dict())

        # Create new link with retry for code collision
        for attempt in range(10):
            code = self._generate_code()
            doc_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code)
            doc_snapshot = doc_ref.get()
            if doc_snapshot.exists:
                logger.warning("Code collision on attempt %d: %s", attempt + 1, code)
                continue

            now = datetime.utcnow()
            link = ReferralLink(
                code=code,
                owner_email=owner_email,
                created_at=now,
                updated_at=now,
            )
            doc_ref.set(link.model_dump())
            return link

        raise RuntimeError("Failed to generate unique referral code after 10 attempts")

    def create_vanity_link(
        self,
        code: str,
        owner_email: str,
        display_name: Optional[str] = None,
        custom_message: Optional[str] = None,
        discount_percent: int = 10,
        kickback_percent: int = 20,
        discount_duration_days: int = 30,
        earning_duration_days: int = 365,
    ) -> Tuple[bool, Optional[ReferralLink], str]:
        """Create a vanity referral link.

        Returns (success, link_or_none, message).
        """
        valid, msg = self._validate_vanity_code(code)
        if not valid:
            return False, None, msg

        normalized = code.lower()
        doc_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(normalized)
        doc_snapshot = doc_ref.get()
        if doc_snapshot.exists:
            return False, None, f"Code '{normalized}' is already taken"

        now = datetime.utcnow()
        link = ReferralLink(
            code=normalized,
            owner_email=owner_email,
            display_name=display_name,
            custom_message=custom_message,
            discount_percent=discount_percent,
            kickback_percent=kickback_percent,
            discount_duration_days=discount_duration_days,
            earning_duration_days=earning_duration_days,
            is_vanity=True,
            created_at=now,
            updated_at=now,
        )
        doc_ref.set(link.model_dump())
        return True, link, "Vanity link created"

    def update_link(self, code: str, **updates) -> Tuple[bool, str]:
        """Update fields on an existing referral link.

        Returns (success, message).
        """
        doc_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code)
        doc_snapshot = doc_ref.get()
        if not doc_snapshot.exists:
            return False, f"Link '{code}' not found"

        updates["updated_at"] = datetime.utcnow()
        doc_ref.update(updates)
        return True, "Link updated"

    def increment_clicks(self, code: str) -> None:
        """Increment the click counter for a referral link."""
        doc_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code)
        doc_ref.update({"stats.clicks": firestore.Increment(1)})

    # ========================================================================
    # Attribution
    # ========================================================================

    def attribute_referral(self, referred_email: str, referral_code: str) -> Tuple[bool, str]:
        """Attribute a referral to a user. Called during first login/signup."""
        referred_email = referred_email.lower()
        referral_code = referral_code.lower()

        link = self.get_link_by_code(referral_code)
        if not link:
            return False, "Invalid or disabled referral code"

        if link.owner_email == referred_email:
            return False, "Cannot use your own referral code"

        # Increment signup count
        self.db.collection(REFERRAL_LINKS_COLLECTION).document(referral_code).update({
            "stats.signups": firestore.Increment(1),
        })

        return True, "Referral attributed"

    def get_attribution_data(self, referral_code: str) -> Optional[dict]:
        """Get the data to set on user doc for referral attribution."""
        link = self.get_link_by_code(referral_code)
        if not link:
            return None
        now = datetime.utcnow()
        return {
            "referred_by_code": referral_code.lower(),
            "referred_at": now,
            "referral_discount_expires_at": now + timedelta(days=link.discount_duration_days),
        }

    def get_or_create_stripe_coupon(self, discount_percent: int) -> Optional[str]:
        if not self.stripe_service:
            return None
        return self.stripe_service.get_or_create_referral_coupon(discount_percent)

    def should_apply_discount(self, user_data: dict) -> bool:
        if not user_data.get("referred_by_code"):
            return False
        expires_at = user_data.get("referral_discount_expires_at")
        if not expires_at:
            return False
        if isinstance(expires_at, datetime):
            return expires_at > datetime.utcnow()
        return False

    def get_discount_for_checkout(self, user_email: str) -> Optional[dict]:
        from backend.services.user_service import get_user_service
        user_service = get_user_service()
        user = user_service.get_user(user_email)
        if not user or not user.referred_by_code:
            return None
        user_data = {
            "referred_by_code": user.referred_by_code,
            "referral_discount_expires_at": user.referral_discount_expires_at,
        }
        if not self.should_apply_discount(user_data):
            return None
        link = self.get_link_by_code(user.referred_by_code)
        if not link:
            return None
        coupon_id = self.get_or_create_stripe_coupon(link.discount_percent)
        if not coupon_id:
            return None
        return {"coupon_id": coupon_id, "discount_percent": link.discount_percent}

    def list_links(self, limit: int = 50, offset: int = 0) -> list[ReferralLink]:
        """List referral links for admin view."""
        query = (
            self.db.collection(REFERRAL_LINKS_COLLECTION)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .offset(offset)
            .limit(limit)
        )
        links = []
        for doc in query.stream():
            try:
                links.append(ReferralLink(**doc.to_dict()))
            except Exception:
                logger.warning("Skipping malformed referral link doc: %s", doc.id)
        return links


# ============================================================================
# Singleton accessor
# ============================================================================

_referral_service = None


def get_referral_service() -> ReferralService:
    global _referral_service
    if _referral_service is None:
        from backend.services.stripe_service import get_stripe_service
        _referral_service = ReferralService(stripe_service=get_stripe_service())
    return _referral_service
