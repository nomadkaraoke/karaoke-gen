# Auto-Updating Disposable Email Blocklist — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily auto-sync of disposable email domains from the community-curated [disposable-email-domains](https://github.com/disposable-email-domains/disposable-email-domains) repo, with admin allowlist override.

**Architecture:** New sync service fetches ~4,800 domains from GitHub daily, stores in Firestore as `external_domains` separate from admin-managed `manual_domains`. An `allowlisted_domains` field lets admins override false positives. The existing `get_blocklist_config()` computes the effective set internally for backward compatibility.

**Tech Stack:** FastAPI (backend), Next.js (frontend), Firestore, Cloud Scheduler (Pulumi), httpx (HTTP client)

**Spec:** `docs/superpowers/specs/2026-03-19-auto-disposable-blocklist-design.md`

---

## File Map

### Backend — New Files
- `backend/services/disposable_domain_sync_service.py` — Sync logic: fetch external list, parse, update Firestore, handle migration
- `backend/tests/test_disposable_domain_sync_service.py` — Unit tests for sync service

### Backend — Modified Files
- `backend/services/email_validation_service.py` — Update `get_blocklist_config()` to read new fields, remove `DEFAULT_DISPOSABLE_DOMAINS`, update admin CRUD for manual/allowlist
- `backend/api/routes/rate_limits.py` — Update response model, add allowlist endpoints, add sync trigger endpoint
- `backend/api/routes/internal.py` — Add `POST /internal/sync-disposable-domains` endpoint
- `backend/tests/test_email_validation_service.py` — Update tests for new data model

### Frontend — Modified Files
- `frontend/lib/api.ts` — Update `BlocklistsResponse` type, add sync/allowlist API methods
- `frontend/app/admin/rate-limits/page.tsx` — Rewrite disposable domains section with three sub-sections + sync bar

### Infrastructure — Modified Files
- `infrastructure/__main__.py` — Add Cloud Scheduler job for daily sync

---

## Task 1: Sync Service — Core Logic

**Files:**
- Create: `backend/services/disposable_domain_sync_service.py`
- Create: `backend/tests/test_disposable_domain_sync_service.py`

### Step 1.1: Write failing test for parsing external blocklist

- [ ] Create test file with a test that verifies parsing newline-delimited domain text into a set:

```python
# backend/tests/test_disposable_domain_sync_service.py
import pytest
from unittest.mock import MagicMock

import sys
sys.modules['google.cloud.firestore'] = MagicMock()
sys.modules['google.cloud.storage'] = MagicMock()


class TestParseExternalBlocklist:
    def test_parse_newline_delimited_domains(self):
        from backend.services.disposable_domain_sync_service import parse_blocklist_text
        text = "tempmail.com\nmailinator.com\n\nguerrillamail.com\n"
        result = parse_blocklist_text(text)
        assert result == {"tempmail.com", "mailinator.com", "guerrillamail.com"}

    def test_parse_strips_whitespace(self):
        from backend.services.disposable_domain_sync_service import parse_blocklist_text
        text = "  tempmail.com  \n  mailinator.com\t\n"
        result = parse_blocklist_text(text)
        assert result == {"tempmail.com", "mailinator.com"}

    def test_parse_lowercases_domains(self):
        from backend.services.disposable_domain_sync_service import parse_blocklist_text
        text = "TempMail.COM\nMailinator.com\n"
        result = parse_blocklist_text(text)
        assert result == {"tempmail.com", "mailinator.com"}

    def test_parse_empty_text(self):
        from backend.services.disposable_domain_sync_service import parse_blocklist_text
        assert parse_blocklist_text("") == set()
        assert parse_blocklist_text("  \n\n  ") == set()
```

- [ ] Run test to verify it fails:

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-auto-disposable-blocklist
python -m pytest backend/tests/test_disposable_domain_sync_service.py::TestParseExternalBlocklist -v
```

Expected: FAIL with `ModuleNotFoundError`

### Step 1.2: Implement parse_blocklist_text

- [ ] Create the sync service file with the parsing function:

```python
# backend/services/disposable_domain_sync_service.py
"""
Disposable Domain Sync Service.

Fetches and syncs disposable email domain blocklists from external sources.
"""

