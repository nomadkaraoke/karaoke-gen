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
from unittest.mock import MagicMock
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
