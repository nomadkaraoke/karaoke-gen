"""
User model for authentication and credits.

Supports:
- Magic link authentication (email-based)
- Credit system for karaoke generation
- Role-based access control (user/admin)
- Stripe integration for payments
- Beta tester program with feedback collection
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field


class UserRole(str, Enum):
    """User roles for access control."""
    USER = "user"
    ADMIN = "admin"


class BetaTesterStatus(str, Enum):
    """Status of beta tester participation."""
    ACTIVE = "active"  # Enrolled, free credits available
    PENDING_FEEDBACK = "pending_feedback"  # Job completed, awaiting feedback
    COMPLETED = "completed"  # Feedback submitted
    EXPIRED = "expired"  # 24hr deadline passed without feedback


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
    """
    endpoint: str  # Push service endpoint URL
    keys: Dict[str, str]  # p256dh and auth keys for encryption
    device_name: Optional[str] = None  # e.g., "iPhone", "Chrome on Windows"
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

    # Optional profile fields for future use
    display_name: Optional[str] = None

    # Beta tester program
    is_beta_tester: bool = False
    beta_tester_status: Optional[BetaTesterStatus] = None
    beta_enrolled_at: Optional[datetime] = None
    beta_promise_text: Optional[str] = None  # User's promise statement
    beta_feedback_due_at: Optional[datetime] = None  # 24hr after job completion
    beta_feedback_email_sent: bool = False

    # Push notification subscriptions (Web Push API)
    # Users can subscribe from multiple devices/browsers
    push_subscriptions: List[PushSubscription] = Field(default_factory=list)


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

    # Multi-tenant support (None = default Nomad Karaoke)
    tenant_id: Optional[str] = None


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
    is_active: bool = True

    # Multi-tenant support (None = default Nomad Karaoke)
    tenant_id: Optional[str] = None


# Pydantic models for API requests/responses

class SendMagicLinkRequest(BaseModel):
    """Request to send a magic link email."""
    email: str


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


class UserPublic(BaseModel):
    """Public user information (safe to expose to frontend)."""
    email: str
    role: UserRole
    credits: int
    display_name: Optional[str] = None
    total_jobs_created: int = 0
    total_jobs_completed: int = 0
    tenant_id: Optional[str] = None


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
# Beta Tester Program Models
# ============================================================================

class BetaTesterFeedback(BaseModel):
    """
    Feedback from a beta tester after using the service.

    Stored in Firestore 'beta_feedback' collection.
    """
    id: str  # Unique feedback ID
    user_email: str
    job_id: Optional[str] = None  # The job they're providing feedback on

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
    submitted_via: str = "web"  # 'web' or 'email'


class BetaTesterEnrollRequest(BaseModel):
    """Request to enroll as a beta tester."""
    email: str
    promise_text: str = Field(
        min_length=10,
        description="User's promise to provide feedback (min 10 chars)"
    )
    accept_corrections_work: bool = Field(
        description="User accepts they may need to review/correct lyrics"
    )


class BetaTesterEnrollResponse(BaseModel):
    """Response after enrolling as beta tester."""
    status: str
    message: str
    credits_granted: int
    session_token: Optional[str] = None  # If new user, provide session


class BetaFeedbackRequest(BaseModel):
    """Request to submit beta tester feedback."""
    job_id: Optional[str] = None

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


class BetaFeedbackResponse(BaseModel):
    """Response after submitting feedback."""
    status: str
    message: str
    bonus_credits: int = 0  # Bonus credits for great feedback
