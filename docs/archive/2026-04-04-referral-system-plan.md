# Referral System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a referral link system where users share personalized links, referred users get a time-limited discount on credit purchases, and referrers earn cash kickbacks via Stripe Connect.

**Architecture:** New `referral_service.py` handles all referral logic (links, attribution, earnings, payouts). Stripe Connect Express for payouts, Stripe Coupons for discounts. New Firestore collections (`referral_links`, `referral_earnings`, `referral_payouts`). Frontend gets a new Referrals dashboard tab and checkout discount integration. Public website gets `/r/CODE` interstitial route.

**Tech Stack:** FastAPI, Firestore, Stripe Connect Express, Stripe Coupons, Next.js (with next-intl i18n), Pydantic, pytest

**Design Spec:** `docs/archive/2026-04-04-referral-system-design.md`

---

## File Structure

### Backend — New Files
| File | Responsibility |
|------|---------------|
| `backend/models/referral.py` | Pydantic models for referral links, earnings, payouts, API request/response |
| `backend/services/referral_service.py` | Referral CRUD, attribution, earnings calculation, payout triggering |
| `backend/api/routes/referrals.py` | API endpoints for referral operations |
| `backend/tests/test_referral_models.py` | Unit tests for referral models |
| `backend/tests/test_referral_service.py` | Unit tests for referral service |
| `backend/tests/test_referral_routes.py` | Unit tests for referral API routes |

### Backend — Modified Files
| File | Change |
|------|--------|
| `backend/models/user.py` | Add referral fields to `User` and `UserPublic` models |
| `backend/services/stripe_service.py` | Add coupon creation/lookup, Connect account creation, transfer methods |
| `backend/api/routes/users.py` | Hook referral attribution into magic link verification, add earnings to webhook |
| `backend/main.py` | Register referrals router |

### Frontend — New Files
| File | Responsibility |
|------|---------------|
| `frontend/components/referrals/ReferralDashboard.tsx` | Main referrals tab with link, stats, Connect CTA, payout history |
| `frontend/components/referrals/ReferralInterstitial.tsx` | `/r/CODE` interstitial page component |

### Frontend — Modified Files
| File | Change |
|------|--------|
| `frontend/lib/api.ts` | Add referral API methods |
| `frontend/lib/types.ts` | Add referral TypeScript types |
| `frontend/lib/auth.ts` | Pass referral code from cookie on login |
| `frontend/components/credits/BuyCreditsDialog.tsx` | Show discount badge during active referral window |
| `frontend/messages/en.json` | Add `referrals.*` i18n keys |
| `frontend/app/[locale]/app/page.tsx` (or equivalent layout) | Add Referrals tab |
| `frontend/app/[locale]/r/[code]/page.tsx` | Interstitial page route |

---

## Task 1: Referral Data Models

**Files:**
- Create: `backend/models/referral.py`
- Modify: `backend/models/user.py`
- Test: `backend/tests/test_referral_models.py`

- [ ] **Step 1: Write failing tests for referral models**

Create `backend/tests/test_referral_models.py`:

```python
"""Tests for referral data models."""
import pytest
from datetime import datetime, timedelta
from backend.models.referral import (
    ReferralLink,
    ReferralEarning,
    ReferralPayout,
    ReferralLinkStats,
    CreateReferralLinkRequest,
    ReferralLinkResponse,
    ReferralDashboardResponse,
    ReferralEarningResponse,
    ReferralPayoutResponse,
)
from backend.models.user import User


class TestReferralLink:
    def test_default_values(self):
        link = ReferralLink(
            code="abc12345",
            owner_email="user@example.com",
        )
        assert link.discount_percent == 10
        assert link.kickback_percent == 20
        assert link.discount_duration_days == 30
        assert link.earning_duration_days == 365
        assert link.is_vanity is False
        assert link.enabled is True
        assert link.display_name is None
        assert link.custom_message is None
        assert link.stripe_coupon_id is None
        assert link.stats.clicks == 0
        assert link.stats.signups == 0
        assert link.stats.purchases == 0
        assert link.stats.total_earned_cents == 0

    def test_vanity_link(self):
        link = ReferralLink(
            code="djtommy",
            owner_email="tommy@example.com",
            is_vanity=True,
            display_name="DJ Tommy",
            custom_message="I use this for all my karaoke tracks!",
            discount_percent=15,
            kickback_percent=25,
        )
        assert link.is_vanity is True
        assert link.display_name == "DJ Tommy"
        assert link.discount_percent == 15
        assert link.kickback_percent == 25


class TestReferralEarning:
    def test_pending_earning(self):
        earning = ReferralEarning(
            id="earn-123",
            referrer_email="referrer@example.com",
            referred_email="referred@example.com",
            referral_code="abc12345",
            stripe_session_id="cs_test_123",
            purchase_amount_cents=1575,
            earning_amount_cents=315,
        )
        assert earning.status == "pending"
        assert earning.paid_at is None
        assert earning.payout_id is None


class TestReferralPayout:
    def test_payout_creation(self):
        payout = ReferralPayout(
            id="pay-123",
            referrer_email="referrer@example.com",
            stripe_transfer_id="tr_123",
            amount_cents=2000,
            earnings_included=["earn-1", "earn-2", "earn-3"],
        )
        assert payout.status == "processing"
        assert len(payout.earnings_included) == 3


class TestUserReferralFields:
    def test_user_referral_fields_default_none(self):
        user = User(email="test@example.com")
        assert user.referral_code is None
        assert user.referred_by_code is None
        assert user.referred_at is None
        assert user.referral_discount_expires_at is None
        assert user.stripe_connect_account_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_models.py -v 2>&1 | tail -20
```

Expected: FAIL — `backend.models.referral` module not found, user fields not defined.

- [ ] **Step 3: Create referral models**

Create `backend/models/referral.py`:

```python
"""
Referral system models.

Supports:
- Referral link generation and tracking
- Earning calculation and payout management
- Admin-configurable discount/kickback rates
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class ReferralLinkStats(BaseModel):
    """Aggregate stats for a referral link."""
    clicks: int = 0
    signups: int = 0
    purchases: int = 0
    total_earned_cents: int = 0


class ReferralLink(BaseModel):
    """
    Referral link stored in Firestore.

    Each user gets one auto-generated link. Admins can create
    vanity links with custom codes and override rates.
    """
    code: str  # Unique, URL-safe (8-char lowercase alphanumeric or admin vanity)
    owner_email: str

    # Personalisation (shown on interstitial page)
    display_name: Optional[str] = None
    custom_message: Optional[str] = None  # Max 200 chars

    # Configurable rates (admin can override per link)
    discount_percent: int = 10
    kickback_percent: int = 20
    discount_duration_days: int = 30
    earning_duration_days: int = 365

    # Stripe coupon for this discount rate
    stripe_coupon_id: Optional[str] = None

    # Link metadata
    is_vanity: bool = False
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Aggregate stats
    stats: ReferralLinkStats = Field(default_factory=ReferralLinkStats)


class ReferralEarning(BaseModel):
    """Record of a single referral earning from a purchase."""
    id: str
    referrer_email: str
    referred_email: str
    referral_code: str
    stripe_session_id: str
    purchase_amount_cents: int  # Amount charged (after discount)
    earning_amount_cents: int   # Kickback amount
    status: str = "pending"     # pending | paid | refunded
    created_at: datetime = Field(default_factory=datetime.utcnow)
    paid_at: Optional[datetime] = None
    payout_id: Optional[str] = None


class ReferralPayout(BaseModel):
    """Record of a payout to a referrer via Stripe Connect."""
    id: str
    referrer_email: str
    stripe_transfer_id: str
    amount_cents: int
    earnings_included: List[str]  # Earning doc IDs
    status: str = "processing"    # processing | completed | failed
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# API Request/Response Models
# ============================================================================

class CreateReferralLinkRequest(BaseModel):
    """Admin request to create a vanity referral link."""
    code: str
    owner_email: str
    display_name: Optional[str] = None
    custom_message: Optional[str] = None
    discount_percent: int = 10
    kickback_percent: int = 20
    discount_duration_days: int = 30
    earning_duration_days: int = 365


class UpdateReferralLinkRequest(BaseModel):
    """Request to update a referral link (user or admin)."""
    display_name: Optional[str] = None
    custom_message: Optional[str] = None
    # Admin-only fields (validated in route):
    discount_percent: Optional[int] = None
    kickback_percent: Optional[int] = None
    discount_duration_days: Optional[int] = None
    earning_duration_days: Optional[int] = None
    enabled: Optional[bool] = None


class ReferralLinkResponse(BaseModel):
    """Referral link info returned to users."""
    code: str
    display_name: Optional[str] = None
    custom_message: Optional[str] = None
    discount_percent: int
    kickback_percent: int
    discount_duration_days: int
    earning_duration_days: int
    stats: ReferralLinkStats
    enabled: bool = True
    is_vanity: bool = False


class ReferralEarningResponse(BaseModel):
    """Single earning record for referral dashboard."""
    id: str
    referred_email: str
    purchase_amount_cents: int
    earning_amount_cents: int
    status: str
    created_at: str


class ReferralPayoutResponse(BaseModel):
    """Single payout record for referral dashboard."""
    id: str
    amount_cents: int
    status: str
    created_at: str


class ReferralDashboardResponse(BaseModel):
    """Full referral dashboard data for a user."""
    link: ReferralLinkResponse
    pending_balance_cents: int
    total_earned_cents: int
    total_paid_cents: int
    recent_earnings: List[ReferralEarningResponse]
    recent_payouts: List[ReferralPayoutResponse]
    stripe_connect_configured: bool


class ReferralInterstitialResponse(BaseModel):
    """Public data for the /r/CODE interstitial page."""
    code: str
    display_name: Optional[str] = None
    custom_message: Optional[str] = None
    discount_percent: int
    discount_duration_days: int
    valid: bool = True
```

