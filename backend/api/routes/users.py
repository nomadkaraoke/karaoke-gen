"""
User and authentication API routes.

Handles:
- Magic link authentication (send, verify)
- Session management (logout)
- User profile and credits
- Stripe checkout and webhooks
- Admin user management
"""
import hashlib
import logging
import threading
from datetime import datetime
from typing import Optional, Tuple


def _mask_email(email: str) -> str:
    """Mask email for logging to avoid PII exposure.

    Example: test@example.com -> te***@ex***.com
    """
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    masked_local = local[:2] + "***" if len(local) > 2 else "***"
    domain_parts = domain.split(".")
    if len(domain_parts) >= 2:
        masked_domain = domain_parts[0][:2] + "***." + domain_parts[-1]
    else:
        masked_domain = "***"
    return f"{masked_local}@{masked_domain}"


from fastapi import APIRouter, HTTPException, Depends, Request, Header
from pydantic import BaseModel, EmailStr

from backend.services.email_validation_service import get_email_validation_service
from backend.models.user import (
    UserRole,
    UserPublic,
    SendMagicLinkRequest,
    SendMagicLinkResponse,
    VerifyMagicLinkResponse,
    AddCreditsRequest,
    AddCreditsResponse,
    UserListResponse,
    UserFeedback,
    UserFeedbackRequest,
    UserFeedbackResponse,
    FeedbackEligibilityResponse,
)
from backend.services.user_service import get_user_service, UserService, USERS_COLLECTION
from backend.services.email_service import get_email_service, EmailService
from backend.services.stripe_service import get_stripe_service, StripeService, CREDIT_PACKAGES
from backend.services.theme_service import get_theme_service
from backend.services.job_defaults_service import (
    get_effective_distribution_settings,
    resolve_cdg_txt_defaults,
)
from backend.api.dependencies import require_admin
from backend.services.auth_service import AuthResult
from backend.api.routes.file_upload import _prepare_theme_for_job
from backend.services.auth_service import UserType
from backend.utils.test_data import is_test_email
from backend.services.youtube_download_service import (
    get_youtube_download_service,
    YouTubeDownloadError,
)
from backend.exceptions import InvalidStateTransitionError
from backend.services.tracing import add_span_attribute
from backend.utils.request_helpers import get_client_ip


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])


# =============================================================================
# Request/Response Models
# =============================================================================

class CreateCheckoutRequest(BaseModel):
    """Request to create a Stripe checkout session."""
    package_id: str
    email: EmailStr


class CreateCheckoutResponse(BaseModel):
    """Response with checkout URL."""
    status: str
    checkout_url: str
    message: str


class MadeForYouCheckoutRequest(BaseModel):
    """Request to create a made-for-you karaoke video order."""
    email: EmailStr
    artist: str
    title: str
    source_type: str = "search"  # search, youtube, or upload
    youtube_url: Optional[str] = None
    notes: Optional[str] = None


class CreditPackage(BaseModel):
    """Credit package information."""
    id: str
    credits: int
    price_cents: int
    name: str
    description: str


class CreditPackagesResponse(BaseModel):
    """Response listing available credit packages."""
    packages: list[CreditPackage]


class UserProfileResponse(BaseModel):
    """Response with user profile."""
    user: UserPublic
    has_session: bool


class LogoutResponse(BaseModel):
    """Response after logout."""
    status: str
    message: str


# =============================================================================
# Magic Link Authentication
# =============================================================================


def _precompute_credit_eval(token: str, email: str) -> None:
    """Run credit evaluation in background and store result on magic link doc.

    Called in a background thread after sending a magic link for new users.
    If the user clicks the link before this finishes, verify_magic_link falls
    back to inline evaluation (same as before this optimization).
    """
    try:
        from backend.services.credit_evaluation_service import get_credit_evaluation_service
        from backend.services.user_service import get_user_service, MAGIC_LINKS_COLLECTION

        eval_service = get_credit_evaluation_service()
        evaluation = eval_service.evaluate(email, "welcome")

        user_service = get_user_service()
        user_service.db.collection(MAGIC_LINKS_COLLECTION).document(token).update({
            "credit_eval_decision": evaluation.decision,
            "credit_eval_reasoning": evaluation.reasoning,
            "credit_eval_error": evaluation.error,
        })
        logger.info(f"Pre-computed credit eval for {_mask_email(email)}: {evaluation.decision}")
    except Exception:
        logger.exception(f"Background credit eval failed for {_mask_email(email)} — verify will compute inline")


