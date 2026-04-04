"""
Unit tests for referral system Pydantic models.

Tests validate:
- ReferralLinkStats defaults
- ReferralLink default and custom values
- ReferralEarning pending status default
- ReferralPayout creation with earnings list
- User model referral fields default to None
- UserPublic referral fields
- API request/response model shapes
"""
import pytest
from datetime import datetime, UTC
from pydantic import ValidationError

from backend.models.referral import (
    ReferralLinkStats,
    ReferralLink,
    ReferralEarning,
    ReferralPayout,
    CreateReferralLinkRequest,
    UpdateReferralLinkRequest,
    ReferralLinkResponse,
    ReferralEarningResponse,
    ReferralPayoutResponse,
    ReferralDashboardResponse,
    ReferralInterstitialResponse,
)
from backend.models.user import User, UserPublic, UserRole


class TestReferralLinkStats:
    """Test ReferralLinkStats model defaults."""

    def test_all_defaults_zero(self):
        stats = ReferralLinkStats()
        assert stats.clicks == 0
        assert stats.signups == 0
        assert stats.purchases == 0
        assert stats.total_earned_cents == 0

    def test_custom_values(self):
        stats = ReferralLinkStats(clicks=10, signups=5, purchases=2, total_earned_cents=5000)
        assert stats.clicks == 10
        assert stats.signups == 5
        assert stats.purchases == 2
        assert stats.total_earned_cents == 5000


class TestReferralLink:
    """Test ReferralLink model defaults and custom values."""

    def test_default_values(self):
        link = ReferralLink(
            code="abc123",
            owner_email="user@example.com",
        )
        assert link.code == "abc123"
        assert link.owner_email == "user@example.com"
        assert link.discount_percent == 10
        assert link.kickback_percent == 20
        assert link.discount_duration_days == 30
        assert link.earning_duration_days == 365
        assert link.is_vanity is False
        assert link.enabled is True
        assert link.display_name is None
        assert link.custom_message is None
        assert link.stripe_coupon_id is None
        # Stats should be all zeros
        assert link.stats.clicks == 0
        assert link.stats.signups == 0
        assert link.stats.purchases == 0
        assert link.stats.total_earned_cents == 0

    def test_vanity_link_with_custom_values(self):
        link = ReferralLink(
            code="karaoke-king",
            owner_email="king@example.com",
            display_name="The Karaoke King",
            custom_message="Sing with me!",
            discount_percent=15,
            kickback_percent=25,
            discount_duration_days=60,
            earning_duration_days=730,
            is_vanity=True,
            stripe_coupon_id="coupon_abc",
        )
        assert link.code == "karaoke-king"
        assert link.display_name == "The Karaoke King"
        assert link.custom_message == "Sing with me!"
        assert link.discount_percent == 15
        assert link.kickback_percent == 25
        assert link.discount_duration_days == 60
        assert link.earning_duration_days == 730
        assert link.is_vanity is True
        assert link.stripe_coupon_id == "coupon_abc"

    def test_timestamps_set_automatically(self):
        link = ReferralLink(code="test", owner_email="a@b.com")
        assert isinstance(link.created_at, datetime)
        assert isinstance(link.updated_at, datetime)


class TestReferralEarning:
    """Test ReferralEarning model."""

    def test_pending_status_default(self):
        earning = ReferralEarning(
            id="earn_001",
            referrer_email="referrer@example.com",
            referred_email="newuser@example.com",
            referral_code="abc123",
            stripe_session_id="cs_test_123",
            purchase_amount_cents=2999,
            earning_amount_cents=600,
        )
        assert earning.status == "pending"
        assert earning.paid_at is None
        assert earning.payout_id is None
        assert isinstance(earning.created_at, datetime)

    def test_custom_status(self):
        earning = ReferralEarning(
            id="earn_002",
            referrer_email="referrer@example.com",
            referred_email="newuser@example.com",
            referral_code="abc123",
            stripe_session_id="cs_test_456",
            purchase_amount_cents=2999,
            earning_amount_cents=600,
            status="paid",
        )
        assert earning.status == "paid"


