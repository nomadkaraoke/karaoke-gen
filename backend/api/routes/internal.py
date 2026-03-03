"""
Internal API routes for worker coordination.

These endpoints are for internal use only (backend → workers).
They are protected by admin authentication.

With Cloud Tasks integration, these endpoints may be called multiple times
(retry on failure). Idempotency checks prevent duplicate processing.

Observability:
- Extracts trace context from incoming requests (propagated via Cloud Tasks)
- Creates worker spans linked to the original request trace
- All logs include job_id for easy filtering in Cloud Logging
"""
import logging
import asyncio
import time
from typing import Tuple, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Request
from pydantic import BaseModel

from backend.workers.audio_worker import process_audio_separation
from backend.workers.lyrics_worker import process_lyrics_transcription
from backend.workers.screens_worker import generate_screens
from backend.workers.video_worker import generate_video
from backend.workers.render_video_worker import process_render_video
from backend.api.dependencies import require_admin
from backend.services.auth_service import UserType
from backend.services.job_manager import JobManager
from backend.services.tracing import (
    extract_trace_context,
    start_span_with_context,
    add_span_attribute,
    add_span_event,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])


class WorkerRequest(BaseModel):
    """Request to trigger a worker."""
    job_id: str


class WorkerResponse(BaseModel):
    """Response from worker trigger."""
    status: str
    job_id: str
    message: str


def _check_worker_idempotency(job_id: str, worker_name: str) -> Optional[WorkerResponse]:
    """
    Check if a worker is already running or completed for this job.
    
    This provides idempotency for Cloud Tasks retries - if a task is retried
    but the worker is already running or has completed, we skip processing.
    
    Args:
        job_id: Job ID to check
        worker_name: Worker name (audio, lyrics, screens, render, video)
        
    Returns:
        WorkerResponse if should skip (already running/complete), None to proceed
    """
    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    
    if not job:
        logger.warning(f"[job:{job_id}] Job not found for {worker_name} worker")
        return WorkerResponse(
            status="not_found",
            job_id=job_id,
            message=f"Job {job_id} not found"
        )
    
    # Check worker-specific progress in state_data
    progress_key = f"{worker_name}_progress"
    worker_progress = job.state_data.get(progress_key, {})
    stage = worker_progress.get('stage')
    
    if stage == 'running':
        logger.info(f"[job:{job_id}] {worker_name.capitalize()} worker already running, skipping")
        return WorkerResponse(
            status="already_running",
            job_id=job_id,
            message=f"{worker_name.capitalize()} worker already in progress"
        )
    
    if stage == 'complete':
        logger.info(f"[job:{job_id}] {worker_name.capitalize()} worker already complete, skipping")
        return WorkerResponse(
            status="already_complete",
            job_id=job_id,
            message=f"{worker_name.capitalize()} worker already completed"
        )
    
    # Mark as running before starting (for idempotency on next retry)
    job_manager.update_state_data(job_id, progress_key, {'stage': 'running'})
    return None