@router.post("/auth/magic-link", response_model=SendMagicLinkResponse)
async def send_magic_link(
    request: SendMagicLinkRequest,
    http_request: Request,
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Send a magic link email for passwordless authentication.

    The user will receive an email with a link that logs them in.
    Links expire after 15 minutes and can only be used once.

    For white-label tenants, validates email domain against tenant config.
    """
    from backend.middleware.tenant import get_tenant_from_request, get_tenant_config_from_request

    email = request.email.lower()

    # Check for disposable email domains, blocked emails, and blocked IPs
    # (done before email service check so we can reject without needing email config)
    email_validation = get_email_validation_service()

    if email_validation.is_disposable_domain(email):
        logger.warning(f"Blocked disposable email signup attempt: {_mask_email(email)}")
        raise HTTPException(
            status_code=422,
            detail="disposable_email_not_allowed"
        )

    if email_validation.is_email_blocked(email):
        logger.warning(f"Blocked email signup attempt: {_mask_email(email)}")
        return SendMagicLinkResponse(
            status="success",
            message="If this email is registered, you will receive a sign-in link shortly."
        )

    ip_address = get_client_ip(http_request)
    if ip_address and email_validation.is_ip_blocked(ip_address):
        logger.warning(f"Blocked IP signup attempt: {ip_address}")
        return SendMagicLinkResponse(
            status="success",
            message="If this email is registered, you will receive a sign-in link shortly."
        )

    # Per-IP and per-fingerprint signup rate limit (only for new users)
    device_fingerprint = request.device_fingerprint
    existing_user = user_service.get_user(email)
    if existing_user is None:
        if user_service.is_signup_rate_limited(
            ip_address=ip_address, device_fingerprint=device_fingerprint
        ):
            logger.warning(
                f"Signup rate limit hit: IP={ip_address} for {_mask_email(email)}"
            )
            # Silent reject (anti-enumeration)
            return SendMagicLinkResponse(
                status="success",
                message="If this email is registered, you will receive a sign-in link shortly."
            )

    # Check if email service is configured
    if not email_service.is_configured():
        logger.error("Email service not configured - cannot send magic links")
        raise HTTPException(
            status_code=503,
            detail="Email service is not available. Please contact support."
        )

    # Get tenant context from middleware
    tenant_id = get_tenant_from_request(http_request)
    tenant_config = get_tenant_config_from_request(http_request)

    # Validate email domain for tenant if configured
    if tenant_config and tenant_config.auth.allowed_email_domains:
        if not tenant_config.is_email_allowed(email):
            logger.warning(f"Email domain not allowed for tenant {tenant_id}: {_mask_email(email)}")
            # Return success anyway to prevent email enumeration
            return SendMagicLinkResponse(
                status="success",
                message="If this email is registered, you will receive a sign-in link shortly."
            )

    # Get client info for security logging (ip_address already extracted above for blocklist check)
    user_agent = http_request.headers.get("user-agent")

    # Create magic link token with tenant context and fingerprint
    magic_link = user_service.create_magic_link(
        email,
        ip_address=ip_address,
        user_agent=user_agent,
        tenant_id=tenant_id,
        device_fingerprint=device_fingerprint,
    )

    # Get tenant-specific email configuration
    sender_email = None
    tenant_frontend_url = None
    tenant_name = None
    if tenant_config:
        sender_email = tenant_config.get_sender_email()
        tenant_frontend_url = tenant_config.get_frontend_url()
        tenant_name = tenant_config.name

    # Send email (with tenant-specific sender, URL, and name if configured)
    sent = email_service.send_magic_link(
        email,
        magic_link.token,
        sender_email=sender_email,
        tenant_frontend_url=tenant_frontend_url,
        tenant_name=tenant_name,
    )

    if not sent:
        logger.error(f"Failed to send magic link email to {email}")
        # Don't reveal failure to prevent email enumeration
        # Still return success

    # Pre-compute credit evaluation in background for new users.
    # By the time they check their email and click the link, the decision
    # will be ready and verification will be instant.
    if existing_user is None:
        thread = threading.Thread(
            target=_precompute_credit_eval,
            args=(magic_link.token, email),
            daemon=True,
        )
        thread.start()

    return SendMagicLinkResponse(
        status="success",
        message="If this email is registered, you will receive a sign-in link shortly."
    )


@router.get("/auth/verify", response_model=VerifyMagicLinkResponse)
async def verify_magic_link(
    token: str,
    http_request: Request,
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Verify a magic link token and create a session.

    Returns a session token that should be stored and used for subsequent requests.
    The session will be associated with the tenant from the magic link.
    """
    # Reject empty tokens early to avoid invalid Firestore document paths
    if not token or not token.strip():
        raise HTTPException(status_code=401, detail="Invalid token")

    # Get client info
    ip_address = get_client_ip(http_request)
    user_agent = http_request.headers.get("user-agent")

    # Check if this is a first login BEFORE verification (which sets last_login_at)
    # We need to get the user's state before verify_magic_link updates it
    # Also extract tenant_id from the magic link for session creation
    from backend.services.user_service import MAGIC_LINKS_COLLECTION
    magic_link_doc = user_service.db.collection(MAGIC_LINKS_COLLECTION).document(token).get()
    is_first_login = False
    magic_link_tenant_id = None
    if magic_link_doc.exists:
        magic_link_data = magic_link_doc.to_dict()
        magic_link_tenant_id = magic_link_data.get('tenant_id')
        pre_verify_user = user_service.get_user(magic_link_data.get('email', ''))
        if pre_verify_user:
            is_first_login = pre_verify_user.total_jobs_created == 0 and not pre_verify_user.last_login_at

    # Verify the magic link
    success, user, message = user_service.verify_magic_link(token)

    if not success or not user:
        raise HTTPException(status_code=401, detail=message)

    # Grant welcome credits on first verification (with AI abuse evaluation)
    # Use pre-computed evaluation from magic link if available (computed at send time)
    precomputed_eval = None
    if magic_link_doc.exists:
        precomputed_eval = {
            k: magic_link_data.get(k)
            for k in ("credit_eval_decision", "credit_eval_reasoning", "credit_eval_error")
            if magic_link_data.get(k) is not None
        } or None

    credits_granted = 0
    credit_status = "not_applicable"  # returning user, not first login
    granted, credit_status = user_service.grant_welcome_credits_if_eligible(
        user.email, precomputed_eval=precomputed_eval,
    )
    if granted:
        credits_granted = user_service.NEW_USER_FREE_CREDITS
        logger.info(f"Granted {credits_granted} welcome credits to {_mask_email(user.email)}")
        # Refresh user to get updated credit balance
        user = user_service.get_user(user.email)
    elif credit_status == "denied":
        logger.info(f"Welcome credits denied for {_mask_email(user.email)}")

    # Create session with tenant context and device fingerprint from the magic link
    magic_link_fingerprint = magic_link_data.get('device_fingerprint') if magic_link_doc.exists else None
    session = user_service.create_session(
        user.email,
        ip_address=ip_address,
        user_agent=user_agent,
        tenant_id=magic_link_tenant_id,
        device_fingerprint=magic_link_fingerprint,
    )

    # Send welcome email to first-time users
    if is_first_login:
        email_service.send_welcome_email(user.email, user.credits)

    # Return user info with tenant_id
    user_public = UserPublic(
        email=user.email,
        role=user.role,
        credits=user.credits,
        display_name=user.display_name,
        total_jobs_created=user.total_jobs_created,
        total_jobs_completed=user.total_jobs_completed,
        tenant_id=user.tenant_id,
    )

    # Resolve tenant subdomain for cross-domain redirect safety
    tenant_subdomain = None
    if magic_link_tenant_id:
        from backend.services.tenant_service import get_tenant_service
        tenant_service = get_tenant_service()
        tenant_cfg = tenant_service.get_tenant_config(magic_link_tenant_id)
        if tenant_cfg:
            tenant_subdomain = tenant_cfg.subdomain

    return VerifyMagicLinkResponse(
        status="success",
        session_token=session.token,
        user=user_public,
        message="Successfully signed in",
        tenant_subdomain=tenant_subdomain,
        credits_granted=credits_granted,
        credit_status=credit_status,
    )


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(
    authorization: Optional[str] = Header(None),
    user_service: UserService = Depends(get_user_service),
):
    """
    Logout and invalidate the current session.
    """
    if not authorization:
        return LogoutResponse(status="success", message="Already logged out")

    # Extract token from "Bearer <token>"
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization

    user_service.revoke_session(token)

    return LogoutResponse(status="success", message="Successfully logged out")


# =============================================================================
# User Profile
# =============================================================================

@router.get("/me", response_model=UserProfileResponse)
async def get_current_user(
    authorization: Optional[str] = Header(None),
    user_service: UserService = Depends(get_user_service),
):
    """
    Get the current user's profile.

    Requires a valid session token or admin token.
    """
    from backend.services.auth_service import get_auth_service

    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Extract token
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization

    # First try auth service (handles admin tokens and auth_tokens)
    auth_service = get_auth_service()
    auth_result = auth_service.validate_token_full(token)

    if auth_result.is_valid and auth_result.user_email:
        # Get or create user record for the authenticated email
        user = user_service.get_or_create_user(auth_result.user_email)
        feedback_eligible = (
            user.total_jobs_completed >= 2
            and not user.has_submitted_feedback
        )
        user_public = UserPublic(
            email=user.email,
            role=user.role if not auth_result.is_admin else UserRole.ADMIN,
            credits=user.credits,
            display_name=user.display_name,
            total_jobs_created=user.total_jobs_created,
            total_jobs_completed=user.total_jobs_completed,
            feedback_eligible=feedback_eligible,
        )
        return UserProfileResponse(user=user_public, has_session=True)

    # Fall back to session validation (for magic link sessions)
    valid, user, message = user_service.validate_session(token)

    if not valid or not user:
        raise HTTPException(status_code=401, detail=message)

    feedback_eligible = (
        user.total_jobs_completed >= 2
        and not user.has_submitted_feedback
    )
    user_public = UserPublic(
        email=user.email,
        role=user.role,
        credits=user.credits,
        display_name=user.display_name,
        total_jobs_created=user.total_jobs_created,
        total_jobs_completed=user.total_jobs_completed,
        feedback_eligible=feedback_eligible,
    )

    return UserProfileResponse(user=user_public, has_session=True)


# =============================================================================
# Credit Packages & Checkout
# =============================================================================

@router.get("/credits/packages", response_model=CreditPackagesResponse)
async def list_credit_packages():
    """
    List available credit packages for purchase.

    No authentication required - this is public information.
    """
    packages = [
        CreditPackage(
            id=pkg_id,
            credits=pkg["credits"],
            price_cents=pkg["price_cents"],
            name=pkg["name"],
            description=pkg["description"],
        )
        for pkg_id, pkg in CREDIT_PACKAGES.items()
    ]

    return CreditPackagesResponse(packages=packages)


@router.post("/credits/checkout", response_model=CreateCheckoutResponse)
async def create_checkout(
    request: CreateCheckoutRequest,
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Create a Stripe checkout session for purchasing credits.

    Returns a URL to redirect the user to Stripe's hosted checkout page.
    No authentication required - email is provided in the request.
    """
    if not stripe_service.is_configured():
        raise HTTPException(status_code=503, detail="Payment processing is not available")

    success, checkout_url, message = stripe_service.create_checkout_session(
        package_id=request.package_id,
        user_email=request.email,
    )

    if not success or not checkout_url:
        raise HTTPException(status_code=400, detail=message)

    return CreateCheckoutResponse(
        status="success",
        checkout_url=checkout_url,
        message=message,
    )


@router.post("/made-for-you/checkout", response_model=CreateCheckoutResponse)
async def create_made_for_you_checkout(
    request: MadeForYouCheckoutRequest,
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Create a Stripe checkout session for a made-for-you karaoke video order.

    This is the full-service option where Nomad Karaoke handles everything:
    - Finding or processing the audio
    - Reviewing and correcting lyrics
    - Selecting the best instrumental
    - Generating the final video

    $15 with 24-hour delivery guarantee.
    No authentication required - customer email is provided in the request.
    """
    if not stripe_service.is_configured():
        raise HTTPException(status_code=503, detail="Payment processing is not available")

    success, checkout_url, message = stripe_service.create_made_for_you_checkout_session(
        customer_email=request.email,
        artist=request.artist,
        title=request.title,
        source_type=request.source_type,
        youtube_url=request.youtube_url,
        notes=request.notes,
    )

    if not success or not checkout_url:
        raise HTTPException(status_code=400, detail=message)

    return CreateCheckoutResponse(
        status="success",
        checkout_url=checkout_url,
        message=message,
    )


# =============================================================================
# Stripe Webhooks
# =============================================================================

# Admin email for made-for-you order notifications
ADMIN_EMAIL = "madeforyou@nomadkaraoke.com"


async def _handle_made_for_you_order(
    session_id: str,
    metadata: dict,
    user_service: UserService,
    email_service: EmailService,
) -> None:
    """
    Handle a completed made-for-you order by creating a job and notifying Andrew.

    The made-for-you flow:
    1. Job is created with made_for_you=True, owned by admin during processing
    2. For search orders: audio search runs, results stored, job pauses at AWAITING_AUDIO_SELECTION
    3. Admin receives notification email with link to select audio source
    4. Customer receives order confirmation email
    5. Admin selects audio in UI, job proceeds through normal pipeline
    6. On completion, ownership transfers to customer

    For orders with a YouTube URL, workers are triggered immediately (no search needed).

    Args:
        session_id: Stripe checkout session ID
        metadata: Order metadata from Stripe session
        user_service: User service for marking session processed
        email_service: Email service for notifications
    """
    from backend.models.job import JobCreate, JobStatus
    from backend.services.job_manager import JobManager
    from backend.services.audio_search_service import (
        get_audio_search_service,
        NoResultsError,
        AudioSearchError,
    )
    from backend.services.storage_service import StorageService
    import tempfile
    import os

    customer_email = metadata.get("customer_email", "")
    artist = metadata.get("artist", "Unknown Artist")
    title = metadata.get("title", "Unknown Title")
    source_type = metadata.get("source_type", "search")
    youtube_url = metadata.get("youtube_url")
    notes = metadata.get("notes", "")

    logger.info(
        f"Processing made-for-you order: {artist} - {title} for {customer_email} "
        f"(session: {session_id}, source_type: {source_type})"
    )

    try:
        job_manager = JobManager()
        storage_service = StorageService()

        # Apply default theme (Nomad) - same as audio_search endpoint
        theme_service = get_theme_service()
        effective_theme_id = theme_service.get_default_theme_id()
        if effective_theme_id:
            logger.info(f"Applying default theme '{effective_theme_id}' for made-for-you order")

        # Get distribution defaults using centralized service
        dist = get_effective_distribution_settings()

        # Resolve CDG/TXT defaults based on theme (uses centralized service)
        # This ensures made-for-you jobs get the same CDG/TXT defaults as regular jobs
        resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(effective_theme_id)

        # Create job with admin ownership during processing
        # CRITICAL: made_for_you=True, user_email=ADMIN_EMAIL, customer_email for delivery
        # auto_download=False to pause at audio selection for admin to choose
        job_create = JobCreate(
            url=youtube_url if youtube_url else None,
            artist=artist,
            title=title,
            user_email=ADMIN_EMAIL,  # Admin owns during processing
            theme_id=effective_theme_id,  # Apply default theme
            non_interactive=False,  # Admin will review lyrics/instrumental
            # Made-for-you specific fields
            made_for_you=True,  # Flag for ownership transfer on completion
            customer_email=customer_email,  # Customer email for final delivery
            customer_notes=notes if notes else None,  # Customer's special requests
            # Audio search fields for search-based orders
            audio_search_artist=artist if not youtube_url else None,
            audio_search_title=title if not youtube_url else None,
            auto_download=False,  # Pause at audio selection for admin to choose
            # CDG/TXT settings (resolved via centralized service)
            enable_cdg=resolved_cdg,
            enable_txt=resolved_txt,
            # Distribution settings from centralized defaults
            enable_youtube_upload=dist.enable_youtube_upload,
            dropbox_path=dist.dropbox_path,
            gdrive_folder_id=dist.gdrive_folder_id,
            brand_prefix=dist.brand_prefix,
            discord_webhook_url=dist.discord_webhook_url,
            youtube_description=dist.youtube_description,
        )
        # Made-for-you jobs are created by admin (via Stripe webhook) - bypass rate limits
        job = job_manager.create_job(job_create, is_admin=True)
        job_id = job.job_id

        # Trace attributes for observability
        add_span_attribute("job_id", job_id)
        add_span_attribute("job.source", "made_for_you")
        add_span_attribute("job.is_private", False)

        logger.info(f"Created made-for-you job {job_id} for {_mask_email(customer_email)} (owned by {_mask_email(ADMIN_EMAIL)})")

        # Prepare theme style assets for the job (same as audio_search endpoint)
        if effective_theme_id:
            try:
                style_params_path, theme_style_assets, youtube_desc = _prepare_theme_for_job(
                    job_id, effective_theme_id, None  # No color overrides for made-for-you
                )
                theme_update = {
                    'style_params_gcs_path': style_params_path,
                    'style_assets': theme_style_assets,
                }
                if youtube_desc:
                    theme_update['youtube_description_template'] = youtube_desc
                job_manager.update_job(job_id, theme_update)
                logger.info(f"Applied theme '{effective_theme_id}' to made-for-you job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to prepare theme for made-for-you job {job_id}: {e}")

        # Mark session as processed for idempotency
        # Note: Using internal method since this isn't a credit transaction
        user_service._mark_stripe_session_processed(
            stripe_session_id=session_id,
            email=customer_email,
            amount=0  # No credits, just tracking the session
        )

        # Initialize search_results for later use in email notification
        search_results = None

        # Handle based on whether we have a YouTube URL or need to search
        if youtube_url:
            # URL provided - download audio first, then trigger workers
            # CRITICAL: Workers require input_media_gcs_path to be set before they can process
            # See docs/LESSONS-LEARNED.md "YouTube URL Downloads Must Happen Before Workers"
            logger.info(f"Job {job_id}: YouTube URL provided, downloading audio first")

            try:
                youtube_service = get_youtube_download_service()
                audio_gcs_path = await youtube_service.download(
                    url=youtube_url,
                    job_id=job_id,
                    artist=artist,
                    title=title,
                )

                # Update job with the downloaded audio path BEFORE triggering workers
                job_manager.update_job(job_id, {
                    'input_media_gcs_path': audio_gcs_path,
                    'filename': os.path.basename(audio_gcs_path),
                })

                logger.info(f"Job {job_id}: YouTube audio downloaded to {audio_gcs_path}")

                # Use centralized helper that handles transition + worker triggers
                # This ensures consistent state machine flow and raises on invalid transitions
                try:
                    await job_manager.start_job_processing(job_id)
                except InvalidStateTransitionError as e:
                    logger.error(f"Job {job_id}: Failed to start processing - invalid state transition: {e}")
                    job_manager.transition_to_state(
                        job_id=job_id,
                        new_status=JobStatus.FAILED,
                        progress=0,
                        message=f"Failed to start processing: {str(e)}",
                        raise_on_invalid=False  # Don't raise if already failed
                    )
                    raise

            except YouTubeDownloadError as e:
                logger.error(f"Job {job_id}: YouTube download failed: {e}")
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.FAILED,
                    progress=0,
                    message=f"YouTube download failed: {str(e)}"
                )
                raise
        else:
            # No URL - use audio search flow, pause for admin selection
            # Made-for-you jobs require admin to select audio source
            logger.info(f"Job {job_id}: No URL, using audio search for '{artist} - {title}'")

            # Update job with audio search fields
            job_manager.update_job(job_id, {
                'audio_search_artist': artist,
                'audio_search_title': title,
                'auto_download': False,  # Admin must select
            })

            # Transition to searching state
            job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.SEARCHING_AUDIO,
                progress=5,
                message=f"Searching for audio: {artist} - {title}"
            )

            # Perform audio search
            audio_search_service = get_audio_search_service()

            try:
                search_results = audio_search_service.search(artist, title)
            except NoResultsError as e:
                # No results found - still transition to AWAITING_AUDIO_SELECTION
                # Admin can manually provide audio
                logger.warning(f"Job {job_id}: No audio sources found for '{artist} - {title}'")
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.AWAITING_AUDIO_SELECTION,
                    progress=10,
                    message=f"No automatic audio sources found. Manual intervention required."
                )
                search_results = None
            except AudioSearchError as e:
                logger.error(f"Job {job_id}: Audio search failed: {e}")
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.AWAITING_AUDIO_SELECTION,
                    progress=10,
                    message=f"Audio search error. Manual intervention required."
                )
                search_results = None

            if search_results:
                # Store search results in state_data for admin to review
                results_dicts = [r.to_dict() for r in search_results]
                state_data_update = {
                    'audio_search_results': results_dicts,
                    'audio_search_count': len(results_dicts),
                }
                if audio_search_service.last_remote_search_id:
                    state_data_update['remote_search_id'] = audio_search_service.last_remote_search_id
                job_manager.update_job(job_id, {'state_data': state_data_update})

                # Transition to AWAITING_AUDIO_SELECTION for admin to choose
                # Do NOT auto-select or download - admin must review and select
                logger.info(f"Job {job_id}: Found {len(results_dicts)} audio sources, awaiting admin selection")

                # Transition to AWAITING_AUDIO_SELECTION for admin to choose
                # Admin will select from results in the UI, then job proceeds
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.AWAITING_AUDIO_SELECTION,
                    progress=10,
                    message=f"Found {len(results_dicts)} audio sources. Awaiting admin selection."
                )

        # Get audio source count for admin notification
        # (search_results may be set from the search flow above, or None for YouTube URL orders)
        audio_source_count = len(search_results) if search_results else 0

        # Generate admin login token for one-click email access (24hr expiry)
        admin_login = user_service.create_admin_login_token(
            email=ADMIN_EMAIL,
            expiry_hours=24,
        )

        # Send confirmation email to customer using professional template
        email_service.send_made_for_you_order_confirmation(
            to_email=customer_email,
            artist=artist,
            title=title,
            job_id=job_id,
            notes=notes,
        )

        # Send notification email to admin using professional template
        email_service.send_made_for_you_admin_notification(
            to_email=ADMIN_EMAIL,
            customer_email=customer_email,
            artist=artist,
            title=title,
            job_id=job_id,
            admin_login_token=admin_login.token,
            notes=notes,
            audio_source_count=audio_source_count,
        )

        logger.info(f"Sent made-for-you order notifications for job {job_id}")

    except Exception as e:
        logger.error(f"Error processing made-for-you order: {e}", exc_info=True)
        # Still try to notify Andrew of the failure
        try:
            email_service.send_email(
                to_email=ADMIN_EMAIL,
                subject=f"[FAILED] Made For You Order: {artist} - {title}",
                html_content=f"""
                <h2>Made-For-You Order Failed</h2>
                <p>An error occurred processing this order:</p>
                <ul>
                    <li><strong>Customer:</strong> {customer_email}</li>
                    <li><strong>Artist:</strong> {artist}</li>
                    <li><strong>Title:</strong> {title}</li>
                    <li><strong>Error:</strong> {str(e)}</li>
                </ul>
                <p>Please manually create this job and notify the customer.</p>
                """,
                text_content=f"""
Made-For-You Order Failed

Customer: {customer_email}
Artist: {artist}
Title: {title}
Error: {str(e)}

Please manually create this job and notify the customer.
                """.strip(),
            )
        except Exception as email_error:
            logger.error(f"Failed to send error notification: {email_error}")


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    stripe_service: StripeService = Depends(get_stripe_service),
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Handle Stripe webhook events.

    This endpoint receives events from Stripe about payment status.
    It verifies the webhook signature and processes the event.
    """
    from backend.services.stripe_admin_service import get_stripe_admin_service

    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe signature")

    # Get raw body for signature verification
    payload = await request.body()

    # Verify signature
    valid, event, message = stripe_service.verify_webhook_signature(payload, stripe_signature)

    if not valid:
        logger.warning(f"Invalid Stripe webhook signature: {message}")
        raise HTTPException(status_code=400, detail=message)

    # Handle the event
    event_type = event.get("type")
    event_id = event.get("id", "")
    logger.info(f"Received Stripe webhook: {event_type}")

    # Log webhook event for audit trail
    admin_service = get_stripe_admin_service()
    admin_service.log_webhook_event(
        event_id=event_id,
        event_type=event_type,
        status="processing",
    )

    try:
        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            session_id = session.get("id")
            metadata = session.get("metadata", {})
            customer_email = (
                metadata.get("user_email")
                or metadata.get("customer_email")
                or session.get("customer_email", "")
            )

            # Idempotency check: Skip if this session was already processed
            if session_id and user_service.is_stripe_session_processed(session_id):
                logger.info(f"Skipping already processed session: {session_id}")
                admin_service.log_webhook_event(
                    event_id=event_id,
                    event_type=event_type,
                    status="skipped",
                    session_id=session_id,
                    customer_email=customer_email,
                    summary="Already processed (idempotency check)",
                )
                return {"status": "received", "type": event_type, "note": "already_processed"}

            # Check if this is a made-for-you order
            if metadata.get("order_type") == "made_for_you":
                # Handle made-for-you order - create a job
                await _handle_made_for_you_order(
                    session_id=session_id,
                    metadata=metadata,
                    user_service=user_service,
                    email_service=email_service,
                )
            else:
                # Handle regular credit purchase
                success, user_email, credits, _ = stripe_service.handle_checkout_completed(session)

                if success and user_email and credits > 0:
                    # Add credits to user account
                    ok, new_balance, credit_msg = user_service.add_credits(
                        email=user_email,
                        amount=credits,
                        reason="stripe_purchase",
                        stripe_session_id=session_id,
                    )

                    if ok:
                        # Send confirmation email
                        email_service.send_credits_added(user_email, credits, new_balance)
                        logger.info(f"Added {credits} credits to {user_email}, new balance: {new_balance}")
                    else:
                        logger.error(f"Failed to add credits: {credit_msg}")

            # Update user's cached total_spent (non-blocking)
            amount_total = session.get("amount_total", 0)
            if amount_total > 0 and customer_email:
                try:
                    from google.cloud.firestore_v1 import Increment
                    user_doc_ref = user_service.get_user_doc_ref(customer_email)
                    if user_doc_ref:
                        user_doc_ref.update({"total_spent": Increment(amount_total)})
                    else:
                        logger.warning(f"No user doc found for {customer_email} to update total_spent")
                except Exception as spend_err:
                    logger.warning(f"Failed to update total_spent for {customer_email}: {spend_err}")

            # Store enriched payment data (non-blocking - failure won't break credit granting)
            try:
                import stripe as stripe_lib
                expanded_session = stripe_lib.checkout.Session.retrieve(
                    session_id,
                    expand=["payment_intent.latest_charge.balance_transaction"],
                )
                admin_service.store_payment(dict(expanded_session))
            except Exception as store_err:
                logger.warning(f"Failed to store payment data for {session_id}: {store_err}")

            admin_service.log_webhook_event(
                event_id=event_id,
                event_type=event_type,
                status="success",
                session_id=session_id,
                customer_email=customer_email,
                summary=f"Payment ${session.get('amount_total', 0) / 100:.2f} processed",
            )

        elif event_type == "charge.refunded":
            charge = event["data"]["object"]
            payment_intent_id = charge.get("payment_intent")
            refund_amount = charge.get("amount_refunded", 0)
            original_amount = charge.get("amount", 0)

            # Find the payment by payment_intent_id
            try:
                collection = admin_service.db.collection("stripe_payments")
                docs = list(
                    collection.where("payment_intent_id", "==", payment_intent_id)
                    .limit(1)
                    .stream()
                )
                if docs:
                    doc = docs[0]
                    status = "refunded" if refund_amount >= original_amount else "partially_refunded"
                    admin_service.update_payment_status(doc.id, {
                        "status": status,
                        "refund_amount": refund_amount,
                        "refunded_at": datetime.utcnow(),
                    })
                    logger.info(f"Updated payment {doc.id} to {status} (refund: ${refund_amount / 100:.2f})")
            except Exception as e:
                logger.warning(f"Failed to update refund status: {e}")

            admin_service.log_webhook_event(
                event_id=event_id,
                event_type=event_type,
                status="success",
                summary=f"Refund ${refund_amount / 100:.2f} on {payment_intent_id}",
            )

        elif event_type in ("charge.dispute.created", "charge.dispute.closed"):
            dispute = event["data"]["object"]
            payment_intent_id = dispute.get("payment_intent")
            dispute_status = dispute.get("status")
            dispute_amount = dispute.get("amount", 0)

            try:
                collection = admin_service.db.collection("stripe_payments")
                docs = list(
                    collection.where("payment_intent_id", "==", payment_intent_id)
                    .limit(1)
                    .stream()
                )
                if docs:
                    doc = docs[0]
                    new_status = "disputed" if event_type == "charge.dispute.created" else doc.to_dict().get("status", "succeeded")
                    if event_type == "charge.dispute.closed" and dispute_status == "won":
                        new_status = "succeeded"
                    admin_service.update_payment_status(doc.id, {"status": new_status})
            except Exception as e:
                logger.warning(f"Failed to update dispute status: {e}")

            admin_service.log_webhook_event(
                event_id=event_id,
                event_type=event_type,
                status="success",
                summary=f"Dispute {dispute_status}: ${dispute_amount / 100:.2f}",
            )

        elif event_type == "checkout.session.expired":
            logger.info(f"Checkout session expired: {event['data']['object'].get('id')}")
            admin_service.log_webhook_event(
                event_id=event_id,
                event_type=event_type,
                status="success",
                session_id=event["data"]["object"].get("id"),
                summary="Checkout session expired",
            )

        elif event_type == "payment_intent.payment_failed":
            logger.warning(f"Payment failed: {event['data']['object'].get('id')}")
            admin_service.log_webhook_event(
                event_id=event_id,
                event_type=event_type,
                status="success",
                summary=f"Payment failed: {event['data']['object'].get('id')}",
            )

        else:
            admin_service.log_webhook_event(
                event_id=event_id,
                event_type=event_type,
                status="success",
                summary=f"Unhandled event type: {event_type}",
            )

    except Exception as e:
        admin_service.log_webhook_event(
            event_id=event_id,
            event_type=event_type,
            status="error",
            error_message=str(e),
        )
        raise

    # Return 200 to acknowledge receipt (Stripe will retry on non-2xx)
    return {"status": "received", "type": event_type}


# =============================================================================
# User Feedback-for-Credits Endpoints
# =============================================================================

@router.get("/feedback/eligibility", response_model=FeedbackEligibilityResponse)
async def check_feedback_eligibility(
    authorization: Optional[str] = Header(None),
    user_service: UserService = Depends(get_user_service),
):
    """
    Check if the current user is eligible to submit feedback for credits.

    Eligible if: total_jobs_completed >= 2 AND has not already submitted feedback.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization

    # Try auth service first (admin tokens, auth_tokens)
    from backend.services.auth_service import get_auth_service
    auth_service = get_auth_service()
    auth_result = auth_service.validate_token_full(token)

    user = None
    if auth_result.is_valid and auth_result.user_email:
        user = user_service.get_or_create_user(auth_result.user_email)
    else:
        valid, user, message = user_service.validate_session(token)
        if not valid or not user:
            raise HTTPException(status_code=401, detail=message)

    eligible = user.total_jobs_completed >= 2 and not user.has_submitted_feedback

    return FeedbackEligibilityResponse(
        eligible=eligible,
        has_submitted=user.has_submitted_feedback,
        jobs_completed=user.total_jobs_completed,
        credits_reward=1,
    )