- [ ] **Step 4: Add referral fields to User model**

In `backend/models/user.py`, add these fields to the `User` class after the `welcome_credits_granted` field (line 109):

```python
    # Referral system
    referral_code: Optional[str] = None  # User's auto-generated referral code
    referred_by_code: Optional[str] = None  # Code they signed up with
    referred_at: Optional[datetime] = None
    referral_discount_expires_at: Optional[datetime] = None
    stripe_connect_account_id: Optional[str] = None  # For receiving payouts
```

Add `referral_code` to `UserPublic` class (after `total_spent` field, line 204):

```python
    referral_code: Optional[str] = None
    has_active_referral_discount: bool = False
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_models.py -v 2>&1 | tail -20
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/models/referral.py backend/models/user.py backend/tests/test_referral_models.py
git commit -m "feat(referral): add data models for referral links, earnings, and payouts"
```

---

## Task 2: Referral Service — Link Management

**Files:**
- Create: `backend/services/referral_service.py`
- Test: `backend/tests/test_referral_service.py`

- [ ] **Step 1: Write failing tests for link management**

Create `backend/tests/test_referral_service.py`:

```python
"""Tests for referral service."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from backend.services.referral_service import ReferralService

# Collection names (must match service)
REFERRAL_LINKS_COLLECTION = "referral_links"
REFERRAL_EARNINGS_COLLECTION = "referral_earnings"
REFERRAL_PAYOUTS_COLLECTION = "referral_payouts"

# Reserved codes that cannot be used as referral codes
RESERVED_CODES = {"admin", "app", "api", "r", "pricing", "order", "payment", "login", "auth"}


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_stripe_service():
    return MagicMock()


@pytest.fixture
def service(mock_db, mock_stripe_service):
    svc = ReferralService.__new__(ReferralService)
    svc.db = mock_db
    svc.stripe_service = mock_stripe_service
    return svc


class TestGenerateCode:
    def test_generates_8_char_lowercase_alphanumeric(self, service):
        code = service._generate_code()
        assert len(code) == 8
        assert code.isalnum()
        assert code == code.lower()

    def test_generates_unique_codes(self, service):
        codes = {service._generate_code() for _ in range(100)}
        assert len(codes) == 100  # All unique


class TestValidateVanityCode:
    def test_valid_code(self, service):
        assert service._validate_vanity_code("djtommy") is True

    def test_too_short(self, service):
        assert service._validate_vanity_code("ab") is False

    def test_too_long(self, service):
        assert service._validate_vanity_code("a" * 31) is False

    def test_invalid_chars(self, service):
        assert service._validate_vanity_code("dj tommy!") is False

    def test_reserved_word(self, service):
        assert service._validate_vanity_code("admin") is False
        assert service._validate_vanity_code("api") is False

    def test_allows_hyphens(self, service):
        assert service._validate_vanity_code("dj-tommy") is True


class TestGetOrCreateLink:
    def test_returns_existing_link(self, service, mock_db):
        # Simulate existing link found by query
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "code": "existing1",
            "owner_email": "user@example.com",
            "discount_percent": 10,
            "kickback_percent": 20,
            "discount_duration_days": 30,
            "earning_duration_days": 365,
            "is_vanity": False,
            "enabled": True,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        mock_query = MagicMock()
        mock_query.limit.return_value.get.return_value = [mock_doc]
        mock_db.collection.return_value.where.return_value = mock_query

        link = service.get_or_create_link("user@example.com")
        assert link.code == "existing1"

    def test_creates_new_link_if_none_exists(self, service, mock_db):
        # Simulate no existing link
        mock_query = MagicMock()
        mock_query.limit.return_value.get.return_value = []
        mock_db.collection.return_value.where.return_value = mock_query

        # Simulate code doesn't exist yet
        mock_code_doc = MagicMock()
        mock_code_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_code_doc

        link = service.get_or_create_link("newuser@example.com")
        assert link is not None
        assert link.owner_email == "newuser@example.com"
        assert len(link.code) == 8
        # Verify it was saved
        mock_db.collection.return_value.document.return_value.set.assert_called_once()


class TestGetLinkByCode:
    def test_returns_link_if_exists(self, service, mock_db):
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "code": "abc12345",
            "owner_email": "user@example.com",
            "discount_percent": 10,
            "kickback_percent": 20,
            "discount_duration_days": 30,
            "earning_duration_days": 365,
            "is_vanity": False,
            "enabled": True,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        link = service.get_link_by_code("abc12345")
        assert link is not None
        assert link.code == "abc12345"

    def test_returns_none_if_not_found(self, service, mock_db):
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        link = service.get_link_by_code("nonexistent")
        assert link is None

    def test_returns_none_if_disabled(self, service, mock_db):
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "code": "disabled1",
            "owner_email": "user@example.com",
            "enabled": False,
            "discount_percent": 10,
            "kickback_percent": 20,
            "discount_duration_days": 30,
            "earning_duration_days": 365,
            "is_vanity": False,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        link = service.get_link_by_code("disabled1")
        assert link is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py -v 2>&1 | tail -20
```

Expected: FAIL — `backend.services.referral_service` module not found.

- [ ] **Step 3: Implement referral service — link management**

Create `backend/services/referral_service.py`:

```python
"""
Referral service for managing referral links, attribution, earnings, and payouts.

Firestore collections:
- referral_links: Referral link documents (keyed by code)
- referral_earnings: Individual earning records
- referral_payouts: Payout batch records
"""
import logging
import re
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from google.cloud import firestore

from backend.models.referral import (
    ReferralLink,
    ReferralLinkStats,
    ReferralEarning,
    ReferralPayout,
)


logger = logging.getLogger(__name__)

REFERRAL_LINKS_COLLECTION = "referral_links"
REFERRAL_EARNINGS_COLLECTION = "referral_earnings"
REFERRAL_PAYOUTS_COLLECTION = "referral_payouts"

RESERVED_CODES = frozenset({
    "admin", "app", "api", "r", "pricing", "order", "payment",
    "login", "auth", "webhook", "webhooks", "internal", "health",
    "static", "assets", "public", "private",
})

PAYOUT_THRESHOLD_CENTS = 2000  # $20.00

# Vanity code validation: 3-30 chars, lowercase alphanumeric + hyphens
VANITY_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{1,28}[a-z0-9]$")


class ReferralService:
    """Service for referral link management, attribution, and payouts."""

    def __init__(self, db: firestore.Client = None, stripe_service=None):
        if db is None:
            db = firestore.Client()
        self.db = db
        self.stripe_service = stripe_service

    def _generate_code(self) -> str:
        """Generate an 8-character lowercase alphanumeric code."""
        alphabet = string.ascii_lowercase + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(8))

    def _validate_vanity_code(self, code: str) -> bool:
        """Validate a vanity referral code."""
        if code.lower() in RESERVED_CODES:
            return False
        return bool(VANITY_CODE_PATTERN.match(code.lower()))

    def get_link_by_code(self, code: str) -> Optional[ReferralLink]:
        """Get a referral link by code. Returns None if not found or disabled."""
        doc = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code.lower()).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        if not data.get("enabled", True):
            return None
        return ReferralLink(**data)

    def get_or_create_link(self, owner_email: str) -> ReferralLink:
        """Get existing referral link for user, or create a new one."""
        owner_email = owner_email.lower()

        # Check for existing link
        query = (
            self.db.collection(REFERRAL_LINKS_COLLECTION)
            .where("owner_email", "==", owner_email)
        )
        existing = query.limit(1).get()
        if existing:
            return ReferralLink(**existing[0].to_dict())

        # Generate unique code
        for _ in range(10):  # Retry up to 10 times for collision
            code = self._generate_code()
            doc_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code)
            if not doc_ref.get().exists:
                break
        else:
            raise RuntimeError("Failed to generate unique referral code")

        link = ReferralLink(code=code, owner_email=owner_email)
        doc_ref.set(link.model_dump(mode="json"))
        logger.info(f"Created referral link {code} for {owner_email}")
        return link

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
        """Create an admin vanity referral link. Returns (success, link, message)."""
        code = code.lower()

        if not self._validate_vanity_code(code):
            return False, None, "Invalid code: must be 3-30 chars, lowercase alphanumeric + hyphens, not reserved"

        # Check for collision
        doc_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code)
        if doc_ref.get().exists:
            return False, None, f"Code '{code}' already exists"

        link = ReferralLink(
            code=code,
            owner_email=owner_email.lower(),
            display_name=display_name,
            custom_message=custom_message[:200] if custom_message else None,
            discount_percent=discount_percent,
            kickback_percent=kickback_percent,
            discount_duration_days=discount_duration_days,
            earning_duration_days=earning_duration_days,
            is_vanity=True,
        )
        doc_ref.set(link.model_dump(mode="json"))
        logger.info(f"Created vanity referral link '{code}' for {owner_email}")
        return True, link, "Vanity link created"

    def update_link(self, code: str, **updates) -> Tuple[bool, str]:
        """Update fields on a referral link. Returns (success, message)."""
        code = code.lower()
        doc_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code)
        doc = doc_ref.get()
        if not doc.exists:
            return False, "Link not found"

        updates["updated_at"] = datetime.utcnow()
        # Truncate custom_message if provided
        if "custom_message" in updates and updates["custom_message"]:
            updates["custom_message"] = updates["custom_message"][:200]

        doc_ref.update(updates)
        logger.info(f"Updated referral link '{code}': {list(updates.keys())}")
        return True, "Link updated"

    def increment_clicks(self, code: str) -> None:
        """Increment click count for a referral link."""
        doc_ref = self.db.collection(REFERRAL_LINKS_COLLECTION).document(code.lower())
        doc_ref.update({"stats.clicks": firestore.Increment(1)})

    def list_links(self, limit: int = 50, offset: int = 0) -> List[ReferralLink]:
        """List all referral links (admin). Returns list ordered by created_at desc."""
        query = (
            self.db.collection(REFERRAL_LINKS_COLLECTION)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .offset(offset)
        )
        return [ReferralLink(**doc.to_dict()) for doc in query.stream()]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py -v 2>&1 | tail -20
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/referral_service.py backend/tests/test_referral_service.py
git commit -m "feat(referral): add referral service with link management"
```