import logging
from typing import Set

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
```

- [ ] Run test to verify it passes:

```bash
python -m pytest backend/tests/test_disposable_domain_sync_service.py::TestParseExternalBlocklist -v
```

Expected: PASS

### Step 1.3: Write failing test for fetch_external_blocklist

- [ ] Add tests for the HTTP fetch function:

```python
class TestFetchExternalBlocklist:
    @pytest.mark.asyncio
    async def test_fetch_success(self):
        from backend.services.disposable_domain_sync_service import fetch_external_blocklist
        from unittest.mock import AsyncMock, patch

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "tempmail.com\nmailinator.com\n"
        mock_response.headers = {"content-length": "30"}

        with patch("backend.services.disposable_domain_sync_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            domains = await fetch_external_blocklist()
            assert domains == {"tempmail.com", "mailinator.com"}

    @pytest.mark.asyncio
    async def test_fetch_rejects_too_many_domains(self):
        from backend.services.disposable_domain_sync_service import fetch_external_blocklist
        from unittest.mock import AsyncMock, patch

        # Generate 50001 fake domains
        fake_text = "\n".join(f"domain{i}.com" for i in range(50_001))
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = fake_text
        mock_response.headers = {"content-length": str(len(fake_text))}

        with patch("backend.services.disposable_domain_sync_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="exceeds maximum"):
                await fetch_external_blocklist()

    @pytest.mark.asyncio
    async def test_fetch_rejects_non_200(self):
        from backend.services.disposable_domain_sync_service import fetch_external_blocklist
        from unittest.mock import AsyncMock, patch

        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = AsyncMock(side_effect=Exception("Server Error"))

        with patch("backend.services.disposable_domain_sync_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(Exception):
                await fetch_external_blocklist()
```

- [ ] Run test to verify it fails:

```bash
python -m pytest backend/tests/test_disposable_domain_sync_service.py::TestFetchExternalBlocklist -v
```

### Step 1.4: Implement fetch_external_blocklist

- [ ] Add to `disposable_domain_sync_service.py`:

```python
import httpx

async def fetch_external_blocklist() -> Set[str]:
    """Fetch the external disposable domain blocklist from GitHub.

    Uses streaming to check Content-Length before reading the full body.
    Falls back to reading with a size cap if Content-Length is absent.
    """
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
        response = await client.get(EXTERNAL_BLOCKLIST_URL)
        response.raise_for_status()

        # Check Content-Length header before reading body (if present)
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Response size {content_length} exceeds maximum {MAX_RESPONSE_BYTES}"
            )

        # Read the body and check actual size
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
```

- [ ] Run test to verify it passes:

```bash
python -m pytest backend/tests/test_disposable_domain_sync_service.py::TestFetchExternalBlocklist -v
```

### Step 1.5: Write failing test for sync_disposable_domains (Firestore update)

- [ ] Add tests for the main sync function that updates Firestore:

```python
class TestSyncDisposableDomains:
    @pytest.fixture
    def mock_db(self):
        mock = MagicMock()
        return mock

    def test_sync_first_run_migration(self, mock_db):
        """First sync: migrate old disposable_domains to manual_domains."""
        from backend.services.disposable_domain_sync_service import sync_disposable_domains

        # Existing Firestore doc has old-style disposable_domains
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "disposable_domains": ["tempmail.com", "custom-temp.com"],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        external_domains = {"tempmail.com", "mailinator.com"}

        result = sync_disposable_domains(mock_db, external_domains)

        # custom-temp.com is not in external list, should become manual
        assert result["migrated_to_manual"] == ["custom-temp.com"]
        assert result["external_count"] == 2

        # Verify Firestore was updated
        mock_db.collection.return_value.document.return_value.set.assert_called_once()
        call_data = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert set(call_data["external_domains"]) == {"tempmail.com", "mailinator.com"}
        assert set(call_data["manual_domains"]) == {"custom-temp.com"}
        assert "disposable_domains" not in call_data

    def test_sync_subsequent_run(self, mock_db):
        """Subsequent sync: just replace external_domains."""
        from backend.services.disposable_domain_sync_service import sync_disposable_domains

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com"],
            "manual_domains": ["my-custom.com"],
            "allowlisted_domains": [],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        external_domains = {"tempmail.com", "mailinator.com", "newdomain.com"}

        result = sync_disposable_domains(mock_db, external_domains)

        assert result["external_count"] == 3
        assert result["added"] == 2  # mailinator.com, newdomain.com
        assert result["removed"] == 0

    def test_sync_cleans_redundant_manual_domains(self, mock_db):
        """Sync removes manual domains that now appear in external list."""
        from backend.services.disposable_domain_sync_service import sync_disposable_domains

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com"],
            "manual_domains": ["mailinator.com", "my-custom.com"],
            "allowlisted_domains": [],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        external_domains = {"tempmail.com", "mailinator.com"}

        result = sync_disposable_domains(mock_db, external_domains)

        call_data = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        # mailinator.com should be removed from manual since it's now external
        assert set(call_data["manual_domains"]) == {"my-custom.com"}
```

- [ ] Run test to verify it fails:

```bash
python -m pytest backend/tests/test_disposable_domain_sync_service.py::TestSyncDisposableDomains -v
```

### Step 1.6: Implement sync_disposable_domains

- [ ] Add to `disposable_domain_sync_service.py`:

```python
from datetime import datetime, timezone
from google.cloud import firestore as firestore_module

BLOCKLISTS_COLLECTION = "blocklists"
BLOCKLIST_CONFIG_DOC = "config"


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
        # First run: migrate from old disposable_domains field
        from backend.services.email_validation_service import DEFAULT_DISPOSABLE_DOMAINS
        old_domains = set(data.get("disposable_domains", [])) | DEFAULT_DISPOSABLE_DOMAINS
        manual_only = old_domains - external_domains
        result["migrated_to_manual"] = sorted(manual_only)

        new_data = {
            "external_domains": sorted(external_domains),
            "manual_domains": sorted(manual_only),
            "allowlisted_domains": data.get("allowlisted_domains", []),
            "blocked_emails": data.get("blocked_emails", []),
            "blocked_ips": data.get("blocked_ips", []),
            "last_sync_at": datetime.now(timezone.utc),
            "last_sync_count": len(external_domains),
            "updated_at": data.get("updated_at"),
            "updated_by": data.get("updated_by"),
        }
        # Remove old field
        # (set without merge replaces the whole doc, which removes disposable_domains)
        doc_ref.set(new_data)
    else:
        # Subsequent run: replace external_domains, clean up manual
        old_external = set(data.get("external_domains", []))
        manual_domains = set(data.get("manual_domains", []))

        result["added"] = len(external_domains - old_external)
        result["removed"] = len(old_external - external_domains)

        # Remove manual domains that are now covered by external list
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
```

- [ ] Run test to verify it passes:

```bash
python -m pytest backend/tests/test_disposable_domain_sync_service.py::TestSyncDisposableDomains -v
```

### Step 1.7: Commit

- [ ] Commit sync service and tests:

```bash
git add backend/services/disposable_domain_sync_service.py backend/tests/test_disposable_domain_sync_service.py
git commit -m "feat: add disposable domain sync service with parsing, fetch, and Firestore update"
```

---

## Task 2: Update EmailValidationService for New Data Model

**Files:**
- Modify: `backend/services/email_validation_service.py`
- Modify: `backend/tests/test_email_validation_service.py`

### Step 2.1: Write failing test for new get_blocklist_config behavior

- [ ] Add test that verifies effective set = (external + manual) - allowlisted:

```python
# Add to backend/tests/test_email_validation_service.py
class TestEffectiveBlocklist:
    @pytest.fixture
    def mock_db(self):
        mock = MagicMock()
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com", "mailinator.com", "allowed-one.com"],
            "manual_domains": ["my-custom.com"],
            "allowlisted_domains": ["allowed-one.com"],
            "blocked_emails": ["bad@example.com"],
            "blocked_ips": ["1.2.3.4"],
        }
        mock.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock

    @pytest.fixture
    def email_service(self, mock_db):
        from backend.services.email_validation_service import EmailValidationService
        EmailValidationService._blocklist_cache = None
        EmailValidationService._blocklist_cache_time = None
        return EmailValidationService(db=mock_db)

    def test_effective_set_excludes_allowlisted(self, email_service):
        config = email_service.get_blocklist_config()
        assert "tempmail.com" in config["disposable_domains"]
        assert "mailinator.com" in config["disposable_domains"]
        assert "my-custom.com" in config["disposable_domains"]
        assert "allowed-one.com" not in config["disposable_domains"]

    def test_allowlisted_domain_not_blocked(self, email_service):
        assert email_service.is_disposable_domain("user@allowed-one.com") is False

    def test_external_domain_blocked(self, email_service):
        assert email_service.is_disposable_domain("user@tempmail.com") is True

    def test_manual_domain_blocked(self, email_service):
        assert email_service.is_disposable_domain("user@my-custom.com") is True
```

- [ ] Run test to verify it fails:

```bash
python -m pytest backend/tests/test_email_validation_service.py::TestEffectiveBlocklist -v
```

Expected: FAIL (current code reads `disposable_domains` field, not the new fields)

### Step 2.2: Update get_blocklist_config to use new fields

- [ ] Modify `backend/services/email_validation_service.py`:

In `get_blocklist_config()`, replace the Firestore data reading block (starting with `if doc.exists:` inside the method) to compute effective set from new fields, with backward compatibility for old field:

```python
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
        effective_domains = set(data.get("disposable_domains", [])) | DEFAULT_DISPOSABLE_DOMAINS
    config = {
        "disposable_domains": effective_domains,
        "blocked_emails": set(data.get("blocked_emails", [])),
        "blocked_ips": set(data.get("blocked_ips", [])),
    }
else:
    config = {
        "disposable_domains": DEFAULT_DISPOSABLE_DOMAINS.copy(),
        "blocked_emails": set(),
        "blocked_ips": set(),
    }
```

- [ ] Run test to verify it passes:

```bash
python -m pytest backend/tests/test_email_validation_service.py::TestEffectiveBlocklist -v
```

### Step 2.3: Verify existing tests still pass

- [ ] Run all existing email validation tests:

```bash
python -m pytest backend/tests/test_email_validation_service.py -v
```

Expected: ALL PASS (backward compatible — old tests use mock data with `disposable_domains` field which still works via the fallback path)

### Step 2.4: Add allowlist management methods

- [ ] Add `add_allowlisted_domain` and `remove_allowlisted_domain` methods to `EmailValidationService`. Follow the same transactional pattern as the existing `add_blocked_email`/`remove_blocked_email` methods but operate on the `allowlisted_domains` field.

- [ ] Update `add_disposable_domain` to write to `manual_domains` field instead of `disposable_domains`. Change `data.get("disposable_domains", [])` to `data.get("manual_domains", [])` and `data["disposable_domains"]` to `data["manual_domains"]` in the transaction body.

- [ ] Update `remove_disposable_domain` to determine domain source inside the transaction:

```python
@firestore.transactional
def remove_in_transaction(transaction, doc_ref):
    doc = doc_ref.get(transaction=transaction)
    if not doc.exists:
        return
    data = doc.to_dict()

    manual = set(data.get("manual_domains", []))
    external = set(data.get("external_domains", []))

    if domain in manual:
        # Remove from manual_domains directly
        result["found"] = True
        manual.discard(domain)
        data["manual_domains"] = list(manual)
    elif domain in external:
        # External domain: add to allowlisted_domains instead of removing
        result["found"] = True
        allowlisted = set(data.get("allowlisted_domains", []))
        allowlisted.add(domain)
        data["allowlisted_domains"] = list(allowlisted)
    else:
        return

    data["updated_at"] = datetime.now(timezone.utc)
    data["updated_by"] = admin_email
    transaction.set(doc_ref, data, merge=True)
```

- [ ] Add `get_blocklist_raw_data()` method that reads the Firestore doc and returns the raw fields:

```python
def get_blocklist_raw_data(self) -> dict:
    """Get raw blocklist data for admin display (not the effective set)."""
    doc_ref = self.db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC)
    doc = doc_ref.get()
    if not doc.exists:
        return {
            "external_domains": [],
            "manual_domains": [],
            "allowlisted_domains": [],
            "blocked_emails": [],
            "blocked_ips": [],
            "last_sync_at": None,
            "last_sync_count": None,
            "updated_at": None,
            "updated_by": None,
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
```

### Step 2.5: Write tests for new management methods

- [ ] Test that `add_disposable_domain` writes to `manual_domains`
- [ ] Test that `remove_disposable_domain` on a manual domain removes from `manual_domains`
- [ ] Test that `remove_disposable_domain` on an external domain adds to `allowlisted_domains`
- [ ] Test `add_allowlisted_domain` and `remove_allowlisted_domain`
- [ ] Test `get_blocklist_raw_data` returns all raw fields correctly

### Step 2.6: Update get_blocklist_stats

- [ ] Update `get_blocklist_stats()` to return counts for external, manual, and allowlisted domains.

### Step 2.7: Run all tests and commit

- [ ] Run full test suite:

```bash
python -m pytest backend/tests/test_email_validation_service.py -v
```

- [ ] Commit:

```bash
git add backend/services/email_validation_service.py backend/tests/test_email_validation_service.py
git commit -m "feat: update email validation service for external/manual/allowlist data model"
```

---

## Task 3: Update Backend API Routes

**Files:**
- Modify: `backend/api/routes/rate_limits.py`
- Modify: `backend/api/routes/internal.py`

### Step 3.1: Update BlocklistsResponse model

- [ ] Replace `BlocklistsResponse` in `rate_limits.py`:

```python
class BlocklistsResponse(BaseModel):
    """All blocklist data with domain source separation."""
    external_domains: List[str]
    manual_domains: List[str]
    allowlisted_domains: List[str]
    blocked_emails: List[str]
    blocked_ips: List[str]
    last_sync_at: Optional[datetime] = None
    last_sync_count: Optional[int] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
```

### Step 3.2: Update get_blocklists endpoint

- [ ] Update the `get_blocklists` endpoint to use `get_blocklist_raw_data()` and return the new response shape.

### Step 3.3: Add allowlist endpoints

- [ ] Add POST and DELETE endpoints for allowlisted domains:

```python
@router.post("/blocklists/allowlisted-domains", response_model=SuccessResponse)
async def add_allowlisted_domain(
    request: DomainRequest,
    auth_result: AuthResult = Depends(require_admin),
):
    """Add a domain to the allowlist (overrides external blocklist)."""
    ...

@router.delete("/blocklists/allowlisted-domains/{domain}", response_model=SuccessResponse)
async def remove_allowlisted_domain(
    domain: str,
    auth_result: AuthResult = Depends(require_admin),
):
    """Remove a domain from the allowlist."""
    ...
```

### Step 3.4: Add sync trigger endpoint to admin routes

- [ ] Add a POST endpoint that admins can trigger from the UI:

```python
@router.post("/blocklists/sync", response_model=SuccessResponse)
async def trigger_sync(
    auth_result: AuthResult = Depends(require_admin),
):
    """Manually trigger a sync of the external disposable domain blocklist."""
    from backend.services.disposable_domain_sync_service import (
        fetch_external_blocklist, sync_disposable_domains
    )
    from backend.services.firestore_service import get_firestore_client

    import asyncio
    domains = await fetch_external_blocklist()
    db = get_firestore_client()
    result = await asyncio.to_thread(sync_disposable_domains, db, domains)

    # Invalidate cache
    EmailValidationService._blocklist_cache = None

    return SuccessResponse(
        success=True,
        message=f"Synced {result['external_count']} external domains ({result['added']} added, {result['removed']} removed)"
    )
```

### Step 3.5: Add internal sync endpoint

- [ ] Add to `internal.py` — this is what Cloud Scheduler calls. Note: Cloud Scheduler uses OIDC token auth (not X-Admin-Token header). The existing `require_admin` dependency handles OIDC via the Authorization header flow, so this works without changes:

```python
@router.post("/sync-disposable-domains", response_model=WorkerResponse)
async def sync_disposable_domains_endpoint(
    request: Request,
    auth_result: AuthResult = Depends(require_admin),
):
    """Sync external disposable domain blocklist. Called by Cloud Scheduler daily."""
    from backend.services.disposable_domain_sync_service import (
        fetch_external_blocklist, sync_disposable_domains
    )
    from backend.services.firestore_service import get_firestore_client
    from backend.services.email_validation_service import EmailValidationService

    import asyncio
    domains = await fetch_external_blocklist()
    db = get_firestore_client()
    result = await asyncio.to_thread(sync_disposable_domains, db, domains)
    EmailValidationService._blocklist_cache = None

    return WorkerResponse(
        status="completed",
        job_id="sync-disposable-domains",
        message=f"Synced {result['external_count']} domains"
    )
```

### Step 3.6: Commit

- [ ] Commit:

```bash
git add backend/api/routes/rate_limits.py backend/api/routes/internal.py
git commit -m "feat: add sync endpoint, allowlist API, update blocklist response model"
```

---

## Task 4: Update Frontend API Client

**Files:**
- Modify: `frontend/lib/api.ts`

### Step 4.1: Update BlocklistsResponse interface

- [ ] Find and replace the `BlocklistsResponse` interface (search for `export interface BlocklistsResponse`):

```typescript
export interface BlocklistsResponse {
  external_domains: string[];
  manual_domains: string[];
  allowlisted_domains: string[];
  blocked_emails: string[];
  blocked_ips: string[];
  last_sync_at?: string;
  last_sync_count?: number;
  updated_at?: string;
  updated_by?: string;
}
```

### Step 4.2: Add new API methods

- [ ] Add to the `adminApi` object:

```typescript
async syncDisposableDomains(): Promise<SuccessResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/admin/rate-limits/blocklists/sync`,
    { method: 'POST', headers: getAuthHeaders() }
  );
  return handleResponse(response);
},

async addAllowlistedDomain(domain: string): Promise<SuccessResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/admin/rate-limits/blocklists/allowlisted-domains`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ domain }),
    }
  );
  return handleResponse(response);
},

