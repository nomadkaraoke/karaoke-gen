"""
Rate limiting service for job creation, YouTube uploads, and beta enrollment.

Uses Firestore for distributed rate limit tracking with date-based document IDs
that automatically reset at UTC midnight.
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Tuple, Optional, List, Dict, Any

from google.cloud import firestore

from backend.config import settings


logger = logging.getLogger(__name__)

# Firestore collection names
RATE_LIMITS_COLLECTION = "rate_limits"
BLOCKLISTS_COLLECTION = "blocklists"
OVERRIDES_COLLECTION = "overrides"


def _get_today_date_str() -> str:
    """Get today's date in UTC as YYYY-MM-DD string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seconds_until_midnight_utc() -> int:
    """Calculate seconds until UTC midnight (when rate limits reset)."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Add 1 day to get next midnight
    from datetime import timedelta
    next_midnight = midnight + timedelta(days=1)
    return int((next_midnight - now).total_seconds())


def _hash_ip(ip_address: str) -> str:
    """Hash an IP address for storage (privacy-preserving)."""
    return hashlib.sha256(ip_address.encode()).hexdigest()[:16]


class RateLimitService:
    """
    Service for managing rate limits across the platform.

    Rate limits use Firestore for distributed tracking with automatic
    reset at UTC midnight via date-based document IDs.
    """

    def __init__(self, db: Optional[firestore.Client] = None):
        """Initialize rate limit service with Firestore client."""
        if db is None:
            self.db = firestore.Client(project=settings.google_cloud_project)
        else:
            self.db = db
        self._blocklist_cache: Optional[Dict[str, Any]] = None
        self._blocklist_cache_time: Optional[datetime] = None
        self._cache_ttl_seconds = 300  # 5 minute cache

    # -------------------------------------------------------------------------
    # Job Rate Limiting
    # -------------------------------------------------------------------------

    def check_user_job_limit(self, user_email: str, is_admin: bool = False) -> Tuple[bool, int, str]:
        """
        Check if a user can create a new job.

        Args:
            user_email: User's email address
            is_admin: Whether the user is an admin (admins bypass limits)

        Returns:
            Tuple of (allowed, remaining, message)
            - allowed: True if user can create job
            - remaining: Number of jobs remaining today
            - message: Human-readable status message
        """
        if not settings.enable_rate_limiting:
            return True, -1, "Rate limiting disabled"

        if is_admin:
            return True, -1, "Admin users bypass rate limits"

        limit = settings.rate_limit_jobs_per_day
        if limit == 0:
            return True, -1, "No job limit configured"

        # Check for user override
        override = self.get_user_override(user_email)
        if override and override.get("bypass_job_limit"):
            return True, -1, "User has rate limit bypass"
        if override and override.get("custom_daily_job_limit"):
            limit = override["custom_daily_job_limit"]

        # Get current count
        current_count = self.get_user_job_count_today(user_email)
        remaining = max(0, limit - current_count)

        if current_count >= limit:
            seconds = _seconds_until_midnight_utc()
            logger.warning(
                f"Rate limit exceeded for {user_email}: {current_count}/{limit} jobs today"
            )
            return (
                False,
                0,
                f"Daily job limit reached ({limit} jobs per day). Resets in {seconds // 3600}h {(seconds % 3600) // 60}m."
            )

        return True, remaining, f"{remaining} jobs remaining today"

    def get_user_job_count_today(self, user_email: str) -> int:
        """Get the number of jobs created by a user today."""
        date_str = _get_today_date_str()
        doc_ref = self.db.collection(RATE_LIMITS_COLLECTION).document(
            f"user_jobs_{user_email}_{date_str}"
        )
        doc = doc_ref.get()

        if not doc.exists:
            return 0

        return doc.to_dict().get("count", 0)

    def record_job_creation(self, user_email: str, job_id: str) -> None:
        """
        Record a job creation for rate limiting.

        Uses Firestore transactions for atomic increment.
        """
        if not settings.enable_rate_limiting:
            return

        date_str = _get_today_date_str()
        doc_ref = self.db.collection(RATE_LIMITS_COLLECTION).document(
            f"user_jobs_{user_email}_{date_str}"
        )

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                count = data.get("count", 0) + 1
                job_ids = data.get("job_ids", [])
                job_ids.append(job_id)
            else:
                count = 1
                job_ids = [job_id]

            transaction.set(doc_ref, {
                "user_email": user_email,
                "date": date_str,
                "count": count,
                "job_ids": job_ids,
                "updated_at": datetime.now(timezone.utc),
            })

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)
        logger.info(f"Recorded job {job_id} for user {user_email} rate limiting")

    # -------------------------------------------------------------------------
    # YouTube Upload Rate Limiting (DEPRECATED - use YouTubeQuotaService)
    # -------------------------------------------------------------------------

    def check_youtube_upload_limit(self) -> Tuple[bool, int, str]:
        """
        Check if the system can perform a YouTube upload.

        DEPRECATED: Use YouTubeQuotaService.check_quota_available() instead.
        This method now delegates to the quota service for backward compatibility.

        Returns:
            Tuple of (allowed, remaining, message)
        """
        try:
            from backend.services.youtube_quota_service import get_youtube_quota_service
            quota_service = get_youtube_quota_service()
        except (ImportError, Exception) as e:
            logger.warning(f"Quota service unavailable, falling back to legacy check: {e}")
            # Fallback to legacy behavior below
            quota_service = None

        if quota_service is not None:
            try:
                return quota_service.check_quota_available()
            except Exception as e:
                logger.error(f"Quota service check failed: {e}")
                return False, 0, f"Quota service error: {e}"

        # Fallback to legacy behavior
        if not settings.enable_rate_limiting:
            return True, -1, "Rate limiting disabled"
        limit = settings.rate_limit_youtube_uploads_per_day
        if limit == 0:
            return True, -1, "No YouTube upload limit configured"
        current_count = self.get_youtube_uploads_today()
        remaining = max(0, limit - current_count)
        if current_count >= limit:
            return False, 0, f"Daily YouTube upload limit reached ({limit} per day)"
        return True, remaining, f"{remaining} YouTube uploads remaining today"

    def get_youtube_uploads_today(self) -> int:
        """Get the number of YouTube uploads performed today (system-wide)."""
        date_str = _get_today_date_str()
        doc_ref = self.db.collection(RATE_LIMITS_COLLECTION).document(
            f"youtube_uploads_{date_str}"
        )
        doc = doc_ref.get()

        if not doc.exists:
            return 0

        return doc.to_dict().get("count", 0)

    def record_youtube_upload(self, job_id: str, user_email: str) -> None:
        """
        Record a YouTube upload for rate limiting.

        DEPRECATED: Use YouTubeQuotaService.record_operation() instead.
        This method still records to the legacy collection for backward compatibility.
        """
        if not settings.enable_rate_limiting:
            return

        date_str = _get_today_date_str()
        doc_ref = self.db.collection(RATE_LIMITS_COLLECTION).document(
            f"youtube_uploads_{date_str}"
        )

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                count = data.get("count", 0) + 1
                uploads = data.get("uploads", [])
            else:
                count = 1
                uploads = []

            uploads.append({
                "job_id": job_id,
                "user_email": user_email,
                "timestamp": datetime.now(timezone.utc),
            })

            transaction.set(doc_ref, {
                "date": date_str,
                "count": count,
                "uploads": uploads,
                "updated_at": datetime.now(timezone.utc),
            })

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)
        logger.info(f"Recorded YouTube upload for job {job_id}")

    # -------------------------------------------------------------------------
    # Beta Enrollment IP Rate Limiting
    # -------------------------------------------------------------------------

    def check_beta_ip_limit(self, ip_address: str) -> Tuple[bool, int, str]:
        """
        Check if an IP address can enroll in the beta program.

        Args:
            ip_address: Client IP address

        Returns:
            Tuple of (allowed, remaining, message)
        """
        if not settings.enable_rate_limiting:
            return True, -1, "Rate limiting disabled"

        limit = settings.rate_limit_beta_ip_per_day
        if limit == 0:
            return True, -1, "No beta IP limit configured"

        # Check today's enrollment count for this IP
        ip_hash = _hash_ip(ip_address)
        date_str = _get_today_date_str()

        doc_ref = self.db.collection(RATE_LIMITS_COLLECTION).document(
            f"beta_ip_{ip_hash}_{date_str}"
        )
        doc = doc_ref.get()

        current_count = 0
        if doc.exists:
            current_count = doc.to_dict().get("count", 0)

        remaining = max(0, limit - current_count)

        if current_count >= limit:
            seconds = _seconds_until_midnight_utc()
            logger.warning(
                f"Beta enrollment IP limit exceeded for {ip_hash}: {current_count}/{limit} enrollments today"
            )
            return (
                False,
                0,
                f"Too many beta enrollments from this network today. Please try again tomorrow."
            )

        return True, remaining, f"{remaining} beta enrollments remaining from this IP today"

    def record_beta_enrollment(self, ip_address: str, email: str) -> None:
        """
        Record a beta enrollment for IP rate limiting.

        Uses Firestore transactions for atomic increment.
        """
        if not settings.enable_rate_limiting:
            return

        ip_hash = _hash_ip(ip_address)
        date_str = _get_today_date_str()
        doc_ref = self.db.collection(RATE_LIMITS_COLLECTION).document(
            f"beta_ip_{ip_hash}_{date_str}"
        )

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                count = data.get("count", 0) + 1
                enrollments = data.get("enrollments", [])
            else:
                count = 1
                enrollments = []

            enrollments.append({
                "email": email,
                "timestamp": datetime.now(timezone.utc),
            })

            transaction.set(doc_ref, {
                "ip_hash": ip_hash,
                "date": date_str,
                "count": count,
                "enrollments": enrollments,
                "updated_at": datetime.now(timezone.utc),
            })

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)
        logger.info(f"Recorded beta enrollment from IP {ip_hash} for {email}")

    # -------------------------------------------------------------------------
    # User Overrides (Whitelist)
    # -------------------------------------------------------------------------

    def get_user_override(self, user_email: str) -> Optional[Dict[str, Any]]:
        """
        Get rate limit override settings for a user.

        Returns:
            Override settings dict or None if no override exists
        """
        doc_ref = self.db.collection(OVERRIDES_COLLECTION).document(user_email.lower())
        doc = doc_ref.get()

        if not doc.exists:
            return None

        return doc.to_dict()

    def set_user_override(
        self,
        user_email: str,
        bypass_job_limit: bool = False,
        custom_daily_job_limit: Optional[int] = None,
        reason: str = "",
        admin_email: str = ""
    ) -> None:
        """
        Set rate limit override for a user.

        Args:
            user_email: User to override
            bypass_job_limit: If True, user bypasses all job limits
            custom_daily_job_limit: Custom limit (None = use default)
            reason: Reason for override
            admin_email: Admin who set the override
        """
        doc_ref = self.db.collection(OVERRIDES_COLLECTION).document(user_email.lower())
        doc_ref.set({
            "email": user_email.lower(),
            "bypass_job_limit": bypass_job_limit,
            "custom_daily_job_limit": custom_daily_job_limit,
            "reason": reason,
            "created_by": admin_email,
            "created_at": datetime.now(timezone.utc),
        })
        logger.info(f"Set rate limit override for {user_email} by {admin_email}")

    def remove_user_override(self, user_email: str, admin_email: str = "") -> bool:
        """Remove rate limit override for a user."""
        doc_ref = self.db.collection(OVERRIDES_COLLECTION).document(user_email.lower())
        doc = doc_ref.get()

        if not doc.exists:
            return False

        doc_ref.delete()
        logger.info(f"Removed rate limit override for {user_email} by {admin_email}")
        return True

    def list_user_overrides(self) -> List[Dict[str, Any]]:
        """List all user rate limit overrides."""
        docs = self.db.collection(OVERRIDES_COLLECTION).stream()
        return [doc.to_dict() for doc in docs]

    def get_all_overrides(self) -> Dict[str, Dict[str, Any]]:
        """Get all user rate limit overrides as a dict keyed by email."""
        docs = self.db.collection(OVERRIDES_COLLECTION).stream()
        return {doc.id: doc.to_dict() for doc in docs}

    # -------------------------------------------------------------------------
    # Blocklist Management
    # -------------------------------------------------------------------------

    def _load_blocklist(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Load blocklist from Firestore with caching.

        Returns:
            Dict with keys: disposable_domains, blocked_emails, blocked_ips
        """
        now = datetime.now(timezone.utc)

        # Check cache
        if not force_refresh and self._blocklist_cache and self._blocklist_cache_time:
            cache_age = (now - self._blocklist_cache_time).total_seconds()
            if cache_age < self._cache_ttl_seconds:
                return self._blocklist_cache

        # Load from Firestore
        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document("config")
        doc = doc_ref.get()

        if not doc.exists:
            # Initialize with empty blocklist
            self._blocklist_cache = {
                "disposable_domains": [],
                "blocked_emails": [],
                "blocked_ips": [],
            }
        else:
            self._blocklist_cache = doc.to_dict()

        self._blocklist_cache_time = now
        return self._blocklist_cache

    def is_disposable_domain(self, domain: str) -> bool:
        """Check if a domain is in the disposable domains blocklist."""
        blocklist = self._load_blocklist()
        return domain.lower() in [d.lower() for d in blocklist.get("disposable_domains", [])]

    def is_blocked_email(self, email: str) -> bool:
        """Check if an email is explicitly blocked."""
        blocklist = self._load_blocklist()
        return email.lower() in [e.lower() for e in blocklist.get("blocked_emails", [])]

    def is_blocked_ip(self, ip_address: str) -> bool:
        """Check if an IP address is blocked."""
        blocklist = self._load_blocklist()
        return ip_address in blocklist.get("blocked_ips", [])

    def get_blocklist(self) -> Dict[str, Any]:
        """Get all blocklist data."""
        return self._load_blocklist(force_refresh=True)

    def add_disposable_domain(self, domain: str, admin_email: str) -> None:
        """Add a domain to the disposable domains blocklist."""
        blocklist = self._load_blocklist(force_refresh=True)
        domains = set(blocklist.get("disposable_domains", []))
        domains.add(domain.lower())

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document("config")
        doc_ref.set({
            **blocklist,
            "disposable_domains": list(domains),
            "updated_at": datetime.now(timezone.utc),
            "updated_by": admin_email,
        }, merge=True)

        self._blocklist_cache = None  # Invalidate cache
        logger.info(f"Added disposable domain {domain} by {admin_email}")

    def remove_disposable_domain(self, domain: str) -> bool:
        """Remove a domain from the disposable domains blocklist."""
        blocklist = self._load_blocklist(force_refresh=True)
        domains = set(blocklist.get("disposable_domains", []))

        if domain.lower() not in [d.lower() for d in domains]:
            return False

        domains = {d for d in domains if d.lower() != domain.lower()}

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document("config")
        doc_ref.set({
            **blocklist,
            "disposable_domains": list(domains),
            "updated_at": datetime.now(timezone.utc),
        }, merge=True)

        self._blocklist_cache = None
        logger.info(f"Removed disposable domain {domain}")
        return True

    def add_blocked_email(self, email: str, admin_email: str) -> None:
        """Add an email to the blocked emails list."""
        blocklist = self._load_blocklist(force_refresh=True)
        emails = set(blocklist.get("blocked_emails", []))
        emails.add(email.lower())

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document("config")
        doc_ref.set({
            **blocklist,
            "blocked_emails": list(emails),
            "updated_at": datetime.now(timezone.utc),
            "updated_by": admin_email,
        }, merge=True)

        self._blocklist_cache = None
        logger.info(f"Added blocked email {email} by {admin_email}")

    def remove_blocked_email(self, email: str) -> bool:
        """Remove an email from the blocked emails list."""
        blocklist = self._load_blocklist(force_refresh=True)
        emails = set(blocklist.get("blocked_emails", []))

        if email.lower() not in [e.lower() for e in emails]:
            return False

        emails = {e for e in emails if e.lower() != email.lower()}

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document("config")
        doc_ref.set({
            **blocklist,
            "blocked_emails": list(emails),
            "updated_at": datetime.now(timezone.utc),
        }, merge=True)

        self._blocklist_cache = None
        logger.info(f"Removed blocked email {email}")
        return True

    def add_blocked_ip(self, ip_address: str, admin_email: str) -> None:
        """Add an IP address to the blocked IPs list."""
        blocklist = self._load_blocklist(force_refresh=True)
        ips = set(blocklist.get("blocked_ips", []))
        ips.add(ip_address)

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document("config")
        doc_ref.set({
            **blocklist,
            "blocked_ips": list(ips),
            "updated_at": datetime.now(timezone.utc),
            "updated_by": admin_email,
        }, merge=True)

        self._blocklist_cache = None
        logger.info(f"Added blocked IP {ip_address} by {admin_email}")

    def remove_blocked_ip(self, ip_address: str) -> bool:
        """Remove an IP address from the blocked IPs list."""
        blocklist = self._load_blocklist(force_refresh=True)
        ips = set(blocklist.get("blocked_ips", []))

        if ip_address not in ips:
            return False

        ips.discard(ip_address)

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document("config")
        doc_ref.set({
            **blocklist,
            "blocked_ips": list(ips),
            "updated_at": datetime.now(timezone.utc),
        }, merge=True)

        self._blocklist_cache = None
        logger.info(f"Removed blocked IP {ip_address}")
        return True

    # -------------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------------

    def get_rate_limit_stats(self) -> Dict[str, Any]:
        """
        Get current rate limit statistics.

        Returns:
            Dict with current usage stats
        """
        date_str = _get_today_date_str()

        # Get YouTube upload count
        youtube_count = self.get_youtube_uploads_today()

        # Count unique users with jobs today
        users_with_jobs = set()
        total_jobs_today = 0

        # Query all user job documents for today
        docs = self.db.collection(RATE_LIMITS_COLLECTION).where(
            "date", "==", date_str
        ).stream()

        for doc in docs:
            data = doc.to_dict()
            if data.get("user_email"):
                users_with_jobs.add(data["user_email"])
                total_jobs_today += data.get("count", 0)

        return {
            "date": date_str,
            "youtube_uploads_today": youtube_count,
            "youtube_uploads_limit": settings.rate_limit_youtube_uploads_per_day,
            "total_jobs_today": total_jobs_today,
            "users_with_jobs_today": len(users_with_jobs),
            "job_limit_per_user": settings.rate_limit_jobs_per_day,
            "beta_ip_limit_per_day": settings.rate_limit_beta_ip_per_day,
            "rate_limiting_enabled": settings.enable_rate_limiting,
            "seconds_until_reset": _seconds_until_midnight_utc(),
        }


# Singleton instance
_rate_limit_service: Optional[RateLimitService] = None


def get_rate_limit_service() -> RateLimitService:
    """Get the singleton RateLimitService instance."""
    global _rate_limit_service
    if _rate_limit_service is None:
        _rate_limit_service = RateLimitService()
    return _rate_limit_service