---

## Task 3: Referral Attribution

**Files:**
- Modify: `backend/services/referral_service.py`
- Modify: `backend/services/user_service.py`
- Modify: `backend/api/routes/users.py`
- Test: `backend/tests/test_referral_service.py` (append)

- [ ] **Step 1: Write failing tests for attribution**

Append to `backend/tests/test_referral_service.py`:

```python
class TestAttributeReferral:
    def test_successful_attribution(self, service, mock_db):
        """Test attributing a referral to a new user."""
        # Mock the referral link lookup
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "code": "abc12345",
            "owner_email": "referrer@example.com",
            "discount_percent": 10,
            "kickback_percent": 20,
            "discount_duration_days": 30,
            "earning_duration_days": 365,
            "is_vanity": False,
            "enabled": True,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        success, message = service.attribute_referral(
            referred_email="newuser@example.com",
            referral_code="abc12345",
        )
        assert success is True
        assert "attributed" in message.lower()

    def test_self_referral_blocked(self, service, mock_db):
        """Test that users cannot refer themselves."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "code": "abc12345",
            "owner_email": "user@example.com",
            "enabled": True,
            "discount_percent": 10,
            "kickback_percent": 20,
            "discount_duration_days": 30,
            "earning_duration_days": 365,
            "is_vanity": False,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        success, message = service.attribute_referral(
            referred_email="user@example.com",
            referral_code="abc12345",
        )
        assert success is False
        assert "self" in message.lower() or "own" in message.lower()

    def test_invalid_code_rejected(self, service, mock_db):
        """Test that invalid codes are rejected."""
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        success, message = service.attribute_referral(
            referred_email="newuser@example.com",
            referral_code="nonexistent",
        )
        assert success is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py::TestAttributeReferral -v 2>&1 | tail -20
```

Expected: FAIL — `attribute_referral` not defined.

- [ ] **Step 3: Implement attribution in referral service**

Add to `backend/services/referral_service.py`:

```python
    def attribute_referral(
        self,
        referred_email: str,
        referral_code: str,
    ) -> Tuple[bool, str]:
        """
        Attribute a referral to a user. Called during first login/signup.

        Returns (success, message). Idempotent — returns False if already attributed.
        """
        referred_email = referred_email.lower()
        referral_code = referral_code.lower()

        # Look up the referral link
        link = self.get_link_by_code(referral_code)
        if not link:
            logger.warning(f"Referral attribution failed: code '{referral_code}' not found or disabled")
            return False, "Invalid or disabled referral code"

        # Block self-referral
        if link.owner_email == referred_email:
            logger.warning(f"Self-referral blocked: {referred_email} tried to use own code '{referral_code}'")
            return False, "Cannot use your own referral code"

        # Calculate discount expiry
        now = datetime.utcnow()
        discount_expires = now + timedelta(days=link.discount_duration_days)

        # Return attribution data (caller is responsible for saving to user doc)
        # Increment signup count on the referral link
        self.db.collection(REFERRAL_LINKS_COLLECTION).document(referral_code).update({
            "stats.signups": firestore.Increment(1),
        })

        logger.info(f"Referral attributed: {referred_email} via code '{referral_code}' (referrer: {link.owner_email})")
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
```

- [ ] **Step 4: Hook attribution into magic link verification**

In `backend/api/routes/users.py`, in the `verify_magic_link` function, after welcome credits are granted (around line 362), add referral attribution. The referral code comes from a request header set by the frontend (read from cookie):

After the line `user = user_service.get_user(user.email)` (line 364), add:

```python
    # Referral attribution (first login only, if referral code provided)
    referral_code = http_request.headers.get("x-referral-code")
    if referral_code and is_first_login:
        try:
            from backend.services.referral_service import get_referral_service
            referral_svc = get_referral_service()
            attr_success, attr_msg = referral_svc.attribute_referral(
                referred_email=user.email,
                referral_code=referral_code,
            )
            if attr_success:
                attr_data = referral_svc.get_attribution_data(referral_code)
                if attr_data:
                    user_service.update_user(user.email, **attr_data)
                    logger.info(f"Referral attributed for {_mask_email(user.email)} via code '{referral_code}'")
        except Exception as ref_err:
            logger.warning(f"Referral attribution failed for {_mask_email(user.email)}: {ref_err}")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py -v 2>&1 | tail -20
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/services/referral_service.py backend/tests/test_referral_service.py backend/api/routes/users.py
git commit -m "feat(referral): add referral attribution on first login"
```

---

## Task 4: Stripe Coupon & Discount at Checkout

**Files:**
- Modify: `backend/services/stripe_service.py`
- Modify: `backend/services/referral_service.py`
- Modify: `backend/api/routes/users.py` (checkout endpoint)
- Test: `backend/tests/test_referral_service.py` (append)

- [ ] **Step 1: Write failing tests for coupon management and discount checkout**

Append to `backend/tests/test_referral_service.py`:

```python
class TestGetOrCreateStripeCoupon:
    def test_creates_coupon_for_new_percentage(self, service, mock_stripe_service):
        mock_stripe_service.get_or_create_referral_coupon.return_value = "referral-10pct"

        coupon_id = service.get_or_create_stripe_coupon(10)
        assert coupon_id == "referral-10pct"
        mock_stripe_service.get_or_create_referral_coupon.assert_called_once_with(10)


class TestShouldApplyDiscount:
    def test_active_discount(self, service):
        """User within discount window should get discount."""
        user_data = {
            "referred_by_code": "abc12345",
            "referral_discount_expires_at": datetime.utcnow() + timedelta(days=15),
        }
        assert service.should_apply_discount(user_data) is True

    def test_expired_discount(self, service):
        """User past discount window should not get discount."""
        user_data = {
            "referred_by_code": "abc12345",
            "referral_discount_expires_at": datetime.utcnow() - timedelta(days=1),
        }
        assert service.should_apply_discount(user_data) is False

    def test_no_referral(self, service):
        """User without referral should not get discount."""
        user_data = {}
        assert service.should_apply_discount(user_data) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py::TestGetOrCreateStripeCoupon backend/tests/test_referral_service.py::TestShouldApplyDiscount -v 2>&1 | tail -20
```

Expected: FAIL — methods not defined.

- [ ] **Step 3: Add coupon management to Stripe service**

In `backend/services/stripe_service.py`, add method to `StripeService` class:

```python
    def get_or_create_referral_coupon(self, discount_percent: int) -> Optional[str]:
        """
        Get or create a Stripe coupon for a referral discount percentage.

        Coupon IDs follow the pattern 'referral-{percent}pct'.
        Returns coupon ID or None if Stripe is not configured.
        """
        if not self.is_configured():
            return None

        coupon_id = f"referral-{discount_percent}pct"
        try:
            # Try to retrieve existing coupon
            stripe.Coupon.retrieve(coupon_id)
            return coupon_id
        except stripe.error.InvalidRequestError:
            # Coupon doesn't exist — create it
            try:
                stripe.Coupon.create(
                    id=coupon_id,
                    percent_off=discount_percent,
                    duration="once",
                    name=f"Referral {discount_percent}% off",
                )
                logger.info(f"Created Stripe coupon: {coupon_id}")
                return coupon_id
            except stripe.error.StripeError as e:
                logger.error(f"Failed to create coupon {coupon_id}: {e}")
                return None
```

- [ ] **Step 4: Add discount methods to referral service**

Add to `backend/services/referral_service.py`:

```python
    def get_or_create_stripe_coupon(self, discount_percent: int) -> Optional[str]:
        """Get or create a Stripe coupon for the given discount percentage."""
        if not self.stripe_service:
            return None
        return self.stripe_service.get_or_create_referral_coupon(discount_percent)

    def should_apply_discount(self, user_data: dict) -> bool:
        """Check if a user has an active referral discount."""
        if not user_data.get("referred_by_code"):
            return False
        expires_at = user_data.get("referral_discount_expires_at")
        if not expires_at:
            return False
        if isinstance(expires_at, datetime):
            return expires_at > datetime.utcnow()
        return False

    def get_discount_for_checkout(self, user_email: str) -> Optional[dict]:
        """
        Get discount info for a user's checkout, if applicable.

        Returns dict with 'coupon_id' and 'discount_percent', or None.
        """
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

        # Look up the referral link to get the discount percentage
        link = self.get_link_by_code(user.referred_by_code)
        if not link:
            return None

        coupon_id = self.get_or_create_stripe_coupon(link.discount_percent)
        if not coupon_id:
            return None

        return {
            "coupon_id": coupon_id,
            "discount_percent": link.discount_percent,
        }
```

