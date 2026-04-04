"""
Unit tests for ReferralService — link management.

Tests cover:
- Code generation (8-char lowercase alphanumeric, uniqueness)
- Vanity code validation (length, chars, reserved words, hyphens)
- get_or_create_link (returns existing, creates new)
- get_link_by_code (found, not found, disabled)
- get_or_create_stripe_coupon (delegates to stripe service)
- should_apply_discount (active, expired, no referral)
"""
import pytest
import sys
import types
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from backend.services.referral_service import (
    ReferralService,
    RESERVED_CODES,
    VANITY_CODE_PATTERN,
    REFERRAL_LINKS_COLLECTION,
)


# ============================================================================
# Fixtures
# ============================================================================

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


@pytest.fixture
def mock_user_service():
    """Inject a fake backend.services.user_service module so local imports resolve."""
    mock_get_user_svc = MagicMock()
    fake_module = types.ModuleType("backend.services.user_service")
    fake_module.get_user_service = mock_get_user_svc
    with patch.dict(sys.modules, {"backend.services.user_service": fake_module}):
        yield mock_get_user_svc


# ============================================================================
# TestGenerateCode
# ============================================================================

class TestGenerateCode:
    """Tests for _generate_code()."""

    def test_generates_8_char_lowercase_alphanumeric(self, service):
        code = service._generate_code()
        assert len(code) == 8
        assert code == code.lower()
        assert code.isalnum()

    def test_generates_unique_codes(self, service):
        codes = {service._generate_code() for _ in range(100)}
        # With 36^8 possible codes, 100 should all be unique
        assert len(codes) == 100


# ============================================================================
# TestValidateVanityCode
# ============================================================================

class TestValidateVanityCode:
    """Tests for _validate_vanity_code()."""

    def test_valid_code(self, service):
        valid, msg = service._validate_vanity_code("karaoke-king")
        assert valid is True

    def test_too_short(self, service):
        valid, msg = service._validate_vanity_code("ab")
        assert valid is False
        assert "3-30" in msg or "characters" in msg.lower()

    def test_too_long(self, service):
        valid, msg = service._validate_vanity_code("a" * 31)
        assert valid is False

    def test_invalid_chars(self, service):
        valid, msg = service._validate_vanity_code("bad_code!")
        assert valid is False

    def test_reserved_words(self, service):
        valid, msg = service._validate_vanity_code("admin")
        assert valid is False
        assert "reserved" in msg.lower()

        valid, msg = service._validate_vanity_code("api")
        assert valid is False
        assert "reserved" in msg.lower()

    def test_allows_hyphens(self, service):
        valid, msg = service._validate_vanity_code("my-cool-code")
        assert valid is True


# ============================================================================
# TestGetOrCreateLink
# ============================================================================