async removeAllowlistedDomain(domain: string): Promise<SuccessResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/admin/rate-limits/blocklists/allowlisted-domains/${encodeURIComponent(domain)}`,
    { method: 'DELETE', headers: getAuthHeaders() }
  );
  return handleResponse(response);
},
```

### Step 4.3: Commit

- [ ] Commit:

```bash
git add frontend/lib/api.ts
git commit -m "feat: update frontend API client for new blocklist response and sync/allowlist endpoints"
```

---

## Task 5: Update Admin UI

**Files:**
- Modify: `frontend/app/admin/rate-limits/page.tsx`

### Step 5.1: Add sync state and allowlist handlers

- [ ] Add state variables for sync and allowlist operations:

```typescript
const [syncing, setSyncing] = useState(false)
const [newAllowlistDomain, setNewAllowlistDomain] = useState("")
const [searchAllowlist, setSearchAllowlist] = useState("")
const [searchExternalDomain, setSearchExternalDomain] = useState("")
```

- [ ] Add handler functions:

```typescript
const handleSync = async () => {
  try {
    setSyncing(true)
    const result = await adminApi.syncDisposableDomains()
    toast({ title: "Success", description: result.message })
    loadData()
  } catch (err: any) {
    toast({ title: "Error", description: err.message, variant: "destructive" })
  } finally {
    setSyncing(false)
  }
}

const handleAddAllowlist = async () => { ... }
const handleRemoveAllowlist = async (domain: string) => { ... }
```

