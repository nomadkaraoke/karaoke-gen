"""
Brand code allocation service using Firestore atomic counters.

Replaces the previous Dropbox folder-scanning approach which was susceptible to
TOCTOU race conditions when concurrent jobs read the same folder state.

Uses Firestore transactions to atomically allocate unique brand codes.
Supports recycling numbers when E2E cleanup deletes test brand codes.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

from backend.config import settings

logger = logging.getLogger(__name__)

BRAND_CODE_COUNTERS_COLLECTION = "brand_code_counters"

# Singleton instance
_brand_code_service: Optional["BrandCodeService"] = None


class BrandCodeService:
    """Atomic brand code allocation using Firestore counters."""

    def __init__(self, db: Optional[firestore.Client] = None):
        if db is None:
            self.db = firestore.Client(project=settings.google_cloud_project)
        else:
            self.db = db

    def allocate_brand_code(self, prefix: str, dropbox_path: str) -> str:
        """
        Atomically allocate the next brand code for a given prefix.

        Uses a Firestore transaction to ensure no two concurrent callers
        receive the same number. If recycled numbers are available (from
        E2E cleanup), the smallest recycled number is reused first.

        On first use for a prefix, initializes the counter by scanning
        Dropbox to find the current max number.

        Args:
            prefix: Brand prefix (e.g., "NOMAD" or "NOMADNP")
            dropbox_path: Dropbox path for one-time initialization scan

        Returns:
            Brand code string (e.g., "NOMAD-1264")
        """
        doc_ref = self.db.collection(BRAND_CODE_COUNTERS_COLLECTION).document(prefix)

        @firestore.transactional
        def allocate_in_transaction(transaction):
            doc = doc_ref.get(transaction=transaction)

            if not doc.exists:
                # First use — initialize from Dropbox scan
                next_number = self._get_initial_next_number(prefix, dropbox_path)
                allocated = next_number
                transaction.set(doc_ref, {
                    "prefix": prefix,
                    "next_number": next_number + 1,
                    "recycled": [],
                    "initialized_at": datetime.now(timezone.utc),
                    "last_updated": datetime.now(timezone.utc),
                })
                return allocated

            data = doc.to_dict()
            recycled = data.get("recycled", [])

            if recycled:
                # Reuse smallest recycled number
                recycled.sort()
                allocated = recycled.pop(0)
                transaction.update(doc_ref, {
                    "recycled": recycled,
                    "last_updated": datetime.now(timezone.utc),
                })
            else:
                # Allocate next sequential number
                allocated = data["next_number"]
                transaction.update(doc_ref, {
                    "next_number": allocated + 1,
                    "last_updated": datetime.now(timezone.utc),
                })

            return allocated

        transaction = self.db.transaction()
        number = allocate_in_transaction(transaction)
        brand_code = f"{prefix}-{number:04d}"
        logger.info(f"Allocated brand code: {brand_code}")
        return brand_code

    def recycle_brand_code(self, prefix: str, number: int) -> None:
        """
        Return a brand code number to the recycled pool.

        Called when E2E cleanup deletes a brand code's distribution.
        The number will be reused on the next allocation for this prefix.

        Args:
            prefix: Brand prefix (e.g., "NOMAD")
            number: The numeric portion to recycle (e.g., 1234)
        """
        doc_ref = self.db.collection(BRAND_CODE_COUNTERS_COLLECTION).document(prefix)

        @firestore.transactional
        def recycle_in_transaction(transaction):
            doc = doc_ref.get(transaction=transaction)

            if not doc.exists:
                # Counter doesn't exist yet — nothing to recycle into.
                # The number will naturally be below next_number when
                # the counter is initialized, so it would create a gap.
                # Just log and skip.
                logger.warning(
                    f"Cannot recycle {prefix}-{number:04d}: counter doc doesn't exist"
                )
                return

            data = doc.to_dict()
            next_number = data.get("next_number", 0)

            if number <= 0 or number >= next_number:
                logger.warning(
                    f"Cannot recycle {prefix}-{number:04d}: "
                    f"invalid (next_number={next_number})"
                )
                return

            recycled = data.get("recycled", [])

            if number not in recycled:
                recycled.append(number)
                transaction.update(doc_ref, {
                    "recycled": recycled,
                    "last_updated": datetime.now(timezone.utc),
                })
                logger.info(f"Recycled brand code number: {prefix}-{number:04d}")
            else:
                logger.warning(f"Number {number} already in recycled pool for {prefix}")

        transaction = self.db.transaction()
        recycle_in_transaction(transaction)

    def _get_initial_next_number(self, prefix: str, dropbox_path: str) -> int:
        """
        Scan Dropbox once to determine the starting next_number.

        This is only called when the counter document doesn't exist yet
        (one-time migration from the old Dropbox-scanning approach).

        Args:
            prefix: Brand prefix to scan for
            dropbox_path: Dropbox path containing organized folders

        Returns:
            The next number to allocate (max existing + 1, minimum 1)
        """
        try:
            from backend.services.dropbox_service import get_dropbox_service

            dropbox = get_dropbox_service()
            if not dropbox.is_configured:
                logger.warning(
                    f"Dropbox not configured for initialization of {prefix} counter, starting at 1"
                )
                return 1

            folders = dropbox.list_folders(dropbox_path)
            pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{4}})")

            existing_nums: set[int] = set()
            for folder in folders:
                match = pattern.match(folder)
                if match:
                    existing_nums.add(int(match.group(1)))

            if existing_nums:
                next_num = max(existing_nums) + 1
                logger.info(
                    f"Initialized {prefix} counter from Dropbox: "
                    f"max existing={max(existing_nums)}, next_number={next_num}"
                )
                return next_num

            logger.info(f"No existing {prefix} codes found in Dropbox, starting at 1")
            return 1

        except Exception as e:
            logger.error(f"Failed to initialize {prefix} counter from Dropbox: {e}")
            raise

    @staticmethod
    def parse_brand_code(brand_code: str) -> tuple[str, int]:
        """
        Parse a brand code string into prefix and number.

        Args:
            brand_code: e.g., "NOMAD-1234" or "NOMADNP-0012"

        Returns:
            Tuple of (prefix, number), e.g., ("NOMAD", 1234)

        Raises:
            ValueError: If brand_code doesn't match expected format
        """
        match = re.match(r"^([A-Z]+)-(\d+)$", brand_code)
        if not match:
            raise ValueError(f"Invalid brand code format: {brand_code}")
        return match.group(1), int(match.group(2))


def is_preserved_brand_code(job, brand_code: Optional[str]) -> bool:
    """True when ``brand_code`` came from the job itself (``keep_brand_code`` — e.g. a
    tenant's own catalogue code like "VS6588"), not from the allocator.

    Such codes must never be recycled into a counter: a tenant code that happens to
    look like an allocated one (e.g. "VSTAR-0005") would otherwise put that number
    back in the pool and cause a duplicate later.
    """
    kept = getattr(job, "keep_brand_code", None)
    return bool(kept) and kept == brand_code


def get_brand_code_service() -> BrandCodeService:
    """Get or create the singleton BrandCodeService instance."""
    global _brand_code_service
    if _brand_code_service is None:
        _brand_code_service = BrandCodeService()
    return _brand_code_service
