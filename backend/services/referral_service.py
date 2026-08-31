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
from typing import List, Optional, Tuple

from google.cloud import firestore

from backend.models.referral import (
    ReferralLink,
    ReferralLinkStats,
    ReferralEarning,
    ReferralPayout,
    VanityRequestDoc,
)


logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

REFERRAL_LINKS_COLLECTION = "referral_links"
REFERRAL_EARNINGS_COLLECTION = "referral_earnings"
REFERRAL_PAYOUTS_COLLECTION = "referral_payouts"
REFERRAL_VANITY_REQUESTS_COLLECTION = "referral_vanity_requests"
USERS_COLLECTION = "gen_users"

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
        # Check for an existing link. Prefer an enabled one — a link that was
        # renamed (vanity approval) leaves a disabled alias doc behind with the
        # same owner_email, and we must never hand that stale code back.
        existing = (
            self.db.collection(REFERRAL_LINKS_COLLECTION)
            .where("owner_email", "==", owner_email)
            .stream()
        )
        fallback: Optional[ReferralLink] = None
        for doc in existing:
            link = ReferralLink(**doc.to_dict())
            if link.enabled:
                return link
            fallback = fallback or link
        if fallback is not None:
            return fallback

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

    def rename_link(self, old_code: str, new_code: str) -> Tuple[bool, Optional[ReferralLink], str]:
        """Rename a referral link's code in place, preserving stats and config.

        The Firestore doc id *is* the code, so this copies the old doc to a new
        doc under ``new_code`` (marked as a vanity link), migrates referral
        attribution that pointed at ``old_code``, then disables the old doc and
        records ``renamed_to`` so stale links resolve to nothing.

        Returns (success, new_link_or_none, message).
        """
        old_code = old_code.lower()
        valid, msg = self._validate_vanity_code(new_code)
        if not valid:
            return False, None, msg

        new_code = new_code.lower()
        if new_code == old_code:
            return False, None, "New code is the same as the current code"

        old_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(old_code)
        old_snapshot = old_ref.get()
        if not old_snapshot.exists:
            return False, None, f"Link '{old_code}' not found"

        new_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(new_code)
        if new_ref.get().exists:
            return False, None, f"Code '{new_code}' is already taken"

        data = old_snapshot.to_dict()
        now = datetime.utcnow()
        data["code"] = new_code
        data["is_vanity"] = True
        data["enabled"] = True
        data["updated_at"] = now
        data.pop("renamed_to", None)
        new_ref.set(data)

        # Migrate attribution that referenced the old code so existing referred
        # users keep earning under the renamed link.
        self._migrate_code_references(old_code, new_code)

        # Disable the old doc, leaving a breadcrumb. Keeping (not deleting) it
        # reserves the code so it can't be re-issued to someone else.
        old_ref.update({
            "enabled": False,
            "renamed_to": new_code,
            "updated_at": now,
        })

        return True, ReferralLink(**data), f"Renamed '{old_code}' to '{new_code}'"

    def _migrate_code_references(self, old_code: str, new_code: str) -> None:
        """Repoint user attribution and earnings records from old_code to new_code."""
        users = (
            self.db.collection(USERS_COLLECTION)
            .where("referred_by_code", "==", old_code)
            .stream()
        )
        for doc in users:
            doc.reference.update({"referred_by_code": new_code})

        earnings = (
            self.db.collection(REFERRAL_EARNINGS_COLLECTION)
            .where("referral_code", "==", old_code)
            .stream()
        )
        for doc in earnings:
            doc.reference.update({"referral_code": new_code})

    # ========================================================================
    # Vanity requests (user-requested code changes, admin-reviewed)
    # ========================================================================

    def create_vanity_request(
        self, owner_email: str, current_code: str, desired_code: str
    ) -> VanityRequestDoc:
        """Persist a pending vanity request. Keyed by owner so re-requests overwrite."""
        owner_email = owner_email.lower()
        req = VanityRequestDoc(
            id=owner_email,
            owner_email=owner_email,
            current_code=current_code.lower(),
            desired_code=desired_code.lower(),
            status="pending",
            created_at=datetime.utcnow(),
        )
        self.db.collection(REFERRAL_VANITY_REQUESTS_COLLECTION).document(owner_email).set(
            req.model_dump()
        )
        return req

    def get_vanity_request(self, request_id: str) -> Optional[VanityRequestDoc]:
        doc = (
            self.db.collection(REFERRAL_VANITY_REQUESTS_COLLECTION)
            .document(request_id.lower())
            .get()
        )
        if not doc.exists:
            return None
        return VanityRequestDoc(**doc.to_dict())

    def list_vanity_requests(self, status: Optional[str] = "pending") -> List[VanityRequestDoc]:
        """List vanity requests, optionally filtered by status."""
        col = self.db.collection(REFERRAL_VANITY_REQUESTS_COLLECTION)
        query = col.where("status", "==", status) if status else col
        requests = [VanityRequestDoc(**doc.to_dict()) for doc in query.stream()]
        requests.sort(key=lambda r: r.created_at, reverse=True)
        return requests

    def approve_vanity_request(
        self, request_id: str, resolved_by: Optional[str] = None
    ) -> Tuple[bool, Optional[ReferralLink], str]:
        """Approve a pending vanity request by renaming the owner's link.

        Returns (success, new_link_or_none, message).
        """
        req = self.get_vanity_request(request_id)
        if not req:
            return False, None, "Request not found"
        if req.status != "pending":
            return False, None, f"Request already {req.status}"

        # Re-fetch the owner's current enabled link rather than trusting the
        # stored current_code, in case it changed since the request was filed.
        current = self.get_or_create_link(req.owner_email)

        success, link, message = self.rename_link(current.code, req.desired_code)
        if not success:
            return False, None, message

        self.db.collection(REFERRAL_VANITY_REQUESTS_COLLECTION).document(req.id).update({
            "status": "approved",
            "resolved_at": datetime.utcnow(),
            "resolved_by": resolved_by,
        })
        return True, link, message

    def deny_vanity_request(
        self, request_id: str, resolved_by: Optional[str] = None, note: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Deny a pending vanity request."""
        req = self.get_vanity_request(request_id)
        if not req:
            return False, "Request not found"
        if req.status != "pending":
            return False, f"Request already {req.status}"

        self.db.collection(REFERRAL_VANITY_REQUESTS_COLLECTION).document(req.id).update({
            "status": "denied",
            "resolved_at": datetime.utcnow(),
            "resolved_by": resolved_by,
            "note": note,
        })
        return True, "Request denied"

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
            now = datetime.utcnow()
            # Firestore returns timezone-aware datetimes; ensure comparison is compatible
            if expires_at.tzinfo is not None:
                now = now.replace(tzinfo=expires_at.tzinfo)
            return expires_at > now
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

    # ========================================================================
    # Earnings
    # ========================================================================

    def record_earning(self, referred_email: str, stripe_session_id: str, purchase_amount_cents: int) -> Optional[dict]:
        """Record a referral earning after a credit purchase."""
        referred_email = referred_email.lower()

        # Look up the referred user
        user_doc = self.db.collection("gen_users").document(referred_email).get()
        if not user_doc.exists:
            return None

        user_data = user_doc.to_dict()
        referral_code = user_data.get("referred_by_code")
        if not referral_code:
            return None

        referred_at = user_data.get("referred_at")
        if not referred_at:
            return None

        link = self.get_link_by_code(referral_code)
        if not link:
            return None

        # Check earning window. Firestore returns timezone-aware datetimes;
        # datetime.utcnow() is naive — comparing the two raises TypeError.
        earning_expires = referred_at + timedelta(days=link.earning_duration_days)
        now = datetime.utcnow()
        if earning_expires.tzinfo is not None:
            now = now.replace(tzinfo=earning_expires.tzinfo)
        if now > earning_expires:
            return None

        # Calculate earning
        earning_amount = int(purchase_amount_cents * link.kickback_percent / 100)
        if earning_amount <= 0:
            return None

        import uuid
        earning_id = str(uuid.uuid4())
        earning = ReferralEarning(
            id=earning_id,
            referrer_email=link.owner_email,
            referred_email=referred_email,
            referral_code=referral_code,
            stripe_session_id=stripe_session_id,
            purchase_amount_cents=purchase_amount_cents,
            earning_amount_cents=earning_amount,
        )

        self.db.collection(REFERRAL_EARNINGS_COLLECTION).document(earning_id).set(
            earning.model_dump(mode="json")
        )

        self.db.collection(REFERRAL_LINKS_COLLECTION).document(referral_code).update({
            "stats.purchases": firestore.Increment(1),
            "stats.total_earned_cents": firestore.Increment(earning_amount),
        })

        logger.info(f"Referral earning recorded: ${earning_amount / 100:.2f} for {link.owner_email}")
        return {"earning_id": earning_id, "referrer_email": link.owner_email, "earning_amount_cents": earning_amount}

    # ========================================================================
    # Stripe Connect & Payouts
    # ========================================================================

    def create_connect_account(self, email: str) -> Tuple[Optional[str], Optional[str]]:
        """Create Stripe Connect account for a referrer."""
        if not self.stripe_service:
            return None, None
        return self.stripe_service.create_connect_account(email)

    def get_connect_account_status(self, account_id: str) -> Optional[dict]:
        """Get Stripe Connect account status."""
        if not self.stripe_service:
            return None
        return self.stripe_service.get_connect_account_status(account_id)

    def create_connect_login_link(self, account_id: str) -> Optional[str]:
        """Create login link for Stripe Express dashboard."""
        if not self.stripe_service:
            return None
        return self.stripe_service.create_connect_login_link(account_id)

    def create_connect_update_link(self, account_id: str) -> Optional[str]:
        """Create account link for updating Connect info."""
        if not self.stripe_service:
            return None
        return self.stripe_service.create_connect_update_link(account_id)

    def get_pending_earnings(self, referrer_email: str) -> List[dict]:
        """Get all pending earnings for a referrer."""
        query = (
            self.db.collection(REFERRAL_EARNINGS_COLLECTION)
            .where("referrer_email", "==", referrer_email.lower())
            .where("status", "==", "pending")
        )
        return [
            {**doc.to_dict(), "_ref": doc.reference}
            for doc in query.get()
        ]

    def check_and_trigger_payout(self, referrer_email: str, stripe_connect_account_id: str) -> bool:
        """Check if pending earnings meet threshold and trigger payout."""
        pending = self.get_pending_earnings(referrer_email)
        total_pending = sum(e.get("earning_amount_cents", 0) for e in pending)

        if total_pending < PAYOUT_THRESHOLD_CENTS:
            return False

        if not self.stripe_service:
            return False

        transfer_id = self.stripe_service.create_transfer(
            amount_cents=total_pending,
            destination_account_id=stripe_connect_account_id,
        )
        if not transfer_id:
            return False

        import uuid
        payout_id = str(uuid.uuid4())
        earning_ids = [e["id"] for e in pending]

        payout = ReferralPayout(
            id=payout_id,
            referrer_email=referrer_email.lower(),
            stripe_transfer_id=transfer_id,
            amount_cents=total_pending,
            earnings_included=earning_ids,
        )
        self.db.collection(REFERRAL_PAYOUTS_COLLECTION).document(payout_id).set(
            payout.model_dump(mode="json")
        )

        for earning in pending:
            if "_ref" in earning:
                earning["_ref"].update({
                    "status": "paid",
                    "paid_at": datetime.utcnow(),
                    "payout_id": payout_id,
                })

        logger.info(f"Payout triggered: ${total_pending / 100:.2f} to {referrer_email} (transfer: {transfer_id})")
        return True

    def get_dashboard_data(self, user_email: str) -> dict:
        """Get full referral dashboard data for a user."""
        user_email = user_email.lower()
        link = self.get_or_create_link(user_email)

        # Get recent earnings (last 20)
        earnings_query = (
            self.db.collection(REFERRAL_EARNINGS_COLLECTION)
            .where("referrer_email", "==", user_email)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(20)
        )
        earnings = [doc.to_dict() for doc in earnings_query.stream()]

        # Get recent payouts (last 10)
        payouts_query = (
            self.db.collection(REFERRAL_PAYOUTS_COLLECTION)
            .where("referrer_email", "==", user_email)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(10)
        )
        payouts = [doc.to_dict() for doc in payouts_query.stream()]

        # Compute pending balance from ALL pending earnings (not limited to recent)
        pending_earnings = self.get_pending_earnings(user_email)
        pending_balance = sum(e.get("earning_amount_cents", 0) for e in pending_earnings)
        total_earned = link.stats.total_earned_cents
        # total_paid = total_earned - pending (since total_earned tracks all time)
        total_paid = total_earned - pending_balance

        # Check Connect status and fetch account details
        from backend.services.user_service import get_user_service
        user = get_user_service().get_user(user_email)
        has_connect = bool(user and user.stripe_connect_account_id)

        connect_account = None
        if has_connect and self.stripe_service:
            connect_account = self.stripe_service.get_connect_account_status(
                user.stripe_connect_account_id
            )

        return {
            "link": {
                "code": link.code,
                "display_name": link.display_name,
                "custom_message": link.custom_message,
                "discount_percent": link.discount_percent,
                "kickback_percent": link.kickback_percent,
                "discount_duration_days": link.discount_duration_days,
                "earning_duration_days": link.earning_duration_days,
                "stats": link.stats.model_dump(),
                "enabled": link.enabled,
                "is_vanity": link.is_vanity,
            },
            "pending_balance_cents": pending_balance,
            "total_earned_cents": total_earned,
            "total_paid_cents": total_paid,
            "recent_earnings": [
                {
                    "id": e["id"],
                    "referred_email": e.get("referred_email", ""),
                    "purchase_amount_cents": e.get("purchase_amount_cents", 0),
                    "earning_amount_cents": e.get("earning_amount_cents", 0),
                    "status": e.get("status", "pending"),
                    "created_at": str(e.get("created_at", "")),
                }
                for e in earnings
            ],
            "recent_payouts": [
                {
                    "id": p["id"],
                    "amount_cents": p.get("amount_cents", 0),
                    "status": p.get("status", "processing"),
                    "created_at": str(p.get("created_at", "")),
                }
                for p in payouts
            ],
            "stripe_connect_configured": has_connect,
            "stripe_connect_account": connect_account,
        }

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