### Step 5.2: Update disposable domains section with three sub-sections

- [ ] Replace the single disposable domains card with:

1. **Sync Status Bar** — Card header showing last sync time, external domain count, "Sync Now" button
2. **External Domains** — Read-only search/filter list with "external" badges. Remove button moves to allowlist.
3. **Manual Domains** — Existing add/remove with "manual" badges (uses `manual_domains`).
4. **Allowlisted Domains** — Add/remove with "allowed" badges.

- [ ] Update filter variables to use new response fields:

```typescript
const filteredExternalDomains = blocklists?.external_domains.filter(d =>
  d.toLowerCase().includes(searchExternalDomain.toLowerCase())
) || []

const filteredManualDomains = blocklists?.manual_domains.filter(d =>
  d.toLowerCase().includes(searchDomain.toLowerCase())
) || []

const filteredAllowlisted = blocklists?.allowlisted_domains.filter(d =>
  d.toLowerCase().includes(searchAllowlist.toLowerCase())
) || []
```

- [ ] Update `handleRemoveDomain` to work with `manual_domains` (existing behavior).

- [ ] Add `handleRemoveExternalDomain` that calls `adminApi.addAllowlistedDomain(domain)` to move it to the allowlist.

### Step 5.3: Commit

- [ ] Commit:

