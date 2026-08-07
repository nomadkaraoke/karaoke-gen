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
from backend.workers.auto_correct_worker import process_proactive_auto_correct
from backend.workers.lyrics_worker import process_lyrics_transcription
from backend.workers.screens_worker import generate_screens
from backend.workers.video_worker import generate_video
from backend.workers.render_video_worker import process_render_video
from backend.api.dependencies import require_admin
from backend.services.auth_service import AuthResult, UserType
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
    
    # Reject workers for jobs in terminal states (failed, cancelled, complete)
    # This prevents Cloud Tasks retries from triggering workers on dead jobs
    terminal_statuses = {'failed', 'cancelled', 'complete', 'prep_complete', 'error'}
    job_status = job.status.value if hasattr(job.status, 'value') else str(job.status)
    if job_status in terminal_statuses:
        logger.info(f"[job:{job_id}] Job in terminal state '{job_status}', skipping {worker_name} worker")
        return WorkerResponse(
            status="skipped",
            job_id=job_id,
            message=f"Job is in terminal state '{job_status}', {worker_name} worker not needed"
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


@router.post("/sync-disposable-domains", response_model=WorkerResponse)
async def sync_disposable_domains_endpoint(
    request: Request,
    auth_result: AuthResult = Depends(require_admin),
):
    """Sync external disposable domain blocklist. Called by Cloud Scheduler daily."""
    import asyncio
    from backend.services.disposable_domain_sync_service import (
        fetch_external_blocklist, sync_disposable_domains
    )
    from backend.services.firestore_service import get_firestore_client
    from backend.services.email_validation_service import EmailValidationService

    domains = await fetch_external_blocklist()
    db = get_firestore_client()
    result = await asyncio.to_thread(sync_disposable_domains, db, domains)
    EmailValidationService._blocklist_cache = None

    return WorkerResponse(
        status="completed",
        job_id="sync-disposable-domains",
        message=f"Synced {result['external_count']} domains"
    )


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


@router.post("/workers/auto-correct", response_model=WorkerResponse)
async def trigger_auto_correct_worker(
    request: WorkerRequest,
    http_request: Request,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """Proactively pre-generate + cache AI auto-correct suggestions for a job.

    Called by the lyrics worker once transcription + references are ready, so
    the suggestions are cached before the reviewer opens the lyrics UI. Runs
    HERE (the API service) because the service has the working Anthropic key /
    compare-models config; the lyrics job does not.

    Best-effort and runs synchronously within the request (the caller awaits
    with its own timeout): the work never fails the karaoke job, and the
    response just reports what happened. Gated by AUTO_CORRECT_PROACTIVE_ENABLED.
    """
    job_id = request.job_id

    # Extract trace context from incoming request (propagated via the trigger).
    trace_context = extract_trace_context(dict(http_request.headers))

    logger.info(f"[job:{job_id}] WORKER_TRIGGER worker=auto-correct")
    add_span_attribute("job_id", job_id)
    add_span_attribute("worker", "auto-correct")

    # process_proactive_auto_correct already swallows its own errors; the extra
    # guard keeps the endpoint itself best-effort no matter what.
    try:
        result = await process_proactive_auto_correct(job_id)
        add_span_event("auto_correct_proactive", {"result": result.get("status", "unknown")})
        return WorkerResponse(
            status=result.get("status", "unknown"),
            job_id=job_id,
            message=f"Proactive auto-correct: {result.get('status', 'unknown')}",
        )
    except Exception as e:  # noqa: BLE001 — best-effort, never fail the caller
        logger.warning(f"[job:{job_id}] auto-correct endpoint error (non-fatal): {e}")
        add_span_event("auto_correct_proactive", {"result": "error"})
        return WorkerResponse(status="error", job_id=job_id, message=f"error: {e}")


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

    This endpoint is called by a Cloud Tasks scheduled task after a job enters
    a blocking state (AWAITING_REVIEW, AWAITING_AUDIO_EDIT, AWAITING_DURATION_CONFIRM,
    or the legacy AWAITING_INSTRUMENTAL_SELECTION).

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
    blocking_states = [
        JobStatus.AWAITING_REVIEW,
        JobStatus.AWAITING_INSTRUMENTAL_SELECTION,
        JobStatus.AWAITING_DURATION_CONFIRM,
    ]
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
        if job.status == JobStatus.AWAITING_REVIEW.value:
            action_type = "lyrics"
        elif job.status == JobStatus.AWAITING_DURATION_CONFIRM.value:
            action_type = "duration_confirm"
        else:
            action_type = "instrumental"

    # Send the reminder email
    try:
        notification_service = get_job_notification_service()

        if action_type == "duration_confirm":
            # Duration-confirm has its own dedicated reminder method
            success = await notification_service.send_duration_confirm_reminder(job)
        else:
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


@router.post("/process-stale-reviews")
async def process_stale_reviews_endpoint(
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Process stale review jobs — send reminders and expire old ones.

    Called by Cloud Scheduler (hourly). Can also be triggered manually
    from the admin dashboard.

    - Jobs in review for >= 24h get a reminder email
    - Jobs in review for >= 48h are auto-cancelled with credit refund
    """
    from backend.workers.stale_review_processor import process_stale_reviews

    trace_context = extract_trace_context(dict(http_request.headers))

    logger.info("STALE_REVIEW_PROCESS starting")
    add_span_attribute("operation", "stale_review_process")

    async def _process():
        try:
            result = await process_stale_reviews()
            logger.info(f"STALE_REVIEW_PROCESS complete: {result}")
        except Exception as e:
            logger.exception(f"STALE_REVIEW_PROCESS failed: {e}")

    background_tasks.add_task(_process)

    add_span_event("stale_review_processing_started")
    return {
        "status": "started",
        "message": "Stale review processing started in background"
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


@router.post("/retry-pending-render-jobs")
async def retry_pending_render_jobs(
    http_request: Request,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Auto-retry jobs parked in RENDER_PENDING_CAPACITY.

    Called every 5 minutes by Cloud Scheduler. When the GCE encoding worker
    can't be started because the zone is exhausted, the render worker parks
    the job in this state instead of failing it. This endpoint re-attempts
    the render — when GCE has capacity, the start succeeds and the job
    completes normally.

    Strategy:
      - Process up to MAX_PER_TICK jobs (the GCE worker is single-tenant per
        render, so spinning up many in parallel just collides on the same VM)
      - Pick the oldest first (longest waiter gets fairness)
      - Time out jobs that have been waiting longer than MAX_WAIT_SECONDS;
        transition them to FAILED with a clear permanent-failure message
      - For each remaining job: clear error state, reset to REVIEW_COMPLETE,
        kick off the render worker again
    """
    from datetime import datetime, UTC, timedelta
    from google.cloud.firestore_v1 import FieldFilter
    from backend.services.worker_service import get_worker_service
    from backend.models.job import JobStatus

    # Long enough to ride out a sustained capacity outage but short enough
    # that an op gets paged about it before the user sees a "still pending"
    # job >24h old.
    MAX_WAIT_SECONDS = 24 * 60 * 60  # 24h
    MAX_PER_TICK = 1                  # one render at a time on the GCE worker

    extract_trace_context(dict(http_request.headers))
    add_span_attribute("operation", "retry_pending_render_jobs")
    logger.info("RETRY_PENDING_RENDER_JOBS starting")

    job_manager = JobManager()
    worker_service = get_worker_service()

    jobs_ref = job_manager.firestore.db.collection("jobs")
    # Filter by status only — sorting by updated_at server-side would require
    # a composite Firestore index that we don't have. The pending-capacity
    # population is small (handful of jobs at most), so streaming and
    # sorting in Python is cheap and avoids the index ceremony.
    pending_query = (
        jobs_ref
        .where(filter=FieldFilter("status", "==", JobStatus.RENDER_PENDING_CAPACITY.value))
        .limit(50)  # cap defensively in case the queue ever grows
    )

    now = datetime.now(UTC)
    timed_out = []
    retried = []
    skipped = 0

    # Materialize and sort by first_seen_at (oldest waiter first). Falls back
    # to updated_at, then doc id, when meta is missing.
    pending_docs = list(pending_query.stream())

    def _sort_key(doc):
        data = doc.to_dict() or {}
        meta = (data.get("state_data") or {}).get("render_pending_capacity") or {}
        first_seen = meta.get("first_seen_at") or ""
        updated = data.get("updated_at")
        # Firestore returns a datetime; isoformat sorts correctly within the same TZ.
        updated_str = updated.isoformat() if hasattr(updated, "isoformat") else str(updated or "")
        return (first_seen, updated_str, doc.id)

    pending_docs.sort(key=_sort_key)

    for doc in pending_docs:
        job_data = doc.to_dict()
        job_id = job_data.get("job_id", doc.id)

        sd = job_data.get("state_data") or {}
        pending_meta = sd.get("render_pending_capacity") or {}
        first_seen = pending_meta.get("first_seen_at")

        # Permanent-failure timeout
        if first_seen:
            try:
                first_seen_dt = datetime.fromisoformat(first_seen)
                age = now - first_seen_dt
            except (ValueError, TypeError):
                age = timedelta(0)
            if age.total_seconds() > MAX_WAIT_SECONDS:
                permanent_msg = (
                    "Encoding capacity remained unavailable after "
                    f"{int(age.total_seconds() // 3600)}h of automatic retries. "
                    "Please retry manually or contact support."
                )
                logger.error(
                    f"[job:{job_id}] Capacity wait exceeded {MAX_WAIT_SECONDS}s — failing permanently"
                )
                job_manager.fail_job(job_id, permanent_msg, error_details={
                    "stage": "render_video",
                    "permanent_capacity_timeout": True,
                    "first_seen_at": first_seen,
                    "attempts": pending_meta.get("attempt_count", 0),
                })
                timed_out.append(job_id)
                continue

        if len(retried) >= MAX_PER_TICK:
            skipped += 1
            continue

        # Clear error state and reset render progress for idempotency.
        # render_pending_capacity meta is preserved so the next failure (if any)
        # increments the existing attempt counter.
        job_manager.update_job(job_id, {
            "error_message": None,
            "error_details": None,
        })
        job_manager.update_state_data(job_id, "render_progress", {"stage": "pending"})

        if not job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.REVIEW_COMPLETE,
            progress=70,
            message="Auto-retrying video render — encoding capacity check",
            raise_on_invalid=False,
        ):
            logger.warning(f"[job:{job_id}] Could not transition to REVIEW_COMPLETE for retry")
            continue

        try:
            await worker_service.trigger_render_video_worker(job_id)
            retried.append(job_id)
            logger.info(f"[job:{job_id}] Auto-retry triggered")
        except Exception as e:
            logger.error(f"[job:{job_id}] Failed to trigger render worker: {e}")
            # Job remains in REVIEW_COMPLETE — next tick will pick it up if it
            # falls back to RENDER_PENDING_CAPACITY again.

    add_span_event("retry_complete", {
        "retried": len(retried),
        "timed_out": len(timed_out),
        "skipped": skipped,
    })
    logger.info(
        f"RETRY_PENDING_RENDER_JOBS complete: retried={len(retried)} "
        f"timed_out={len(timed_out)} skipped={skipped}"
    )

    return {
        "status": "success",
        "retried_jobs": retried,
        "retried_count": len(retried),
        "timed_out_jobs": timed_out,
        "timed_out_count": len(timed_out),
        "skipped_for_next_tick": skipped,
    }


@router.post("/recover-stuck-jobs")
async def recover_stuck_jobs(
    http_request: Request,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Detect and recover jobs stuck in a processing status.

    Called by Cloud Scheduler (every 5 minutes) or manually from admin.
    - DOWNLOADING_AUDIO stuck >10 min:
        * torrent sources (RED/OPS) → park into DOWNLOAD_PENDING_RETRY and keep
          auto-retrying for up to 24h (handles rare tracks with intermittent
          seeders and transient tracker outages gracefully).
        * other sources → fail so the user can retry.
    - DOWNLOAD_PENDING_RETRY → re-trigger the download; permanently fail after 24h.
    - RENDERING_VIDEO stuck with no progress (worker died mid-render) →
      re-park into RENDER_PENDING_CAPACITY so the existing
      retry-pending-render-jobs cron resumes it automatically. Closes the orphan
      gap where a hard-killed render worker leaves the job frozen at
      "rendering_video" forever with no error and no Retry button.
    """
    from backend.services.job_health_service import check_job_consistency
    from backend.services.worker_service import get_worker_service
    from backend.models.job import JobStatus

    extract_trace_context(dict(http_request.headers))
    logger.info("RECOVER_STUCK_JOBS starting")
    add_span_attribute("operation", "recover_stuck_jobs")

    job_manager = JobManager()
    worker_service = get_worker_service()

    from google.cloud.firestore_v1 import FieldFilter
    jobs_ref = job_manager.firestore.db.collection("jobs")

    recovered = []       # download jobs failed for user retry
    download_parked = [] # transient torrent downloads parked for auto-retry
    download_retried = []  # parked downloads re-triggered this tick
    download_timed_out = []  # parked downloads that exceeded the 24h ceiling
    reparked = []        # render jobs re-parked for auto-retry

    # --- Stuck audio downloads: park torrents for auto-retry, else fail ---
    stuck_query = jobs_ref.where(
        filter=FieldFilter("status", "==", JobStatus.DOWNLOADING_AUDIO.value)
    ).stream()
    for doc in stuck_query:
        job_id = (doc.to_dict() or {}).get("job_id", doc.id)
        job = job_manager.get_job(job_id)
        if not job:
            continue
        if not any("downloading_audio_stuck" in i for i in check_job_consistency(job)):
            continue
        if _is_retryable_torrent_source(job):
            logger.warning(f"[job:{job_id}] Parking stuck torrent download for auto-retry")
            if _park_download_for_retry(job_manager, job_id, "download stalled (>10 min)"):
                download_parked.append(job_id)
        else:
            logger.warning(f"[job:{job_id}] Recovering stuck download (non-torrent)")
            job_manager.fail_job(
                job_id,
                "Audio download timed out (stuck >10 minutes). Use retry to re-attempt."
            )
            recovered.append(job_id)

    # --- Parked downloads: re-trigger, or permanently fail after 24h ---
    retried_this_tick = 0
    pending_query = jobs_ref.where(
        filter=FieldFilter("status", "==", JobStatus.DOWNLOAD_PENDING_RETRY.value)
    ).limit(100).stream()
    for doc in pending_query:
        job_id = (doc.to_dict() or {}).get("job_id", doc.id)
        job = job_manager.get_job(job_id)
        if not job:
            continue
        meta = (job.state_data or {}).get("download_retry", {}) or {}
        first_seen = meta.get("first_seen_at")
        if _download_retry_expired(first_seen):
            hours = int(DOWNLOAD_RETRY_MAX_SECONDS // 3600)
            logger.error(f"[job:{job_id}] Download retries exhausted after {hours}h — failing")
            job_manager.fail_job(
                job_id,
                f"Couldn't find a working source for this track after {hours}h of "
                "automatic retries. It may be too rare to source right now — please "
                "try a different version or contact support.",
                error_details={"stage": "audio_download", "permanent_download_timeout": True},
            )
            download_timed_out.append(job_id)
            continue
        if retried_this_tick >= DOWNLOAD_RETRIES_PER_TICK:
            continue
        if await _retrigger_parked_download(job_manager, worker_service, job_id):
            retried_this_tick += 1
            download_retried.append(job_id)

    # --- Orphaned renders: re-park for automatic retry ---
    render_query = jobs_ref.where(
        filter=FieldFilter("status", "==", JobStatus.RENDERING_VIDEO.value)
    ).stream()
    for doc in render_query:
        job_id = (doc.to_dict() or {}).get("job_id", doc.id)
        job = job_manager.get_job(job_id)
        if not job:
            continue
        if any("rendering_video_stuck" in i for i in check_job_consistency(job)):
            logger.warning(f"[job:{job_id}] Re-parking orphaned render for auto-retry")
            if _repark_stalled_render(job_manager, job_id):
                reparked.append(job_id)

    logger.info(
        f"RECOVER_STUCK_JOBS complete: recovered={len(recovered)} "
        f"download_parked={len(download_parked)} download_retried={len(download_retried)} "
        f"download_timed_out={len(download_timed_out)} reparked={len(reparked)}"
    )
    add_span_event("recovery_complete", {
        "recovered_count": len(recovered),
        "download_parked_count": len(download_parked),
        "download_retried_count": len(download_retried),
        "download_timed_out_count": len(download_timed_out),
        "reparked_count": len(reparked),
    })

    return {
        "status": "success",
        "recovered_jobs": recovered,
        "recovered_count": len(recovered),
        "download_parked_jobs": download_parked,
        "download_retried_jobs": download_retried,
        "download_timed_out_jobs": download_timed_out,
        "reparked_render_jobs": reparked,
        "reparked_render_count": len(reparked),
    }


# Torrent sources whose downloads are worth auto-retrying over a long window —
# they fail transiently when a rare release has few/intermittent seeders or the
# private tracker is briefly unreachable. Non-torrent sources (YouTube/Spotify/
# URL) fail deterministically, so they are not auto-retried here.
RETRYABLE_TORRENT_SOURCES = frozenset({"RED", "OPS"})
DOWNLOAD_RETRY_MAX_SECONDS = 24 * 60 * 60  # keep retrying a transient download for 24h
DOWNLOAD_RETRIES_PER_TICK = 10             # cap re-triggers per 5-min tick


def _is_retryable_torrent_source(job) -> bool:
    """True if the job's audio came from a torrent tracker (RED/OPS)."""
    return (getattr(job, "source_name", "") or "").upper() in RETRYABLE_TORRENT_SOURCES


def _download_retry_expired(first_seen_at: Optional[str]) -> bool:
    """True if the first parked failure is older than the 24h retry ceiling."""
    if not first_seen_at:
        return False
    from datetime import datetime, UTC
    try:
        age = datetime.now(UTC) - datetime.fromisoformat(first_seen_at)
    except (ValueError, TypeError):
        return False
    return age.total_seconds() > DOWNLOAD_RETRY_MAX_SECONDS


def _park_download_for_retry(job_manager: JobManager, job_id: str, reason: str) -> bool:
    """Park a transient torrent-download failure into DOWNLOAD_PENDING_RETRY.

    The recover-stuck cron re-triggers parked downloads on later ticks (up to
    24h) instead of dead-ending the job at FAILED after ~1h.
    """
    from datetime import datetime, UTC
    from backend.models.job import JobStatus

    now = datetime.now(UTC).isoformat()
    job = job_manager.get_job(job_id)
    existing = (job.state_data or {}).get("download_retry", {}) if job else {}
    meta = {
        "first_seen_at": existing.get("first_seen_at") or now,
        "last_attempt_at": now,
        "attempt_count": int(existing.get("attempt_count", 0)) + 1,
        "last_reason": str(reason)[:300],
    }
    job_manager.update_state_data(job_id, "download_retry", meta)
    # Clear any Cloud Run auto-retry marker so the UI reflects the parked state.
    job_manager.update_state_data(job_id, "cloud_run_retry_pending", None)
    return bool(job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.DOWNLOAD_PENDING_RETRY,
        progress=12,
        message=(
            "Still finding a good source for this track — it may be rare, or a "
            "source is temporarily offline. We'll keep trying automatically for up "
            "to 24 hours; no action needed."
        ),
        raise_on_invalid=False,
    ))


async def _retrigger_parked_download(job_manager: JobManager, worker_service, job_id: str) -> bool:
    """Re-trigger the audio download for a DOWNLOAD_PENDING_RETRY job."""
    from backend.models.job import JobStatus

    job_manager.update_job(job_id, {"error_message": None, "error_details": None})
    if not job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.DOWNLOADING_AUDIO,
        progress=12,
        message="Retrying audio download — checking sources again",
        raise_on_invalid=False,
    ):
        logger.warning(f"[job:{job_id}] Could not transition parked download for retry")
        return False
    try:
        await worker_service.trigger_audio_download_worker(job_id)
        logger.info(f"[job:{job_id}] Parked download re-triggered")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"[job:{job_id}] Failed to re-trigger parked download: {e}")
        return False


