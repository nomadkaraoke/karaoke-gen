"""
Disposable Domain Sync Service.

Fetches and syncs disposable email domain blocklists from external sources.
"""

import logging
from datetime import datetime, timezone
from typing import Set

import httpx

from backend.services.email_validation_service import (
    BLOCKLISTS_COLLECTION,
    BLOCKLIST_CONFIG_DOC,
    DEFAULT_DISPOSABLE_DOMAINS,
)

logger = logging.getLogger(__name__)

EXTERNAL_BLOCKLIST_URL = (
    "https://raw.githubusercontent.com/disposable-email-domains/"
    "disposable-email-domains/refs/heads/main/disposable_email_blocklist.conf"
)
FETCH_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_DOMAIN_COUNT = 50_000


def parse_blocklist_text(text: str) -> Set[str]:
    """Parse newline-delimited blocklist text into a set of lowercase domains."""
    domains = set()
    for line in text.splitlines():
        domain = line.strip().lower()
        if domain:
            domains.add(domain)
    return domains


async def fetch_external_blocklist() -> Set[str]:
    """Fetch the external disposable domain blocklist from GitHub.

    Checks Content-Length header and response body size against safety limits.
    """
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
        response = await client.get(EXTERNAL_BLOCKLIST_URL)
        response.raise_for_status()

        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Response size {content_length} exceeds maximum {MAX_RESPONSE_BYTES}"
            )

        text = response.text
        if len(text.encode()) > MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Response body size exceeds maximum {MAX_RESPONSE_BYTES}"
            )

        domains = parse_blocklist_text(text)

        if len(domains) > MAX_DOMAIN_COUNT:
            raise ValueError(
                f"Domain count {len(domains)} exceeds maximum {MAX_DOMAIN_COUNT}"
            )

        logger.info(f"Fetched {len(domains)} domains from external blocklist")
        return domains


def sync_disposable_domains(db, external_domains: Set[str]) -> dict:
    """
    Sync external domains into Firestore.

    Handles first-run migration from old disposable_domains field,
    and cleans up redundant manual domains on subsequent runs.

    Note: This is a synchronous function (Firestore SDK is sync).
    Call from async code with `await asyncio.to_thread(sync_disposable_domains, db, domains)`.

    Returns a summary dict with counts.
    """
    doc_ref = db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
    doc = doc_ref.get()

    data = doc.to_dict() if doc.exists else {}
    result = {"external_count": len(external_domains), "added": 0, "removed": 0, "migrated_to_manual": []}

    is_migration = "external_domains" not in data

    if is_migration:
        old_domains = set(data.get("disposable_domains", [])) | DEFAULT_DISPOSABLE_DOMAINS
        manual_only = old_domains - external_domains
        result["migrated_to_manual"] = sorted(manual_only)

        new_data = {
            **data,  # preserve all existing fields
            "external_domains": sorted(external_domains),
            "manual_domains": sorted(manual_only),
            "allowlisted_domains": data.get("allowlisted_domains", []),
            "last_sync_at": datetime.now(timezone.utc),
            "last_sync_count": len(external_domains),
        }
        new_data.pop("disposable_domains", None)  # remove legacy field
        doc_ref.set(new_data)
    else:
        old_external = set(data.get("external_domains", []))
        manual_domains = set(data.get("manual_domains", []))

        result["added"] = len(external_domains - old_external)
        result["removed"] = len(old_external - external_domains)

        cleaned_manual = manual_domains - external_domains

        new_data = {
            **data,
            "external_domains": sorted(external_domains),
            "manual_domains": sorted(cleaned_manual),
            "last_sync_at": datetime.now(timezone.utc),
            "last_sync_count": len(external_domains),
        }
        doc_ref.set(new_data)

    logger.info(
        f"Sync complete: {result['external_count']} external domains, "
        f"{result['added']} added, {result['removed']} removed"
    )
    return result