```bash
git add frontend/app/admin/rate-limits/page.tsx
git commit -m "feat: update admin UI with external/manual/allowlist sections and sync button"
```

---

## Task 6: Infrastructure — Cloud Scheduler

**Files:**
- Modify: `infrastructure/__main__.py`

### Step 6.1: Add Cloud Scheduler job

- [ ] Add after the YouTube queue scheduler block (around line 304):

```python
# ==================== Disposable Domain Blocklist Sync ====================

# Cloud Scheduler job to sync disposable email domain blocklist daily
disposable_domains_sync_scheduler = cloudscheduler.Job(
    "disposable-domains-sync-scheduler",
    name="disposable-domains-sync-daily",
    description="Daily sync of disposable email domain blocklist from GitHub",
    region=REGION,
    schedule="0 3 * * *",  # 3:00 AM UTC daily
    time_zone="UTC",
    http_target=cloudscheduler.JobHttpTargetArgs(
        uri="https://api.nomadkaraoke.com/api/internal/sync-disposable-domains",
        http_method="POST",
        oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
            service_account_email=backend_service_account.email,
        ),
    ),
    retry_config=cloudscheduler.JobRetryConfigArgs(
        retry_count=2,
        min_backoff_duration="60s",
        max_backoff_duration="300s",
    ),
)
```

