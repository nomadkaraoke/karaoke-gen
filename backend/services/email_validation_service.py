"""
Email Validation Service.

Provides email validation, normalization, and blocklist checking
to prevent abuse during enrollment and other flows.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Tuple, Set

from google.cloud import firestore

from backend.config import settings

logger = logging.getLogger(__name__)


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

# Default disposable email domains (can be extended via admin UI)
DEFAULT_DISPOSABLE_DOMAINS = {
    # Popular temporary email services
    "10minutemail.com",
    "10minutemail.net",
    "guerrillamail.com",
    "guerrillamail.org",
    "guerrillamail.net",
    "guerrillamail.biz",
    "guerrillamailblock.com",
    "tempmail.com",
    "tempmail.net",
    "temp-mail.org",
    "temp-mail.io",
    "throwawaymail.com",
    "mailinator.com",
    "mailinator.net",
    "mailinator.org",
    "mailinator.info",
    "maildrop.cc",
    "yopmail.com",
    "yopmail.fr",
    "yopmail.net",
    "fakeinbox.com",
    "fakemailgenerator.com",
    "dispostable.com",
    "getairmail.com",
    "getnada.com",
    "mohmal.com",
    "trashmail.com",
    "trashmail.net",
    "trashmail.org",
    "sharklasers.com",
    "grr.la",
    "guerrillamail.de",
    "pokemail.net",
    "spam4.me",
    "spamgourmet.com",
    "mytrashmail.com",
    "mailnesia.com",
    "mailcatch.com",
    "mintemail.com",
    "tempr.email",
    "tempail.com",
    "emailondeck.com",
    "incognitomail.com",
    "inboxalias.com",
    "33mail.com",
    "spamex.com",
    "spamfree24.org",
    "spamspot.com",
    "mailnull.com",
    "mailsac.com",
    "emailfake.com",
    "fakemail.fr",
    "tempinbox.com",
    "throwaway.email",
    "burnermail.io",
    "anonbox.net",
    "anonymbox.com",
    "discard.email",
    "discardmail.com",
    "discardmail.de",
    "mailexpire.com",
    "mailforspam.com",
    "meltmail.com",
    "mt2009.com",
    "mt2014.com",
    "nospam.ze.tc",
    "nospamfor.us",
    "nowmymail.com",
    "receiveee.com",
    "safe-mail.net",
    "spamavert.com",
    "spambob.com",
    "spambog.com",
    "spambox.us",
    "spamcannon.com",
    "spamcannon.net",
    "spamcon.org",
    "spamcorptastic.com",
    "spamday.com",
    "spamfree.eu",
    "spamherelots.com",
    "spamhereplease.com",
    "spamhole.com",
    "spamify.com",
    "spaminator.de",
    "spamkill.info",
    "spaml.com",
    "spaml.de",
    "spamoff.de",
    "spamobox.com",
    "spamslicer.com",
    "spamstack.net",
    "spamthis.co.uk",
    "spamthisplease.com",
    "supergreatmail.com",
    "suremail.info",
    "teleworm.us",
    "tempemail.co.za",
    "tempemail.net",
    "tempmailaddress.com",
    "tempmailo.com",
    "thankyou2010.com",
    "thisisnotmyrealemail.com",
    "tm.slsrs.ru",
    "tmpeml.info",
    "trash-mail.at",
    "trash-mail.de",
    "trash2009.com",
    "trashemail.de",
    "trashmail.at",
    "trashmailer.com",
    "wegwerfmail.de",
    "wegwerfmail.net",
    "wegwerfmail.org",
    "wh4f.org",
    "willhackforfood.biz",
    "willselfdestruct.com",
    "xmaily.com",
    "xyzfree.net",
    "yep.it",
    "yogamaven.com",
    "yuurok.com",
    "zehnminutenmail.de",
    "zippymail.info",
    # Newer disposable services
    "emailhook.site",
    # Additional common ones
    "mailnator.com",
    "bugmenot.com",
    "dodgeit.com",
    "dodgit.com",
    "e4ward.com",
    "emailsensei.com",
    "hushmail.com",
    "jetable.org",
    "kasmail.com",
    "mailblock.net",
    "mailcatch.com",
    "mymailoasis.com",
    "nervmich.net",
    "nervtmansen.de",
    "oneoffemail.com",
    "pookmail.com",
    "shortmail.net",
    "sneakemail.com",
    "sogetthis.com",
    "tempemailaddress.com",
    "tempomail.fr",
    "temporaryemail.net",
    "temporaryforwarding.com",
    "temporaryinbox.com",
    "thankyou2010.com",
    "tyldd.com",
    "uggsrock.com",
    "veryrealemail.com",
    "yourewronghereswhy.com",
}

# Gmail-like domains that support alias normalization
GMAIL_LIKE_DOMAINS = {
    "gmail.com",
    "googlemail.com",
}


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
            config = {
                "disposable_domains": set(data.get("disposable_domains", [])) | DEFAULT_DISPOSABLE_DOMAINS,
                "blocked_emails": set(data.get("blocked_emails", [])),
                "blocked_ips": set(data.get("blocked_ips", [])),
            }
        else:
            # Use defaults if no config exists
            config = {
                "disposable_domains": DEFAULT_DISPOSABLE_DOMAINS.copy(),
                "blocked_emails": set(),
                "blocked_ips": set(),
            }

        # Update cache
        EmailValidationService._blocklist_cache = config
        EmailValidationService._blocklist_cache_time = now

        return config

    def is_disposable_domain(self, email: str) -> bool:
        """
        Check if an email uses a disposable domain.

        Args:
            email: Email address to check

        Returns:
            True if the domain is known to be disposable
        """
        if not email or "@" not in email:
            return False

        domain = email.lower().split("@")[-1]
        config = self.get_blocklist_config()
        return domain in config["disposable_domains"]

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
                domains = set(data.get("disposable_domains", []))
            else:
                data = {}
                domains = set()

            domains.add(domain)
            data["disposable_domains"] = list(domains)
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
            domains = set(data.get("disposable_domains", []))

            if domain not in domains:
                return

            result["found"] = True
            domains.discard(domain)
            data["disposable_domains"] = list(domains)
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

    def get_blocklist_stats(self) -> dict:
        """Get statistics about current blocklists."""
        config = self.get_blocklist_config(force_refresh=True)
        return {
            "disposable_domains_count": len(config["disposable_domains"]),
            "blocked_emails_count": len(config["blocked_emails"]),
            "blocked_ips_count": len(config["blocked_ips"]),
            "default_disposable_domains_count": len(DEFAULT_DISPOSABLE_DOMAINS),
        }


def get_email_validation_service(
    db: Optional[firestore.Client] = None,
) -> EmailValidationService:
    """Get the singleton EmailValidationService instance."""
    return EmailValidationService.get_instance(db)