@router.post("/workers/audio", response_model=WorkerResponse)
async def trigger_audio_worker(
    request: WorkerRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Trigger audio separation worker for a job.
    
    This endpoint is called internally after job creation to start
    the audio processing track (parallel with lyrics processing).
    
    Idempotency: If worker is already running or complete, returns early.
    
    The worker runs in the background and updates job state as it progresses.
    """
    job_id = request.job_id
    
    # Extract trace context from incoming request (propagated via Cloud Tasks)
    trace_context = extract_trace_context(dict(http_request.headers))
    
    logger.info(f"[job:{job_id}] WORKER_TRIGGER worker=audio")
    add_span_attribute("job_id", job_id)
    add_span_attribute("worker", "audio")
    
    # Idempotency check
    skip_response = _check_worker_idempotency(job_id, "audio")
    if skip_response:
        add_span_event("worker_skipped", {"reason": skip_response.status})
        return skip_response
    
    # Add task to background tasks
    # This allows the HTTP response to return immediately
    # while the worker continues processing
    background_tasks.add_task(process_audio_separation, job_id)
    
    add_span_event("worker_started")
    return WorkerResponse(
        status="started",
        job_id=job_id,
        message="Audio separation worker started"
    )


@router.post("/workers/lyrics", response_model=WorkerResponse)
async def trigger_lyrics_worker(
    request: WorkerRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Trigger lyrics transcription worker for a job.
    
    This endpoint is called internally after job creation to start
    the lyrics processing track (parallel with audio processing).
    
    Idempotency: If worker is already running or complete, returns early.
    
    The worker runs in the background and updates job state as it progresses.
    """
    job_id = request.job_id
    
    # Extract trace context from incoming request
    trace_context = extract_trace_context(dict(http_request.headers))
    
    logger.info(f"[job:{job_id}] WORKER_TRIGGER worker=lyrics")
    add_span_attribute("job_id", job_id)
    add_span_attribute("worker", "lyrics")
    
    # Idempotency check
    skip_response = _check_worker_idempotency(job_id, "lyrics")
    if skip_response:
        add_span_event("worker_skipped", {"reason": skip_response.status})
        return skip_response
    
    # Add task to background tasks
    background_tasks.add_task(process_lyrics_transcription, job_id)
    
    add_span_event("worker_started")
    return WorkerResponse(
        status="started",
        job_id=job_id,
        message="Lyrics transcription worker started"
    )


@router.post("/workers/screens", response_model=WorkerResponse)
async def trigger_screens_worker(
    request: WorkerRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Trigger title/end screen generation worker.
    
    This is called automatically when both audio and lyrics are complete.
    
    Idempotency: If worker is already running or complete, returns early.
    """
    job_id = request.job_id
    
    # Extract trace context from incoming request
    trace_context = extract_trace_context(dict(http_request.headers))
    
    logger.info(f"[job:{job_id}] WORKER_TRIGGER worker=screens")
    add_span_attribute("job_id", job_id)
    add_span_attribute("worker", "screens")
    
    # Idempotency check
    skip_response = _check_worker_idempotency(job_id, "screens")
    if skip_response:
        add_span_event("worker_skipped", {"reason": skip_response.status})
        return skip_response
    
    # Add task to background tasks
    background_tasks.add_task(generate_screens, job_id)
    
    add_span_event("worker_started")
    return WorkerResponse(
        status="started",
        job_id=job_id,
        message="Screens generation worker started"
    )


@router.post("/workers/video", response_model=WorkerResponse)
async def trigger_video_worker(
    request: WorkerRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Trigger final video generation and encoding worker.
    
    This is called after user selects their preferred instrumental.
    This is the longest-running stage (15-20 minutes).
    
    Idempotency: If worker is already running or complete, returns early.
    """
    job_id = request.job_id
    
    # Extract trace context from incoming request
    trace_context = extract_trace_context(dict(http_request.headers))
    
    logger.info(f"[job:{job_id}] WORKER_TRIGGER worker=video")
    add_span_attribute("job_id", job_id)
    add_span_attribute("worker", "video")
    
    # Idempotency check
    skip_response = _check_worker_idempotency(job_id, "video")
    if skip_response:
        add_span_event("worker_skipped", {"reason": skip_response.status})
        return skip_response
    
    # Add task to background tasks
    background_tasks.add_task(generate_video, job_id)
    
    add_span_event("worker_started")
    return WorkerResponse(
        status="started",
        job_id=job_id,
        message="Video generation worker started"
    )


@router.post("/workers/render-video", response_model=WorkerResponse)
async def trigger_render_video_worker(
    request: WorkerRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Trigger render video worker (post-review).
    
    This is called after human review is complete.
    Uses OutputGenerator from LyricsTranscriber to generate the karaoke video
    with the corrected lyrics.
    
    Idempotency: If worker is already running or complete, returns early.
    
    Output: with_vocals.mkv in GCS
    Next state: INSTRUMENTAL_SELECTED (instrumental is now selected during review)
    """
    job_id = request.job_id
    
    # Extract trace context from incoming request
    trace_context = extract_trace_context(dict(http_request.headers))
    
    logger.info(f"[job:{job_id}] WORKER_TRIGGER worker=render-video")
    add_span_attribute("job_id", job_id)
    add_span_attribute("worker", "render-video")
    
    # Idempotency check
    skip_response = _check_worker_idempotency(job_id, "render")
    if skip_response:
        add_span_event("worker_skipped", {"reason": skip_response.status})
        return skip_response
    
    # Add task to background tasks
    background_tasks.add_task(process_render_video, job_id)
    
    add_span_event("worker_started")
    return WorkerResponse(
        status="started",
        job_id=job_id,
        message="Render video worker started (post-review)"
    )


@router.post("/jobs/{job_id}/check-idle-reminder")
async def check_idle_reminder(
    job_id: str,
    http_request: Request,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Check if a job needs an idle reminder email.

    This endpoint is called by a Cloud Tasks scheduled task 5 minutes after
    a job enters AWAITING_REVIEW state.

    If the job is still in the blocking state and no reminder has been sent yet,
    sends a reminder email to the user.

    Idempotency: Only one reminder per job (tracked via reminder_sent flag).
    """
    from backend.models.job import JobStatus
    from backend.services.job_notification_service import get_job_notification_service

    # Extract trace context from incoming request
    trace_context = extract_trace_context(dict(http_request.headers))

    logger.info(f"[job:{job_id}] IDLE_REMINDER_CHECK starting")
    add_span_attribute("job_id", job_id)
    add_span_attribute("operation", "idle_reminder_check")

    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        logger.warning(f"[job:{job_id}] Job not found for idle reminder check")
        add_span_event("job_not_found")
        return {"status": "not_found", "job_id": job_id, "message": "Job not found"}

    # Check if job is still in a blocking state
    # Note: AWAITING_INSTRUMENTAL_SELECTION is LEGACY - kept for historical jobs only
    blocking_states = [JobStatus.AWAITING_REVIEW, JobStatus.AWAITING_INSTRUMENTAL_SELECTION]
    if job.status not in [s.value for s in blocking_states]:
        logger.info(f"[job:{job_id}] Job no longer in blocking state ({job.status}), skipping reminder")
        add_span_event("not_blocking", {"current_status": job.status})
        return {
            "status": "skipped",
            "job_id": job_id,
            "message": f"Job not in blocking state (current: {job.status})"
        }

    # Normalize state_data to prevent None errors
    state_data = job.state_data or {}

    # Check if reminder was already sent (idempotency)
    if state_data.get('reminder_sent'):
        logger.info(f"[job:{job_id}] Reminder already sent, skipping")
        add_span_event("already_sent")
        return {"status": "already_sent", "job_id": job_id, "message": "Reminder already sent"}

    # Skip reminders for made-for-you jobs (admin handles these directly, no intermediate customer emails)
    if getattr(job, 'made_for_you', False):
        logger.info(f"[job:{job_id}] Made-for-you job, skipping customer reminder (admin handles)")
        add_span_event("made_for_you_skip")
        return {"status": "skipped", "job_id": job_id, "message": "Made-for-you job - admin handles directly"}

    # Check if user has an email
    if not job.user_email:
        logger.warning(f"[job:{job_id}] No user email, cannot send reminder")
        add_span_event("no_email")
        return {"status": "no_email", "job_id": job_id, "message": "No user email configured"}

    # Determine action type
    action_type = state_data.get('blocking_action_type')
    if not action_type:
        action_type = "lyrics" if job.status == JobStatus.AWAITING_REVIEW.value else "instrumental"

    # Send the reminder email
    try:
        notification_service = get_job_notification_service()

        success = await notification_service.send_action_reminder_email(
            job_id=job.job_id,
            user_email=job.user_email,
            action_type=action_type,
            user_name=None,  # Could fetch from user service if needed
            artist=job.artist,
            title=job.title,
            audio_hash=job.audio_hash,
            review_token=job.review_token,
            instrumental_token=job.instrumental_token,
        )

        if success:
            # Mark reminder as sent (prevents duplicate sends)
            job_manager.firestore.update_job(job_id, {
                'state_data': {**state_data, 'reminder_sent': True}
            })
            logger.info(f"[job:{job_id}] Sent {action_type} reminder email to {job.user_email}")
            add_span_event("reminder_sent", {"action_type": action_type})
            return {
                "status": "sent",
                "job_id": job_id,
                "message": f"Sent {action_type} reminder to {job.user_email}"
            }
        else:
            logger.error(f"[job:{job_id}] Failed to send reminder email")
            add_span_event("send_failed")
            return {"status": "failed", "job_id": job_id, "message": "Failed to send reminder"}

    except Exception as e:
        logger.exception(f"[job:{job_id}] Error sending reminder: {e}")
        add_span_event("error", {"error": str(e)})
        return {"status": "error", "job_id": job_id, "message": str(e)}


@router.post("/youtube-queue/process")
async def process_youtube_upload_queue(
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Process queued YouTube uploads.

    Called by Cloud Scheduler (hourly) to retry uploads that were deferred
    due to YouTube API quota exhaustion. Can also be triggered manually
    from the admin dashboard.

    Processes queued uploads one at a time, stopping if quota is exhausted.
    """
    from backend.workers.youtube_queue_processor import process_youtube_upload_queue as process_queue

    trace_context = extract_trace_context(dict(http_request.headers))

    logger.info("YOUTUBE_QUEUE_PROCESS starting")
    add_span_attribute("operation", "youtube_queue_process")

    # Run in background so the HTTP response returns quickly
    # (Cloud Scheduler has a 30min timeout but we don't want to block)
    async def _process():
        try:
            result = await process_queue()
            logger.info(f"YOUTUBE_QUEUE_PROCESS complete: {result}")
        except Exception as e:
            logger.exception(f"YOUTUBE_QUEUE_PROCESS failed: {e}")

    background_tasks.add_task(_process)

    add_span_event("queue_processing_started")
    return {
        "status": "started",
        "message": "YouTube upload queue processing started in background"
    }


@router.post("/trigger-gdrive-validation")
async def trigger_gdrive_validation_endpoint(
    http_request: Request,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Trigger GDrive validation via the Cloud Function.

    Called by a Cloud Tasks delayed task after a job completes and uploads
    to the public GDrive share. The 5-minute delay ensures E2E test cleanup
    finishes before the validator checks for sequence gaps.
    """
    from backend.services.gdrive_validator_client import trigger_gdrive_validation

    trace_context = extract_trace_context(dict(http_request.headers))

    logger.info("GDRIVE_VALIDATION_TRIGGER starting (delayed post-job)")
    add_span_attribute("operation", "gdrive_validation_trigger")

    try:
        result = trigger_gdrive_validation()
        if result is None:
            return {"status": "skipped", "message": "GDRIVE_VALIDATOR_URL not configured"}

        status = result.get("status", "unknown")
        add_span_event("validation_complete", {"result_status": status})
        return {"status": status, "message": "GDrive validation completed", "result": result}
    except Exception as e:
        logger.exception(f"GDrive validation trigger failed: {e}")
        add_span_event("error", {"error": str(e)})
        return {"status": "error", "message": str(e)}


@router.get("/health")
async def internal_health(
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Internal health check endpoint.

    Used to verify the internal API is responsive.
    Requires admin authentication.
    """
    return {"status": "healthy", "service": "karaoke-backend-internal"}


# =============================================================================
# Test Webhook Endpoint (for E2E testing)
# =============================================================================

class TestWebhookRequest(BaseModel):
    """
    Request to simulate a Stripe webhook event for E2E testing.

    This allows E2E tests to trigger payment flow logic without requiring
    actual Stripe checkout sessions or valid webhook signatures.
    """
    event_type: str  # e.g., "checkout.session.completed"
    session_id: str  # Must start with "e2e-test-" prefix
    customer_email: str
    metadata: dict  # order_type, package_id, credits, artist, title, etc.


class TestWebhookResponse(BaseModel):
    """Response from test webhook processing."""
    status: str  # "processed", "already_processed", "error"
    job_id: Optional[str] = None  # For made-for-you orders
    credits_added: Optional[int] = None  # For credit purchases
    new_balance: Optional[int] = None  # For credit purchases
    message: str


@router.post("/test-webhook", response_model=TestWebhookResponse)
async def test_webhook(
    request: TestWebhookRequest,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Test endpoint that simulates Stripe webhook events for E2E testing.

    SECURITY:
    - Protected by admin authentication (X-Admin-Token header)
    - Session IDs must start with "e2e-test-" prefix to prevent collision
      with real Stripe sessions
    - Only for E2E testing - bypasses Stripe signature verification

    This endpoint reuses the same handler logic as the real webhook endpoint,
    ensuring E2E tests validate actual business logic.

    Supported event types:
    - checkout.session.completed: Handles credit purchases and made-for-you orders

    For credit purchases, metadata must include:
    - package_id: e.g., "1_credit"
    - credits: e.g., "1"
    - user_email: Email of user to credit

    For made-for-you orders, metadata must include:
    - order_type: "made_for_you"
    - customer_email: Customer email for delivery
    - artist: Song artist
    - title: Song title
    - source_type: "search" or "youtube"
    - youtube_url: (optional) If source_type is "youtube"
    - notes: (optional) Customer notes
    """
    from backend.services.user_service import get_user_service
    from backend.services.email_service import get_email_service
    from backend.services.stripe_service import get_stripe_service
    from backend.api.routes.users import _handle_made_for_you_order

    # Validate session_id prefix for safety
    if not request.session_id.startswith("e2e-test-"):
        logger.warning(f"Test webhook rejected: session_id '{request.session_id}' missing required prefix")
        raise HTTPException(
            status_code=400,
            detail="Session ID must start with 'e2e-test-' prefix for test webhooks"
        )

    logger.info(f"TEST_WEBHOOK event_type={request.event_type} session_id={request.session_id}")
    add_span_attribute("event_type", request.event_type)
    add_span_attribute("session_id", request.session_id)
    add_span_attribute("is_test_webhook", True)

    user_service = get_user_service()
    email_service = get_email_service()
    stripe_service = get_stripe_service()

    if request.event_type == "checkout.session.completed":
        session_id = request.session_id
        metadata = request.metadata

        # Idempotency check: Skip if this session was already processed
        if user_service.is_stripe_session_processed(session_id):
            logger.info(f"Test webhook: session {session_id} already processed")
            return TestWebhookResponse(
                status="already_processed",
                message=f"Session {session_id} was already processed"
            )

        # Check if this is a made-for-you order
        if metadata.get("order_type") == "made_for_you":
            try:
                # Call the same handler used by the real webhook
                await _handle_made_for_you_order(
                    session_id=session_id,
                    metadata=metadata,
                    user_service=user_service,
                    email_service=email_service,
                )

                # Get the job ID from the most recent job for this customer
                # The handler creates a job, so we need to find it
                from google.cloud import firestore
                from google.cloud.firestore_v1 import FieldFilter

                db = user_service.db
                # Look for the job by session_id pattern in state_data or by customer_email
                # Since the job was just created, query by customer_email and made_for_you flag
                customer_email = metadata.get("customer_email", "")
                jobs_query = db.collection("jobs").where(
                    filter=FieldFilter("customer_email", "==", customer_email)
                ).where(
                    filter=FieldFilter("made_for_you", "==", True)
                ).order_by("created_at", direction=firestore.Query.DESCENDING).limit(1)

                jobs = list(jobs_query.stream())
                job_id = jobs[0].to_dict().get("job_id") if jobs else None

                logger.info(f"Test webhook: made-for-you order processed, job_id={job_id}")
                return TestWebhookResponse(
                    status="processed",
                    job_id=job_id,
                    message=f"Made-for-you order created successfully"
                )
            except Exception as e:
                logger.exception(f"Test webhook: error processing made-for-you order: {e}")
                return TestWebhookResponse(
                    status="error",
                    message=f"Error processing made-for-you order: {str(e)}"
                )
        else:
            # Handle regular credit purchase
            # Build a synthetic session object that matches Stripe's format
            synthetic_session = {
                "id": session_id,
                "customer_email": request.customer_email,
                "metadata": metadata,
            }

            success, user_email, credits, msg = stripe_service.handle_checkout_completed(
                synthetic_session
            )

            if not success:
                logger.warning(f"Test webhook: credit purchase validation failed: {msg}")
                return TestWebhookResponse(
                    status="error",
                    message=msg
                )

            if user_email and credits > 0:
                # Add credits to user account
                ok, new_balance, credit_msg = user_service.add_credits(
                    email=user_email,
                    amount=credits,
                    reason="stripe_purchase",
                    stripe_session_id=session_id,
                )

                if ok:
                    # Send confirmation email (same as real webhook)
                    email_service.send_credits_added(user_email, credits, new_balance)
                    logger.info(f"Test webhook: added {credits} credits to {user_email}, new balance: {new_balance}")
                    return TestWebhookResponse(
                        status="processed",
                        credits_added=credits,
                        new_balance=new_balance,
                        message=f"Added {credits} credits to {user_email}"
                    )
                else:
                    logger.error(f"Test webhook: failed to add credits: {credit_msg}")
                    return TestWebhookResponse(
                        status="error",
                        message=f"Failed to add credits: {credit_msg}"
                    )

            return TestWebhookResponse(
                status="error",
                message="Invalid credit purchase data"
            )
    else:
        # Unsupported event type
        logger.warning(f"Test webhook: unsupported event type '{request.event_type}'")
        return TestWebhookResponse(
            status="error",
            message=f"Unsupported event type: {request.event_type}"
        )