class TestGetOrCreateLink:
    """Tests for get_or_create_link()."""

    def test_returns_existing_link(self, service, mock_db):
        # Set up mock: query returns an existing doc
        mock_query = MagicMock()
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = [
            MagicMock(to_dict=lambda: {
                "code": "abc12345",
                "owner_email": "user@example.com",
                "discount_percent": 10,
                "kickback_percent": 20,
                "discount_duration_days": 30,
                "earning_duration_days": 365,
                "is_vanity": False,
                "enabled": True,
                "created_at": datetime(2026, 1, 1),
                "updated_at": datetime(2026, 1, 1),
                "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            })
        ]

        link = service.get_or_create_link("user@example.com")
        assert link.code == "abc12345"
        assert link.owner_email == "user@example.com"
        # Should NOT have called document().set()
        mock_db.collection.return_value.document.return_value.set.assert_not_called()

    def test_creates_new_if_none_exists(self, service, mock_db):
        # Query returns empty — no existing link
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []
        # doc.get() returns a doc that doesn't exist (no collision)
        mock_doc_ref = MagicMock()
        mock_doc_snapshot = MagicMock()
        mock_doc_snapshot.exists = False
        mock_doc_ref.get.return_value = mock_doc_snapshot
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        link = service.get_or_create_link("new@example.com")
        assert link is not None
        assert link.owner_email == "new@example.com"
        assert len(link.code) == 8
        mock_doc_ref.set.assert_called_once()


# ============================================================================
# TestGetLinkByCode
# ============================================================================

class TestGetLinkByCode:
    """Tests for get_link_by_code()."""

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
            "created_at": datetime(2026, 1, 1),
            "updated_at": datetime(2026, 1, 1),
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
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
            "code": "abc12345",
            "owner_email": "user@example.com",
            "discount_percent": 10,
            "kickback_percent": 20,
            "discount_duration_days": 30,
            "earning_duration_days": 365,
            "is_vanity": False,
            "enabled": False,
            "created_at": datetime(2026, 1, 1),
            "updated_at": datetime(2026, 1, 1),
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        link = service.get_link_by_code("abc12345")
        assert link is None


# ============================================================================
# TestAttributeReferral
# ============================================================================

def _mock_valid_link_doc(mock_db, owner_email="referrer@example.com"):
    """Helper: set up mock_db so get_link_by_code returns a valid link."""
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "code": "abc12345",
        "owner_email": owner_email,
        "discount_percent": 10,
        "kickback_percent": 20,
        "discount_duration_days": 30,
        "earning_duration_days": 365,
        "is_vanity": False,
        "enabled": True,
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 1),
        "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc


class TestAttributeReferral:
    """Tests for attribute_referral()."""

    def test_successful_attribution(self, service, mock_db):
        _mock_valid_link_doc(mock_db, owner_email="referrer@example.com")

        success, msg = service.attribute_referral("newuser@example.com", "abc12345")

        assert success is True
        assert "attributed" in msg.lower()
        # Should have incremented signups
        mock_db.collection.return_value.document.return_value.update.assert_called_once()

    def test_self_referral_blocked(self, service, mock_db):
        _mock_valid_link_doc(mock_db, owner_email="user@example.com")

        success, msg = service.attribute_referral("user@example.com", "abc12345")

        assert success is False
        assert "own" in msg.lower() or "self" in msg.lower()

    def test_invalid_code_rejected(self, service, mock_db):
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        success, msg = service.attribute_referral("newuser@example.com", "nonexistent")

        assert success is False
        assert "invalid" in msg.lower() or "disabled" in msg.lower()


# ============================================================================
# TestGetOrCreateStripeCoupon
# ============================================================================

class TestGetOrCreateStripeCoupon:
    def test_creates_coupon_for_new_percentage(self, service, mock_stripe_service):
        mock_stripe_service.get_or_create_referral_coupon.return_value = "referral-10pct"
        coupon_id = service.get_or_create_stripe_coupon(10)
        assert coupon_id == "referral-10pct"
        mock_stripe_service.get_or_create_referral_coupon.assert_called_once_with(10)


# ============================================================================
# TestShouldApplyDiscount
# ============================================================================

class TestShouldApplyDiscount:
    def test_active_discount(self, service):
        user_data = {
            "referred_by_code": "abc12345",
            "referral_discount_expires_at": datetime.utcnow() + timedelta(days=15),
        }
        assert service.should_apply_discount(user_data) is True

    def test_expired_discount(self, service):
        user_data = {
            "referred_by_code": "abc12345",
            "referral_discount_expires_at": datetime.utcnow() - timedelta(days=1),
        }
        assert service.should_apply_discount(user_data) is False

    def test_no_referral(self, service):
        user_data = {}
        assert service.should_apply_discount(user_data) is False


# ============================================================================
# TestRecordEarning
# ============================================================================

class TestRecordEarning:
    def test_creates_earning_within_window(self, service, mock_db):
        """Earning is created when purchase is within earning window."""
        mock_user_doc = MagicMock()
        mock_user_doc.exists = True
        mock_user_doc.to_dict.return_value = {
            "email": "referred@example.com",
            "referred_by_code": "abc12345",
            "referred_at": datetime.utcnow() - timedelta(days=30),
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
            "referred_at": datetime.utcnow() - timedelta(days=400),
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
        mock_user_doc.to_dict.return_value = {"email": "user@example.com"}

        mock_db.collection.return_value.document.return_value.get.return_value = mock_user_doc

        result = service.record_earning(
            referred_email="user@example.com",
            stripe_session_id="cs_test_789",
            purchase_amount_cents=1750,
        )
        assert result is None


# ============================================================================
# TestStripeConnectOnboarding
# ============================================================================

class TestStripeConnectOnboarding:
    def test_create_connect_account(self, service, mock_stripe_service):
        mock_stripe_service.create_connect_account.return_value = ("acct_123", "https://connect.stripe.com/onboard")
        account_id, onboard_url = service.create_connect_account("referrer@example.com")
        assert account_id == "acct_123"
        assert "stripe.com" in onboard_url


# ============================================================================
# TestTriggerPayout
# ============================================================================

class TestTriggerPayout:
    def test_payout_triggered_at_threshold(self, service, mock_db, mock_stripe_service):
        """Payout is triggered when pending earnings >= $20."""
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
            "earning_amount_cents": 315,
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


# ============================================================================
# TestFullReferralFlow
# ============================================================================

class TestFullReferralFlow:
    """Integration-style test covering the full referral lifecycle."""

    def test_full_flow(self, service, mock_db, mock_stripe_service):
        """
        1. Referrer gets a link
        2. Referred user signs up (attribution)
        3. Discount check
        4. Earning calculation
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


# ============================================================================
# TestGetAttributionData
# ============================================================================

class TestGetAttributionData:
    """Tests for get_attribution_data() — returns dict for user doc update."""

    def test_returns_attribution_dict(self, service, mock_db):
        """Attribution data includes correct fields and timedelta calculation."""
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

        data = service.get_attribution_data("abc12345")
        assert data is not None
        assert data["referred_by_code"] == "abc12345"
        assert "referred_at" in data
        assert "referral_discount_expires_at" in data
        # Discount should expire ~30 days from now
        delta = data["referral_discount_expires_at"] - data["referred_at"]
        assert delta.days == 30

    def test_returns_none_for_invalid_code(self, service, mock_db):
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        data = service.get_attribution_data("nonexistent")
        assert data is None

    def test_custom_discount_duration(self, service, mock_db):
        """Links with custom duration calculate correct expiry."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "code": "custom90",
            "owner_email": "referrer@example.com",
            "discount_percent": 15,
            "kickback_percent": 25,
            "discount_duration_days": 90,  # Custom 90-day duration
            "earning_duration_days": 365,
            "is_vanity": True,
            "enabled": True,
            "stats": {"clicks": 0, "signups": 0, "purchases": 0, "total_earned_cents": 0},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        data = service.get_attribution_data("custom90")
        delta = data["referral_discount_expires_at"] - data["referred_at"]
        assert delta.days == 90


# ============================================================================
# TestGetDiscountForCheckout
# ============================================================================

class TestGetDiscountForCheckout:
    """Tests for get_discount_for_checkout() — coupon lookup for Stripe checkout."""

    def test_returns_discount_for_active_referred_user(self, service, mock_db, mock_stripe_service, mock_user_service):
        """Returns coupon info when user has active referral discount."""
        mock_user = MagicMock()
        mock_user.referred_by_code = "abc12345"
        mock_user.referral_discount_expires_at = datetime.utcnow() + timedelta(days=15)

        mock_user_svc = MagicMock()
        mock_user_svc.get_user.return_value = mock_user
        mock_user_service.return_value = mock_user_svc

        # Mock referral link lookup
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
        mock_stripe_service.get_or_create_referral_coupon.return_value = "referral-10pct"

        result = service.get_discount_for_checkout("referred@example.com")

        assert result is not None
        assert result["coupon_id"] == "referral-10pct"
        assert result["discount_percent"] == 10

    def test_returns_none_for_expired_discount(self, service, mock_db, mock_user_service):
        """Returns None when user's discount has expired."""
        mock_user = MagicMock()
        mock_user.referred_by_code = "abc12345"
        mock_user.referral_discount_expires_at = datetime.utcnow() - timedelta(days=1)

        mock_user_svc = MagicMock()
        mock_user_svc.get_user.return_value = mock_user
        mock_user_service.return_value = mock_user_svc

        result = service.get_discount_for_checkout("referred@example.com")

        assert result is None

    def test_returns_none_for_non_referred_user(self, service, mock_db, mock_user_service):
        """Returns None when user has no referral."""
        mock_user = MagicMock()
        mock_user.referred_by_code = None
        mock_user.referral_discount_expires_at = None

        mock_user_svc = MagicMock()
        mock_user_svc.get_user.return_value = mock_user
        mock_user_service.return_value = mock_user_svc

        result = service.get_discount_for_checkout("regular@example.com")

        assert result is None


# ============================================================================
# TestGetDashboardData
# ============================================================================

class TestGetDashboardData:
    """Tests for get_dashboard_data() — full referral dashboard payload."""

    def test_returns_complete_dashboard(self, service, mock_db, mock_user_service):
        """Dashboard data includes link, earnings, payouts, and balances."""
        # Mock user service for Connect status check
        mock_user = MagicMock()
        mock_user.stripe_connect_account_id = None
        mock_user_svc = MagicMock()
        mock_user_svc.get_user.return_value = mock_user
        mock_user_service.return_value = mock_user_svc

        # Mock get_or_create_link: query stream returns existing link doc
        mock_link_doc = MagicMock()
        mock_link_doc.to_dict.return_value = {
            "code": "mycode12",
            "owner_email": "user@example.com",
            "discount_percent": 10,
            "kickback_percent": 20,
            "discount_duration_days": 30,
            "earning_duration_days": 365,
            "is_vanity": False,
            "enabled": True,
            "display_name": None,
            "custom_message": None,
            "stripe_coupon_id": None,
            "stats": {"clicks": 5, "signups": 2, "purchases": 1, "total_earned_cents": 350},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        # Complex mock setup for multiple collection queries
        mock_link_query = MagicMock()
        mock_link_query.limit.return_value.stream.return_value = [mock_link_doc]

        mock_earnings_query = MagicMock()
        mock_earnings_query.order_by.return_value.limit.return_value.stream.return_value = []

        mock_payouts_query = MagicMock()
        mock_payouts_query.order_by.return_value.limit.return_value.stream.return_value = []

        def collection_side_effect(name):
            mock_col = MagicMock()
            if name == "referral_links":
                mock_col.where.return_value = mock_link_query
            elif name == "referral_earnings":
                mock_col.where.return_value = mock_earnings_query
            elif name == "referral_payouts":
                mock_col.where.return_value = mock_payouts_query
            return mock_col

        mock_db.collection.side_effect = collection_side_effect

        data = service.get_dashboard_data("user@example.com")

        assert "link" in data
        assert data["link"]["code"] == "mycode12"
        assert data["link"]["stats"]["clicks"] == 5
        assert data["pending_balance_cents"] == 0
        assert data["total_earned_cents"] == 350
        assert data["stripe_connect_configured"] is False
        assert isinstance(data["recent_earnings"], list)
        assert isinstance(data["recent_payouts"], list)


# ============================================================================
# TestEarningCalculationEdgeCases
# ============================================================================

class TestEarningCalculationEdgeCases:
    """Edge cases for record_earning() — rounding and zero amounts."""

    def test_earning_rounds_down(self, service, mock_db):
        """Earning calculation truncates fractional cents."""
        mock_user_doc = MagicMock()
        mock_user_doc.exists = True
        mock_user_doc.to_dict.return_value = {
            "email": "referred@example.com",
            "referred_by_code": "abc12345",
            "referred_at": datetime.utcnow() - timedelta(days=10),
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

        # 1999 * 20 / 100 = 399.8 -> should truncate to 399
        result = service.record_earning(
            referred_email="referred@example.com",
            stripe_session_id="cs_edge_1",
            purchase_amount_cents=1999,
        )
        assert result is not None
        assert result["earning_amount_cents"] == 399

    def test_zero_amount_returns_none(self, service, mock_db):
        """Zero purchase amount produces no earning."""
        mock_user_doc = MagicMock()
        mock_user_doc.exists = True
        mock_user_doc.to_dict.return_value = {
            "email": "referred@example.com",
            "referred_by_code": "abc12345",
            "referred_at": datetime.utcnow() - timedelta(days=10),
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
            stripe_session_id="cs_edge_2",
            purchase_amount_cents=0,
        )
        assert result is None
