"""
User service for authentication, credits, and user management.

Handles:
- Magic link authentication (send, verify)
- Session management
- Credit operations (add, deduct, refund)
- User CRUD operations
"""
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud.firestore_v1 import Increment

from backend.config import get_settings
from backend.models.user import (
    User,
    UserRole,
    UserPublic,
    MagicLinkToken,
    Session,
    CreditTransaction,
)


logger = logging.getLogger(__name__)


# Collection names
USERS_COLLECTION = "gen_users"
MAGIC_LINKS_COLLECTION = "magic_links"
SESSIONS_COLLECTION = "sessions"
PROCESSED_STRIPE_SESSIONS_COLLECTION = "processed_stripe_sessions"

# Token/session configuration
MAGIC_LINK_EXPIRY_MINUTES = 15
SESSION_EXPIRY_DAYS = 7
SESSION_ABSOLUTE_EXPIRY_DAYS = 30
MAX_CREDIT_TRANSACTIONS = 100  # Keep last N transactions


class UserService:
    """Service for user management and authentication."""

    # Number of free credits granted to new users
    NEW_USER_FREE_CREDITS = 1

    def __init__(self):
        """Initialize user service with Firestore client."""
        self.settings = get_settings()
        self.db = firestore.Client(project=self.settings.google_cloud_project)

    # =========================================================================
    # User CRUD Operations
    # =========================================================================

    def get_user_doc_ref(self, email: str):
        """Get the Firestore document reference for a user by email.

        Handles both email-keyed docs (doc ID = email) and hash-keyed docs
        (doc ID = hash, email stored as field). Returns None if not found.
        """
        try:
            # First try direct lookup by email as doc ID
            doc_ref = self.db.collection(USERS_COLLECTION).document(email.lower())
            doc = doc_ref.get()
            if doc.exists:
                return doc_ref

            # Fallback: query by email field (for hash-keyed docs)
            query = self.db.collection(USERS_COLLECTION).where(
                filter=FieldFilter('email', '==', email.lower())
            ).limit(1)
            results = list(query.stream())
            if results:
                return results[0].reference

            return None
        except Exception:
            logger.exception(f"Error finding user doc ref for {email}")
            return None

    def get_user(self, email: str) -> Optional[User]:
        """Get a user by email."""
        try:
            doc_ref = self.db.collection(USERS_COLLECTION).document(email.lower())
            doc = doc_ref.get()

            if doc.exists:
                return User(**doc.to_dict())

            # Fallback: query by email field (for hash-keyed docs)
            query = self.db.collection(USERS_COLLECTION).where(
                filter=FieldFilter('email', '==', email.lower())
            ).limit(1)
            results = list(query.stream())
            if results:
                return User(**results[0].to_dict())

            return None
        except Exception:
            logger.exception(f"Error getting user {email}")
            return None

    def get_or_create_user(
        self,
        email: str,
        tenant_id: Optional[str] = None,
        signup_ip: Optional[str] = None,
        device_fingerprint: Optional[str] = None,
    ) -> User:
        """
        Get existing user or create a new one.

        Welcome credits are granted on first magic link verification, not here.

        Args:
            email: User's email address
            tenant_id: Tenant ID for white-label portals (None = default Nomad Karaoke)
            signup_ip: Client IP address at signup (for rate limiting)
            device_fingerprint: Browser fingerprint at signup (for rate limiting)

        Note: If user exists but has a different tenant_id, the existing user is returned.
        Users are uniquely identified by email, not email+tenant.
        """
        email = email.lower()
        user = self.get_user(email)

        if user:
            # If user exists but tenant_id differs, we still return the user
            # This allows users to access multiple tenants with one account
            # (though features may be restricted per-tenant)
            return user

        # Create new user with zero credits (welcome credits granted on first verification)
        user = User(
            email=email,
            credits=0,
            credit_transactions=[],
            tenant_id=tenant_id,  # Associate with tenant on creation
            signup_ip=signup_ip,
            device_fingerprint=device_fingerprint,
        )
        self._save_user(user)
        logger.info(f"Created new user: {email} (tenant: {tenant_id or 'default'}) — credits pending verification")
        return user

    def _save_user(self, user: User) -> None:
        """Save user to Firestore."""
        try:
            user.updated_at = datetime.utcnow()
            doc_ref = self.db.collection(USERS_COLLECTION).document(user.email.lower())
            doc_ref.set(user.model_dump(mode='json'))
        except Exception:
            logger.exception(f"Error saving user {user.email}")
            raise

    def update_user(self, email: str, **updates) -> Optional[User]:
        """Update user fields."""
        try:
            updates['updated_at'] = datetime.utcnow()
            doc_ref = self.get_user_doc_ref(email)
            if not doc_ref:
                logger.warning(f"Cannot update user {email}: doc not found")
                return None
            doc_ref.update(updates)
            return self.get_user(email)
        except Exception:
            logger.exception(f"Error updating user {email}")
            return None

    def list_users(self, limit: int = 100, include_inactive: bool = False) -> List[User]:
        """List all users (admin only)."""
        try:
            query = self.db.collection(USERS_COLLECTION)

            if not include_inactive:
                query = query.where(filter=FieldFilter('is_active', '==', True))

            query = query.order_by('created_at', direction=firestore.Query.DESCENDING)
            query = query.limit(limit)

            docs = query.stream()
            return [User(**doc.to_dict()) for doc in docs]
        except Exception:
            logger.exception("Error listing users")
            return []

    def get_user_public(self, email: str) -> Optional[UserPublic]:
        """Get public user info."""
        user = self.get_user(email)
        if not user:
            return None

        return UserPublic(
            email=user.email,
            role=user.role,
            credits=user.credits,
            display_name=user.display_name,
            total_jobs_created=user.total_jobs_created,
            total_jobs_completed=user.total_jobs_completed,
            tenant_id=user.tenant_id,
        )

    # =========================================================================
    # Magic Link Authentication
    # =========================================================================

    def create_magic_link(
        self,
        email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        tenant_id: Optional[str] = None,
        device_fingerprint: Optional[str] = None,
    ) -> MagicLinkToken:
        """
        Create a magic link token for email authentication.

        Returns the token object. The actual sending of the email
        should be handled by the caller (or an email service).

        Args:
            email: User's email address
            ip_address: Client IP for auditing
            user_agent: Client user agent for auditing
            tenant_id: Tenant ID for white-label portals (None = default Nomad Karaoke)
            device_fingerprint: Browser fingerprint for anti-abuse
        """
        email = email.lower()

        # Ensure user exists (with tenant association, signup IP, and fingerprint)
        self.get_or_create_user(
            email,
            tenant_id=tenant_id,
            signup_ip=ip_address,
            device_fingerprint=device_fingerprint,
        )

        # Generate secure token
        token = secrets.token_urlsafe(32)

        magic_link = MagicLinkToken(
            token=token,
            email=email,
            expires_at=datetime.utcnow() + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES),
            ip_address=ip_address,
            user_agent=user_agent,
            tenant_id=tenant_id,
            device_fingerprint=device_fingerprint,
        )

        # Save to Firestore
        doc_ref = self.db.collection(MAGIC_LINKS_COLLECTION).document(token)
        doc_ref.set(magic_link.model_dump(mode='json'))

        logger.info(f"Created magic link for {email}")
        return magic_link

    def create_admin_login_token(
        self,
        email: str,
        expiry_hours: int = 24,
    ) -> MagicLinkToken:
        """
        Create an admin login token for email-embedded authentication links.

        Similar to magic links but with configurable expiry (default 24 hours).
        Used for made-for-you order notification emails to allow admin one-click login.

        Args:
            email: Admin's email address to authenticate as
            expiry_hours: Hours until token expires (default: 24, max: 168)

        Returns:
            MagicLinkToken object containing the token

        Raises:
            ValueError: If expiry_hours is out of valid range (1-168)
        """
        # Validate expiry_hours (1 hour to 7 days)
        if not 1 <= expiry_hours <= 168:
            raise ValueError(f"expiry_hours must be between 1 and 168, got {expiry_hours}")

        email = email.lower()

        # Ensure user exists
        self.get_or_create_user(email)

        # Generate secure token
        token = secrets.token_urlsafe(32)

        admin_login = MagicLinkToken(
            token=token,
            email=email,
            expires_at=datetime.utcnow() + timedelta(hours=expiry_hours),
        )

        # Save to Firestore (same collection as magic links for unified verification)
        doc_ref = self.db.collection(MAGIC_LINKS_COLLECTION).document(token)
        doc_ref.set(admin_login.model_dump(mode='json'))

        # Log with redacted email (show only domain) for PII protection
        domain = email.split('@')[-1] if '@' in email else 'unknown'
        logger.info(f"Created admin login token for ***@{domain} (expires in {expiry_hours}h)")
        return admin_login

    def verify_magic_link(self, token: str) -> Tuple[bool, Optional[User], str]:
        """
        Verify a magic link token using a Firestore transaction to prevent race conditions.

        Returns:
            (success, user, message)
        """
        try:
            doc_ref = self.db.collection(MAGIC_LINKS_COLLECTION).document(token)

            @firestore.transactional
            def verify_in_transaction(transaction):
                """Atomically verify and mark magic link as used."""
                doc = doc_ref.get(transaction=transaction)

                if not doc.exists:
                    return False, None, "Invalid or expired link"

                magic_link = MagicLinkToken(**doc.to_dict())

                # Check if already used
                if magic_link.used:
                    return False, None, "This link has already been used"

                # Check expiry
                if datetime.utcnow() > magic_link.expires_at:
                    return False, None, "This link has expired"

                # Mark as used atomically within transaction
                transaction.update(doc_ref, {
                    'used': True,
                    'used_at': datetime.utcnow()
                })

                return True, magic_link.email, "Success"

            # Execute the transaction
            transaction = self.db.transaction()
            success, email_or_error, message = verify_in_transaction(transaction)

            if not success:
                return False, None, message

            # Get user and mark email as verified (outside transaction)
            user = self.get_user(email_or_error)
            if user:
                self.update_user(
                    email_or_error,
                    email_verified=True,
                    last_login_at=datetime.utcnow()
                )
                user = self.get_user(email_or_error)  # Refresh

            logger.info(f"Magic link verified for {email_or_error}")
            return True, user, "Successfully authenticated"

        except Exception:
            logger.exception("Error verifying magic link")
            return False, None, "An error occurred during verification"

    def grant_welcome_credits_if_eligible(self, email: str) -> Tuple[bool, str]:
        """
        Grant welcome credits on first magic link verification.

        Uses AI evaluation to check for abuse before granting. Fail-open:
        if evaluation fails, credits are granted anyway.

        Uses a dedicated welcome_credits_granted flag as primary idempotency
        check (survives credit_transactions list trimming). Falls back to
        checking for welcome_credit transaction as secondary defense.

        Returns:
            Tuple of (credits_granted: bool, status: str).
            status is one of: "granted", "denied", "already_granted", "not_found", "error"
        """
        try:
            email = email.lower()
            user = self.get_user(email)
            if not user:
                return False, "not_found"

            # Primary check: dedicated flag (survives transaction list trimming)
            if user.welcome_credits_granted:
                return False, "already_granted"

            # Secondary check: look for existing welcome_credit transaction
            for txn in user.credit_transactions:
                if txn.reason == "welcome_credit":
                    return False, "already_granted"

            # AI evaluation: check for abuse signals before granting
            try:
                from backend.services.credit_evaluation_service import get_credit_evaluation_service
                eval_service = get_credit_evaluation_service()
                evaluation = eval_service.evaluate(email, "welcome")

                if evaluation.decision == "deny":
                    # Mark as evaluated but not granted so we don't re-evaluate
                    self.update_user(email, welcome_credits_granted=True)
                    logger.info(
                        f"Denied welcome credits to {email}: {evaluation.reasoning}"
                    )
                    try:
                        from backend.services.email_service import get_email_service
                        get_email_service().send_credit_denied_email(email, "welcome")
                    except Exception:
                        logger.exception(f"Failed to send credit denied email to {email}")
                    return False, "denied"

                if evaluation.decision == "pending_review":
                    # Don't mark welcome_credits_granted — allow retry after manual review
                    logger.info(
                        f"Welcome credits pending review for {email}: {evaluation.reasoning}"
                    )
                    try:
                        from backend.services.email_service import get_email_service
                        email_service = get_email_service()
                        email_service.send_credit_review_needed_email(
                            email, "welcome", evaluation.reasoning, signals=evaluation.signals,
                        )
                    except Exception:
                        logger.exception(f"Failed to send review needed email for {email}")
                    return False, "pending_review"
            except Exception:
                logger.exception(f"Credit evaluation failed for {email} — pending review (fail-closed)")
                try:
                    from backend.services.email_service import get_email_service
                    email_service = get_email_service()
                    email_service.send_credit_review_needed_email(
                        email, "welcome", f"Evaluation error: {email}",
                    )
                except Exception:
                    pass
                return False, "pending_review"

            # Grant welcome credits
            welcome_credit = self.NEW_USER_FREE_CREDITS
            transaction = CreditTransaction(
                id=str(uuid.uuid4()),
                amount=welcome_credit,
                reason="welcome_credit",
            )

            transactions = user.credit_transactions[-MAX_CREDIT_TRANSACTIONS + 1:]
            transactions.append(transaction)

            new_balance = user.credits + welcome_credit
            self.update_user(
                email,
                credits=new_balance,
                credit_transactions=[t.model_dump(mode='json') for t in transactions],
                welcome_credits_granted=True,
            )

            logger.info(f"Granted {welcome_credit} welcome credits to {email}")
            return True, "granted"

        except Exception:
            logger.exception(f"Error granting welcome credits to {email}")
            return False, "error"

    # =========================================================================
    # Anti-Abuse Rate Limiting
    # =========================================================================

    # Maximum new signups allowed per IP address in the rate limit window
    MAX_SIGNUPS_PER_IP = 2
    SIGNUP_RATE_LIMIT_HOURS = 24

    def count_recent_signups_from_ip(self, ip_address: str, hours: int = 24) -> int:
        """
        Count new user accounts created from this IP in the last N hours.

        Queries gen_users where signup_ip matches and created_at is recent.
        Used to rate-limit new account creation per IP address.

        Args:
            ip_address: Client IP to check
            hours: Lookback window in hours (default: 24)

        Returns:
            Number of recent signups from this IP
        """
        try:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            query = (
                self.db.collection(USERS_COLLECTION)
                .where(filter=FieldFilter("signup_ip", "==", ip_address))
                .where(filter=FieldFilter("created_at", ">=", cutoff))
            )

            count = 0
            for _ in query.stream():
                count += 1
            return count

        except Exception:
            logger.exception(f"Error counting signups from IP {ip_address}")
            # On error, don't block — return 0 to allow the request
            return 0

    def count_recent_signups_from_fingerprint(
        self, device_fingerprint: str, hours: int = 24
    ) -> int:
        """
        Count new user accounts created with this device fingerprint in the last N hours.

        Args:
            device_fingerprint: Browser fingerprint to check
            hours: Lookback window in hours (default: 24)

        Returns:
            Number of recent signups from this fingerprint
        """
        try:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            query = (
                self.db.collection(USERS_COLLECTION)
                .where(filter=FieldFilter("device_fingerprint", "==", device_fingerprint))
                .where(filter=FieldFilter("created_at", ">=", cutoff))
            )

            count = 0
            for _ in query.stream():
                count += 1
            return count

        except Exception:
            logger.exception(f"Error counting signups from fingerprint")
            return 0

    def is_signup_rate_limited(
        self, ip_address: Optional[str] = None, device_fingerprint: Optional[str] = None
    ) -> bool:
        """
        Check if a signup should be rate limited based on IP and/or fingerprint.

        Either signal hitting the limit triggers a block.
        """
        if ip_address:
            ip_count = self.count_recent_signups_from_ip(
                ip_address, hours=self.SIGNUP_RATE_LIMIT_HOURS
            )
            if ip_count >= self.MAX_SIGNUPS_PER_IP:
                return True

        if device_fingerprint:
            fp_count = self.count_recent_signups_from_fingerprint(
                device_fingerprint, hours=self.SIGNUP_RATE_LIMIT_HOURS
            )
            if fp_count >= self.MAX_SIGNUPS_PER_IP:
                return True

        return False

    # =========================================================================
    # Anti-Abuse Investigation
    # =========================================================================

    def find_users_by_signup_ip(self, ip_address: str) -> List[User]:
        """Find all users who signed up from this IP address."""
        try:
            query = (
                self.db.collection(USERS_COLLECTION)
                .where(filter=FieldFilter("signup_ip", "==", ip_address))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(50)
            )
            return [User(**doc.to_dict()) for doc in query.stream()]
        except Exception:
            logger.exception(f"Error finding users by IP {ip_address}")
            return []

    def find_users_by_fingerprint(self, device_fingerprint: str) -> List[User]:
        """Find all users who signed up with this device fingerprint."""
        try:
            query = (
                self.db.collection(USERS_COLLECTION)
                .where(filter=FieldFilter("device_fingerprint", "==", device_fingerprint))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(50)
            )
            return [User(**doc.to_dict()) for doc in query.stream()]
        except Exception:
            logger.exception(f"Error finding users by fingerprint")
            return []

    def find_suspicious_accounts(self, min_jobs: int = 2, max_spend: int = 0) -> List[User]:
        """
        Find accounts with many jobs but no spend (potential free-credit abusers).

        Args:
            min_jobs: Minimum total_jobs_created to flag (default: 2)
            max_spend: Maximum total_spent in cents to include (default: 0 = free only)
        """
        from backend.utils.test_data import is_internal_or_test_email

        try:
            query = (
                self.db.collection(USERS_COLLECTION)
                .where(filter=FieldFilter("total_jobs_created", ">=", min_jobs))
                .order_by("total_jobs_created", direction=firestore.Query.DESCENDING)
                .limit(100)
            )
            users = []
            for doc in query.stream():
                user = User(**doc.to_dict())
                if user.total_spent <= max_spend and not is_internal_or_test_email(user.email):
                    users.append(user)
            return users
        except Exception:
            logger.exception("Error finding suspicious accounts")
            return []

    def find_related_accounts(self, email: str) -> dict:
        """
        Given a user email, find all accounts that share the same signup IP
        or device fingerprint. Returns a dict with grouped results.
        """
        user = self.get_user(email)
        if not user:
            return {"user": None, "by_ip": [], "by_fingerprint": []}

        by_ip = []
        if user.signup_ip:
            by_ip = [
                u for u in self.find_users_by_signup_ip(user.signup_ip)
                if u.email != user.email
            ]

        by_fingerprint = []
        if user.device_fingerprint:
            by_fingerprint = [
                u for u in self.find_users_by_fingerprint(user.device_fingerprint)
                if u.email != user.email
            ]

        return {
            "user": user,
            "by_ip": by_ip,
            "by_fingerprint": by_fingerprint,
        }

    def find_account_correlations(self) -> dict:
        """
        Find clusters of accounts sharing the same fingerprint or signup IP.

        Returns groups of 2+ accounts that share the same identifier,
        sorted by group size (largest first). Excludes internal/test accounts.
        """
        from collections import defaultdict
        from backend.utils.test_data import is_internal_or_test_email

        # Known bad IPs (load balancer, metadata, placeholder) that don't represent real client IPs
        BAD_IPS = {"169.254.169.126", "unknown", ""}

        try:
            all_users = []
            for doc in self.db.collection(USERS_COLLECTION).stream():
                user = User(**doc.to_dict())
                if not is_internal_or_test_email(user.email):
                    all_users.append(user)

            # Group by fingerprint
            fp_groups: dict[str, list[User]] = defaultdict(list)
            for user in all_users:
                if user.device_fingerprint:
                    fp_groups[user.device_fingerprint].append(user)

            # Group by IP (exclude known bad IPs)
            ip_groups: dict[str, list[User]] = defaultdict(list)
            for user in all_users:
                if user.signup_ip and user.signup_ip not in BAD_IPS:
                    ip_groups[user.signup_ip].append(user)

            # Collect user agents from latest sessions for users in clusters
            cluster_emails = set()
            for fp, users in fp_groups.items():
                if len(users) >= 2:
                    cluster_emails.update(u.email for u in users)
            for ip, users in ip_groups.items():
                if len(users) >= 2:
                    cluster_emails.update(u.email for u in users)

            user_agents: dict[str, str] = {}
            for email in cluster_emails:
                try:
                    sessions = (
                        self.db.collection(SESSIONS_COLLECTION)
                        .where(filter=FieldFilter("user_email", "==", email))
                        .order_by("created_at", direction=firestore.Query.DESCENDING)
                        .limit(1)
                        .stream()
                    )
                    for session_doc in sessions:
                        session_data = session_doc.to_dict()
                        ua = session_data.get("user_agent")
                        if ua:
                            user_agents[email] = ua
                except Exception:
                    pass  # Skip if session lookup fails

            # Build response - only clusters with 2+ users
            fingerprint_clusters = []
            for fp, users in fp_groups.items():
                if len(users) >= 2:
                    fingerprint_clusters.append({
                        "fingerprint": fp,
                        "users": sorted(users, key=lambda u: u.created_at or "", reverse=True),
                    })
            fingerprint_clusters.sort(key=lambda c: len(c["users"]), reverse=True)

            ip_clusters = []
            for ip, users in ip_groups.items():
                if len(users) >= 2:
                    ip_clusters.append({
                        "ip": ip,
                        "users": sorted(users, key=lambda u: u.created_at or "", reverse=True),
                    })
            ip_clusters.sort(key=lambda c: len(c["users"]), reverse=True)

            return {
                "fingerprint_clusters": fingerprint_clusters,
                "ip_clusters": ip_clusters,
                "user_agents": user_agents,
            }
        except Exception:
            logger.exception("Error finding account correlations")
            return {"fingerprint_clusters": [], "ip_clusters": [], "user_agents": {}}

    # =========================================================================
    # Session Management
    # =========================================================================

    def create_session(
        self,
        user_email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        tenant_id: Optional[str] = None,
        device_fingerprint: Optional[str] = None,
    ) -> Session:
        """
        Create a new session for an authenticated user.

        Args:
            user_email: User's email address
            ip_address: Client IP for auditing
            user_agent: Client user agent for auditing
            tenant_id: Tenant ID for white-label portals (None = default Nomad Karaoke)
            device_fingerprint: Browser fingerprint for cross-account correlation
        """
        token = secrets.token_urlsafe(32)
        token_prefix = token[:12]

        session = Session(
            token=token,
            user_email=user_email.lower(),
            expires_at=datetime.utcnow() + timedelta(days=SESSION_ABSOLUTE_EXPIRY_DAYS),
            ip_address=ip_address,
            user_agent=user_agent,
            tenant_id=tenant_id,
            device_fingerprint=device_fingerprint,
        )

        # Serialize and write to Firestore
        session_data = session.model_dump(mode='json')
        doc_ref = self.db.collection(SESSIONS_COLLECTION).document(token)
        doc_ref.set(session_data)

        # Verify the write succeeded by reading back
        verify_doc = doc_ref.get()
        if not verify_doc.exists:
            logger.error(f"Session write verification FAILED for {user_email}: {token_prefix}...")
            raise RuntimeError(f"Failed to persist session for {user_email}")

        logger.info(f"Created and verified session for {user_email}: {token_prefix}... (expires: {session.expires_at})")

        return session

    def validate_session(self, token: str) -> Tuple[bool, Optional[User], str]:
        """
        Validate a session token.

        Returns:
            (valid, user, message)
        """
        # Log token info for debugging (only prefix for security)
        token_prefix = token[:12] if token and len(token) >= 12 else token
        logger.debug(f"Validating session token: {token_prefix}... (len={len(token) if token else 0})")

        try:
            doc_ref = self.db.collection(SESSIONS_COLLECTION).document(token)
            doc = doc_ref.get()

            if not doc.exists:
                logger.warning(f"Session not found in Firestore: {token_prefix}...")
                return False, None, "Invalid session"

            raw_data = doc.to_dict()
            logger.debug(f"Session data found for {token_prefix}...: user_email={raw_data.get('user_email')}, is_active={raw_data.get('is_active')}")

            session = Session(**raw_data)

            # Check if active
            if not session.is_active:
                logger.warning(f"Session revoked for {token_prefix}... (user: {session.user_email})")
                return False, None, "Session has been revoked"

            # Use timezone-aware datetime for all comparisons
            # Firestore returns timezone-aware datetimes, so we must use aware datetimes
            now = datetime.now(timezone.utc)

            # Normalize session datetimes to be timezone-aware for comparison
            # (handles legacy naive datetimes that may exist in Firestore)
            expires_at = session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            last_activity = session.last_activity_at
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)

            # Check expiry
            if now > expires_at:
                logger.warning(f"Session expired for {token_prefix}... (user: {session.user_email}, expired_at: {expires_at}, now: {now})")
                return False, None, "Session has expired"

            # Check inactivity (7 days)
            inactivity_limit = now - timedelta(days=SESSION_EXPIRY_DAYS)
            if last_activity < inactivity_limit:
                logger.warning(f"Session inactive for {token_prefix}... (user: {session.user_email}, last_activity: {last_activity}, limit: {inactivity_limit})")
                return False, None, "Session expired due to inactivity"

            # Update last activity (use timezone-aware datetime)
            doc_ref.update({'last_activity_at': now})

            # Get user
            user = self.get_user(session.user_email)
            if not user:
                logger.warning(f"User not found for session {token_prefix}...: {session.user_email}")
                return False, None, "User not found"

            if not user.is_active:
                logger.warning(f"User account disabled for session {token_prefix}...: {session.user_email}")
                return False, None, "User account is disabled"

            logger.debug(f"Session valid for {token_prefix}... (user: {user.email}, credits: {user.credits})")
            return True, user, "Valid session"

        except Exception:
            logger.exception(f"Error validating session {token_prefix}...")
            return False, None, "An error occurred during validation"

    def revoke_session(self, token: str) -> bool:
        """Revoke a session (logout)."""
        try:
            doc_ref = self.db.collection(SESSIONS_COLLECTION).document(token)
            doc_ref.update({'is_active': False})
            logger.info("Session revoked")
            return True
        except Exception:
            logger.exception("Error revoking session")
            return False

    def revoke_all_sessions(self, user_email: str) -> int:
        """Revoke all sessions for a user."""
        try:
            query = self.db.collection(SESSIONS_COLLECTION).where(
                filter=FieldFilter('user_email', '==', user_email.lower())
            ).where(
                filter=FieldFilter('is_active', '==', True)
            )

            count = 0
            for doc in query.stream():
                doc.reference.update({'is_active': False})
                count += 1

            logger.info(f"Revoked {count} sessions for {user_email}")
            return count
        except Exception:
            logger.exception("Error revoking sessions")
            return 0

    def list_sessions_for_user(
        self,
        user_email: str,
        include_revoked: bool = False,
        limit: int = 50
    ) -> List[Session]:
        """
        List all sessions for a user.

        Args:
            user_email: User's email address
            include_revoked: If True, include revoked/inactive sessions
            limit: Maximum number of sessions to return

        Returns:
            List of Session objects, ordered by created_at descending
        """
        try:
            query = self.db.collection(SESSIONS_COLLECTION).where(
                filter=FieldFilter('user_email', '==', user_email.lower())
            )

            if not include_revoked:
                query = query.where(filter=FieldFilter('is_active', '==', True))

            query = query.order_by('created_at', direction=firestore.Query.DESCENDING)
            query = query.limit(limit)

            sessions = []
            for doc in query.stream():
                try:
                    sessions.append(Session(**doc.to_dict()))
                except Exception as e:
                    logger.warning(f"Failed to parse session document: {e}")

            logger.debug(f"Found {len(sessions)} sessions for {user_email}")
            return sessions
        except Exception:
            logger.exception(f"Error listing sessions for {user_email}")
            return []

    # =========================================================================
    # Credit Operations
    # =========================================================================

    def add_credits(
        self,
        email: str,
        amount: int,
        reason: str,
        job_id: Optional[str] = None,
        stripe_session_id: Optional[str] = None,
        admin_email: Optional[str] = None
    ) -> Tuple[bool, int, str]:
        """
        Add credits to a user account.

        Returns:
            (success, new_balance, message)
        """
        try:
            # For Stripe sessions, atomically check and mark as processed
            # Uses create() which fails if document exists - ensures idempotency
            if stripe_session_id:
                doc_ref = self.db.collection(PROCESSED_STRIPE_SESSIONS_COLLECTION).document(stripe_session_id)
                try:
                    doc_ref.create({
                        'stripe_session_id': stripe_session_id,
                        'email': email.lower(),
                        'amount': amount,
                        'processed_at': datetime.utcnow()
                    })
                except Exception:
                    # Document already exists - this session was already processed
                    logger.info(f"Stripe session {stripe_session_id} already processed (idempotent skip)")
                    return False, 0, "Session already processed"

            email = email.lower()
            user = self.get_or_create_user(email)

            # Create transaction record
            transaction = CreditTransaction(
                id=str(uuid.uuid4()),
                amount=amount,
                reason=reason,
                job_id=job_id,
                stripe_session_id=stripe_session_id,
                created_by=admin_email,
            )

            # Add to transaction history (keep last N)
            transactions = user.credit_transactions[-MAX_CREDIT_TRANSACTIONS + 1:]
            transactions.append(transaction)

            # Update user
            new_balance = user.credits + amount
            self.update_user(
                email,
                credits=new_balance,
                credit_transactions=[t.model_dump(mode='json') for t in transactions]
            )

            logger.info(f"Added {amount} credits to {email} ({reason}). New balance: {new_balance}")
            return True, new_balance, f"Added {amount} credits"

        except Exception as e:
            logger.exception(f"Error adding credits to {email}")
            return False, 0, f"Failed to add credits: {e}"

    def deduct_credit(
        self,
        email: str,
        job_id: str,
        reason: str = "job_creation"
    ) -> Tuple[bool, int, str]:
        """
        Deduct one credit from a user account.

        Uses Firestore transaction to prevent race conditions.

        Returns:
            (success, remaining_credits, message)
        """
        try:
            email = email.lower()
            doc_ref = self.db.collection(USERS_COLLECTION).document(email)

            @firestore.transactional
            def deduct_in_transaction(transaction):
                """Atomically check and deduct credit."""
                doc = doc_ref.get(transaction=transaction)

                if not doc.exists:
                    return False, 0, "User not found"

                user_data = doc.to_dict()
                current_credits = user_data.get('credits', 0)

                if current_credits <= 0:
                    return False, 0, "Insufficient credits"

                # Create transaction record
                credit_txn = CreditTransaction(
                    id=str(uuid.uuid4()),
                    amount=-1,
                    reason=reason,
                    job_id=job_id,
                )

                # Get existing transactions and add new one
                existing_transactions = user_data.get('credit_transactions', [])
                # Keep last N-1 transactions to make room for new one
                transactions = existing_transactions[-(MAX_CREDIT_TRANSACTIONS - 1):]
                transactions.append(credit_txn.model_dump(mode='json'))

                # Calculate new values
                new_balance = current_credits - 1
                total_jobs = user_data.get('total_jobs_created', 0) + 1

                # Update atomically within transaction
                transaction.update(doc_ref, {
                    'credits': new_balance,
                    'credit_transactions': transactions,
                    'total_jobs_created': total_jobs,
                    'updated_at': datetime.utcnow()
                })

                return True, new_balance, f"Credit deducted. {new_balance} remaining"

            # Execute the transaction
            fs_transaction = self.db.transaction()
            success, new_balance, message = deduct_in_transaction(fs_transaction)

            if success:
                logger.info(f"Deducted 1 credit from {email} for job {job_id}. Remaining: {new_balance}")

            return success, new_balance, message

        except Exception as e:
            logger.exception(f"Error deducting credit from {email}")
            return False, 0, f"Failed to deduct credit: {e}"

    def refund_credit(
        self,
        email: str,
        job_id: str,
        reason: str = "job_failed"
    ) -> Tuple[bool, int, str]:
        """
        Refund one credit to a user account (e.g., after job failure).

        Returns:
            (success, new_balance, message)
        """
        return self.add_credits(email, 1, reason, job_id=job_id)

    def check_credits(self, email: str) -> int:
        """Check user's credit balance."""
        user = self.get_user(email)
        return user.credits if user else 0

    def has_credits(self, email: str) -> bool:
        """Check if user has at least one credit."""
        return self.check_credits(email) > 0

    def is_stripe_session_processed(self, stripe_session_id: str) -> bool:
        """
        Check if a Stripe session ID has already been processed.

        Used to ensure idempotency of webhook processing - prevents
        duplicate credit additions if Stripe sends the same webhook twice.

        Uses a dedicated collection for O(1) lookup instead of scanning all users.

        Args:
            stripe_session_id: The Stripe checkout session ID

        Returns:
            True if this session was already processed
        """
        try:
            doc_ref = self.db.collection(PROCESSED_STRIPE_SESSIONS_COLLECTION).document(stripe_session_id)
            doc = doc_ref.get()
            if doc.exists:
                logger.info(f"Stripe session {stripe_session_id} already processed")
                return True
            return False
        except Exception:
            logger.exception(f"Error checking if stripe session {stripe_session_id} was processed")
            # On error, return False to allow processing (better to risk duplicate than block)
            return False

    def _mark_stripe_session_processed(self, stripe_session_id: str, email: str, amount: int) -> None:
        """
        Mark a Stripe session as processed.

        Called after successfully adding credits from a Stripe webhook.

        Args:
            stripe_session_id: The Stripe checkout session ID
            email: User email who received the credits
            amount: Number of credits added
        """
        try:
            doc_ref = self.db.collection(PROCESSED_STRIPE_SESSIONS_COLLECTION).document(stripe_session_id)
            doc_ref.set({
                'stripe_session_id': stripe_session_id,
                'email': email,
                'amount': amount,
                'processed_at': datetime.utcnow()
            })
        except Exception:
            # Log but don't fail - the credits were already added
            logger.exception(f"Error marking stripe session {stripe_session_id} as processed")

    # =========================================================================
    # Admin Operations
    # =========================================================================

    def set_user_role(self, email: str, role: UserRole, admin_email: str) -> bool:
        """Set a user's role (admin only)."""
        try:
            user = self.get_user(email)
            if not user:
                return False

            self.update_user(email, role=role.value)
            logger.info(f"Admin {admin_email} set role for {email} to {role.value}")
            return True
        except Exception:
            logger.exception("Error setting user role")
            return False

    def disable_user(self, email: str, admin_email: str) -> bool:
        """Disable a user account (admin only)."""
        try:
            self.update_user(email, is_active=False)
            self.revoke_all_sessions(email)
            logger.info(f"Admin {admin_email} disabled user {email}")
            return True
        except Exception:
            logger.exception("Error disabling user")
            return False

    def enable_user(self, email: str, admin_email: str) -> bool:
        """Enable a user account (admin only)."""
        try:
            self.update_user(email, is_active=True)
            logger.info(f"Admin {admin_email} enabled user {email}")
            return True
        except Exception:
            logger.exception("Error enabling user")
            return False

    def delete_user(self, email: str, admin_email: str) -> bool:
        """
        Permanently delete a user and all their associated data.

        Deletes: user document, sessions, magic links.
        Does NOT delete jobs (they remain as historical records).

        Args:
            email: Email of the user to delete
            admin_email: Email/ID of the admin performing the action

        Returns:
            True if user was deleted, False if user not found

        Raises:
            ValueError: If trying to delete an admin user
        """
        try:
            email = email.lower()
            user = self.get_user(email)

            if not user:
                return False

            if user.role == UserRole.ADMIN:
                raise ValueError("Cannot delete admin users")

            # Revoke all active sessions
            self.revoke_all_sessions(email)

            # Delete magic links for the user
            magic_links = self.db.collection(MAGIC_LINKS_COLLECTION).where(
                filter=FieldFilter('email', '==', email)
            ).stream()
            for doc in magic_links:
                doc.reference.delete()

            # Delete the user document
            self.db.collection(USERS_COLLECTION).document(email).delete()

            logger.info(f"Admin {admin_email} permanently deleted user {email}")
            return True

        except ValueError:
            raise
        except Exception:
            logger.exception(f"Error deleting user {email}")
            return False

    def increment_jobs_completed(self, email: str) -> bool:
        """Increment the completed jobs counter for a user using atomic increment."""
        try:
            doc_ref = self.get_user_doc_ref(email)
            if not doc_ref:
                return False

            doc_ref.update({
                'total_jobs_completed': Increment(1),
                'updated_at': datetime.utcnow()
            })
            return True
        except Exception:
            logger.exception(f"Error incrementing jobs completed for {email}")
            return False


# Global instance
_user_service = None


def get_user_service() -> UserService:
    """Get the global user service instance."""
    global _user_service
    if _user_service is None:
        _user_service = UserService()
    return _user_service