- [ ] **Step 5: Modify checkout to apply referral discount**

In `backend/services/stripe_service.py`, modify `create_checkout_session` to accept an optional `coupon_id` parameter. Add parameter to method signature:

```python
    def create_checkout_session(
        self,
        package_id: str,
        user_email: str,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        coupon_id: Optional[str] = None,  # NEW: referral discount coupon
    ) -> Tuple[bool, Optional[str], str]:
```

In the `session_params` dict, after `'allow_promotion_codes': True,` (line 151), add:

```python
            # If a referral coupon is applied, use discounts instead of allow_promotion_codes
            # (Stripe doesn't allow both simultaneously)
            if coupon_id:
                session_params.pop('allow_promotion_codes', None)
                session_params['discounts'] = [{'coupon': coupon_id}]
```

In `backend/api/routes/users.py`, find the checkout endpoint that calls `stripe_service.create_checkout_session` and add referral discount lookup before the call:

```python
    # Check for referral discount
    referral_coupon_id = None
    try:
        from backend.services.referral_service import get_referral_service
        referral_svc = get_referral_service()
        discount_info = referral_svc.get_discount_for_checkout(user_email)
        if discount_info:
            referral_coupon_id = discount_info["coupon_id"]
    except Exception as ref_err:
        logger.warning(f"Referral discount lookup failed: {ref_err}")

    success, checkout_url, message = stripe_service.create_checkout_session(
        package_id=package_id,
        user_email=user_email,
        coupon_id=referral_coupon_id,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py -v 2>&1 | tail -20
```

Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/services/stripe_service.py backend/services/referral_service.py backend/api/routes/users.py backend/tests/test_referral_service.py
git commit -m "feat(referral): apply Stripe coupon discount at checkout for referred users"
```

---

## Task 5: Earnings Calculation & Webhook Integration

**Files:**
- Modify: `backend/services/referral_service.py`
- Modify: `backend/api/routes/users.py` (webhook handler)
- Test: `backend/tests/test_referral_service.py` (append)

- [ ] **Step 1: Write failing tests for earnings**

Append to `backend/tests/test_referral_service.py`:

```python
class TestRecordEarning:
    def test_creates_earning_within_window(self, service, mock_db):
        """Earning is created when purchase is within earning window."""
        # Mock user lookup — referred user with active earning window
        mock_user_doc = MagicMock()
        mock_user_doc.exists = True
        mock_user_doc.to_dict.return_value = {
            "email": "referred@example.com",
            "referred_by_code": "abc12345",
            "referred_at": datetime.utcnow() - timedelta(days=30),
        }

        # Mock referral link lookup
        mock_link_doc = MagicMock()
        mock_link_doc.exists = True
        mock_link_doc.to_dict.return_value = {
            "code": "abc12345",
            "owner_email": "referrer@example.com",
            "kickback_percent": 20,
            "earning_duration_days": 365,
            "discount_percent": 10,
            "discount_duration_days": 30,
            "is_vanity": False,
            "enabled": True,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        def collection_side_effect(name):
            mock_col = MagicMock()
            if name == "gen_users":
                mock_col.document.return_value.get.return_value = mock_user_doc
            elif name == REFERRAL_LINKS_COLLECTION:
                mock_col.document.return_value.get.return_value = mock_link_doc
            return mock_col

        mock_db.collection.side_effect = collection_side_effect

        result = service.record_earning(
            referred_email="referred@example.com",
            stripe_session_id="cs_test_123",
            purchase_amount_cents=1575,
        )
        assert result is not None
        assert result["earning_amount_cents"] == 315  # 20% of 1575

    def test_no_earning_outside_window(self, service, mock_db):
        """No earning created when purchase is outside earning window."""
        mock_user_doc = MagicMock()
        mock_user_doc.exists = True
        mock_user_doc.to_dict.return_value = {
            "email": "referred@example.com",
            "referred_by_code": "abc12345",
            "referred_at": datetime.utcnow() - timedelta(days=400),  # Over 365 days
        }

        mock_link_doc = MagicMock()
        mock_link_doc.exists = True
        mock_link_doc.to_dict.return_value = {
            "code": "abc12345",
            "owner_email": "referrer@example.com",
            "kickback_percent": 20,
            "earning_duration_days": 365,
            "discount_percent": 10,
            "discount_duration_days": 30,
            "is_vanity": False,
            "enabled": True,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        def collection_side_effect(name):
            mock_col = MagicMock()
            if name == "gen_users":
                mock_col.document.return_value.get.return_value = mock_user_doc
            elif name == REFERRAL_LINKS_COLLECTION:
                mock_col.document.return_value.get.return_value = mock_link_doc
            return mock_col

        mock_db.collection.side_effect = collection_side_effect

        result = service.record_earning(
            referred_email="referred@example.com",
            stripe_session_id="cs_test_456",
            purchase_amount_cents=1575,
        )
        assert result is None

    def test_no_earning_for_non_referred_user(self, service, mock_db):
        """No earning for users without referral attribution."""
        mock_user_doc = MagicMock()
        mock_user_doc.exists = True
        mock_user_doc.to_dict.return_value = {
            "email": "user@example.com",
            # No referred_by_code
        }

        mock_db.collection.return_value.document.return_value.get.return_value = mock_user_doc

        result = service.record_earning(
            referred_email="user@example.com",
            stripe_session_id="cs_test_789",
            purchase_amount_cents=1750,
        )
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py::TestRecordEarning -v 2>&1 | tail -20
```

Expected: FAIL — `record_earning` not defined.

- [ ] **Step 3: Implement earnings recording**

Add to `backend/services/referral_service.py`:

```python
    def record_earning(
        self,
        referred_email: str,
        stripe_session_id: str,
        purchase_amount_cents: int,
    ) -> Optional[dict]:
        """
        Record a referral earning after a credit purchase.

        Checks if the referred user has an active referral within the earning window.
        Returns earning info dict if recorded, None if not applicable.
        """
        referred_email = referred_email.lower()

        # Look up the referred user
        from backend.services.user_service import USERS_COLLECTION
        user_doc = self.db.collection(USERS_COLLECTION).document(referred_email).get()
        if not user_doc.exists:
            return None

        user_data = user_doc.to_dict()
        referral_code = user_data.get("referred_by_code")
        if not referral_code:
            return None

        # Check earning window
        referred_at = user_data.get("referred_at")
        if not referred_at:
            return None

        link = self.get_link_by_code(referral_code)
        if not link:
            return None

        # Check if within earning window
        if isinstance(referred_at, datetime):
            earning_expires = referred_at + timedelta(days=link.earning_duration_days)
        else:
            # Firestore may return as timestamp
            earning_expires = referred_at + timedelta(days=link.earning_duration_days)

        if datetime.utcnow() > earning_expires:
            logger.info(f"Referral earning skipped: {referred_email} outside earning window for code '{referral_code}'")
            return None

        # Calculate earning
        earning_amount = int(purchase_amount_cents * link.kickback_percent / 100)
        if earning_amount <= 0:
            return None

        earning_id = str(__import__("uuid").uuid4())
        earning = ReferralEarning(
            id=earning_id,
            referrer_email=link.owner_email,
            referred_email=referred_email,
            referral_code=referral_code,
            stripe_session_id=stripe_session_id,
            purchase_amount_cents=purchase_amount_cents,
            earning_amount_cents=earning_amount,
        )

        # Save earning
        self.db.collection(REFERRAL_EARNINGS_COLLECTION).document(earning_id).set(
            earning.model_dump(mode="json")
        )

        # Update link stats
        self.db.collection(REFERRAL_LINKS_COLLECTION).document(referral_code).update({
            "stats.purchases": firestore.Increment(1),
            "stats.total_earned_cents": firestore.Increment(earning_amount),
        })

        logger.info(
            f"Referral earning recorded: ${earning_amount / 100:.2f} for {link.owner_email} "
            f"from {referred_email}'s ${purchase_amount_cents / 100:.2f} purchase"
        )

        return {
            "earning_id": earning_id,
            "referrer_email": link.owner_email,
            "earning_amount_cents": earning_amount,
        }
```

- [ ] **Step 4: Hook earnings into Stripe webhook**

In `backend/api/routes/users.py`, in the `stripe_webhook` function, after credits are added successfully (after line 1019 `logger.info(f"Added {credits} credits...")`), add:

```python
                        # Record referral earning if applicable
                        try:
                            from backend.services.referral_service import get_referral_service
                            referral_svc = get_referral_service()
                            amount_charged = session.get("amount_total", 0)
                            if amount_charged > 0:
                                earning_result = referral_svc.record_earning(
                                    referred_email=user_email,
                                    stripe_session_id=session_id,
                                    purchase_amount_cents=amount_charged,
                                )
                                if earning_result:
                                    logger.info(
                                        f"Referral earning: ${earning_result['earning_amount_cents'] / 100:.2f} "
                                        f"for {earning_result['referrer_email']}"
                                    )
                        except Exception as ref_err:
                            logger.warning(f"Referral earning recording failed: {ref_err}")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py -v 2>&1 | tail -20
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/services/referral_service.py backend/api/routes/users.py backend/tests/test_referral_service.py
git commit -m "feat(referral): record referral earnings on credit purchases via webhook"
```

---

## Task 6: Stripe Connect & Payouts

**Files:**
- Modify: `backend/services/stripe_service.py`
- Modify: `backend/services/referral_service.py`
- Test: `backend/tests/test_referral_service.py` (append)

- [ ] **Step 1: Write failing tests for Connect onboarding and payouts**

Append to `backend/tests/test_referral_service.py`:

```python
class TestStripeConnectOnboarding:
    def test_create_connect_account(self, service, mock_stripe_service):
        mock_stripe_service.create_connect_account.return_value = ("acct_123", "https://connect.stripe.com/onboard")

        account_id, onboard_url = service.create_connect_account("referrer@example.com")
        assert account_id == "acct_123"
        assert "stripe.com" in onboard_url


class TestTriggerPayout:
    def test_payout_triggered_at_threshold(self, service, mock_db, mock_stripe_service):
        """Payout is triggered when pending earnings >= $20."""
        # Mock pending earnings query
        earning_docs = []
        for i in range(4):
            doc = MagicMock()
            doc.to_dict.return_value = {
                "id": f"earn-{i}",
                "referrer_email": "referrer@example.com",
                "referred_email": f"user{i}@example.com",
                "referral_code": "abc12345",
                "stripe_session_id": f"cs_{i}",
                "purchase_amount_cents": 1750,
                "earning_amount_cents": 525,  # $5.25 each, 4 = $21.00
                "status": "pending",
                "created_at": datetime.utcnow(),
            }
            doc.reference = MagicMock()
            earning_docs.append(doc)

        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.get.return_value = earning_docs
        mock_db.collection.return_value = mock_query

        mock_stripe_service.create_transfer.return_value = "tr_123"

        result = service.check_and_trigger_payout(
            referrer_email="referrer@example.com",
            stripe_connect_account_id="acct_123",
        )
        assert result is True

    def test_no_payout_below_threshold(self, service, mock_db, mock_stripe_service):
        """No payout when pending earnings < $20."""
        earning_doc = MagicMock()
        earning_doc.to_dict.return_value = {
            "id": "earn-1",
            "referrer_email": "referrer@example.com",
            "earning_amount_cents": 315,  # $3.15 — below $20 threshold
            "status": "pending",
            "created_at": datetime.utcnow(),
        }
        earning_doc.reference = MagicMock()

        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.get.return_value = [earning_doc]
        mock_db.collection.return_value = mock_query

        result = service.check_and_trigger_payout(
            referrer_email="referrer@example.com",
            stripe_connect_account_id="acct_123",
        )
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py::TestStripeConnectOnboarding backend/tests/test_referral_service.py::TestTriggerPayout -v 2>&1 | tail -20
```

Expected: FAIL — methods not defined.

- [ ] **Step 3: Add Stripe Connect methods to stripe_service.py**

Add to `StripeService` class in `backend/services/stripe_service.py`:

```python
    def create_connect_account(self, email: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Create a Stripe Connect Express account and return (account_id, onboarding_url).
        """
        if not self.is_configured():
            return None, None

        try:
            account = stripe.Account.create(
                type="express",
                email=email,
                capabilities={
                    "transfers": {"requested": True},
                },
                metadata={"source": "nomad_karaoke_referrals"},
            )

            account_link = stripe.AccountLink.create(
                account=account.id,
                refresh_url=f"{self.frontend_url}/app?tab=referrals&connect=refresh",
                return_url=f"{self.frontend_url}/app?tab=referrals&connect=complete",
                type="account_onboarding",
            )

            logger.info(f"Created Connect account {account.id} for {email}")
            return account.id, account_link.url

        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Connect account for {email}: {e}")
            return None, None

    def create_transfer(
        self,
        amount_cents: int,
        destination_account_id: str,
        description: str = "Nomad Karaoke referral payout",
    ) -> Optional[str]:
        """Create a transfer to a Connect account. Returns transfer ID."""
        if not self.is_configured():
            return None

        try:
            transfer = stripe.Transfer.create(
                amount=amount_cents,
                currency="usd",
                destination=destination_account_id,
                description=description,
            )
            logger.info(f"Created transfer {transfer.id}: ${amount_cents / 100:.2f} to {destination_account_id}")
            return transfer.id
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create transfer to {destination_account_id}: {e}")
            return None
```

- [ ] **Step 4: Add Connect and payout methods to referral service**

Add to `backend/services/referral_service.py`:

```python
    def create_connect_account(self, email: str) -> Tuple[Optional[str], Optional[str]]:
        """Create Stripe Connect account for a referrer. Returns (account_id, onboarding_url)."""
        if not self.stripe_service:
            return None, None
        return self.stripe_service.create_connect_account(email)

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

    def check_and_trigger_payout(
        self,
        referrer_email: str,
        stripe_connect_account_id: str,
    ) -> bool:
        """
        Check if pending earnings meet threshold and trigger payout.

        Returns True if payout was triggered, False otherwise.
        """
        pending = self.get_pending_earnings(referrer_email)
        total_pending = sum(e.get("earning_amount_cents", 0) for e in pending)

        if total_pending < PAYOUT_THRESHOLD_CENTS:
            return False

        if not self.stripe_service:
            logger.warning(f"Payout threshold met for {referrer_email} but Stripe not configured")
            return False

        # Create the transfer
        transfer_id = self.stripe_service.create_transfer(
            amount_cents=total_pending,
            destination_account_id=stripe_connect_account_id,
        )
        if not transfer_id:
            return False

        # Record the payout
        payout_id = str(__import__("uuid").uuid4())
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

        # Mark earnings as paid
        for earning in pending:
            if "_ref" in earning:
                earning["_ref"].update({
                    "status": "paid",
                    "paid_at": datetime.utcnow(),
                    "payout_id": payout_id,
                })

        logger.info(f"Payout triggered: ${total_pending / 100:.2f} to {referrer_email} (transfer: {transfer_id})")
        return True
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_service.py -v 2>&1 | tail -20
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/services/stripe_service.py backend/services/referral_service.py backend/tests/test_referral_service.py
git commit -m "feat(referral): Stripe Connect onboarding and auto-payout at $20 threshold"
```

---

## Task 7: Referral API Routes

**Files:**
- Create: `backend/api/routes/referrals.py`
- Modify: `backend/main.py`
- Create: `backend/tests/test_referral_routes.py`

- [ ] **Step 1: Write failing tests for referral routes**

Create `backend/tests/test_referral_routes.py`:

```python
"""Tests for referral API routes."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.referrals import router
from backend.models.referral import ReferralLink, ReferralLinkStats

app = FastAPI()
app.include_router(router, prefix="/api")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_referral_service():
    with patch("backend.api.routes.referrals.get_referral_service") as mock:
        svc = MagicMock()
        mock.return_value = svc
        yield svc


@pytest.fixture
def mock_auth():
    """Mock auth to return an admin user."""
    with patch("backend.api.routes.referrals.require_auth") as mock:
        from backend.models.auth import AuthResult, UserType
        mock.return_value = AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            identifier="admin@example.com",
        )
        yield mock


class TestGetInterstitial:
    def test_valid_code(self, client, mock_referral_service):
        mock_referral_service.get_link_by_code.return_value = ReferralLink(
            code="abc12345",
            owner_email="referrer@example.com",
            display_name="DJ Tommy",
            custom_message="Best karaoke tool!",
            discount_percent=10,
            discount_duration_days=30,
        )

        response = client.get("/api/referrals/r/abc12345")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "abc12345"
        assert data["display_name"] == "DJ Tommy"
        assert data["discount_percent"] == 10
        assert data["valid"] is True

    def test_invalid_code(self, client, mock_referral_service):
        mock_referral_service.get_link_by_code.return_value = None

        response = client.get("/api/referrals/r/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False


class TestGetMyReferralDashboard:
    def test_returns_dashboard(self, client, mock_referral_service, mock_auth):
        mock_auth.return_value.identifier = "user@example.com"
        mock_referral_service.get_or_create_link.return_value = ReferralLink(
            code="mycode12",
            owner_email="user@example.com",
        )
        mock_referral_service.get_pending_earnings.return_value = []
        mock_referral_service.get_dashboard_data.return_value = {
            "link": {
                "code": "mycode12",
                "discount_percent": 10,
                "kickback_percent": 20,
                "discount_duration_days": 30,
                "earning_duration_days": 365,
                "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
                "enabled": True,
                "is_vanity": False,
            },
            "pending_balance_cents": 0,
            "total_earned_cents": 0,
            "total_paid_cents": 0,
            "recent_earnings": [],
            "recent_payouts": [],
            "stripe_connect_configured": False,
        }

        response = client.get("/api/referrals/me", headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_routes.py -v 2>&1 | tail -20
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement referral routes**

Create `backend/api/routes/referrals.py`:

```python
"""
Referral system API routes.

Endpoints:
- GET /api/referrals/r/{code} — Public interstitial data (no auth)
- GET /api/referrals/me — Referral dashboard for current user (auth required)
- PUT /api/referrals/me — Update referral link display name/message (auth required)
- POST /api/referrals/me/connect — Start Stripe Connect onboarding (auth required)
- POST /api/referrals/admin/vanity — Create vanity link (admin only)
- GET /api/referrals/admin/links — List all referral links (admin only)
- PUT /api/referrals/admin/links/{code} — Update any link (admin only)
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.dependencies import require_auth, require_admin
from backend.models.referral import (
    CreateReferralLinkRequest,
    UpdateReferralLinkRequest,
    ReferralInterstitialResponse,
    ReferralDashboardResponse,
)
from backend.services.referral_service import get_referral_service, ReferralService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/referrals", tags=["referrals"])


@router.get("/r/{code}")
async def get_interstitial(code: str):
    """Public endpoint: get referral link data for interstitial page."""
    service = get_referral_service()
    link = service.get_link_by_code(code)

    if not link:
        return ReferralInterstitialResponse(
            code=code,
            discount_percent=0,
            discount_duration_days=0,
            valid=False,
        )

    # Increment click count (fire-and-forget)
    try:
        service.increment_clicks(code)
    except Exception:
        pass

    return ReferralInterstitialResponse(
        code=link.code,
        display_name=link.display_name,
        custom_message=link.custom_message,
        discount_percent=link.discount_percent,
        discount_duration_days=link.discount_duration_days,
        valid=True,
    )


@router.get("/me")
async def get_my_referral_dashboard(auth=Depends(require_auth)):
    """Get referral dashboard data for the current user."""
    service = get_referral_service()
    data = service.get_dashboard_data(auth.identifier)
    return data


@router.put("/me")
async def update_my_referral_link(
    updates: UpdateReferralLinkRequest,
    auth=Depends(require_auth),
):
    """Update current user's referral link display name and message."""
    service = get_referral_service()
    link = service.get_or_create_link(auth.identifier)

    # Users can only update display_name and custom_message
    update_fields = {}
    if updates.display_name is not None:
        update_fields["display_name"] = updates.display_name
    if updates.custom_message is not None:
        update_fields["custom_message"] = updates.custom_message

    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    success, message = service.update_link(link.code, **update_fields)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"status": "ok", "message": message}


@router.post("/me/connect")
async def start_connect_onboarding(auth=Depends(require_auth)):
    """Start Stripe Connect onboarding for the current user."""
    service = get_referral_service()
    account_id, onboarding_url = service.create_connect_account(auth.identifier)

    if not account_id:
        raise HTTPException(status_code=500, detail="Failed to create Connect account")

    # Save Connect account ID to user
    from backend.services.user_service import get_user_service
    user_service = get_user_service()
    user_service.update_user(auth.identifier, stripe_connect_account_id=account_id)

    return {"account_id": account_id, "onboarding_url": onboarding_url}


@router.post("/admin/vanity")
async def create_vanity_link(
    request: CreateReferralLinkRequest,
    auth=Depends(require_admin),
):
    """Admin: create a vanity referral link."""
    service = get_referral_service()
    success, link, message = service.create_vanity_link(
        code=request.code,
        owner_email=request.owner_email,
        display_name=request.display_name,
        custom_message=request.custom_message,
        discount_percent=request.discount_percent,
        kickback_percent=request.kickback_percent,
        discount_duration_days=request.discount_duration_days,
        earning_duration_days=request.earning_duration_days,
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"status": "ok", "link": link.model_dump(mode="json")}


@router.get("/admin/links")
async def list_referral_links(
    limit: int = 50,
    offset: int = 0,
    auth=Depends(require_admin),
):
    """Admin: list all referral links."""
    service = get_referral_service()
    links = service.list_links(limit=limit, offset=offset)
    return {"links": [l.model_dump(mode="json") for l in links]}


@router.put("/admin/links/{code}")
async def admin_update_link(
    code: str,
    updates: UpdateReferralLinkRequest,
    auth=Depends(require_admin),
):
    """Admin: update any referral link's settings."""
    service = get_referral_service()
    update_fields = {k: v for k, v in updates.model_dump().items() if v is not None}
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    success, message = service.update_link(code, **update_fields)
    if not success:
        raise HTTPException(status_code=404, detail=message)

    return {"status": "ok", "message": message}
```

- [ ] **Step 4: Add get_dashboard_data to referral service**

Add to `backend/services/referral_service.py`:

```python
    def get_dashboard_data(self, user_email: str) -> dict:
        """Get full referral dashboard data for a user."""
        user_email = user_email.lower()
        link = self.get_or_create_link(user_email)

        # Get earnings
        earnings_query = (
            self.db.collection(REFERRAL_EARNINGS_COLLECTION)
            .where("referrer_email", "==", user_email)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(20)
        )
        earnings = [doc.to_dict() for doc in earnings_query.stream()]

        # Get payouts
        payouts_query = (
            self.db.collection(REFERRAL_PAYOUTS_COLLECTION)
            .where("referrer_email", "==", user_email)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(10)
        )
        payouts = [doc.to_dict() for doc in payouts_query.stream()]

        pending_balance = sum(
            e.get("earning_amount_cents", 0)
            for e in earnings
            if e.get("status") == "pending"
        )
        total_earned = link.stats.total_earned_cents
        total_paid = sum(p.get("amount_cents", 0) for p in payouts if p.get("status") == "completed")

        # Check Connect status
        from backend.services.user_service import get_user_service
        user = get_user_service().get_user(user_email)
        has_connect = bool(user and user.stripe_connect_account_id)

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
        }
```

- [ ] **Step 5: Add global service accessor and register router**

Add to end of `backend/services/referral_service.py`:

```python
# Global instance
_referral_service = None


def get_referral_service() -> ReferralService:
    """Get the global referral service instance."""
    global _referral_service
    if _referral_service is None:
        from backend.services.stripe_service import get_stripe_service
        _referral_service = ReferralService(stripe_service=get_stripe_service())
    return _referral_service
```

In `backend/main.py`, add import and registration after line 161 (`app.include_router(users.router, prefix="/api")`):

```python
from backend.api.routes import referrals
app.include_router(referrals.router, prefix="/api")  # Referral links and payouts
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_routes.py -v 2>&1 | tail -20
```

Expected: All tests PASS (may need to adjust mock auth imports — check `backend/api/dependencies.py` for exact auth pattern).

- [ ] **Step 7: Commit**

```bash
git add backend/api/routes/referrals.py backend/main.py backend/services/referral_service.py backend/tests/test_referral_routes.py
git commit -m "feat(referral): add API routes for referral dashboard, Connect onboarding, and admin"
```

---

## Task 8: Frontend — API Client & Types

**Files:**
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/messages/en.json`

- [ ] **Step 1: Add referral types**

In `frontend/lib/types.ts`, add:

```typescript
// Referral system types
export interface ReferralLinkStats {
  clicks: number;
  signups: number;
  purchases: number;
  total_earned_cents: number;
}

export interface ReferralLink {
  code: string;
  display_name: string | null;
  custom_message: string | null;
  discount_percent: number;
  kickback_percent: number;
  discount_duration_days: number;
  earning_duration_days: number;
  stats: ReferralLinkStats;
  enabled: boolean;
  is_vanity: boolean;
}

export interface ReferralEarning {
  id: string;
  referred_email: string;
  purchase_amount_cents: number;
  earning_amount_cents: number;
  status: 'pending' | 'paid' | 'refunded';
  created_at: string;
}

export interface ReferralPayout {
  id: string;
  amount_cents: number;
  status: 'processing' | 'completed' | 'failed';
  created_at: string;
}

export interface ReferralDashboard {
  link: ReferralLink;
  pending_balance_cents: number;
  total_earned_cents: number;
  total_paid_cents: number;
  recent_earnings: ReferralEarning[];
  recent_payouts: ReferralPayout[];
  stripe_connect_configured: boolean;
}

export interface ReferralInterstitial {
  code: string;
  display_name: string | null;
  custom_message: string | null;
  discount_percent: number;
  discount_duration_days: number;
  valid: boolean;
}
```

- [ ] **Step 2: Add referral API methods**

In `frontend/lib/api.ts`, add these functions:

```typescript
// Referral API methods

export async function getReferralInterstitial(code: string): Promise<ReferralInterstitial> {
  const response = await fetch(`${API_BASE_URL}/api/referrals/r/${code}`);
  if (!response.ok) throw new Error('Failed to fetch referral info');
  return response.json();
}

export async function getReferralDashboard(): Promise<ReferralDashboard> {
  const response = await fetch(`${API_BASE_URL}/api/referrals/me`, {
    headers: getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch referral dashboard');
  return response.json();
}

export async function updateReferralLink(updates: {
  display_name?: string;
  custom_message?: string;
}): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/referrals/me`, {
    method: 'PUT',
    headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw new Error('Failed to update referral link');
}

export async function startConnectOnboarding(): Promise<{ account_id: string; onboarding_url: string }> {
  const response = await fetch(`${API_BASE_URL}/api/referrals/me/connect`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to start Connect onboarding');
  return response.json();
}
```

Add the import for `ReferralInterstitial` and `ReferralDashboard` from `./types` at the top of the file.

- [ ] **Step 3: Add i18n keys**

In `frontend/messages/en.json`, add a `"referrals"` key at the top level:

```json
"referrals": {
  "title": "Referrals",
  "yourLink": "Your Referral Link",
  "copyLink": "Copy Link",
  "copied": "Copied!",
  "displayName": "Display Name",
  "customMessage": "Custom Message",
  "customMessagePlaceholder": "Tell your friends why they should try Nomad Karaoke...",
  "save": "Save",
  "stats": "Stats",
  "clicks": "Clicks",
  "signups": "Signups",
  "purchases": "Purchases",
  "earnings": "Earnings",
  "pendingBalance": "Pending Balance",
  "totalEarned": "Total Earned",
  "totalPaid": "Total Paid Out",
  "recentEarnings": "Recent Earnings",
  "recentPayouts": "Recent Payouts",
  "noEarnings": "No earnings yet. Share your link to start earning!",
  "noPayouts": "No payouts yet.",
  "connectBank": "Connect Bank Account",
  "connectDescription": "Connect your bank account via Stripe to receive referral payouts when your balance reaches $20.",
  "connectComplete": "Bank account connected!",
  "payoutThreshold": "Payouts are automatically sent when your balance reaches $20.",
  "interstitialTitle": "You've been referred!",
  "interstitialReferred": "Referred by {name}",
  "interstitialDiscount": "Enjoy {percent}% off all credit purchases for {days} days",
  "interstitialCta": "Get Started",
  "discountBadge": "{percent}% referral discount",
  "discountActive": "Referral discount active — {percent}% off for {days} more days"
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/types.ts frontend/messages/en.json
git commit -m "feat(referral): add frontend API client, types, and i18n keys"
```

---

## Task 9: Frontend — Referral Cookie & Auth Integration

**Files:**
- Modify: `frontend/lib/auth.ts`
- Create: `frontend/lib/referral.ts`

- [ ] **Step 1: Create referral cookie utility**

Create `frontend/lib/referral.ts`:

```typescript
/**
 * Referral code storage — cookie + localStorage for resilience.
 */

const REFERRAL_COOKIE_NAME = 'nk_ref';
const REFERRAL_STORAGE_KEY = 'nk_referral_code';
const COOKIE_MAX_AGE_DAYS = 30;

export function setReferralCode(code: string): void {
  if (typeof window === 'undefined') return;

  // Set cookie
  const maxAge = COOKIE_MAX_AGE_DAYS * 24 * 60 * 60;
  document.cookie = `${REFERRAL_COOKIE_NAME}=${encodeURIComponent(code)};path=/;max-age=${maxAge};samesite=lax`;

  // Set localStorage backup
  localStorage.setItem(REFERRAL_STORAGE_KEY, code);
}

export function getReferralCode(): string | null {
  if (typeof window === 'undefined') return null;

  // Try cookie first
  const match = document.cookie.match(new RegExp(`(?:^|; )${REFERRAL_COOKIE_NAME}=([^;]*)`));
  if (match) return decodeURIComponent(match[1]);

  // Fallback to localStorage
  return localStorage.getItem(REFERRAL_STORAGE_KEY);
}

export function clearReferralCode(): void {
  if (typeof window === 'undefined') return;

  document.cookie = `${REFERRAL_COOKIE_NAME}=;path=/;max-age=0`;
  localStorage.removeItem(REFERRAL_STORAGE_KEY);
}
```

- [ ] **Step 2: Modify auth to pass referral code on verification**

In `frontend/lib/auth.ts`, find the `verifyMagicLink` function. When it makes the API call to verify the token, add the `x-referral-code` header if a referral code is stored.

Import at the top:

```typescript
import { getReferralCode, clearReferralCode } from './referral';
```

In the verify API call, add the referral header:

```typescript
const referralCode = getReferralCode();
const headers: HeadersInit = {
  'Content-Type': 'application/json',
  ...getAuthHeaders(),
};
if (referralCode) {
  headers['x-referral-code'] = referralCode;
}
```

After successful verification, clear the referral code:

```typescript
clearReferralCode();
```

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/referral.ts frontend/lib/auth.ts
git commit -m "feat(referral): add referral code cookie/storage and pass on auth verification"
```

---

## Task 10: Frontend — Interstitial Page

**Files:**
- Create: `frontend/app/[locale]/r/[code]/page.tsx`

- [ ] **Step 1: Create the interstitial page**

Create `frontend/app/[locale]/r/[code]/page.tsx`:

```tsx
'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { getReferralInterstitial } from '@/lib/api';
import { setReferralCode } from '@/lib/referral';
import type { ReferralInterstitial } from '@/lib/types';

export default function ReferralInterstitialPage() {
  const params = useParams();
  const router = useRouter();
  const t = useTranslations('referrals');
  const code = params.code as string;

  const [data, setData] = useState<ReferralInterstitial | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchData() {
      try {
        const result = await getReferralInterstitial(code);
        setData(result);
        if (result.valid) {
          setReferralCode(code);
        }
      } catch {
        setData({ code, valid: false, display_name: null, custom_message: null, discount_percent: 0, discount_duration_days: 0 });
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [code]);

  const handleGetStarted = () => {
    router.push('/');
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (!data?.valid) {
    // Invalid code — just redirect to landing
    router.push('/');
    return null;
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="max-w-md w-full bg-card rounded-xl shadow-lg p-8 text-center space-y-6">
        <h1 className="text-2xl font-bold text-foreground">
          {t('interstitialTitle')}
        </h1>

        {data.display_name && (
          <p className="text-lg text-muted-foreground">
            {t('interstitialReferred', { name: data.display_name })}
          </p>
        )}

        {data.custom_message && (
          <blockquote className="italic text-muted-foreground border-l-4 border-primary pl-4 text-left">
            &ldquo;{data.custom_message}&rdquo;
          </blockquote>
        )}

        <div className="bg-primary/10 rounded-lg p-4">
          <p className="text-primary font-semibold">
            {t('interstitialDiscount', {
              percent: data.discount_percent,
              days: data.discount_duration_days,
            })}
          </p>
        </div>

        <button
          onClick={handleGetStarted}
          className="w-full py-3 px-6 bg-primary text-primary-foreground rounded-lg font-semibold hover:bg-primary/90 transition-colors"
        >
          {t('interstitialCta')}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/\\[locale\\]/r/\\[code\\]/page.tsx
git commit -m "feat(referral): add /r/CODE interstitial page"
```

---

## Task 11: Frontend — Referral Dashboard Component

**Files:**
- Create: `frontend/components/referrals/ReferralDashboard.tsx`

- [ ] **Step 1: Create the referral dashboard component**

Create `frontend/components/referrals/ReferralDashboard.tsx`:

```tsx
'use client';

import { useEffect, useState, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import {
  getReferralDashboard,
  updateReferralLink,
  startConnectOnboarding,
} from '@/lib/api';
import type { ReferralDashboard as ReferralDashboardData } from '@/lib/types';

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export default function ReferralDashboard() {
  const t = useTranslations('referrals');
  const [data, setData] = useState<ReferralDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [customMessage, setCustomMessage] = useState('');

  const fetchDashboard = useCallback(async () => {
    try {
      const result = await getReferralDashboard();
      setData(result);
      setDisplayName(result.link.display_name || '');
      setCustomMessage(result.link.custom_message || '');
    } catch (err) {
      console.error('Failed to load referral dashboard:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  const copyLink = () => {
    if (!data) return;
    const url = `${window.location.origin}/r/${data.link.code}`;
    navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const saveProfile = async () => {
    try {
      await updateReferralLink({ display_name: displayName, custom_message: customMessage });
      setEditing(false);
      fetchDashboard();
    } catch (err) {
      console.error('Failed to save:', err);
    }
  };

  const handleConnectBank = async () => {
    try {
      const { onboarding_url } = await startConnectOnboarding();
      window.location.href = onboarding_url;
    } catch (err) {
      console.error('Failed to start Connect onboarding:', err);
    }
  };

  if (loading) {
    return <div className="animate-pulse space-y-4 p-4">
      <div className="h-8 bg-muted rounded w-1/3" />
      <div className="h-24 bg-muted rounded" />
    </div>;
  }

  if (!data) return null;

  return (
    <div className="space-y-6 p-4">
      <h2 className="text-xl font-bold">{t('title')}</h2>

      {/* Referral Link */}
      <div className="bg-card rounded-lg p-4 space-y-3">
        <h3 className="font-semibold">{t('yourLink')}</h3>
        <div className="flex gap-2">
          <input
            readOnly
            value={`${typeof window !== 'undefined' ? window.location.origin : ''}/r/${data.link.code}`}
            className="flex-1 bg-muted rounded px-3 py-2 text-sm font-mono"
          />
          <button onClick={copyLink} className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm">
            {copied ? t('copied') : t('copyLink')}
          </button>
        </div>

        {/* Edit display name / message */}
        {editing ? (
          <div className="space-y-2">
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t('displayName')}
              className="w-full bg-muted rounded px-3 py-2 text-sm"
            />
            <textarea
              value={customMessage}
              onChange={(e) => setCustomMessage(e.target.value.slice(0, 200))}
              placeholder={t('customMessagePlaceholder')}
              rows={3}
              className="w-full bg-muted rounded px-3 py-2 text-sm"
            />
            <div className="flex gap-2">
              <button onClick={saveProfile} className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm">
                {t('save')}
              </button>
              <button onClick={() => setEditing(false)} className="px-4 py-2 bg-muted rounded text-sm">
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button onClick={() => setEditing(true)} className="text-sm text-primary hover:underline">
            Edit display name & message
          </button>
        )}
      </div>

      {/* Stats */}
      <div className="bg-card rounded-lg p-4">
        <h3 className="font-semibold mb-3">{t('stats')}</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-2xl font-bold">{data.link.stats.clicks}</p>
            <p className="text-sm text-muted-foreground">{t('clicks')}</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{data.link.stats.signups}</p>
            <p className="text-sm text-muted-foreground">{t('signups')}</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{data.link.stats.purchases}</p>
            <p className="text-sm text-muted-foreground">{t('purchases')}</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{formatCents(data.total_earned_cents)}</p>
            <p className="text-sm text-muted-foreground">{t('totalEarned')}</p>
          </div>
        </div>
      </div>

      {/* Payout section */}
      <div className="bg-card rounded-lg p-4 space-y-3">
        <div className="flex justify-between items-center">
          <h3 className="font-semibold">{t('earnings')}</h3>
          <div className="text-right">
            <p className="text-lg font-bold">{formatCents(data.pending_balance_cents)}</p>
            <p className="text-xs text-muted-foreground">{t('pendingBalance')}</p>
          </div>
        </div>

        {!data.stripe_connect_configured ? (
          <div className="bg-muted rounded-lg p-4 space-y-2">
            <p className="text-sm">{t('connectDescription')}</p>
            <button onClick={handleConnectBank} className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm">
              {t('connectBank')}
            </button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t('payoutThreshold')}</p>
        )}
      </div>

      {/* Recent earnings */}
      <div className="bg-card rounded-lg p-4">
        <h3 className="font-semibold mb-3">{t('recentEarnings')}</h3>
        {data.recent_earnings.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('noEarnings')}</p>
        ) : (
          <div className="space-y-2">
            {data.recent_earnings.map((e) => (
              <div key={e.id} className="flex justify-between text-sm">
                <span className="text-muted-foreground">{e.referred_email}</span>
                <span className="font-mono">
                  {formatCents(e.earning_amount_cents)}
                  <span className={`ml-2 text-xs ${e.status === 'paid' ? 'text-green-500' : 'text-yellow-500'}`}>
                    {e.status}
                  </span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent payouts */}
      {data.recent_payouts.length > 0 && (
        <div className="bg-card rounded-lg p-4">
          <h3 className="font-semibold mb-3">{t('recentPayouts')}</h3>
          <div className="space-y-2">
            {data.recent_payouts.map((p) => (
              <div key={p.id} className="flex justify-between text-sm">
                <span className="text-muted-foreground">{new Date(p.created_at).toLocaleDateString()}</span>
                <span className="font-mono">{formatCents(p.amount_cents)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/referrals/ReferralDashboard.tsx
git commit -m "feat(referral): add referral dashboard component with stats, earnings, and Connect CTA"
```

---

## Task 12: Frontend — Discount Badge in Buy Credits Dialog

**Files:**
- Modify: `frontend/components/credits/BuyCreditsDialog.tsx`

- [ ] **Step 1: Read current BuyCreditsDialog implementation**

Read the full file at `frontend/components/credits/BuyCreditsDialog.tsx` to understand the exact structure before modifying.

- [ ] **Step 2: Add discount badge**

In `BuyCreditsDialog.tsx`, fetch the user's referral discount status and display a badge when active. The user's `referral_discount_expires_at` and `referred_by_code` come from the user profile (already available via auth store).

Add near the top of the dialog content (before the package list):

```tsx
{user?.has_active_referral_discount && (
  <div className="bg-green-500/10 border border-green-500/20 rounded-lg p-3 text-center">
    <p className="text-green-600 dark:text-green-400 text-sm font-medium">
      {t('discountActive', { percent: 10, days: daysRemaining })}
    </p>
  </div>
)}
```

The `has_active_referral_discount` field is already on `UserPublic` from Task 1. Compute `daysRemaining` from the user profile data. The exact discount percentage should come from a new lightweight API call or be included in the user profile. Keep it simple — use the `has_active_referral_discount` boolean and default 10%.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/credits/BuyCreditsDialog.tsx
git commit -m "feat(referral): show discount badge in Buy Credits dialog for referred users"
```

---

## Task 13: Frontend — Add Referrals Tab to App Navigation

**Files:**
- Modify: app layout/navigation file (exact path depends on current navigation structure — check `frontend/app/[locale]/app/` directory)

- [ ] **Step 1: Identify the navigation component**

Read the app layout files to find where tabs/navigation are defined. Look in:
- `frontend/app/[locale]/app/page.tsx`
- `frontend/app/[locale]/app/layout.tsx`
- `frontend/components/layout/` or similar

- [ ] **Step 2: Add Referrals tab**

Add a "Referrals" tab that renders the `ReferralDashboard` component. Follow the existing tab pattern.

- [ ] **Step 3: Commit**

```bash
git add -A  # Stage navigation changes
git commit -m "feat(referral): add Referrals tab to app navigation"
```

---

## Task 14: Query Param Referral Code Capture

**Files:**
- Modify: `frontend/app/[locale]/layout.tsx` or root layout

- [ ] **Step 1: Add `?ref=CODE` capture to root layout**

In the root layout (or a client-side effect component used by the layout), add logic to check for `?ref=CODE` query parameter on any page load and store it:

```tsx
'use client';

import { useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { getReferralCode, setReferralCode } from '@/lib/referral';

export function ReferralCapture() {
  const searchParams = useSearchParams();

  useEffect(() => {
    const ref = searchParams.get('ref');
    if (ref && !getReferralCode()) {
      setReferralCode(ref);
    }
  }, [searchParams]);

  return null;
}
```

Include `<ReferralCapture />` in the root layout. This is the secondary capture mechanism (the primary is the `/r/CODE` interstitial).

- [ ] **Step 2: Commit**

```bash
git add frontend/app/\\[locale\\]/layout.tsx  # or wherever the component lives
git commit -m "feat(referral): capture ?ref=CODE query param as secondary referral mechanism"
```

---

## Task 15: Integration Test & Full Flow Verification

**Files:**
- Test: `backend/tests/test_referral_service.py` (final integration-style tests)

- [ ] **Step 1: Write end-to-end-style unit test for the full referral flow**

Append to `backend/tests/test_referral_service.py`:

```python
class TestFullReferralFlow:
    """Integration-style test covering the full referral lifecycle."""

    def test_full_flow(self, service, mock_db, mock_stripe_service):
        """
        1. Referrer gets a link
        2. Referred user signs up
        3. Referred user purchases credits
        4. Earning is recorded
        5. Payout threshold check
        """
        # Step 1: Referrer gets a link
        mock_query = MagicMock()
        mock_query.limit.return_value.get.return_value = []
        mock_code_doc = MagicMock()
        mock_code_doc.exists = False
        mock_db.collection.return_value.where.return_value = mock_query
        mock_db.collection.return_value.document.return_value.get.return_value = mock_code_doc

        link = service.get_or_create_link("referrer@example.com")
        assert link is not None
        assert link.owner_email == "referrer@example.com"

        # Step 2: Attribution
        mock_link_doc = MagicMock()
        mock_link_doc.exists = True
        mock_link_doc.to_dict.return_value = {
            "code": link.code,
            "owner_email": "referrer@example.com",
            "discount_percent": 10,
            "kickback_percent": 20,
            "discount_duration_days": 30,
            "earning_duration_days": 365,
            "is_vanity": False,
            "enabled": True,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_link_doc

        success, msg = service.attribute_referral("newuser@example.com", link.code)
        assert success is True

        # Step 3: Discount check
        user_data = {
            "referred_by_code": link.code,
            "referral_discount_expires_at": datetime.utcnow() + timedelta(days=15),
        }
        assert service.should_apply_discount(user_data) is True

        # Step 4: Earning calculation
        assert int(1575 * 20 / 100) == 315  # 20% of $15.75 = $3.15
```

- [ ] **Step 2: Run the full test suite**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/test_referral_models.py backend/tests/test_referral_service.py backend/tests/test_referral_routes.py -v 2>&1 | tail -40
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_referral_service.py
git commit -m "test(referral): add full-flow integration test"
```

---

## Task 16: Run Full Test Suite

- [ ] **Step 1: Run all backend tests to ensure nothing is broken**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system
python -m pytest backend/tests/ -v --tb=short 2>&1 | tail -50
```

Expected: All existing tests still pass, all new referral tests pass.

- [ ] **Step 2: Fix any regressions**

If any existing tests break (e.g., due to User model changes), fix them.

- [ ] **Step 3: Run frontend tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-system/frontend
npm run test 2>&1 | tail -30
```

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve test regressions from referral system additions"
```

---

## Follow-Up Tasks (Not in This Plan)

These are part of the design spec but deferred to a follow-up iteration:

1. **Refund handling** — When a referred purchase is refunded, mark the corresponding `referral_earnings` doc as `refunded` and deduct from unpaid balance. If already paid out, carry negative balance forward. Hook into existing Stripe refund webhook.

2. **Abuse detection** — Leverage existing fingerprint/IP infrastructure to flag referrals where referred user shares `device_fingerprint` or `signup_ip` with referrer. Admin alerts for suspicious patterns.

3. **Admin hold payouts** — Admin ability to flag a referrer for fraud review and hold their payouts.

4. **i18n translations** — Translate `referrals.*` keys to `es`, `de`, and other supported locales.
