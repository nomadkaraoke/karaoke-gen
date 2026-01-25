"""
Job management routes.

Handles job lifecycle endpoints including:
- Job creation and submission
- Status polling
- Human-in-the-loop interactions (combined lyrics + instrumental review)
- Job deletion and cancellation
"""
import asyncio
import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Request

from backend.models.job import Job, JobCreate, JobResponse, JobStatus
from backend.models.requests import (
    URLSubmissionRequest,
    CorrectionsSubmission,
    StartReviewRequest,
    CancelJobRequest,
    InstrumentalSelection,
    CompleteReviewRequest,
)
from backend.services.job_manager import JobManager
from backend.services.worker_service import get_worker_service
from backend.services.storage_service import StorageService
from backend.services.theme_service import get_theme_service
from backend.config import get_settings
from backend.api.dependencies import require_admin, require_auth
from backend.services.auth_service import AuthResult
from backend.services.metrics import metrics
from backend.middleware.tenant import get_tenant_from_request
from backend.utils.test_data import is_test_email


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])

# Initialize services
job_manager = JobManager()
worker_service = get_worker_service()
settings = get_settings()


async def _trigger_workers_parallel(job_id: str) -> None:
    """
    Trigger both audio and lyrics workers in parallel.
    
    FastAPI's BackgroundTasks runs async tasks sequentially, so we use
    asyncio.gather to ensure both workers start at the same time.
    """
    await asyncio.gather(
        worker_service.trigger_audio_worker(job_id),
        worker_service.trigger_lyrics_worker(job_id)
    )


