"""
Referral system models.

Supports:
- Referral link creation and management
- Earnings tracking from referred purchases
- Payout processing via Stripe Connect
- API request/response shapes for referral endpoints
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ============================================================================
# Firestore Document Models
# ============================================================================

class ReferralLinkStats(BaseModel):
    """Aggregated statistics for a referral link."""
    clicks: int = 0
    signups: int = 0
    purchases: int = 0
    total_earned_cents: int = 0


class ReferralLink(BaseModel):
    """
    Referral link stored in Firestore.

    Each user can have one referral link. Vanity links use a custom code
    (e.g., "karaoke-king") while standard links use a generated code.
    """
    code: str  # Unique referral code (URL slug)
    owner_email: str  # Email of the referrer
    display_name: Optional[str] = None  # Public name shown on interstitial
    custom_message: Optional[str] = None  # Optional message on interstitial

    # Reward configuration
    discount_percent: int = 10  # Discount for referred user
    kickback_percent: int = 20  # Earnings for referrer
    discount_duration_days: int = 30  # How long the discount lasts
    earning_duration_days: int = 365  # How long referrer earns from this link

    # Stripe
    stripe_coupon_id: Optional[str] = None  # Stripe coupon for this link

    # Flags
    is_vanity: bool = False  # Custom vanity code vs generated
    enabled: bool = True  # Can be disabled without deleting

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Aggregated stats
    stats: ReferralLinkStats = Field(default_factory=ReferralLinkStats)


class ReferralEarning(BaseModel):
    """
    Record of a single earning from a referred purchase.

    Created when a referred user completes a Stripe checkout.
    """
    id: str
    referrer_email: str
    referred_email: str
    referral_code: str
    stripe_session_id: str
    purchase_amount_cents: int
    earning_amount_cents: int
    status: str = "pending"  # pending, paid, cancelled
    created_at: datetime = Field(default_factory=datetime.utcnow)
    paid_at: Optional[datetime] = None
    payout_id: Optional[str] = None


class ReferralPayout(BaseModel):
    """
    Record of a payout to a referrer via Stripe Connect.

    Groups multiple earnings into a single transfer.
    """
    id: str
    referrer_email: str
    stripe_transfer_id: str
    amount_cents: int
    earnings_included: List[str]  # List of ReferralEarning IDs
    status: str = "processing"  # processing, completed, failed
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# API Request Models
# ============================================================================

class CreateReferralLinkRequest(BaseModel):
    """Admin request to create a vanity referral link."""
    vanity_code: Optional[str] = None
    owner_email: str
    display_name: Optional[str] = None
    custom_message: Optional[str] = None
    discount_percent: int = 10
    kickback_percent: int = 20
    discount_duration_days: int = 30
    earning_duration_days: int = 365


class UpdateReferralLinkRequest(BaseModel):
    """Request to update a referral link (user: name/message only, admin: all fields)."""
    display_name: Optional[str] = None
    custom_message: Optional[str] = None
    discount_percent: Optional[int] = None
    kickback_percent: Optional[int] = None
    discount_duration_days: Optional[int] = None
    earning_duration_days: Optional[int] = None
    enabled: Optional[bool] = None


# ============================================================================
# API Response Models
# ============================================================================

class ReferralLinkResponse(BaseModel):
    """Public referral link information."""
    code: str
    owner_email: str
    display_name: Optional[str] = None
    custom_message: Optional[str] = None
    discount_percent: int
    kickback_percent: int
    is_vanity: bool
    enabled: bool
    stats: ReferralLinkStats
    created_at: datetime


class ReferralEarningResponse(BaseModel):
    """Public earning information (for referrer dashboard)."""
    id: str
    referred_email: str
    referral_code: str
    purchase_amount_cents: int
    earning_amount_cents: int
    status: str
    created_at: datetime
    paid_at: Optional[datetime] = None


class ReferralPayoutResponse(BaseModel):
    """Public payout information (for referrer dashboard)."""
    id: str
    amount_cents: int
    earnings_count: int
    status: str
    created_at: datetime


class ReferralDashboardResponse(BaseModel):
    """Full referral dashboard data for the referrer."""
    referral_link: ReferralLinkResponse
    recent_earnings: List[ReferralEarningResponse]
    recent_payouts: List[ReferralPayoutResponse]
    total_earned_cents: int
    total_paid_cents: int
    pending_balance_cents: int
    stripe_connect_onboarded: bool


class ReferralInterstitialResponse(BaseModel):
    """Data for the referral landing/interstitial page."""
    valid: bool = True
    referral_code: str = ""
    referrer_display_name: Optional[str] = None
    discount_percent: int = 0
    discount_duration_days: int = 0
    custom_message: Optional[str] = None