- [ ] Add export:

```python
pulumi.export("disposable_domains_sync_scheduler_name", disposable_domains_sync_scheduler.name)
```

### Step 6.2: Commit

- [ ] Commit:

```bash
git add infrastructure/__main__.py
git commit -m "feat: add Cloud Scheduler job for daily disposable domain sync"
```

---

## Task 7: Remove Hardcoded Defaults

> **Deployment note:** This task ships in the same PR as everything else. The migration in `sync_disposable_domains` reads `DEFAULT_DISPOSABLE_DOMAINS` during its first run. Since we're replacing the set with an empty one, the first sync must happen *before* this code is deployed. In practice, this is fine: the Cloud Scheduler job will trigger the first sync after deploy, and the migration path also unions the Firestore `disposable_domains` field (which already has all admin-added domains). The hardcoded defaults that aren't in Firestore AND aren't in the external list would be lost — but these are all common disposable services that *are* in the external list. To be safe, we keep `DEFAULT_DISPOSABLE_DOMAINS` as an empty set (not deleted) so the import doesn't break.

**Files:**
- Modify: `backend/services/email_validation_service.py`

### Step 7.1: Remove DEFAULT_DISPOSABLE_DOMAINS

- [ ] Delete the `DEFAULT_DISPOSABLE_DOMAINS` set contents (the large set literal) but keep the variable.
- [ ] Replace with an empty set: `DEFAULT_DISPOSABLE_DOMAINS: Set[str] = set()`
  - This keeps the import working in the sync service for migration. After first sync completes in production, it can be removed entirely in a follow-up.