@router.post("", response_model=JobResponse)
async def create_job(
    request: URLSubmissionRequest,
    background_tasks: BackgroundTasks,
    auth_result: AuthResult = Depends(require_auth)
) -> JobResponse:
    """
    Create a new karaoke generation job from a URL.
    
    This triggers the complete workflow:
    1. Job created in PENDING state
    2. Audio and lyrics workers triggered in parallel
    3. Both workers update job state as they progress
    4. When both complete, job transitions to AWAITING_REVIEW
    """
    try:
        # Determine job owner email:
        # All authentication methods must provide a user_email for job ownership
        if auth_result.user_email:
            # Use authenticated user's email (standard case)
            user_email = auth_result.user_email
        else:
            # This should never happen - all auth methods now require user_email
            logger.error("Authentication succeeded but no user_email provided")
            raise HTTPException(
                status_code=500,
                detail="Authentication error: no user identity available"
            )

        # Admins can optionally create jobs on behalf of other users
        if request.user_email and auth_result.is_admin and request.user_email != auth_result.user_email:
            user_email = request.user_email
            logger.info(f"Admin {auth_result.user_email} creating job on behalf of {user_email}")

        # Apply YouTube upload default from settings
        # Use explicit value if provided, otherwise fall back to server default
        settings = get_settings()
        effective_enable_youtube_upload = request.enable_youtube_upload if request.enable_youtube_upload is not None else settings.default_enable_youtube_upload

        # Apply default theme - all jobs require a theme
        theme_service = get_theme_service()
        effective_theme_id = theme_service.get_default_theme_id()
        if not effective_theme_id:
            raise HTTPException(
                status_code=422,
                detail="No default theme configured. Please contact support or specify a theme_id."
            )
        logger.info(f"Applying default theme: {effective_theme_id}")

        # Create job with all preferences
        job_create = JobCreate(
            url=str(request.url),
            artist=request.artist,
            title=request.title,
            theme_id=effective_theme_id,  # Required - all jobs must have a theme
            enable_cdg=request.enable_cdg,
            enable_txt=request.enable_txt,
            enable_youtube_upload=effective_enable_youtube_upload,
            youtube_description=request.youtube_description,
            webhook_url=request.webhook_url,
            user_email=user_email
        )
        job = job_manager.create_job(job_create, is_admin=auth_result.is_admin)

        # Record job creation metric
        metrics.record_job_created(job.job_id, source="url")
        
        # Trigger both workers in parallel using asyncio.gather
        # (FastAPI's BackgroundTasks runs async tasks sequentially)
        background_tasks.add_task(_trigger_workers_parallel, job.job_id)
        
        logger.info(f"Job {job.job_id} created, workers triggered")
        
        return JobResponse(
            status="success",
            job_id=job.job_id,
            message="Job created successfully. Processing started."
        )
    except Exception as e:
        logger.error(f"Error creating job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Worker triggering is now handled by WorkerService
# See backend/services/worker_service.py


@router.get("/{job_id}", response_model=Job)
async def get_job(
    job_id: str,
    auth_result: AuthResult = Depends(require_auth)
) -> Job:
    """Get job status and details."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership - users can only see their own jobs, admins can see all
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to access this job")

    # If job is complete, include download URLs
    if job.status == JobStatus.COMPLETE:
        job.download_urls = job_manager.get_output_urls(job_id)

    return job


def _check_job_ownership(job: Job, auth_result: AuthResult) -> bool:
    """
    Check if the authenticated user owns the job or has admin access.

    Returns:
        True if user can access the job, False otherwise
    """
    # Admins can access all jobs
    if auth_result.is_admin:
        return True

    # Check if user owns the job
    if auth_result.user_email and job.user_email:
        return auth_result.user_email.lower() == job.user_email.lower()

    # If no user_email on auth (token auth without email), deny access to jobs with user_email
    # This prevents token-based auth from accessing user jobs
    if job.user_email:
        return False

    # Legacy jobs without user_email - allow access for backward compatibility
    # TODO: Consider restricting this in the future
    return True


@router.get("", response_model=List[Job])
async def list_jobs(
    request: Request,
    status: Optional[JobStatus] = None,
    environment: Optional[str] = None,
    client_id: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    exclude_test: bool = True,
    limit: int = 100,
    auth_result: AuthResult = Depends(require_auth)
) -> List[Job]:
    """
    List jobs with optional filters.

    Regular users only see their own jobs. Admins see all jobs.
    Users on tenant portals only see jobs from their tenant.

    Args:
        status: Filter by job status (pending, complete, failed, etc.)
        environment: Filter by request_metadata.environment (test/production/development)
        client_id: Filter by request_metadata.client_id (customer identifier)
        created_after: Filter jobs created after this ISO datetime (e.g., 2024-01-01T00:00:00Z)
        created_before: Filter jobs created before this ISO datetime
        exclude_test: If True (default), exclude jobs from test users (admin only)
        limit: Maximum number of jobs to return (default 100)

    Returns:
        List of jobs matching filters, ordered by created_at descending
    """
    from datetime import datetime

    try:
        # Parse datetime strings if provided
        created_after_dt = None
        created_before_dt = None

        if created_after:
            try:
                created_after_dt = datetime.fromisoformat(created_after.replace('Z', '+00:00'))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid created_after format: {created_after}") from e

        if created_before:
            try:
                created_before_dt = datetime.fromisoformat(created_before.replace('Z', '+00:00'))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid created_before format: {created_before}") from e

        # Determine user_email filter based on admin status
        # Admins see all jobs, regular users only see their own
        user_email_filter = None
        if not auth_result.is_admin:
            if auth_result.user_email:
                user_email_filter = auth_result.user_email
                logger.debug(f"Filtering jobs for user: {user_email_filter}")
            else:
                # Token-based auth without user email - show no jobs for security
                logger.warning("Non-admin auth without user_email, returning empty job list")
                return []

        # Get tenant_id from request for portal scoping
        # Tenant users only see jobs from their tenant
        tenant_id = get_tenant_from_request(request)

        jobs = job_manager.list_jobs(
            status=status,
            environment=environment,
            client_id=client_id,
            created_after=created_after_dt,
            created_before=created_before_dt,
            user_email=user_email_filter,
            tenant_id=tenant_id,
            limit=limit
        )

        # Filter out test user jobs if exclude_test is True (admin only)
        if exclude_test and auth_result.is_admin:
            jobs = [j for j in jobs if not is_test_email(j.user_email or "")]

        logger.debug(f"Listed {len(jobs)} jobs for user={auth_result.user_email}, admin={auth_result.is_admin}")
        return jobs
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{job_id}")
async def delete_job(
    job_id: str,
    delete_files: bool = True,
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """Delete a job and optionally its output files."""
    try:
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Check ownership - users can only delete their own jobs
        if not _check_job_ownership(job, auth_result):
            raise HTTPException(status_code=403, detail="You don't have permission to delete this job")

        job_manager.delete_job(job_id, delete_files=delete_files)

        return {"status": "success", "message": f"Job {job_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("")
async def bulk_delete_jobs(
    environment: Optional[str] = None,
    client_id: Optional[str] = None,
    status: Optional[JobStatus] = None,
    created_before: Optional[str] = None,
    delete_files: bool = True,
    confirm: bool = False,
    _auth_result: AuthResult = Depends(require_admin)
) -> dict:
    """
    Delete multiple jobs matching filter criteria.
    
    CAUTION: This is a destructive operation. Requires confirm=true.
    
    Use cases:
    - Delete all test jobs: ?environment=test&confirm=true
    - Delete jobs from a specific client: ?client_id=test-runner&confirm=true
    - Delete old failed jobs: ?status=failed&created_before=2024-01-01T00:00:00Z&confirm=true
    
    Args:
        environment: Delete jobs with this environment (test/production/development)
        client_id: Delete jobs from this client
        status: Delete jobs with this status
        created_before: Delete jobs created before this ISO datetime
        delete_files: Also delete GCS files (default True)
        confirm: Must be True to execute deletion (safety check)
        
    Returns:
        Statistics about the deletion
    """
    from datetime import datetime
    
    # Require at least one filter to prevent accidental deletion of all jobs
    if not any([environment, client_id, status, created_before]):
        raise HTTPException(
            status_code=400,
            detail="At least one filter (environment, client_id, status, created_before) is required"
        )
    
    # Require explicit confirmation
    if not confirm:
        # Return preview of what would be deleted
        created_before_dt = None
        if created_before:
            try:
                created_before_dt = datetime.fromisoformat(created_before.replace('Z', '+00:00'))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid created_before format: {created_before}")
        
        jobs = job_manager.list_jobs(
            status=status,
            environment=environment,
            client_id=client_id,
            created_before=created_before_dt,
            limit=1000
        )
        
        return {
            "status": "preview",
            "message": "Add &confirm=true to execute deletion",
            "jobs_to_delete": len(jobs),
            "sample_jobs": [
                {
                    "job_id": j.job_id,
                    "artist": j.artist,
                    "title": j.title,
                    "status": j.status,
                    "environment": j.request_metadata.get('environment'),
                    "client_id": j.request_metadata.get('client_id'),
                    "created_at": j.created_at.isoformat() if j.created_at else None
                }
                for j in jobs[:10]  # Show first 10 as sample
            ]
        }
    
    try:
        # Parse datetime string
        created_before_dt = None
        if created_before:
            try:
                created_before_dt = datetime.fromisoformat(created_before.replace('Z', '+00:00'))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid created_before format: {created_before}")
        
        result = job_manager.delete_jobs_by_filter(
            environment=environment,
            client_id=client_id,
            status=status,
            created_before=created_before_dt,
            delete_files=delete_files
        )
        
        return {
            "status": "success",
            "message": f"Deleted {result['jobs_deleted']} jobs",
            "jobs_deleted": result['jobs_deleted'],
            "files_deleted": result.get('files_deleted', 0)
        }
        
    except Exception as e:
        logger.error(f"Error bulk deleting jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Human-in-the-Loop Interaction Endpoints
# ============================================================================

@router.get("/{job_id}/review-data")
async def get_review_data(
    job_id: str,
    auth_result: AuthResult = Depends(require_auth)
) -> Dict[str, Any]:
    """
    Get data needed for lyrics review interface.

    Returns corrections JSON URL and audio URL.
    Frontend loads these to render the review UI.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to access this job")

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not ready for review (current status: {job.status})"
        )
    
    # Get URLs from file_urls
    corrections_url = job.file_urls.get('lyrics', {}).get('corrections')

    # For audio, try multiple sources in order of preference:
    # 1. Explicit lyrics audio (if worker uploaded it)
    # 2. Lead vocals stem (best for reviewing lyrics sync)
    # 3. Input media (original audio)
    audio_url = (
        job.file_urls.get('lyrics', {}).get('audio') or
        job.file_urls.get('stems', {}).get('lead_vocals') or
        job.input_media_gcs_path
    )

    if not corrections_url:
        raise HTTPException(
            status_code=500,
            detail="Corrections data not available"
        )

    if not audio_url:
        raise HTTPException(
            status_code=500,
            detail="Audio not available for review"
        )
    
    # Generate signed URLs for direct access
    from backend.services.storage_service import StorageService
    storage = StorageService()
    
    return {
        "corrections_url": storage.generate_signed_url(corrections_url, expiration_minutes=120),
        "audio_url": storage.generate_signed_url(audio_url, expiration_minutes=120),
        "status": job.status,
        "artist": job.artist,
        "title": job.title
    }


