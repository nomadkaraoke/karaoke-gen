"""
Email Validation Service.

Provides email validation, normalization, and blocklist checking
to prevent abuse during enrollment and other flows.

Uses a tiered disposable domain detection strategy:
  1. Static blocklist (instant, from Firestore)
  2. Verified-clean domain cache (skip API calls for 7 days)
  3. DeBounce API (free, <50ms)
  4. verifymail.io API (paid, only if DeBounce says clean)

Auto-learns: flagged domains are persisted to manual_domains,
clean domains are cached for 7 days in verified_clean_domains.
"""

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Set

import httpx
from google.cloud import firestore

from backend.config import settings

logger = logging.getLogger(__name__)

# Module-level HTTP client for connection pooling (external API checks)
_http_client: Optional[httpx.Client] = None
_HTTP_TIMEOUT = 2.0  # seconds per external API call


def _get_http_client() -> httpx.Client:
    """Get or create the module-level HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.Client(timeout=_HTTP_TIMEOUT)
    return _http_client


def _mask_email(email: str) -> str:
    """Mask an email address for privacy-safe logging."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


# Firestore collection for blocklist data
BLOCKLISTS_COLLECTION = "blocklists"
BLOCKLIST_CONFIG_DOC = "config"

# Legacy default set — kept as empty set for import compatibility.
# The external sync (disposable_domain_sync_service) replaces this with ~4,800
# domains from the community-curated disposable-email-domains repo.
# The migration path in sync_disposable_domains() references this set to ensure
# no domains are lost during the transition.
DEFAULT_DISPOSABLE_DOMAINS: Set[str] = set()

# Gmail-like domains that support alias normalization
GMAIL_LIKE_DOMAINS = {
    "gmail.com",
    "googlemail.com",
}

# Well-known email providers that are never disposable.
# These skip all external API checks for zero-latency validation.
WELL_KNOWN_PROVIDERS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.co.jp",
    "yahoo.fr",
    "hotmail.com",
    "hotmail.co.uk",
    "outlook.com",
    "outlook.fr",
    "live.com",
    "live.co.uk",
    "msn.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    "zoho.com",
    "yandex.com",
    "yandex.ru",
    "mail.com",
    "gmx.com",
    "gmx.de",
    "fastmail.com",
    "hey.com",
    "tutanota.com",
    "tuta.io",
}

# How long a verified-clean domain result is trusted before re-checking
VERIFIED_CLEAN_TTL = timedelta(days=7)

# External API URLs
DEBOUNCE_API_URL = "https://disposable.debounce.io/"
VERIFYMAIL_API_URL = "https://verifymail.io/api"