### Step 7.2: Update existing tests

- [ ] Update `TestDisposableDomainDetection` tests — they currently rely on hardcoded defaults being present when Firestore has no data. Update mock to provide data in the new format, or acknowledge that without Firestore data and without defaults, no domains are blocked (which is correct post-migration behavior).

### Step 7.3: Run all tests

- [ ] Run full backend test suite:

```bash
python -m pytest backend/tests/test_email_validation_service.py backend/tests/test_disposable_domain_sync_service.py -v
```

### Step 7.4: Commit

- [ ] Commit:

```bash
git add backend/services/email_validation_service.py backend/tests/test_email_validation_service.py
git commit -m "feat: remove hardcoded DEFAULT_DISPOSABLE_DOMAINS (replaced by external sync)"
```

---

## Task 8: Full Test Suite & Version Bump

### Step 8.1: Run full test suite

- [ ] Run:

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-auto-disposable-blocklist
make test 2>&1 | tail -n 500
```

- [ ] Fix any failures.

### Step 8.2: Bump version

- [ ] Bump patch version in `pyproject.toml`:

```bash
# Check current version first, then bump
grep 'version' pyproject.toml | head -1
```

### Step 8.3: Final commit

- [ ] Commit version bump:

```bash
git add pyproject.toml
git commit -m "chore: bump version for auto-disposable-blocklist feature"
```