@router.post("/{job_id}/start-review")
async def start_review(
    job_id: str,
    request: StartReviewRequest,
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Mark job as IN_REVIEW (user opened review interface).

    This helps track that the user is actively working on the review.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to access this job")

    success = job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.IN_REVIEW,
        message="User started reviewing lyrics"
    )
    
    if not success:
        raise HTTPException(status_code=400, detail="Cannot start review")
    
    return {"status": "success", "job_status": "in_review"}


@router.post("/{job_id}/corrections")
async def submit_corrections(
    job_id: str,
    submission: CorrectionsSubmission,
    background_tasks: BackgroundTasks,
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Save corrected lyrics during human review.

    This endpoint saves review progress but does NOT complete the review.
    Call POST /{job_id}/complete-review to finish and trigger video rendering.

    Can be called multiple times to save progress.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to modify this job")

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state (current status: {job.status})"
        )
    
    try:
        # Store corrected lyrics in state_data
        job_manager.update_state_data(job_id, 'corrected_lyrics', submission.corrections)
        if submission.user_notes:
            job_manager.update_state_data(job_id, 'review_notes', submission.user_notes)
        
        # Transition to IN_REVIEW if not already
        if job.status == JobStatus.AWAITING_REVIEW:
            job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.IN_REVIEW,
                message="User is reviewing lyrics"
            )
        
        # Save updated corrections to GCS for the render worker
        from backend.services.storage_service import StorageService
        storage = StorageService()
        
        corrections_gcs_path = f"jobs/{job_id}/lyrics/corrections_updated.json"
        storage.upload_json(corrections_gcs_path, submission.corrections)
        job_manager.update_file_url(job_id, 'lyrics', 'corrections_updated', corrections_gcs_path)
        
        logger.info(f"Job {job_id}: Corrections saved (review in progress)")
        
        return {
            "status": "success",
            "job_status": "in_review",
            "message": "Corrections saved. Call /complete-review when done."
        }
        
    except Exception as e:
        logger.error(f"Error saving corrections for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/complete-review")