@router.post("/feedback", response_model=UserFeedbackResponse)
async def submit_user_feedback(
    request: UserFeedbackRequest,
    authorization: Optional[str] = Header(None),
    user_service: UserService = Depends(get_user_service),
):
    """
    Submit product feedback to earn 1 free credit.

    Requires authentication. Users must have completed 2+ jobs and
    not have already submitted feedback. At least one text field
    must have >50 characters.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization

    # Try auth service first (admin tokens, auth_tokens)
    from backend.services.auth_service import get_auth_service
    auth_service = get_auth_service()
    auth_result = auth_service.validate_token_full(token)

    user = None
    if auth_result.is_valid and auth_result.user_email:
        user = user_service.get_or_create_user(auth_result.user_email)
    else:
        valid, user, message = user_service.validate_session(token)
        if not valid or not user:
            raise HTTPException(status_code=401, detail=message)

    # Check eligibility: must have completed 2+ jobs
    if user.total_jobs_completed < 2:
        raise HTTPException(
            status_code=400,
            detail="You need to complete at least 2 karaoke videos before submitting feedback."
        )

    # Check if already submitted
    if user.has_submitted_feedback:
        raise HTTPException(
            status_code=400,
            detail="You have already submitted feedback. Thank you!"
        )

    # Validate: at least one text field has >50 characters
    has_detailed_feedback = (
        (request.what_went_well and len(request.what_went_well) > 50) or
        (request.what_could_improve and len(request.what_could_improve) > 50) or
        (request.additional_comments and len(request.additional_comments) > 50)
    )
    if not has_detailed_feedback:
        raise HTTPException(
            status_code=400,
            detail="Please provide detailed feedback in at least one text field (more than 50 characters)."
        )

    # Save feedback to Firestore
    import uuid

    feedback = UserFeedback(
        id=str(uuid.uuid4()),
        user_email=user.email,
        overall_rating=request.overall_rating,
        ease_of_use_rating=request.ease_of_use_rating,
        lyrics_accuracy_rating=request.lyrics_accuracy_rating,
        correction_experience_rating=request.correction_experience_rating,
        what_went_well=request.what_went_well,
        what_could_improve=request.what_could_improve,
        additional_comments=request.additional_comments,
        would_recommend=request.would_recommend,
        would_use_again=request.would_use_again,
        submitted_via="web",
    )

    user_service.db.collection("user_feedback").document(feedback.id).set(
        feedback.model_dump(mode='json')
    )

    # Mark user as having submitted feedback
    user_service.update_user(
        user.email,
        has_submitted_feedback=True,
    )

    # AI evaluation before granting feedback credits
    credits_granted = 0
    feedback_content = {
        "overall_rating": request.overall_rating,
        "what_went_well": request.what_went_well,
        "what_could_improve": request.what_could_improve,
        "additional_comments": request.additional_comments,
    }
    try:
        from backend.services.credit_evaluation_service import get_credit_evaluation_service
        eval_service = get_credit_evaluation_service()
        evaluation = eval_service.evaluate(user.email, "feedback", feedback_content)

        if evaluation.decision == "deny":
            logger.info(f"Feedback credits denied for {_mask_email(user.email)}: {evaluation.reasoning}")
            try:
                from backend.services.email_service import get_email_service
                get_email_service().send_credit_denied_email(user.email, "feedback")
            except Exception:
                logger.exception(f"Failed to send credit denied email to {_mask_email(user.email)}")
            return UserFeedbackResponse(
                status="success",
                message="Thank you for your feedback! We appreciate your input.",
                credits_granted=0,
            )

        if evaluation.decision == "pending_review":
            logger.info(f"Feedback credits pending review for {_mask_email(user.email)}: {evaluation.reasoning}")
            try:
                from backend.services.email_service import get_email_service
                get_email_service().send_credit_review_needed_email(user.email, "feedback", evaluation.reasoning)
            except Exception:
                logger.exception(f"Failed to send review needed email for {_mask_email(user.email)}")
            return UserFeedbackResponse(
                status="success",
                message="Thank you for your feedback! Our team will review your account shortly.",
                credits_granted=0,
            )
    except Exception:
        logger.exception(f"Credit evaluation failed for {_mask_email(user.email)} — pending review (fail-closed)")
        try:
            from backend.services.email_service import get_email_service
            get_email_service().send_credit_review_needed_email(user.email, "feedback", "Evaluation error")
        except Exception:
            pass
        return UserFeedbackResponse(
            status="success",
            message="Thank you for your feedback! Our team will review your account shortly.",
            credits_granted=0,
        )

    # Grant 1 credit (passed evaluation)
    credits_granted = 1
    user_service.add_credits(
        email=user.email,
        amount=credits_granted,
        reason="feedback_bonus",
    )

    logger.info(
        f"User feedback received from {_mask_email(user.email)}, "
        f"credits granted: {credits_granted}"
    )

    # Send admin notification email (fire-and-forget)
    try:
        from backend.services.email_service import get_email_service
        email_service = get_email_service()
        email_service.send_feedback_notification(
            user_email=user.email,
            overall_rating=request.overall_rating,
            ease_of_use_rating=request.ease_of_use_rating,
            lyrics_accuracy_rating=request.lyrics_accuracy_rating,
            correction_experience_rating=request.correction_experience_rating,
            what_went_well=request.what_went_well,
            what_could_improve=request.what_could_improve,
            additional_comments=request.additional_comments,
            would_recommend=request.would_recommend,
            would_use_again=request.would_use_again,
        )
    except Exception as e:
        logger.warning(f"Failed to send feedback notification email: {e}")

    return UserFeedbackResponse(
        status="success",
        message=f"Thank you for your feedback! You earned {credits_granted} free credits.",
        credits_granted=credits_granted,
    )


# =============================================================================
# Admin Endpoints
# =============================================================================

class UserListResponsePaginated(BaseModel):
    """Paginated response for user list."""
    users: list[UserPublic]
    total: int
    offset: int
    limit: int
    has_more: bool


class UserDetailResponse(BaseModel):
    """Detailed user information for admin view."""
    email: str
    role: UserRole
    credits: int
    display_name: Optional[str] = None
    is_active: bool = True
    email_verified: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_login_at: Optional[str] = None
    total_jobs_created: int = 0
    total_jobs_completed: int = 0
    total_spent: int = 0
    credit_transactions: list[dict] = []
    recent_jobs: list[dict] = []
    active_sessions_count: int = 0
    # Anti-abuse / identity info
    signup_ip: Optional[str] = None
    device_fingerprint: Optional[str] = None
    welcome_credits_granted: bool = False
    has_submitted_feedback: bool = False
    # Recent session details (IP, user agent, fingerprint)
    recent_sessions: list[dict] = []


@router.get("/admin/users", response_model=UserListResponsePaginated)
async def list_users(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    include_inactive: bool = False,
    exclude_test: bool = True,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    List all users with search, pagination, and sorting (admin only).

    Args:
        limit: Maximum users to return (default 50, max 100)
        offset: Number of users to skip for pagination
        search: Search by email (case-insensitive prefix match)
        sort_by: Field to sort by (created_at, last_login_at, credits, email)
        sort_order: Sort direction (asc, desc)
        include_inactive: Include disabled users
        exclude_test: If True (default), exclude test users (e.g., @inbox.testmail.app)
    """
    from google.cloud import firestore
    from google.cloud.firestore_v1 import FieldFilter

    # Validate and cap limit
    limit = min(limit, 100)

    db = user_service.db
    query = db.collection(USERS_COLLECTION)

    # Filter inactive users
    if not include_inactive:
        query = query.where(filter=FieldFilter('is_active', '==', True))

    # Search by email prefix (case-insensitive via range query)
    search = search.strip() if search else None
    if search:
        search_lower = search.lower()
        # Use range query for prefix matching
        query = query.where(filter=FieldFilter('email', '>=', search_lower))
        query = query.where(filter=FieldFilter('email', '<', search_lower + '\uffff'))

    # Sorting
    # When search is active, Firestore range filters on 'email' conflict with
    # order_by on a different field (requires composite index). Since we already
    # fetch all docs and paginate in Python, just sort in Python when searching.
    sort_field = sort_by if sort_by in ["created_at", "last_login_at", "credits", "email"] else "created_at"
    if not search:
        direction = firestore.Query.DESCENDING if sort_order == "desc" else firestore.Query.ASCENDING
        query = query.order_by(sort_field, direction=direction)

    # Get all docs and filter in Python
    # Note: This is expensive for large datasets, consider caching
    all_docs = list(query.stream())

    # Sort in Python when search is active (can't use Firestore order_by with range filter)
    if search:
        reverse = sort_order == "desc"
        all_docs.sort(key=lambda d: (d.to_dict().get(sort_field) is None, d.to_dict().get(sort_field)), reverse=reverse)

    # Filter out test users if exclude_test is True
    if exclude_test:
        all_docs = [d for d in all_docs if not is_test_email(d.to_dict().get('email', ''))]

    total_count = len(all_docs)

    # Apply pagination manually (Firestore doesn't support offset well)
    paginated_docs = all_docs[offset:offset + limit]

    users_public = []
    for doc in paginated_docs:
        data = doc.to_dict()
        created_at_val = data.get("created_at")
        last_login_val = data.get("last_login_at")
        users_public.append(UserPublic(
            email=data.get("email", ""),
            role=data.get("role", UserRole.USER),
            credits=data.get("credits", 0),
            display_name=data.get("display_name"),
            total_jobs_created=data.get("total_jobs_created", 0),
            total_jobs_completed=data.get("total_jobs_completed", 0),
            total_spent=data.get("total_spent", 0),
            created_at=created_at_val.isoformat() if hasattr(created_at_val, 'isoformat') else str(created_at_val) if created_at_val else None,
            last_login_at=last_login_val.isoformat() if hasattr(last_login_val, 'isoformat') else str(last_login_val) if last_login_val else None,
        ))

    return UserListResponsePaginated(
        users=users_public,
        total=total_count,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total_count,
    )


