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

from backend.models.user import (
    UserRole,
    UserPublic,
    SendMagicLinkRequest,
    SendMagicLinkResponse,
    VerifyMagicLinkResponse,
    AddCreditsRequest,
    AddCreditsResponse,
    UserListResponse,
    BetaTesterStatus,
    BetaTesterEnrollRequest,
    BetaTesterEnrollResponse,
    BetaFeedbackRequest,
    BetaFeedbackResponse,
    BetaTesterFeedback,
)
from backend.services.user_service import get_user_service, UserService, USERS_COLLECTION
from backend.services.email_service import get_email_service, EmailService
from backend.services.stripe_service import get_stripe_service, StripeService, CREDIT_PACKAGES
from backend.services.theme_service import get_theme_service
from backend.api.dependencies import require_admin
from backend.api.routes.file_upload import _prepare_theme_for_job
from backend.services.auth_service import UserType
from backend.utils.test_data import is_test_email


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


class DoneForYouCheckoutRequest(BaseModel):
    """Request to create a done-for-you karaoke video order."""
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

    # Check if email service is configured
    if not email_service.is_configured():
        logger.error("Email service not configured - cannot send magic links")
        raise HTTPException(
            status_code=503,
            detail="Email service is not available. Please contact support."
        )

    email = request.email.lower()

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

    # Get client info for security logging
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")

    # Create magic link token with tenant context
    magic_link = user_service.create_magic_link(
        email,
        ip_address=ip_address,
        user_agent=user_agent,
        tenant_id=tenant_id
    )

    # Get tenant-specific sender email
    sender_email = None
    if tenant_config:
        sender_email = tenant_config.get_sender_email()

    # Send email (with tenant-specific sender if configured)
    sent = email_service.send_magic_link(email, magic_link.token, sender_email=sender_email)

    if not sent:
        logger.error(f"Failed to send magic link email to {email}")
        # Don't reveal failure to prevent email enumeration
        # Still return success

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
    # Get client info
    ip_address = http_request.client.host if http_request.client else None
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

    # Create session with tenant context from the magic link
    session = user_service.create_session(
        user.email,
        ip_address=ip_address,
        user_agent=user_agent,
        tenant_id=magic_link_tenant_id
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

    return VerifyMagicLinkResponse(
        status="success",
        session_token=session.token,
        user=user_public,
        message="Successfully signed in"
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
        user_public = UserPublic(
            email=user.email,
            role=user.role if not auth_result.is_admin else UserRole.ADMIN,
            credits=user.credits,
            display_name=user.display_name,
            total_jobs_created=user.total_jobs_created,
            total_jobs_completed=user.total_jobs_completed,
        )
        return UserProfileResponse(user=user_public, has_session=True)

    # Fall back to session validation (for magic link sessions)
    valid, user, message = user_service.validate_session(token)

    if not valid or not user:
        raise HTTPException(status_code=401, detail=message)

    user_public = UserPublic(
        email=user.email,
        role=user.role,
        credits=user.credits,
        display_name=user.display_name,
        total_jobs_created=user.total_jobs_created,
        total_jobs_completed=user.total_jobs_completed,
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


@router.post("/done-for-you/checkout", response_model=CreateCheckoutResponse)
async def create_done_for_you_checkout(
    request: DoneForYouCheckoutRequest,
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """
    Create a Stripe checkout session for a done-for-you karaoke video order.

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

    success, checkout_url, message = stripe_service.create_done_for_you_checkout_session(
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

# Admin email for done-for-you order notifications
ADMIN_EMAIL = "andrew@nomadkaraoke.com"


async def _handle_done_for_you_order(
    session_id: str,
    metadata: dict,
    user_service: UserService,
    email_service: EmailService,
) -> None:
    """
    Handle a completed done-for-you order by creating a job and notifying Andrew.

    For orders with a YouTube URL, the job is created and workers are triggered immediately.
    For orders without a URL (search mode), the audio search flow is used to find and
    download the best matching audio source automatically.

    Args:
        session_id: Stripe checkout session ID
        metadata: Order metadata from Stripe session
        user_service: User service for marking session processed
        email_service: Email service for notifications
    """
    from backend.models.job import JobCreate, JobStatus
    from backend.services.job_manager import JobManager
    from backend.services.worker_service import get_worker_service
    from backend.services.audio_search_service import (
        get_audio_search_service,
        NoResultsError,
        AudioSearchError,
    )
    from backend.services.storage_service import StorageService
    import asyncio
    import tempfile
    import os

    customer_email = metadata.get("customer_email", "")
    artist = metadata.get("artist", "Unknown Artist")
    title = metadata.get("title", "Unknown Title")
    source_type = metadata.get("source_type", "search")
    youtube_url = metadata.get("youtube_url")
    notes = metadata.get("notes", "")

    logger.info(
        f"Processing done-for-you order: {artist} - {title} for {customer_email} "
        f"(session: {session_id}, source_type: {source_type})"
    )

    try:
        job_manager = JobManager()
        worker_service = get_worker_service()
        storage_service = StorageService()

        # Apply default theme (Nomad) - same as audio_search endpoint
        theme_service = get_theme_service()
        effective_theme_id = theme_service.get_default_theme_id()
        if effective_theme_id:
            logger.info(f"Applying default theme '{effective_theme_id}' for done-for-you order")

        # Create job for the customer
        # Note: done-for-you jobs should NOT be non_interactive - Andrew needs to review
        job_create = JobCreate(
            url=youtube_url if youtube_url else None,
            artist=artist,
            title=title,
            user_email=customer_email,  # Customer owns the job
            theme_id=effective_theme_id,  # Apply default theme
            non_interactive=False,  # Andrew will review lyrics/instrumental
            # Set audio search fields for search-based orders
            audio_search_artist=artist if not youtube_url else None,
            audio_search_title=title if not youtube_url else None,
            auto_download=True,  # Auto-select best audio source
        )
        job = job_manager.create_job(job_create)
        job_id = job.job_id

        logger.info(f"Created done-for-you job {job_id} for {customer_email}")

        # Prepare theme style assets for the job (same as audio_search endpoint)
        if effective_theme_id:
            try:
                style_params_path, theme_style_assets, youtube_desc = _prepare_theme_for_job(
                    job_id, effective_theme_id, None  # No color overrides for done-for-you
                )
                theme_update = {
                    'style_params_gcs_path': style_params_path,
                    'style_assets': theme_style_assets,
                }
                if youtube_desc:
                    theme_update['youtube_description_template'] = youtube_desc
                job_manager.update_job(job_id, theme_update)
                logger.info(f"Applied theme '{effective_theme_id}' to done-for-you job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to prepare theme for done-for-you job {job_id}: {e}")

        # Mark session as processed for idempotency
        # Note: Using internal method since this isn't a credit transaction
        user_service._mark_stripe_session_processed(
            stripe_session_id=session_id,
            email=customer_email,
            amount=0  # No credits, just tracking the session
        )

        # Handle based on whether we have a YouTube URL or need to search
        if youtube_url:
            # URL provided - trigger workers directly
            logger.info(f"Job {job_id}: YouTube URL provided, triggering workers")
            await asyncio.gather(
                worker_service.trigger_audio_worker(job_id),
                worker_service.trigger_lyrics_worker(job_id)
            )
        else:
            # No URL - use audio search flow with auto_download
            logger.info(f"Job {job_id}: No URL, using audio search for '{artist} - {title}'")

            # Update job with audio search fields
            job_manager.update_job(job_id, {
                'audio_search_artist': artist,
                'audio_search_title': title,
                'auto_download': True,
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
                # No results found - transition to AWAITING_AUDIO_SELECTION so Andrew can handle manually
                logger.warning(f"Job {job_id}: No audio sources found for '{artist} - {title}'")
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.AWAITING_AUDIO_SELECTION,
                    progress=10,
                    message=f"No automatic audio sources found. Manual intervention required."
                )
                # Don't fail the job - Andrew can manually provide audio
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
                # Store search results in state_data
                results_dicts = [r.to_dict() for r in search_results]
                state_data_update = {
                    'audio_search_results': results_dicts,
                    'audio_search_count': len(results_dicts),
                }
                if audio_search_service.last_remote_search_id:
                    state_data_update['remote_search_id'] = audio_search_service.last_remote_search_id
                job_manager.update_job(job_id, {'state_data': state_data_update})

                # Auto-select best result and download
                best_index = audio_search_service.select_best(search_results)
                selected = results_dicts[best_index]

                logger.info(f"Job {job_id}: Auto-selected result {best_index}: {selected.get('provider')} - {selected.get('title')}")

                # Transition to downloading state
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.DOWNLOADING_AUDIO,
                    progress=10,
                    message=f"Downloading from {selected.get('provider')}: {selected.get('artist')} - {selected.get('title')}",
                    state_data_updates={
                        'selected_audio_index': best_index,
                        'selected_audio_provider': selected.get('provider'),
                    }
                )

                # Download audio
                try:
                    is_torrent_source = selected.get('provider') in ['RED', 'OPS']
                    is_remote_enabled = audio_search_service.is_remote_enabled()
                    source_id = selected.get('source_id')
                    source_name = selected.get('provider')
                    target_file = selected.get('target_file')
                    download_url = selected.get('url')
                    remote_search_id = state_data_update.get('remote_search_id')

                    if is_torrent_source and is_remote_enabled:
                        # Remote torrent download - upload directly to GCS
                        gcs_destination = f"uploads/{job_id}/audio/"

                        if source_id and source_name:
                            result = audio_search_service.download_by_id(
                                source_name=source_name,
                                source_id=source_id,
                                output_dir="",
                                target_file=target_file,
                                download_url=download_url,
                                gcs_path=gcs_destination,
                            )
                        else:
                            result = audio_search_service.download(
                                result_index=best_index,
                                output_dir="",
                                gcs_path=gcs_destination,
                                remote_search_id=remote_search_id,
                            )

                        # Extract GCS path
                        if result.filepath.startswith("gs://"):
                            parts = result.filepath.replace("gs://", "").split("/", 1)
                            audio_gcs_path = parts[1] if len(parts) == 2 else result.filepath
                            filename = os.path.basename(result.filepath)
                        else:
                            filename = os.path.basename(result.filepath)
                            audio_gcs_path = f"uploads/{job_id}/audio/{filename}"
                    else:
                        # Local download (YouTube or local torrent)
                        temp_dir = tempfile.mkdtemp(prefix=f"audio_download_{job_id}_")
                        import shutil

                        try:
                            if source_id and source_name and is_remote_enabled:
                                result = audio_search_service.download_by_id(
                                    source_name=source_name,
                                    source_id=source_id,
                                    output_dir=temp_dir,
                                    target_file=target_file,
                                    download_url=download_url,
                                )
                            elif source_name == 'YouTube' and download_url:
                                # YouTube download
                                from backend.workers.audio_worker import download_from_url
                                local_path = await download_from_url(
                                    download_url,
                                    temp_dir,
                                    selected.get('artist'),
                                    selected.get('title')
                                )
                                if not local_path or not os.path.exists(local_path):
                                    raise Exception(f"Failed to download from YouTube: {download_url}")

                                class DownloadResult:
                                    def __init__(self, filepath):
                                        self.filepath = filepath
                                result = DownloadResult(local_path)
                            else:
                                result = audio_search_service.download(
                                    result_index=best_index,
                                    output_dir=temp_dir,
                                    remote_search_id=remote_search_id,
                                )

                            # Upload to GCS
                            filename = os.path.basename(result.filepath)
                            audio_gcs_path = f"uploads/{job_id}/audio/{filename}"

                            with open(result.filepath, 'rb') as f:
                                storage_service.upload_fileobj(f, audio_gcs_path, content_type='audio/flac')
                        finally:
                            # Always cleanup temp directory
                            shutil.rmtree(temp_dir, ignore_errors=True)

                    # Update job with GCS path
                    job_manager.update_job(job_id, {
                        'input_media_gcs_path': audio_gcs_path,
                        'filename': filename,
                    })

                    # Transition to DOWNLOADING and trigger workers
                    job_manager.transition_to_state(
                        job_id=job_id,
                        new_status=JobStatus.DOWNLOADING,
                        progress=15,
                        message="Audio downloaded, starting processing"
                    )

                    # Trigger workers
                    await asyncio.gather(
                        worker_service.trigger_audio_worker(job_id),
                        worker_service.trigger_lyrics_worker(job_id)
                    )

                    logger.info(f"Job {job_id}: Audio downloaded and workers triggered")

                except Exception as download_error:
                    logger.error(f"Job {job_id}: Audio download failed: {download_error}")
                    # Don't fail job - transition to awaiting selection so Andrew can handle
                    job_manager.transition_to_state(
                        job_id=job_id,
                        new_status=JobStatus.AWAITING_AUDIO_SELECTION,
                        progress=10,
                        message=f"Auto-download failed: {download_error}. Manual intervention required."
                    )

        # Send confirmation email to customer
        email_service.send_email(
            to_email=customer_email,
            subject=f"Your Karaoke Video Order: {artist} - {title}",
            html_content=f"""
            <h2>Thank you for your order!</h2>
            <p>We've received your request for a karaoke video:</p>
            <ul>
                <li><strong>Artist:</strong> {artist}</li>
                <li><strong>Title:</strong> {title}</li>
                {f'<li><strong>Notes:</strong> {notes}</li>' if notes else ''}
            </ul>
            <p>Our team will review and create your video within <strong>24 hours</strong>.</p>
            <p>You'll receive another email with download links when your video is ready.</p>
            <p>If you have any questions, reply to this email or contact us at support@nomadkaraoke.com</p>
            <p>Thanks for using Nomad Karaoke!</p>
            """,
            text_content=f"""
Thank you for your order!

We've received your request for a karaoke video:
- Artist: {artist}
- Title: {title}
{f'- Notes: {notes}' if notes else ''}

Our team will review and create your video within 24 hours.
You'll receive another email with download links when your video is ready.

If you have any questions, reply to this email or contact us at support@nomadkaraoke.com

Thanks for using Nomad Karaoke!
            """.strip(),
        )

        # Send notification email to Andrew
        email_service.send_email(
            to_email=ADMIN_EMAIL,
            subject=f"[Done For You Order] {artist} - {title}",
            html_content=f"""
            <h2>New Done-For-You Order</h2>
            <p>A customer has ordered a karaoke video:</p>
            <ul>
                <li><strong>Customer:</strong> {customer_email}</li>
                <li><strong>Artist:</strong> {artist}</li>
                <li><strong>Title:</strong> {title}</li>
                <li><strong>Source:</strong> {source_type}</li>
                {f'<li><strong>YouTube URL:</strong> <a href="{youtube_url}">{youtube_url}</a></li>' if youtube_url else ''}
                {f'<li><strong>Notes:</strong> {notes}</li>' if notes else ''}
            </ul>
            <p><strong>Job ID:</strong> {job_id}</p>
            <p>View job in admin: <a href="https://gen.nomadkaraoke.com/admin/jobs/{job_id}">Admin Link</a></p>
            <p>View job as customer: <a href="https://gen.nomadkaraoke.com/jobs/{job_id}">Customer Link</a></p>
            <p><strong>Deadline:</strong> 24 hours from now</p>
            """,
            text_content=f"""
New Done-For-You Order

Customer: {customer_email}
Artist: {artist}
Title: {title}
Source: {source_type}
{f'YouTube URL: {youtube_url}' if youtube_url else ''}
{f'Notes: {notes}' if notes else ''}

Job ID: {job_id}
Admin: https://gen.nomadkaraoke.com/admin/jobs/{job_id}
Customer: https://gen.nomadkaraoke.com/jobs/{job_id}

Deadline: 24 hours from now
            """.strip(),
        )

        logger.info(f"Sent done-for-you order notifications for job {job_id}")

    except Exception as e:
        logger.error(f"Error processing done-for-you order: {e}", exc_info=True)
        # Still try to notify Andrew of the failure
        try:
            email_service.send_email(
                to_email=ADMIN_EMAIL,
                subject=f"[FAILED] Done For You Order: {artist} - {title}",
                html_content=f"""
                <h2>Done-For-You Order Failed</h2>
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
Done-For-You Order Failed

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
    logger.info(f"Received Stripe webhook: {event_type}")

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session.get("id")
        metadata = session.get("metadata", {})

        # Idempotency check: Skip if this session was already processed
        if session_id and user_service.is_stripe_session_processed(session_id):
            logger.info(f"Skipping already processed session: {session_id}")
            return {"status": "received", "type": event_type, "note": "already_processed"}

        # Check if this is a done-for-you order
        if metadata.get("order_type") == "done_for_you":
            # Handle done-for-you order - create a job
            await _handle_done_for_you_order(
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

    elif event_type == "checkout.session.expired":
        logger.info(f"Checkout session expired: {event['data']['object'].get('id')}")

    elif event_type == "payment_intent.payment_failed":
        logger.warning(f"Payment failed: {event['data']['object'].get('id')}")

    # Return 200 to acknowledge receipt (Stripe will retry on non-2xx)
    return {"status": "received", "type": event_type}


# =============================================================================
# Beta Tester Program
# =============================================================================

BETA_TESTER_FREE_CREDITS = 1  # Number of free credits for beta testers


@router.post("/beta/enroll", response_model=BetaTesterEnrollResponse)
async def enroll_beta_tester(
    request: BetaTesterEnrollRequest,
    http_request: Request,
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Enroll as a beta tester to receive free karaoke credits.

    Requirements:
    - Accept that there may be work to review/correct lyrics
    - Promise to provide feedback after using the tool

    Returns free credits and optionally a session token for new users.
    """
    # Check if email service is configured
    if not email_service.is_configured():
        logger.error("Email service not configured - cannot send beta welcome emails")
        raise HTTPException(
            status_code=503,
            detail="Email service is not available. Please contact support."
        )

    email = request.email.lower()

    # Validate acceptance
    if not request.accept_corrections_work:
        raise HTTPException(
            status_code=400,
            detail="You must accept that you may need to review/correct lyrics"
        )

    if len(request.promise_text.strip()) < 10:
        raise HTTPException(
            status_code=400,
            detail="Please write a sentence confirming your promise to provide feedback"
        )

    # Get or create user
    user = user_service.get_or_create_user(email)

    # Check if already enrolled as beta tester
    if user.is_beta_tester:
        raise HTTPException(
            status_code=400,
            detail="You are already enrolled in the beta program"
        )

    # Get client info
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")

    # Enroll as beta tester
    from datetime import datetime
    user_service.update_user(
        email,
        is_beta_tester=True,
        beta_tester_status=BetaTesterStatus.ACTIVE.value,
        beta_enrolled_at=datetime.utcnow(),
        beta_promise_text=request.promise_text.strip(),
    )

    # Add free credits
    _, new_balance, _ = user_service.add_credits(
        email=email,
        amount=BETA_TESTER_FREE_CREDITS,
        reason="beta_tester_enrollment",
    )

    # Create session for the user (so they can start using the service immediately)
    session = user_service.create_session(email, ip_address=ip_address, user_agent=user_agent)

    # Send welcome email
    email_service.send_beta_welcome_email(email, BETA_TESTER_FREE_CREDITS)

    logger.info(f"Beta tester enrolled: {email}, granted {BETA_TESTER_FREE_CREDITS} credits")

    return BetaTesterEnrollResponse(
        status="success",
        message=f"Welcome to the beta program! You have {new_balance} free credits.",
        credits_granted=BETA_TESTER_FREE_CREDITS,
        session_token=session.token,
    )


@router.post("/beta/feedback", response_model=BetaFeedbackResponse)
async def submit_beta_feedback(
    request: BetaFeedbackRequest,
    authorization: Optional[str] = Header(None),
    user_service: UserService = Depends(get_user_service),
):
    """
    Submit feedback as a beta tester.

    Requires authentication. Updates beta tester status to completed.
    May grant bonus credits for detailed feedback.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Extract token
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization

    # Validate session
    valid, user, message = user_service.validate_session(token)
    if not valid or not user:
        raise HTTPException(status_code=401, detail=message)

    # Check if user is a beta tester
    if not user.is_beta_tester:
        raise HTTPException(
            status_code=400,
            detail="Only beta testers can submit feedback through this endpoint"
        )

    # Check if already completed feedback
    if user.beta_tester_status == BetaTesterStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail="You have already submitted feedback. Thank you!"
        )

    # Save feedback to Firestore
    import uuid

    feedback = BetaTesterFeedback(
        id=str(uuid.uuid4()),
        user_email=user.email,
        job_id=request.job_id,
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

    # Save to Firestore
    user_service.db.collection("beta_feedback").document(feedback.id).set(
        feedback.model_dump(mode='json')
    )

    # Update user status
    user_service.update_user(
        user.email,
        beta_tester_status=BetaTesterStatus.COMPLETED.value,
    )

    # Calculate bonus credits for detailed feedback
    bonus_credits = 0
    has_detailed_feedback = (
        (request.what_went_well and len(request.what_went_well) > 50) or
        (request.what_could_improve and len(request.what_could_improve) > 50) or
        (request.additional_comments and len(request.additional_comments) > 50)
    )

    if has_detailed_feedback:
        # Grant bonus credit for detailed feedback
        bonus_credits = 1
        user_service.add_credits(
            email=user.email,
            amount=bonus_credits,
            reason="beta_feedback_bonus",
        )

    logger.info(f"Beta feedback received from {user.email}, bonus: {bonus_credits}")

    return BetaFeedbackResponse(
        status="success",
        message="Thank you for your feedback!" + (
            f" You earned {bonus_credits} bonus credit for your detailed response!"
            if bonus_credits > 0 else ""
        ),
        bonus_credits=bonus_credits,
    )


@router.get("/beta/feedback-form")
async def get_feedback_form_data(
    authorization: Optional[str] = Header(None),
    user_service: UserService = Depends(get_user_service),
):
    """
    Get data needed to show the feedback form.

    Returns whether the user needs to submit feedback and any job context.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    valid, user, message = user_service.validate_session(token)

    if not valid or not user:
        raise HTTPException(status_code=401, detail=message)

    return {
        "is_beta_tester": user.is_beta_tester,
        "beta_status": user.beta_tester_status,
        "needs_feedback": (
            user.is_beta_tester and
            user.beta_tester_status == BetaTesterStatus.PENDING_FEEDBACK.value
        ),
        "can_submit_feedback": (
            user.is_beta_tester and
            user.beta_tester_status != BetaTesterStatus.COMPLETED.value
        ),
    }


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
    is_beta_tester: bool = False
    beta_tester_status: Optional[str] = None
    credit_transactions: list[dict] = []
    recent_jobs: list[dict] = []
    active_sessions_count: int = 0


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
    if search:
        search_lower = search.lower()
        # Use range query for prefix matching
        query = query.where(filter=FieldFilter('email', '>=', search_lower))
        query = query.where(filter=FieldFilter('email', '<', search_lower + '\uffff'))

    # Sorting
    direction = firestore.Query.DESCENDING if sort_order == "desc" else firestore.Query.ASCENDING
    if sort_by in ["created_at", "last_login_at", "credits", "email"]:
        query = query.order_by(sort_by, direction=direction)
    else:
        query = query.order_by("created_at", direction=direction)

    # Get all docs and filter in Python
    # Note: This is expensive for large datasets, consider caching
    all_docs = list(query.stream())

    # Filter out test users if exclude_test is True
    if exclude_test:
        all_docs = [d for d in all_docs if not is_test_email(d.to_dict().get('email', ''))]

    total_count = len(all_docs)

    # Apply pagination manually (Firestore doesn't support offset well)
    paginated_docs = all_docs[offset:offset + limit]

    users_public = []
    for doc in paginated_docs:
        data = doc.to_dict()
        users_public.append(UserPublic(
            email=data.get("email", ""),
            role=data.get("role", UserRole.USER),
            credits=data.get("credits", 0),
            display_name=data.get("display_name"),
            total_jobs_created=data.get("total_jobs_created", 0),
            total_jobs_completed=data.get("total_jobs_completed", 0),
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

    # Count active sessions
    sessions_query = db.collection("sessions").where(
        filter=FieldFilter("user_email", "==", email)
    ).where(
        filter=FieldFilter("is_active", "==", True)
    )
    active_sessions_count = sum(1 for _ in sessions_query.stream())

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
        is_beta_tester=user.is_beta_tester,
        beta_tester_status=user.beta_tester_status,
        credit_transactions=credit_transactions,
        recent_jobs=recent_jobs,
        active_sessions_count=active_sessions_count,
    )


@router.post("/admin/credits", response_model=AddCreditsResponse)
async def add_credits_to_user(
    request: AddCreditsRequest,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Add credits to a user's account (admin only).

    Use this to grant free credits to users, e.g., for beta testers or promotions.
    """
    admin_token, _, _ = auth_data

    # TODO: Enhance auth system to track admin email identity for better audit trails.
    # Current token-based admin auth doesn't include email identity.
    # For now, we log the token prefix for traceability.
    admin_id = f"admin:{admin_token[:8]}..." if admin_token else "admin:unknown"

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
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Disable a user account (admin only).
    """
    admin_token, _, _ = auth_data
    admin_id = f"admin:{admin_token[:8]}..." if admin_token else "admin:unknown"

    success = user_service.disable_user(email, admin_email=admin_id)

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "message": f"User {email} has been disabled"}


@router.post("/admin/users/{email}/enable")
async def enable_user(
    email: str,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Enable a user account (admin only).
    """
    admin_token, _, _ = auth_data
    admin_id = f"admin:{admin_token[:8]}..." if admin_token else "admin:unknown"

    success = user_service.enable_user(email, admin_email=admin_id)

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "message": f"User {email} has been enabled"}


@router.post("/admin/users/{email}/role")
async def set_user_role(
    email: str,
    role: UserRole,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Set a user's role (admin only).
    """
    admin_token, _, _ = auth_data
    admin_id = f"admin:{admin_token[:8]}..." if admin_token else "admin:unknown"

    success = user_service.set_user_role(email, role, admin_email=admin_id)

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "message": f"User {email} role set to {role.value}"}


@router.get("/admin/beta/feedback")
async def list_beta_feedback(
    limit: int = 50,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    List all beta tester feedback (admin only).
    """
    from google.cloud import firestore

    query = user_service.db.collection("beta_feedback")
    query = query.order_by("created_at", direction=firestore.Query.DESCENDING)
    query = query.limit(limit)

    docs = query.stream()
    feedback_list = [doc.to_dict() for doc in docs]

    return {
        "feedback": feedback_list,
        "total": len(feedback_list),
    }


@router.get("/admin/beta/stats")
async def get_beta_stats(
    exclude_test: bool = True,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Get beta tester program statistics (admin only).

    Args:
        exclude_test: If True (default), exclude test users from beta stats
    """
    from google.cloud.firestore_v1 import FieldFilter
    from google.cloud.firestore_v1 import aggregation

    users_collection = user_service.db.collection(USERS_COLLECTION)

    if exclude_test:
        # Stream and filter in Python since Firestore doesn't support "not ends with"
        all_beta_users = []
        for doc in users_collection.where(filter=FieldFilter("is_beta_tester", "==", True)).stream():
            data = doc.to_dict()
            if not is_test_email(data.get("email", "")):
                all_beta_users.append(data)

        total_beta_testers = len(all_beta_users)
        active_testers = sum(1 for u in all_beta_users if u.get("beta_tester_status") == "active")
        pending_feedback = sum(1 for u in all_beta_users if u.get("beta_tester_status") == "pending_feedback")
        completed_feedback = sum(1 for u in all_beta_users if u.get("beta_tester_status") == "completed")

        # Filter feedback by non-test users
        all_feedback = []
        for doc in user_service.db.collection("beta_feedback").stream():
            data = doc.to_dict()
            if not is_test_email(data.get("user_email", "")):
                all_feedback.append(data)
        feedback_docs = all_feedback
    else:
        # Use efficient aggregation queries when including test data
        def get_count(query) -> int:
            agg_query = aggregation.AggregationQuery(query)
            agg_query.count(alias="count")
            results = agg_query.get()
            return results[0][0].value if results else 0

        total_beta_testers = get_count(
            users_collection.where(filter=FieldFilter("is_beta_tester", "==", True))
        )
        active_testers = get_count(
            users_collection.where(filter=FieldFilter("beta_tester_status", "==", "active"))
        )
        pending_feedback = get_count(
            users_collection.where(filter=FieldFilter("beta_tester_status", "==", "pending_feedback"))
        )
        completed_feedback = get_count(
            users_collection.where(filter=FieldFilter("beta_tester_status", "==", "completed"))
        )
        feedback_docs = [doc.to_dict() for doc in user_service.db.collection("beta_feedback").stream()]

    # Calculate average ratings from feedback
    avg_overall = 0
    avg_ease = 0
    avg_accuracy = 0
    avg_correction = 0

    if feedback_docs:
        total = len(feedback_docs)
        for data in feedback_docs:
            avg_overall += data.get("overall_rating", 0)
            avg_ease += data.get("ease_of_use_rating", 0)
            avg_accuracy += data.get("lyrics_accuracy_rating", 0)
            avg_correction += data.get("correction_experience_rating", 0)

        avg_overall = round(avg_overall / total, 2)
        avg_ease = round(avg_ease / total, 2)
        avg_accuracy = round(avg_accuracy / total, 2)
        avg_correction = round(avg_correction / total, 2)

    return {
        "total_beta_testers": total_beta_testers,
        "active_testers": active_testers,
        "pending_feedback": pending_feedback,
        "completed_feedback": completed_feedback,
        "total_feedback_submissions": len(feedback_docs),
        "average_ratings": {
            "overall": avg_overall,
            "ease_of_use": avg_ease,
            "lyrics_accuracy": avg_accuracy,
            "correction_experience": avg_correction,
        },
    }