class TestReferralPayout:
    """Test ReferralPayout model."""

    def test_creation_with_earnings_list(self):
        payout = ReferralPayout(
            id="payout_001",
            referrer_email="referrer@example.com",
            stripe_transfer_id="tr_test_123",
            amount_cents=1200,
            earnings_included=["earn_001", "earn_002"],
        )
        assert payout.id == "payout_001"
        assert payout.amount_cents == 1200
        assert payout.earnings_included == ["earn_001", "earn_002"]
        assert payout.status == "processing"
        assert isinstance(payout.created_at, datetime)

    def test_empty_earnings_list(self):
        payout = ReferralPayout(
            id="payout_002",
            referrer_email="referrer@example.com",
            stripe_transfer_id="tr_test_456",
            amount_cents=0,
            earnings_included=[],
        )
        assert payout.earnings_included == []


class TestUserReferralFields:
    """Test that User model has referral fields defaulting to None."""

    def test_referral_fields_default_none(self):
        user = User(email="test@example.com")
        assert user.referral_code is None
        assert user.referred_by_code is None
        assert user.referred_at is None
        assert user.referral_discount_expires_at is None
        assert user.stripe_connect_account_id is None

    def test_referral_fields_set(self):
        now = datetime.now(UTC)
        user = User(
            email="test@example.com",
            referral_code="mycode",
            referred_by_code="friendcode",
            referred_at=now,
            referral_discount_expires_at=now,
            stripe_connect_account_id="acct_123",
        )
        assert user.referral_code == "mycode"
        assert user.referred_by_code == "friendcode"
        assert user.referred_at == now
        assert user.referral_discount_expires_at == now
        assert user.stripe_connect_account_id == "acct_123"


class TestUserPublicReferralFields:
    """Test UserPublic has referral fields."""

    def test_defaults(self):
        pub = UserPublic(email="test@example.com", role=UserRole.USER, credits=0)
        assert pub.referral_code is None
        assert pub.has_active_referral_discount is False

    def test_with_referral(self):
        pub = UserPublic(
            email="test@example.com",
            role=UserRole.USER,
            credits=5,
            referral_code="mycode",
            has_active_referral_discount=True,
        )
        assert pub.referral_code == "mycode"
        assert pub.has_active_referral_discount is True


class TestReferralAPIModels:
    """Test API request/response models exist and have correct shapes."""

    def test_create_request(self):
        req = CreateReferralLinkRequest(owner_email="user@example.com", display_name="My Link")
        assert req.display_name == "My Link"
        assert req.owner_email == "user@example.com"
        assert req.discount_percent == 10
        assert req.kickback_percent == 20

    def test_update_request(self):
        req = UpdateReferralLinkRequest(enabled=False)
        assert req.enabled is False

    def test_link_response(self):
        resp = ReferralLinkResponse(
            code="abc",
            owner_email="a@b.com",
            discount_percent=10,
            kickback_percent=20,
            is_vanity=False,
            enabled=True,
            stats=ReferralLinkStats(),
            created_at=datetime.now(UTC),
        )
        assert resp.code == "abc"

    def test_earning_response(self):
        resp = ReferralEarningResponse(
            id="e1",
            referred_email="new@b.com",
            referral_code="abc",
            purchase_amount_cents=2999,
            earning_amount_cents=600,
            status="pending",
            created_at=datetime.now(UTC),
        )
        assert resp.status == "pending"

    def test_payout_response(self):
        resp = ReferralPayoutResponse(
            id="p1",
            amount_cents=1200,
            earnings_count=2,
            status="processing",
            created_at=datetime.now(UTC),
        )
        assert resp.amount_cents == 1200

    def test_dashboard_response(self):
        resp = ReferralDashboardResponse(
            referral_link=ReferralLinkResponse(
                code="abc",
                owner_email="a@b.com",
                discount_percent=10,
                kickback_percent=20,
                is_vanity=False,
                enabled=True,
                stats=ReferralLinkStats(),
                created_at=datetime.now(UTC),
            ),
            recent_earnings=[],
            recent_payouts=[],
            total_earned_cents=0,
            total_paid_cents=0,
            pending_balance_cents=0,
            stripe_connect_onboarded=False,
        )
        assert resp.total_earned_cents == 0

    def test_interstitial_response(self):
        resp = ReferralInterstitialResponse(
            referral_code="abc",
            referrer_display_name="Friend",
            discount_percent=10,
            discount_duration_days=30,
        )
        assert resp.referral_code == "abc"
        assert resp.referrer_display_name == "Friend"