@router.get("/admin/users/{email}/detail", response_model=UserDetailResponse)
async def get_user_detail(
    email: str,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Get detailed user information including credit history and recent jobs (admin only).
    """
    from google.cloud import firestore
    from google.cloud.firestore_v1 import FieldFilter
    from urllib.parse import unquote

    # URL decode the email (handles @ and other special chars)
    email = unquote(email).lower()

    user = user_service.get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db = user_service.db

    # Get recent jobs for this user
    jobs_query = db.collection("jobs").where(
        filter=FieldFilter("user_email", "==", email)
    ).order_by("created_at", direction=firestore.Query.DESCENDING).limit(10)

    recent_jobs = []
    for job_doc in jobs_query.stream():
        job_data = job_doc.to_dict()
        created_at = job_data.get("created_at")
        # Handle both datetime objects and ISO strings
        if created_at:
            created_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at)
        else:
            created_at_str = None
        recent_jobs.append({
            "job_id": job_data.get("job_id"),
            "status": job_data.get("status"),
            "artist": job_data.get("artist"),
            "title": job_data.get("title"),
            "created_at": created_at_str,
        })

    # Get active sessions with details
    sessions_query = db.collection("sessions").where(
        filter=FieldFilter("user_email", "==", email)
    ).where(
        filter=FieldFilter("is_active", "==", True)
    )
    active_sessions = []
    for session_doc in sessions_query.stream():
        sd = session_doc.to_dict()
        created = sd.get("created_at")
        active_sessions.append({
            "ip_address": sd.get("ip_address"),
            "user_agent": sd.get("user_agent"),
            "device_fingerprint": sd.get("device_fingerprint"),
            "created_at": created.isoformat() if hasattr(created, 'isoformat') else str(created) if created else None,
            "last_activity_at": str(sd.get("last_activity_at", "")),
        })
    active_sessions_count = len(active_sessions)

    # Format credit transactions
    credit_transactions = []
    for txn in user.credit_transactions[-20:]:  # Last 20 transactions
        if hasattr(txn, 'model_dump'):
            credit_transactions.append(txn.model_dump(mode='json'))
        elif isinstance(txn, dict):
            credit_transactions.append(txn)

    return UserDetailResponse(
        email=user.email,
        role=user.role,
        credits=user.credits,
        display_name=user.display_name,
        is_active=user.is_active,
        email_verified=user.email_verified,
        created_at=user.created_at.isoformat() if user.created_at else None,
        updated_at=user.updated_at.isoformat() if user.updated_at else None,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        total_jobs_created=user.total_jobs_created,
        total_jobs_completed=user.total_jobs_completed,
        total_spent=user.total_spent,
        credit_transactions=credit_transactions,
        recent_jobs=recent_jobs,
        active_sessions_count=active_sessions_count,
        signup_ip=user.signup_ip,
        device_fingerprint=user.device_fingerprint,
        welcome_credits_granted=user.welcome_credits_granted,
        has_submitted_feedback=user.has_submitted_feedback,
        recent_sessions=active_sessions,
    )


@router.post("/admin/credits", response_model=AddCreditsResponse)
async def add_credits_to_user(
    request: AddCreditsRequest,
    auth_data: AuthResult = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Add credits to a user's account (admin only).

    Use this to grant free credits to users, e.g., for beta testers or promotions.
    """
    admin_id = auth_data.user_email or "admin:unknown"

    success, new_balance, message = user_service.add_credits(
        email=request.email,
        amount=request.amount,
        reason=request.reason,
        admin_email=admin_id,
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    # Send notification email
    if request.amount > 0:
        email_service.send_credits_added(request.email, request.amount, new_balance)

    return AddCreditsResponse(
        status="success",
        email=request.email,
        credits_added=request.amount,
        new_balance=new_balance,
        message=message,
    )


@router.post("/admin/users/{email}/disable")
async def disable_user(
    email: str,
    auth_data: AuthResult = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Disable a user account (admin only).
    """
    admin_id = auth_data.user_email or "admin:unknown"

    success = user_service.disable_user(email, admin_email=admin_id)

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "message": f"User {email} has been disabled"}


@router.post("/admin/users/{email}/enable")
async def enable_user(
    email: str,
    auth_data: AuthResult = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Enable a user account (admin only).
    """
    admin_id = auth_data.user_email or "admin:unknown"

    success = user_service.enable_user(email, admin_email=admin_id)

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "message": f"User {email} has been enabled"}


@router.delete("/admin/users/{email}")
async def delete_user(
    email: str,
    auth_data: AuthResult = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Permanently delete a user and all associated data (admin only).

    Jobs are NOT deleted - they remain as historical records.
    """
    admin_id = auth_data.user_email or "admin:unknown"

    try:
        success = user_service.delete_user(email, admin_email=admin_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "message": f"User {email} has been deleted"}


@router.post("/admin/users/{email}/role")
async def set_user_role(
    email: str,
    role: UserRole,
    auth_data: AuthResult = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Set a user's role (admin only).
    """
    admin_id = auth_data.user_email or "admin:unknown"

    success = user_service.set_user_role(email, role, admin_email=admin_id)

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "message": f"User {email} role set to {role.value}"}