def _repark_stalled_render(job_manager: JobManager, job_id: str) -> bool:
    """Re-park a render orphaned at RENDERING_VIDEO into RENDER_PENDING_CAPACITY.

    Mirrors render_video_worker._park_job_for_capacity_retry but for a mid-render
    stall (no EncodingWorkerStartError to carry). The existing
    retry-pending-render-jobs cron then resets it to REVIEW_COMPLETE and
    re-triggers the render, with the same 24h permanent-failure ceiling.
    """
    from datetime import datetime, UTC
    from backend.models.job import JobStatus

    now = datetime.now(UTC).isoformat()
    job = job_manager.get_job(job_id)
    existing = (job.state_data or {}).get("render_pending_capacity", {}) if job else {}
    pending_meta = {
        "first_seen_at": existing.get("first_seen_at") or now,
        "last_attempt_at": now,
        "attempt_count": int(existing.get("attempt_count", 0)) + 1,
        "last_code": "render_stalled",
        "last_zone": existing.get("last_zone", ""),
        "last_vm": existing.get("last_vm", ""),
    }
    job_manager.update_state_data(job_id, "render_pending_capacity", pending_meta)
    return bool(job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.RENDER_PENDING_CAPACITY,
        progress=70,
        message=(
            "The render was interrupted (a worker was recycled). Your job will "
            "retry automatically — no action needed."
        ),
        raise_on_invalid=False,
    ))


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