async def complete_review(
    job_id: str,
    background_tasks: BackgroundTasks,
    auth_result: AuthResult = Depends(require_auth),
    body: Optional[CompleteReviewRequest] = None
) -> dict:
    """
    Complete the human review and trigger video rendering.

    Supports both legacy flows (no body) and combined review flow (with instrumental_selection).
    When instrumental_selection is provided, it's stored in state_data for the render worker.

    After this:
    1. Job transitions to REVIEW_COMPLETE
    2. Render video worker is triggered
    3. Worker uses OutputGenerator to create with_vocals.mkv
    4. Job transitions to INSTRUMENTAL_SELECTED then GENERATING_VIDEO
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to modify this job")

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state (current status: {job.status})"
        )

    try:
        # Store instrumental selection if provided (combined review flow)
        instrumental_selection = body.instrumental_selection if body else None
        if instrumental_selection:
            job_manager.update_state_data(job_id, 'instrumental_selection', instrumental_selection)
            logger.info(f"Job {job_id}: Stored instrumental selection: {instrumental_selection}")

        # Transition to REVIEW_COMPLETE
        message = f"Review complete (instrumental: {instrumental_selection})" if instrumental_selection else "Review complete"
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.REVIEW_COMPLETE,
            progress=70,
            message=f"{message}, rendering video with corrected lyrics"
        )

        # Trigger render video worker
        background_tasks.add_task(worker_service.trigger_render_video_worker, job_id)

        logger.info(f"Job {job_id}: Review complete, triggering render video worker")

        return {
            "status": "success",
            "job_status": "review_complete",
            "message": "Review complete. Video rendering started."
        }

    except Exception as e:
        logger.error(f"Error completing review for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/select-instrumental")
async def select_instrumental(
    job_id: str,
    selection: InstrumentalSelection,
    background_tasks: BackgroundTasks,
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Select instrumental audio option for finalise-only jobs.

    This endpoint is used for jobs that enter AWAITING_INSTRUMENTAL_SELECTION
    state directly (finalise-only jobs where users upload pre-rendered video).

    For normal jobs, instrumental selection happens during the combined review
    flow via the complete-review endpoint.

    Args:
        job_id: Job identifier
        selection: Instrumental selection (clean, with_backing, custom, uploaded)

    Returns:
        Status and confirmation of selection
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to access this job")

    if job.status != JobStatus.AWAITING_INSTRUMENTAL_SELECTION:
        raise HTTPException(
            status_code=400,
            detail=f"Job not awaiting instrumental selection (current status: {job.status})"
        )

    try:
        # Store selection in state_data
        state_data = job.state_data or {}
        state_data["instrumental_selection"] = selection.selection
        job_manager.update_job(job_id, state_data=state_data)

        # Transition to INSTRUMENTAL_SELECTED
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.INSTRUMENTAL_SELECTED,
            progress=80,
            message=f"Instrumental selection: {selection.selection}"
        )

        # Trigger video worker
        background_tasks.add_task(worker_service.trigger_video_worker, job_id)

        logger.info(f"Job {job_id}: Instrumental selected ({selection.selection}), triggering video worker")

        return {
            "status": "success",
            "job_status": "instrumental_selected",
            "selection": selection.selection,
            "message": "Instrumental selected. Video generation started."
        }

    except Exception as e:
        logger.error(f"Error selecting instrumental for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}/download-urls")
async def get_download_urls(
    job_id: str,
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Get download URLs for all job output files.

    Returns a dictionary mapping file types to download URLs.
    Uses the streaming download endpoint which proxies through the backend.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to access this job")

    if job.status != JobStatus.COMPLETE:
        raise HTTPException(
            status_code=400,
            detail=f"Job not complete (current status: {job.status})"
        )
    
    file_urls = job.file_urls or {}
    download_urls = {}
    
    # Build download URLs using the streaming endpoint
    base_url = f"/api/jobs/{job_id}/download"
    
    for category, files in file_urls.items():
        if isinstance(files, dict):
            download_urls[category] = {}
            for file_key, gcs_path in files.items():
                if gcs_path:
                    download_urls[category][file_key] = f"{base_url}/{category}/{file_key}"
        elif isinstance(files, str) and files:
            # For single-file categories, use the category name as the file_key
            download_urls[category] = f"{base_url}/{category}/{category}"
    
    return {
        "job_id": job_id,
        "artist": job.artist,
        "title": job.title,
        "download_urls": download_urls
    }


# Map file keys to human-readable suffixes (matching Dropbox naming from karaoke_finalise.py)
DOWNLOAD_FILENAME_SUFFIXES = {
    "lossless_4k_mp4": " (Final Karaoke Lossless 4k).mp4",
    "lossless_4k_mkv": " (Final Karaoke Lossless 4k).mkv",
    "lossy_4k_mp4": " (Final Karaoke Lossy 4k).mp4",
    "lossy_720p_mp4": " (Final Karaoke Lossy 720p).mp4",
    "cdg_zip": " (Final Karaoke CDG).zip",
    "txt_zip": " (Final Karaoke TXT).zip",
    "with_vocals": " (With Vocals).mkv",
}


@router.get("/{job_id}/download/{category}/{file_key}")
async def download_file(
    job_id: str,
    category: str,
    file_key: str,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Stream download a specific file from a completed job.

    This endpoint proxies the file from GCS through the backend,
    so no client-side authentication is required.
    """
    from fastapi.responses import StreamingResponse
    import tempfile

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to access this job")

    if job.status != JobStatus.COMPLETE:
        raise HTTPException(
            status_code=400,
            detail=f"Job not complete (current status: {job.status})"
        )
    
    file_urls = job.file_urls or {}
    category_files = file_urls.get(category)
    
    if not category_files:
        raise HTTPException(status_code=404, detail=f"Category '{category}' not found")
    
    if isinstance(category_files, dict):
        gcs_path = category_files.get(file_key)
    else:
        gcs_path = category_files if file_key == category else None
    
    if not gcs_path:
        raise HTTPException(status_code=404, detail=f"File '{file_key}' not found in '{category}'")
    
    # Determine content type based on file extension
    ext = gcs_path.split('.')[-1].lower()
    content_types = {
        'mp4': 'video/mp4',
        'mkv': 'video/x-matroska',
        'mov': 'video/quicktime',
        'flac': 'audio/flac',
        'mp3': 'audio/mpeg',
        'wav': 'audio/wav',
        'ass': 'text/plain',
        'lrc': 'text/plain',
        'txt': 'text/plain',
        'json': 'application/json',
        'zip': 'application/zip',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
    }
    content_type = content_types.get(ext, 'application/octet-stream')
    
    # Build proper filename: "Artist - Title (Final Karaoke Lossy 4k).mp4"
    # Use sanitize_filename to handle Unicode characters (curly quotes, em dashes, etc.)
    # that cause HTTP header encoding issues (Content-Disposition uses latin-1)
    from karaoke_gen.utils import sanitize_filename
    artist_clean = sanitize_filename(job.artist) if job.artist else None
    title_clean = sanitize_filename(job.title) if job.title else None
    base_name = f"{artist_clean} - {title_clean}" if artist_clean and title_clean else None

    if base_name and file_key in DOWNLOAD_FILENAME_SUFFIXES:
        filename = f"{base_name}{DOWNLOAD_FILENAME_SUFFIXES[file_key]}"
    else:
        filename = gcs_path.split('/')[-1]  # Fallback to original
    
    try:
        # Download to temp file and stream
        storage = StorageService()
        with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as tmp:
            tmp_path = tmp.name
        
        storage.download_file(gcs_path, tmp_path)
        
        def file_iterator():
            try:
                with open(tmp_path, 'rb') as f:
                    while chunk := f.read(8192):
                        yield chunk
            finally:
                import os
                os.unlink(tmp_path)
        
        return StreamingResponse(
            file_iterator(),
            media_type=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        logger.error(f"Error downloading {gcs_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error downloading file: {e}")


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    request: CancelJobRequest,
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Cancel a job.

    Jobs can be cancelled at any stage before completion.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to cancel this job")

    success = job_manager.cancel_job(job_id, reason=request.reason)
    
    if not success:
        raise HTTPException(status_code=400, detail="Cannot cancel job")
    
    return {
        "status": "success",
        "job_status": "cancelled",
        "message": "Job cancelled successfully"
    }


@router.post("/{job_id}/retry")
async def retry_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Retry a failed or cancelled job from the last successful checkpoint.

    This endpoint allows resuming jobs that failed or were cancelled during:
    - Audio processing (re-runs from beginning if input audio exists)
    - Video generation (re-runs video worker)
    - Encoding (re-runs video worker)
    - Packaging (re-runs video worker)

    The retry logic determines the appropriate stage to resume from
    based on what files/state already exist.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to retry this job")

    if job.status not in [JobStatus.FAILED, JobStatus.CANCELLED]:
        raise HTTPException(
            status_code=400,
            detail=f"Only failed or cancelled jobs can be retried (current status: {job.status})"
        )
    
    try:
        # Determine retry point based on what's already complete
        error_details = job.error_details or {}
        error_stage = error_details.get('stage', 'unknown')
        original_status = job.status

        logger.info(f"Job {job_id}: Retrying from {original_status} state (error stage: '{error_stage}')")
        
        # Check what state exists to determine retry point
        file_urls = job.file_urls or {}
        state_data = job.state_data or {}
        
        # If we have a video with vocals and instrumental selection, retry video generation
        if (file_urls.get('videos', {}).get('with_vocals') and 
            state_data.get('instrumental_selection')):
            
            logger.info(f"Job {job_id}: Has rendered video and instrumental selection, retrying video generation")
            
            # Clear error state and reset worker progress for idempotency
            job_manager.update_job(job_id, {
                'error_message': None,
                'error_details': None,
            })
            job_manager.update_state_data(job_id, 'video_progress', {'stage': 'pending'})

            # Reset to INSTRUMENTAL_SELECTED and trigger video worker
            if not job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.INSTRUMENTAL_SELECTED,
                progress=65,
                message=f"Retrying video generation from failed state"
            ):
                raise HTTPException(
                    status_code=500,
                    detail="Failed to transition job status for retry"
                )
            
            # Trigger video generation worker
            background_tasks.add_task(worker_service.trigger_video_worker, job_id)
            
            return {
                "status": "success",
                "job_status": "instrumental_selected",
                "message": "Job retry started from video generation stage",
                "retry_stage": "video_generation"
            }
        
        # If we have corrections and screens but no video, retry render
        elif (file_urls.get('lyrics', {}).get('corrections') and 
              file_urls.get('screens', {}).get('title')):
            
            logger.info(f"Job {job_id}: Has corrections and screens, retrying from render stage")

            # Clear error state and reset worker progress for idempotency
            job_manager.update_job(job_id, {
                'error_message': None,
                'error_details': None,
            })
            job_manager.update_state_data(job_id, 'render_progress', {'stage': 'pending'})

            # Reset to REVIEW_COMPLETE and trigger render worker
            if not job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.REVIEW_COMPLETE,
                progress=70,
                message=f"Retrying video render from failed state"
            ):
                raise HTTPException(
                    status_code=500,
                    detail="Failed to transition job status for retry"
                )
            
            # Trigger render video worker
            background_tasks.add_task(worker_service.trigger_render_video_worker, job_id)
            
            return {
                "status": "success",
                "job_status": "review_complete",
                "message": "Job retry started from render stage",
                "retry_stage": "render_video"
            }
        
        # If we have stems and corrections, retry from screens generation
        elif (file_urls.get('stems', {}).get('instrumental_clean') and 
              file_urls.get('lyrics', {}).get('corrections')):
            
            logger.info(f"Job {job_id}: Has stems and corrections, retrying from screens stage")

            # Clear error state and reset worker progress for idempotency
            job_manager.update_job(job_id, {
                'error_message': None,
                'error_details': None,
            })
            job_manager.update_state_data(job_id, 'screens_progress', {'stage': 'pending'})

            # Reset to a state before screens and trigger screens worker
            if not job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.LYRICS_COMPLETE,
                progress=45,
                message=f"Retrying from screens generation"
            ):
                raise HTTPException(
                    status_code=500,
                    detail="Failed to transition job status for retry"
                )
            
            # Trigger screens worker
            background_tasks.add_task(worker_service.trigger_screens_worker, job_id)
            
            return {
                "status": "success",
                "job_status": "lyrics_complete",
                "message": "Job retry started from screens generation",
                "retry_stage": "screens_generation"
            }
        
        # If we have input audio (uploaded or from URL), restart from beginning
        elif job.input_media_gcs_path or job.url:
            logger.info(f"Job {job_id}: Has input audio, restarting from beginning")

            # Clear error state and any partial progress
            job_manager.update_job(job_id, {
                'error_message': None,
                'error_details': None,
                'state_data': {},  # Clear parallel worker progress
            })

            # Reset to DOWNLOADING and trigger audio worker
            if not job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.DOWNLOADING,
                progress=5,
                message=f"Restarting job from {original_status} state"
            ):
                raise HTTPException(
                    status_code=500,
                    detail="Failed to transition job status for retry"
                )

            # Trigger audio worker (which kicks off parallel audio + lyrics processing)
            background_tasks.add_task(worker_service.trigger_audio_worker, job_id)
            background_tasks.add_task(worker_service.trigger_lyrics_worker, job_id)

            return {
                "status": "success",
                "job_id": job_id,
                "job_status": "downloading",
                "message": f"Job restarted from {original_status} state",
                "retry_stage": "from_beginning"
            }

        else:
            # No input audio available - job needs to be resubmitted
            raise HTTPException(
                status_code=400,
                detail="Cannot retry: no input audio available. Job must be resubmitted."
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}/logs")
async def get_worker_logs(
    job_id: str,
    since_index: int = 0,
    worker: Optional[str] = None,
    auth_result: AuthResult = Depends(require_auth)
) -> Dict[str, Any]:
    """
    Get worker logs for debugging.

    This endpoint returns worker logs stored in Firestore.
    Use `since_index` for efficient polling (returns only new logs).

    Logs are stored in a subcollection (jobs/{job_id}/logs) to avoid
    the 1MB document size limit. Older jobs may have logs in an embedded
    array (worker_logs field) - this endpoint handles both transparently.

    Args:
        job_id: Job ID
        since_index: Return only logs after this index (for pagination/polling)
        worker: Filter by worker name (audio, lyrics, screens, video, render, distribution)

    Returns:
        {
            "logs": [{"timestamp": "...", "level": "INFO", "worker": "audio", "message": "..."}],
            "next_index": 42,  # Use this for next poll
            "total_logs": 42
        }
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check ownership - users can only see logs for their own jobs
    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to access logs for this job")

    logs = job_manager.get_worker_logs(job_id, since_index=since_index, worker=worker)
    total = job_manager.get_worker_logs_count(job_id)

    return {
        "logs": logs,
        "next_index": since_index + len(logs),
        "total_logs": total
    }


@router.post("/{job_id}/cleanup-distribution")
async def cleanup_distribution(
    job_id: str,
    delete_job: bool = True,
    auth_result: AuthResult = Depends(require_admin)
) -> dict:
    """
    Clean up all distributed content for a job (YouTube, Dropbox, Google Drive).

    This admin-only endpoint is designed for E2E test cleanup. It:
    1. Deletes YouTube video (if uploaded)
    2. Deletes Dropbox folder (if uploaded)
    3. Deletes Google Drive files (if uploaded)
    4. Optionally deletes the job itself

    Args:
        job_id: Job ID to clean up
        delete_job: If True, also delete the job after cleaning up distribution

    Returns:
        Cleanup results for each service
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    state_data = job.state_data or {}
    results = {
        "job_id": job_id,
        "youtube": {"status": "skipped", "reason": "no youtube_url in state_data"},
        "dropbox": {"status": "skipped", "reason": "no brand_code or dropbox_path"},
        "gdrive": {"status": "skipped", "reason": "no gdrive_files in state_data"},
        "job_deleted": False
    }

    # Clean up YouTube
    youtube_url = state_data.get('youtube_url')
    if youtube_url:
        try:
            # Extract video ID from URL (format: https://youtu.be/VIDEO_ID or https://www.youtube.com/watch?v=VIDEO_ID)
            import re
            video_id_match = re.search(r'(?:youtu\.be/|youtube\.com/watch\?v=)([^&\s]+)', youtube_url)
            if video_id_match:
                video_id = video_id_match.group(1)

                # Import and use karaoke_finalise for YouTube deletion
                from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
                from backend.services.youtube_service import get_youtube_service

                youtube_service = get_youtube_service()
                if youtube_service.is_configured:
                    # Create minimal KaraokeFinalise instance for deletion
                    finalise = KaraokeFinalise(
                        dry_run=False,
                        non_interactive=True,
                        enable_youtube=True,
                        user_youtube_credentials=youtube_service.get_credentials_dict()
                    )
                    success = finalise.delete_youtube_video(video_id)
                    results["youtube"] = {
                        "status": "success" if success else "failed",
                        "video_id": video_id
                    }
                else:
                    results["youtube"] = {"status": "failed", "reason": "YouTube credentials not configured"}
            else:
                results["youtube"] = {"status": "failed", "reason": f"Could not extract video ID from {youtube_url}"}
        except Exception as e:
            logger.error(f"Error cleaning up YouTube for job {job_id}: {e}", exc_info=True)
            results["youtube"] = {"status": "error", "error": str(e)}

    # Clean up Dropbox
    brand_code = state_data.get('brand_code')
    dropbox_path = getattr(job, 'dropbox_path', None)
    if brand_code and dropbox_path:
        try:
            from backend.services.dropbox_service import get_dropbox_service
            dropbox = get_dropbox_service()
            if dropbox.is_configured:
                base_name = f"{job.artist} - {job.title}"
                folder_name = f"{brand_code} - {base_name}"
                full_path = f"{dropbox_path}/{folder_name}"
                success = dropbox.delete_folder(full_path)
                results["dropbox"] = {
                    "status": "success" if success else "failed",
                    "path": full_path
                }
            else:
                results["dropbox"] = {"status": "failed", "reason": "Dropbox credentials not configured"}
        except Exception as e:
            logger.error(f"Error cleaning up Dropbox for job {job_id}: {e}", exc_info=True)
            results["dropbox"] = {"status": "error", "error": str(e)}

    # Clean up Google Drive
    gdrive_files = state_data.get('gdrive_files')
    if gdrive_files:
        try:
            from backend.services.gdrive_service import get_gdrive_service
            gdrive = get_gdrive_service()
            if gdrive.is_configured:
                # gdrive_files is a dict like {"mp4": "file_id", "mp4_720p": "file_id", "cdg": "file_id"}
                file_ids = list(gdrive_files.values()) if isinstance(gdrive_files, dict) else []
                delete_results = gdrive.delete_files(file_ids)
                all_success = all(delete_results.values())
                results["gdrive"] = {
                    "status": "success" if all_success else "partial",
                    "files": delete_results
                }
            else:
                results["gdrive"] = {"status": "failed", "reason": "Google Drive credentials not configured"}
        except Exception as e:
            logger.error(f"Error cleaning up Google Drive for job {job_id}: {e}", exc_info=True)
            results["gdrive"] = {"status": "error", "error": str(e)}

    # Delete the job if requested
    if delete_job:
        try:
            job_manager.delete_job(job_id, delete_files=True)
            results["job_deleted"] = True
            logger.info(f"Deleted job {job_id} after distribution cleanup")
        except Exception as e:
            logger.error(f"Error deleting job {job_id}: {e}", exc_info=True)
            results["job_deleted"] = False
            results["job_delete_error"] = str(e)

    return results