class EmailValidationService:
    """
    Service for email validation and abuse prevention.

    Features:
    - Email normalization (Gmail alias stripping)
    - Disposable domain detection
    - Blocklist checking (emails, IPs)
    - IP-based enrollment rate limiting
    """

    _instance: Optional["EmailValidationService"] = None
    _blocklist_cache: Optional[dict] = None
    _blocklist_cache_time: Optional[datetime] = None
    _cache_ttl_seconds: int = 300  # 5 minutes

    def __init__(self, db: Optional[firestore.Client] = None):
        """Initialize the email validation service."""
        if db is None:
            from backend.services.firestore_service import get_firestore_client
            db = get_firestore_client()
        self.db = db

    @classmethod
    def get_instance(cls, db: Optional[firestore.Client] = None) -> "EmailValidationService":
        """Get singleton instance of the service."""
        if cls._instance is None:
            cls._instance = cls(db)
        return cls._instance

    def normalize_email(self, email: str) -> str:
        """
        Normalize an email address.

        For Gmail-like domains:
        - Removes dots from local part
        - Removes everything after + in local part
        - Converts to lowercase

        For other domains:
        - Only converts to lowercase

        Args:
            email: Email address to normalize

        Returns:
            Normalized email address

        Examples:
            j.o.h.n+spam@gmail.com -> john@gmail.com
            John.Doe@example.com -> john.doe@example.com
        """
        if not email or "@" not in email:
            return email.lower().strip() if email else ""

        email = email.lower().strip()
        local, domain = email.rsplit("@", 1)

        if domain in GMAIL_LIKE_DOMAINS:
            # Remove dots from local part
            local = local.replace(".", "")
            # Remove everything after +
            if "+" in local:
                local = local.split("+")[0]

        return f"{local}@{domain}"

    def get_blocklist_config(self, force_refresh: bool = False) -> dict:
        """
        Get blocklist configuration from Firestore.

        Uses caching to reduce Firestore reads.

        Returns:
            Dict with disposable_domains, blocked_emails, blocked_ips sets
        """
        now = datetime.now(timezone.utc)

        # Check cache validity
        if (
            not force_refresh
            and self._blocklist_cache is not None
            and self._blocklist_cache_time is not None
            and (now - self._blocklist_cache_time).total_seconds() < self._cache_ttl_seconds
        ):
            return self._blocklist_cache

        # Fetch from Firestore
        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
        doc = doc_ref.get()

        if doc.exists:
            data = doc.to_dict()
            if "external_domains" in data:
                # New data model: effective = (external + manual) - allowlisted
                external = set(data.get("external_domains", []))
                manual = set(data.get("manual_domains", []))
                allowlisted = set(data.get("allowlisted_domains", []))
                effective_domains = (external | manual) - allowlisted
            else:
                # Old data model (pre-migration): use disposable_domains + defaults
                allowlisted = set()
                effective_domains = set(data.get("disposable_domains", [])) | DEFAULT_DISPOSABLE_DOMAINS
            config = {
                "disposable_domains": effective_domains,
                "allowlisted_domains": allowlisted,
                "blocked_emails": set(data.get("blocked_emails", [])),
                "blocked_ips": set(data.get("blocked_ips", [])),
                "verified_clean_domains": data.get("verified_clean_domains", {}),
            }
        else:
            config = {
                "disposable_domains": DEFAULT_DISPOSABLE_DOMAINS.copy(),
                "allowlisted_domains": set(),
                "blocked_emails": set(),
                "blocked_ips": set(),
                "verified_clean_domains": {},
            }

        # Update cache
        EmailValidationService._blocklist_cache = config
        EmailValidationService._blocklist_cache_time = now

        return config

    def is_disposable_domain(self, email: str) -> bool:
        """
        Check if an email uses a disposable domain.

        Uses a tiered checking strategy:
          0. Well-known providers (gmail, yahoo, etc.) → instant False
          1. Static blocklist from Firestore → instant
          2. Verified-clean cache (7-day TTL) → skip API calls
          3. DeBounce API (free) → fast external check
          4. verifymail.io API (paid) → only if DeBounce says clean

        Flagged domains are auto-learned to manual_domains.
        Clean domains are cached for 7 days.

        Args:
            email: Email address to check

        Returns:
            True if the domain is known to be disposable
        """
        if not email or "@" not in email:
            return False

        domain = email.lower().split("@")[-1]

        # Tier 0: Well-known providers — skip everything
        if domain in WELL_KNOWN_PROVIDERS:
            return False

        # Tier 1: Static blocklist (instant, always available)
        config = self.get_blocklist_config()
        if domain in config["disposable_domains"]:
            return True

        # Tier 1.5: Explicitly allowlisted domains skip external API checks.
        # Without this, external APIs can override admin allowlist decisions
        # (e.g. inbox.testmail.app is a legitimate testing domain we allow).
        if domain in config.get("allowlisted_domains", set()):
            return False

        # Tier 2: Recently verified clean? Skip API calls
        if self._is_domain_verified_clean(domain, config):
            return False

        # Tier 3: DeBounce API (free, fast)
        debounce_result = self._check_debounce(email)
        if debounce_result is True:
            self._auto_learn_domain(domain)
            return True

        # Tier 4: verifymail.io (paid, only if DeBounce gave definitive clean)
        if debounce_result is False:
            verifymail_result = self._check_verifymail(email)
            if verifymail_result is True:
                self._auto_learn_domain(domain)
                return True

        # Both APIs say clean (or errored) — cache clean result
        if debounce_result is False:
            self._mark_domain_clean(domain)

        return False

    def _check_debounce(self, email: str) -> Optional[bool]:
        """
        Check email against DeBounce disposable email API.

        Returns:
            True if disposable, False if clean, None if error/timeout.
        """
        try:
            client = _get_http_client()
            response = client.get(DEBOUNCE_API_URL, params={"email": email})
            response.raise_for_status()
            data = response.json()
            result = str(data.get("disposable", "")).lower() == "true"
            logger.info(
                f"DeBounce check for {_mask_email(email)}: "
                f"disposable={result}"
            )
            return result
        except httpx.TimeoutException:
            logger.warning(f"DeBounce API timeout for {_mask_email(email)}")
            return None
        except Exception as e:
            logger.warning(f"DeBounce API error for {_mask_email(email)}: {e}")
            return None

    def _check_verifymail(self, email: str) -> Optional[bool]:
        """
        Check email against verifymail.io API.

        Only called when DeBounce says clean (to minimize paid API usage).

        Returns:
            True if disposable/blocked, False if clean, None if error/timeout
            or API key not configured.
        """
        api_key = settings.get_secret("verifymail-api-key")
        if not api_key:
            logger.debug("verifymail.io API key not configured, skipping")
            return None

        try:
            client = _get_http_client()
            response = client.get(
                f"{VERIFYMAIL_API_URL}/{email}",
                params={"key": api_key},
            )
            response.raise_for_status()
            data = response.json()
            # verifymail.io returns both "disposable" and "block" flags
            is_disposable = data.get("disposable") is True
            is_blocked = data.get("block") is True
            result = is_disposable or is_blocked
            logger.info(
                f"verifymail.io check for {_mask_email(email)}: "
                f"disposable={is_disposable}, block={is_blocked}"
            )
            return result
        except httpx.TimeoutException:
            logger.warning(f"verifymail.io API timeout for {_mask_email(email)}")
            return None
        except Exception as e:
            logger.warning(f"verifymail.io API error for {_mask_email(email)}: {e}")
            return None

    def _is_domain_verified_clean(self, domain: str, config: dict) -> bool:
        """Check if a domain was verified clean within the TTL period."""
        verified = config.get("verified_clean_domains", {})
        entry = verified.get(domain)
        if not entry:
            return False

        checked_at = entry.get("checked_at")
        if not checked_at:
            return False

        # Handle both datetime objects and ISO strings
        if isinstance(checked_at, str):
            try:
                checked_at = datetime.fromisoformat(checked_at)
            except (ValueError, TypeError):
                return False

        # Make timezone-aware if naive
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)

        return (datetime.now(timezone.utc) - checked_at) < VERIFIED_CLEAN_TTL

    def _auto_learn_domain(self, domain: str) -> None:
        """
        Add a domain flagged by external API to manual_domains in Firestore.

        This persists the result so future checks don't need the API call.
        Fire-and-forget: errors are logged but don't affect the caller.
        """
        try:
            doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(
                BLOCKLIST_CONFIG_DOC
            )
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                manual = set(data.get("manual_domains", []))
            else:
                manual = set()

            if domain not in manual:
                manual.add(domain)
                doc_ref.set(
                    {
                        "manual_domains": list(manual),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "updated_by": "auto-learn",
                    },
                    merge=True,
                )
                logger.info(f"Auto-learned disposable domain: {domain}")

                # Invalidate cache so the domain is blocked immediately
                EmailValidationService._blocklist_cache = None
        except Exception as e:
            logger.error(f"Failed to auto-learn domain {domain}: {e}")

    def _mark_domain_clean(self, domain: str) -> None:
        """
        Record that a domain was verified clean by external APIs.

        Stored in verified_clean_domains dict keyed by domain.
        Future checks within VERIFIED_CLEAN_TTL will skip API calls.

        Note: Uses read-modify-write instead of dot notation because
        domain names contain dots which Firestore interprets as nested paths.
        """
        try:
            doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(
                BLOCKLIST_CONFIG_DOC
            )
            doc = doc_ref.get()
            verified = doc.to_dict().get("verified_clean_domains", {}) if doc.exists else {}
            verified[domain] = {
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            doc_ref.set({"verified_clean_domains": verified}, merge=True)
            logger.debug(f"Marked domain as verified clean: {domain}")

            # Invalidate cache so new data is picked up
            EmailValidationService._blocklist_cache = None
        except Exception as e:
            logger.error(f"Failed to mark domain clean {domain}: {e}")

    def is_email_blocked(self, email: str) -> bool:
        """
        Check if an email is explicitly blocked.

        Checks both the raw email and normalized version.

        Args:
            email: Email address to check

        Returns:
            True if the email is blocked
        """
        if not email:
            return False

        config = self.get_blocklist_config()
        email_lower = email.lower().strip()
        email_normalized = self.normalize_email(email)

        return (
            email_lower in config["blocked_emails"]
            or email_normalized in config["blocked_emails"]
        )

    def is_ip_blocked(self, ip_address: str) -> bool:
        """
        Check if an IP address is blocked.

        Args:
            ip_address: IP address to check

        Returns:
            True if the IP is blocked
        """
        if not ip_address:
            return False

        config = self.get_blocklist_config()
        return ip_address in config["blocked_ips"]

    def hash_ip(self, ip_address: str) -> str:
        """
        Hash an IP address for privacy-preserving storage.

        Args:
            ip_address: IP address to hash

        Returns:
            SHA-256 hash of the IP
        """
        return hashlib.sha256(ip_address.encode()).hexdigest()

    # -------------------------------------------------------------------------
    # Blocklist Management (Admin)
    # -------------------------------------------------------------------------

    def add_disposable_domain(self, domain: str, admin_email: str) -> bool:
        """Add a domain to the disposable domains blocklist."""
        domain = domain.lower().strip()
        if not domain:
            return False

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                domains = set(data.get("manual_domains", []))
            else:
                data = {}
                domains = set()

            domains.add(domain)
            data["manual_domains"] = list(domains)
            data["updated_at"] = datetime.now(timezone.utc)
            data["updated_by"] = admin_email
            transaction.set(doc_ref, data, merge=True)

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)

        # Invalidate cache
        EmailValidationService._blocklist_cache = None

        logger.info(f"Added disposable domain: {domain} by {admin_email}")
        return True

    def remove_disposable_domain(self, domain: str, admin_email: str) -> bool:
        """Remove a domain from the disposable domains blocklist."""
        domain = domain.lower().strip()

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
        result = {"found": False}

        @firestore.transactional
        def remove_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if not doc.exists:
                return

            data = doc.to_dict()

            manual = set(data.get("manual_domains", []))
            external = set(data.get("external_domains", []))

            if domain in manual:
                result["found"] = True
                manual.discard(domain)
                data["manual_domains"] = list(manual)
            elif domain in external:
                result["found"] = True
                allowlisted = set(data.get("allowlisted_domains", []))
                allowlisted.add(domain)
                data["allowlisted_domains"] = list(allowlisted)
            else:
                return

            data["updated_at"] = datetime.now(timezone.utc)
            data["updated_by"] = admin_email
            transaction.set(doc_ref, data, merge=True)

        transaction = self.db.transaction()
        remove_in_transaction(transaction, doc_ref)

        if not result["found"]:
            return False

        # Invalidate cache
        EmailValidationService._blocklist_cache = None

        logger.info(f"Removed disposable domain: {domain} by {admin_email}")
        return True

    def add_blocked_email(self, email: str, admin_email: str) -> bool:
        """Add an email to the blocked emails list."""
        email = email.lower().strip()
        if not email:
            return False

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                emails = set(data.get("blocked_emails", []))
            else:
                data = {}
                emails = set()

            emails.add(email)
            data["blocked_emails"] = list(emails)
            data["updated_at"] = datetime.now(timezone.utc)
            data["updated_by"] = admin_email
            transaction.set(doc_ref, data, merge=True)

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)

        # Invalidate cache
        EmailValidationService._blocklist_cache = None

        logger.info(f"Added blocked email: {email} by {admin_email}")
        return True

    def remove_blocked_email(self, email: str, admin_email: str) -> bool:
        """Remove an email from the blocked emails list."""
        email = email.lower().strip()

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
        result = {"found": False}

        @firestore.transactional
        def remove_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if not doc.exists:
                return

            data = doc.to_dict()
            emails = set(data.get("blocked_emails", []))

            if email not in emails:
                return

            result["found"] = True
            emails.discard(email)
            data["blocked_emails"] = list(emails)
            data["updated_at"] = datetime.now(timezone.utc)
            data["updated_by"] = admin_email
            transaction.set(doc_ref, data, merge=True)

        transaction = self.db.transaction()
        remove_in_transaction(transaction, doc_ref)

        if not result["found"]:
            return False

        # Invalidate cache
        EmailValidationService._blocklist_cache = None

        logger.info(f"Removed blocked email: {email} by {admin_email}")
        return True

    def add_blocked_ip(self, ip_address: str, admin_email: str) -> bool:
        """Add an IP address to the blocked IPs list."""
        ip_address = ip_address.strip()
        if not ip_address:
            return False

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                ips = set(data.get("blocked_ips", []))
            else:
                data = {}
                ips = set()

            ips.add(ip_address)
            data["blocked_ips"] = list(ips)
            data["updated_at"] = datetime.now(timezone.utc)
            data["updated_by"] = admin_email
            transaction.set(doc_ref, data, merge=True)

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)

        # Invalidate cache
        EmailValidationService._blocklist_cache = None

        logger.info(f"Added blocked IP: {ip_address} by {admin_email}")
        return True

    def remove_blocked_ip(self, ip_address: str, admin_email: str) -> bool:
        """Remove an IP address from the blocked IPs list."""
        ip_address = ip_address.strip()

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
        result = {"found": False}

        @firestore.transactional
        def remove_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if not doc.exists:
                return

            data = doc.to_dict()
            ips = set(data.get("blocked_ips", []))

            if ip_address not in ips:
                return

            result["found"] = True
            ips.discard(ip_address)
            data["blocked_ips"] = list(ips)
            data["updated_at"] = datetime.now(timezone.utc)
            data["updated_by"] = admin_email
            transaction.set(doc_ref, data, merge=True)

        transaction = self.db.transaction()
        remove_in_transaction(transaction, doc_ref)

        if not result["found"]:
            return False

        # Invalidate cache
        EmailValidationService._blocklist_cache = None

        logger.info(f"Removed blocked IP: {ip_address} by {admin_email}")
        return True

    def add_allowlisted_domain(self, domain: str, admin_email: str) -> bool:
        """Add a domain to the allowlisted domains list."""
        domain = domain.lower().strip()
        if not domain:
            return False

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                domains = set(data.get("allowlisted_domains", []))
            else:
                data = {}
                domains = set()

            domains.add(domain)
            data["allowlisted_domains"] = list(domains)
            data["updated_at"] = datetime.now(timezone.utc)
            data["updated_by"] = admin_email
            transaction.set(doc_ref, data, merge=True)

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)

        # Invalidate cache
        EmailValidationService._blocklist_cache = None

        logger.info(f"Added allowlisted domain: {domain} by {admin_email}")
        return True

    def remove_allowlisted_domain(self, domain: str, admin_email: str) -> bool:
        """Remove a domain from the allowlisted domains list."""
        domain = domain.lower().strip()

        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
        result = {"found": False}

        @firestore.transactional
        def remove_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if not doc.exists:
                return

            data = doc.to_dict()
            domains = set(data.get("allowlisted_domains", []))

            if domain not in domains:
                return

            result["found"] = True
            domains.discard(domain)
            data["allowlisted_domains"] = list(domains)
            data["updated_at"] = datetime.now(timezone.utc)
            data["updated_by"] = admin_email
            transaction.set(doc_ref, data, merge=True)

        transaction = self.db.transaction()
        remove_in_transaction(transaction, doc_ref)

        if not result["found"]:
            return False

        # Invalidate cache
        EmailValidationService._blocklist_cache = None

        logger.info(f"Removed allowlisted domain: {domain} by {admin_email}")
        return True

    def get_blocklist_raw_data(self) -> dict:
        """Get raw blocklist data for admin display (not the effective set)."""
        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
        doc = doc_ref.get()
        if not doc.exists:
            return {
                "external_domains": [], "manual_domains": [], "allowlisted_domains": [],
                "blocked_emails": [], "blocked_ips": [],
                "last_sync_at": None, "last_sync_count": None,
                "updated_at": None, "updated_by": None,
            }
        data = doc.to_dict()
        return {
            "external_domains": sorted(data.get("external_domains", [])),
            "manual_domains": sorted(data.get("manual_domains", [])),
            "allowlisted_domains": sorted(data.get("allowlisted_domains", [])),
            "blocked_emails": sorted(data.get("blocked_emails", [])),
            "blocked_ips": sorted(data.get("blocked_ips", [])),
            "last_sync_at": data.get("last_sync_at"),
            "last_sync_count": data.get("last_sync_count"),
            "updated_at": data.get("updated_at"),
            "updated_by": data.get("updated_by"),
        }

    def get_blocklist_stats(self) -> dict:
        """Get statistics about current blocklists."""
        config = self.get_blocklist_config(force_refresh=True)

        # Also get raw data for detailed counts
        doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            external_count = len(data.get("external_domains", []))
            manual_count = len(data.get("manual_domains", []))
            allowlisted_count = len(data.get("allowlisted_domains", []))
        else:
            external_count = 0
            manual_count = 0
            allowlisted_count = 0

        return {
            "disposable_domains_count": len(config["disposable_domains"]),
            "blocked_emails_count": len(config["blocked_emails"]),
            "blocked_ips_count": len(config["blocked_ips"]),
            "default_disposable_domains_count": len(DEFAULT_DISPOSABLE_DOMAINS),
            "external_domains_count": external_count,
            "manual_domains_count": manual_count,
            "allowlisted_domains_count": allowlisted_count,
        }


def get_email_validation_service(
    db: Optional[firestore.Client] = None,
) -> EmailValidationService:
    """Get the singleton EmailValidationService instance."""
    return EmailValidationService.get_instance(db)
