"""
User model for authentication and credits.

Supports:
- Magic link authentication (email-based)
- Credit system for karaoke generation
- Role-based access control (user/admin)
- Stripe integration for payments
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field


class UserRole(str, Enum):
    """User roles for access control."""
    USER = "user"
    ADMIN = "admin"


class CreditTransaction(BaseModel):
    """Record of a credit transaction."""
    id: str
    amount: int  # Positive = add, negative = deduct
    reason: str  # e.g., "purchase", "refund", "admin_grant", "job_creation"
    job_id: Optional[str] = None
    stripe_session_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None  # Admin email if granted by admin


class PushSubscription(BaseModel):
    """
    Web Push subscription for a user's device.

    Stores the push subscription endpoint and encryption keys needed
    to send push notifications to the user's browser/device.

    Subscriptions are scoped by tenant_id to prevent cross-tenant
    notification leakage (e.g., a consumer job notification appearing
    on a tenant portal's service worker).
    """
    endpoint: str  # Push service endpoint URL
    keys: Dict[str, str]  # p256dh and auth keys for encryption
    device_name: Optional[str] = None  # e.g., "iPhone", "Chrome on Windows"
    tenant_id: Optional[str] = None  # Tenant scope (None = consumer/default portal)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: Optional[datetime] = None  # Last time a notification was sent


class User(BaseModel):
    """
    User model stored in Firestore.

    Users are identified by email address. Authentication is via magic links
    (no passwords). Credits are consumed when creating karaoke jobs.

    For multi-tenant white-label portals, users are scoped to a tenant.
    Users with tenant_id=None are default Nomad Karaoke users.
    """
    email: str  # Primary identifier
    role: UserRole = UserRole.USER
    credits: int = 0

    # Multi-tenant support (None = default Nomad Karaoke)
    tenant_id: Optional[str] = None

    # Stripe integration
    stripe_customer_id: Optional[str] = None

    # Account state
    is_active: bool = True
    email_verified: bool = False

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: Optional[datetime] = None

    # Credit history (last 100 transactions)
    credit_transactions: List[CreditTransaction] = Field(default_factory=list)

    # Job tracking
    total_jobs_created: int = 0
    total_jobs_completed: int = 0

    # Payment tracking (cached from stripe_payments, in cents)
    total_spent: int = 0

    # Optional profile fields for future use
    display_name: Optional[str] = None

    # i18n locale preference (set from Accept-Language on login, used for emails/push)
    locale: Optional[str] = None

    # Push notification subscriptions (Web Push API)
    # Users can subscribe from multiple devices/browsers
    push_subscriptions: List[PushSubscription] = Field(default_factory=list)

    # Feedback-for-credits program
    has_submitted_feedback: bool = False

    # Anti-abuse: IP address and device fingerprint used during signup
    signup_ip: Optional[str] = None
    device_fingerprint: Optional[str] = None

    # Flag to prevent duplicate welcome credit grants (idempotency)
    welcome_credits_granted: bool = False

    # Referral system
    referral_code: Optional[str] = None  # User's own referral code
    referred_by_code: Optional[str] = None  # Code used when this user signed up
    referred_at: Optional[datetime] = None  # When the referral was applied
    referral_discount_expires_at: Optional[datetime] = None  # When referral discount ends
    stripe_connect_account_id: Optional[str] = None  # For receiving referral payouts


class MagicLinkToken(BaseModel):
    """
    Magic link token for passwordless authentication.

    Tokens are short-lived (15 minutes) and single-use.
    Stored in Firestore with automatic TTL.

    For multi-tenant portals, the tenant_id determines which portal
    the user will be redirected to after verification.
    """
    token: str  # Secure random token
    email: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    used: bool = False
    used_at: Optional[datetime] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_fingerprint: Optional[str] = None

    # Multi-tenant support (None = default Nomad Karaoke)
    tenant_id: Optional[str] = None

    # Referral attribution (travels with magic link for cross-device support)
    referral_code: Optional[str] = None

    # Pre-computed credit evaluation (set asynchronously after magic link is sent)
    credit_eval_decision: Optional[str] = None  # "grant", "deny", "pending_review"
    credit_eval_reasoning: Optional[str] = None
    credit_eval_error: Optional[str] = None


class Session(BaseModel):
    """
    User session for authenticated requests.

    Sessions are created after successful magic link verification.
    Sessions expire after 7 days of inactivity or 30 days absolute.

    For multi-tenant portals, sessions are scoped to a tenant.
    """
    token: str  # Secure random session token
    user_email: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    last_activity_at: datetime = Field(default_factory=datetime.utcnow)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_fingerprint: Optional[str] = None
    is_active: bool = True

    # Multi-tenant support (None = default Nomad Karaoke)
    tenant_id: Optional[str] = None


# Pydantic models for API requests/responses

class SendMagicLinkRequest(BaseModel):
    """Request to send a magic link email."""
    email: str
    device_fingerprint: Optional[str] = None
    referral_code: Optional[str] = None  # From referral interstitial


class SendMagicLinkResponse(BaseModel):
    """Response after sending magic link."""
    status: str
    message: str


class VerifyMagicLinkRequest(BaseModel):
    """Request to verify a magic link token."""
    token: str


class VerifyMagicLinkResponse(BaseModel):
    """Response after verifying magic link."""
    status: str
    session_token: str
    user: "UserPublic"
    message: str
    tenant_subdomain: Optional[str] = None
    credits_granted: int = 0
    credit_status: str = "not_applicable"  # "granted", "denied", "pending_review", "already_granted", "not_applicable"


class UserPublic(BaseModel):
    """Public user information (safe to expose to frontend)."""
    email: str
    role: UserRole
    credits: int
    display_name: Optional[str] = None
    total_jobs_created: int = 0
    total_jobs_completed: int = 0
    tenant_id: Optional[str] = None
    feedback_eligible: bool = False
    total_spent: int = 0
    referral_code: Optional[str] = None
    has_active_referral_discount: bool = False
    referral_discount_percent: Optional[int] = None
    referral_discount_expires_at: Optional[str] = None  # ISO string
    referred_by_code: Optional[str] = None
    created_at: Optional[str] = None
    last_login_at: Optional[str] = None


class AddCreditsRequest(BaseModel):
    """Admin request to add credits to a user."""
    email: str
    amount: int
    reason: str = "admin_grant"


class AddCreditsResponse(BaseModel):
    """Response after adding credits."""
    status: str
    email: str
    credits_added: int
    new_balance: int
    message: str


class UserListResponse(BaseModel):
    """Response for listing users (admin only)."""
    users: List[UserPublic]
    total: int


# ============================================================================
# User Feedback-for-Credits Models
# ============================================================================

class UserFeedback(BaseModel):
    """
    Feedback from any user (not just beta testers).

    Stored in Firestore 'user_feedback' collection.
    Users earn 1 free credit for submitting detailed feedback.
    """
    id: str
    user_email: str

    # Ratings (1-5 scale)
    overall_rating: int = Field(ge=1, le=5)
    ease_of_use_rating: int = Field(ge=1, le=5)
    lyrics_accuracy_rating: int = Field(ge=1, le=5)
    correction_experience_rating: int = Field(ge=1, le=5)

    # Open-ended feedback
    what_went_well: Optional[str] = None
    what_could_improve: Optional[str] = None
    additional_comments: Optional[str] = None

    # Would they recommend / use again?
    would_recommend: bool = True
    would_use_again: bool = True

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    submitted_via: str = "web"


class UserFeedbackRequest(BaseModel):
    """Request to submit user feedback for credits."""
    # Ratings (1-5 scale)
    overall_rating: int = Field(ge=1, le=5)
    ease_of_use_rating: int = Field(ge=1, le=5)
    lyrics_accuracy_rating: int = Field(ge=1, le=5)
    correction_experience_rating: int = Field(ge=1, le=5)

    # Open-ended feedback
    what_went_well: Optional[str] = None
    what_could_improve: Optional[str] = None
    additional_comments: Optional[str] = None

    # Would they recommend / use again?
    would_recommend: bool = True
    would_use_again: bool = True


class UserFeedbackResponse(BaseModel):
    """Response after submitting user feedback."""
    status: str
    message: str
    credits_granted: int = 0


class FeedbackEligibilityResponse(BaseModel):
    """Response for feedback eligibility check."""
    eligible: bool
    has_submitted: bool
    jobs_completed: int
    credits_reward: int = 1
