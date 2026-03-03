"""
Job management routes.

Handles job lifecycle endpoints including:
- Job creation and submission
- Status polling
- Human-in-the-loop interactions (combined lyrics + instrumental review)
- Job deletion and cancellation
"""
import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Request, UploadFile, File

from datetime import datetime
from backend.models.job import Job, JobCreate, JobResponse, JobStatus
from backend.models.requests import (
    URLSubmissionRequest,
    CorrectionsSubmission,
    StartReviewRequest,
    CancelJobRequest,
    InstrumentalSelection,
    CompleteReviewRequest,
    CreateCustomInstrumentalRequest,
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
from backend.exceptions import InsufficientCreditsError, RateLimitExceededError
from backend.services.firestore_service import FirestoreService
from pydantic import BaseModel, Field, validator


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
            user_email=user_email,
            is_private=request.is_private or False,
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
    except (InsufficientCreditsError, RateLimitExceededError):
        raise
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


_SUMMARY_STATE_DATA_KEYS = {
    'brand_code', 'youtube_url', 'dropbox_link',
    'audio_progress', 'lyrics_progress',
    'audio_complete', 'lyrics_complete',
    'backing_vocals_analysis',
}
_SUMMARY_FILE_URLS_KEYS = {'finals', 'videos', 'packages'}
_HIDE_COMPLETED_STATUSES = ['complete', 'prep_complete']


def _prune_state_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Strip state_data down to dashboard-required keys."""
    sd = data.get('state_data')
    if isinstance(sd, dict):
        data['state_data'] = {k: v for k, v in sd.items() if k in _SUMMARY_STATE_DATA_KEYS}
    return data


def _prune_file_urls(data: Dict[str, Any]) -> Dict[str, Any]:
    """Strip file_urls down to dashboard-required keys."""
    fu = data.get('file_urls')
    if isinstance(fu, dict):
        data['file_urls'] = {k: v for k, v in fu.items() if k in _SUMMARY_FILE_URLS_KEYS}
    return data


@router.get("", response_model=None)
async def list_jobs(
    request: Request,
    status: Optional[JobStatus] = None,
    environment: Optional[str] = None,
    client_id: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    exclude_test: bool = True,
    limit: int = 100,
    fields: Optional[str] = None,
    hide_completed: bool = False,
    auth_result: AuthResult = Depends(require_auth)
):
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
        fields: Set to "summary" for reduced payload with only dashboard-required fields
        hide_completed: If True, exclude successful completions (complete, prep_complete). Failed jobs remain visible.

    Returns:
        List of jobs matching filters, ordered by created_at descending.
        When fields=summary, returns List[dict] (pruned).
        Otherwise returns List[Job] (full model).
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

        # --- Summary mode: field-projected query returning dicts ---
        if fields == "summary":
            exclude_statuses = _HIDE_COMPLETED_STATUSES if hide_completed else None
            jobs_dicts = job_manager.list_jobs_summary(
                status=status,
                exclude_statuses=exclude_statuses,
                environment=environment,
                client_id=client_id,
                created_after=created_after_dt,
                created_before=created_before_dt,
                user_email=user_email_filter,
                tenant_id=tenant_id,
                limit=limit,
            )

            # Exclude test user jobs (Python-side, same as full mode)
            if exclude_test and auth_result.is_admin:
                jobs_dicts = [j for j in jobs_dicts if not is_test_email(j.get('user_email') or "")]

            # Safety-net pruning (in case Firestore returned extra nested keys)
            jobs_dicts = [_prune_file_urls(_prune_state_data(j)) for j in jobs_dicts]

            logger.debug(f"Listed {len(jobs_dicts)} summary jobs for user={auth_result.user_email}")
            return jobs_dicts

        # --- Full mode: existing behaviour (List[Job]) ---
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

        # Recycle any unreturned brand code before deleting the job record
        state_data = job.state_data or {}
        brand_code = state_data.get('brand_code')
        if brand_code:
            if not job.outputs_deleted_at:
                logger.warning(
                    f"Deleting job {job_id} with brand_code {brand_code} whose outputs "
                    f"were not cleaned up first. Brand code will be recycled but "
                    f"distributed files (GDrive/Dropbox/YouTube) may be orphaned."
                )
            try:
                from backend.services.brand_code_service import BrandCodeService, get_brand_code_service
                prefix, number = BrandCodeService.parse_brand_code(brand_code)
                get_brand_code_service().recycle_brand_code(prefix, number)
                logger.info(f"Recycled brand code {brand_code} before deleting job {job_id}")
            except (ValueError, Exception) as e:
                logger.warning(f"Failed to recycle brand code {brand_code}: {e}")

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
        # Strip reference_lyrics from Firestore save to avoid exceeding 1MB document
        # limit. reference_lyrics can be very large (e.g. Genius fetching non-lyrics
        # content like screenplays). The full data is preserved in the GCS upload below.
        corrections_for_firestore = {
            k: v for k, v in submission.corrections.items()
            if k != 'reference_lyrics'
        }
        job_manager.update_state_data(job_id, 'corrected_lyrics', corrections_for_firestore)
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


@router.post("/{job_id}/edit-log")
async def submit_edit_log(
    job_id: str,
    edit_log: Dict[str, Any],
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Store an edit log from a lyrics review session.

    Edit logs capture what edits users made and optional feedback on why,
    for use as training data to improve transcription models.

    Stored to GCS as jobs/{job_id}/lyrics/edit_log_{session_id}.json.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to modify this job")

    session_id = edit_log.get("session_id", "unknown")
    entries = edit_log.get("entries", [])
    feedback_count = sum(1 for e in entries if e.get("feedback") and e["feedback"].get("reason") != "no_response")

    try:
        storage = StorageService()
        gcs_path = f"jobs/{job_id}/lyrics/edit_log_{session_id}.json"
        storage.upload_json(gcs_path, edit_log)

        job_manager.update_state_data(job_id, 'last_edit_log_path', gcs_path)
        job_manager.update_state_data(job_id, 'last_edit_log_session', session_id)

        logger.info(
            f"Job {job_id}: Edit log saved — {len(entries)} entries, "
            f"{feedback_count} with feedback, session {session_id}"
        )

        return {
            "status": "success",
            "entries_count": len(entries),
            "feedback_count": feedback_count,
        }

    except Exception as e:
        logger.error(f"Error saving edit log for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/create-custom-instrumental")
async def create_custom_instrumental(
    job_id: str,
    request: CreateCustomInstrumentalRequest,
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Create a custom instrumental by muting regions of backing vocals.

    Downloads the clean instrumental and backing vocals stems, applies mute
    regions, combines them, uploads to GCS, and returns a signed URL for playback.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to modify this job")

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state (current status: {job.status})"
        )

    # Get stem paths from job
    stems = job.file_urls.get('stems', {})
    clean_path = stems.get('instrumental_clean')
    backing_path = stems.get('backing_vocals')

    if not clean_path or not backing_path:
        raise HTTPException(
            status_code=400,
            detail="Job missing required stems (instrumental_clean and/or backing_vocals)"
        )

    try:
        from backend.services.audio_editing_service import AudioEditingService
        from karaoke_gen.instrumental_review import MuteRegion

        storage = StorageService()
        editing_service = AudioEditingService(storage_service=storage)

        # Convert request mute regions to domain model
        mute_regions = [
            MuteRegion(start_seconds=r.start_seconds, end_seconds=r.end_seconds)
            for r in request.mute_regions
        ]

        # Create custom instrumental and upload to GCS (offload blocking IO to thread)
        output_path = f"jobs/{job_id}/stems/custom_instrumental.flac"
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: editing_service.create_custom_instrumental(
                gcs_clean_instrumental_path=clean_path,
                gcs_backing_vocals_path=backing_path,
                mute_regions=mute_regions,
                gcs_output_path=output_path,
                job_id=job_id,
            )
        )

        # Store the custom instrumental path in job file_urls
        job_manager.update_file_url(job_id, 'stems', 'custom_instrumental', output_path)

        # Generate signed URL for playback
        from backend.services.audio_transcoding_service import AudioTranscodingService
        transcoding = AudioTranscodingService(storage_service=storage)
        audio_url = await transcoding.get_review_audio_url_async(output_path, expiration_minutes=120)

        logger.info(f"Job {job_id}: Custom instrumental created with {len(mute_regions)} mute regions")

        return {
            "status": "success",
            "message": f"Custom instrumental created with {len(mute_regions)} mute regions",
            "audio_url": audio_url,
            "muted_duration_seconds": result.total_muted_duration_seconds,
        }

    except Exception as e:
        logger.error(f"Error creating custom instrumental for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/upload-instrumental")
async def upload_custom_instrumental(
    job_id: str,
    file: UploadFile = File(...),
    auth_result: AuthResult = Depends(require_auth)
) -> dict:
    """
    Upload a custom instrumental audio file for use during review.

    Accepts any audio format supported by pydub/ffmpeg (mp3, wav, flac, ogg, etc.).
    The file is stored to GCS and its path recorded in the job's stems metadata.
    The user can then select 'custom' as their instrumental_selection when completing review.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not _check_job_ownership(job, auth_result):
        raise HTTPException(status_code=403, detail="You don't have permission to modify this job")

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state (current status: {job.status})"
        )

    # Determine extension from filename or content type
    original_filename = file.filename or "instrumental"
    suffix = Path(original_filename).suffix.lower()
    if not suffix:
        content_type = file.content_type or ""
        suffix_map = {
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/flac": ".flac",
            "audio/x-flac": ".flac",
            "audio/ogg": ".ogg",
            "audio/aac": ".aac",
            "audio/mp4": ".m4a",
        }
        suffix = suffix_map.get(content_type, ".audio")

    storage = StorageService()
    tmp_path = None
    flac_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            content = await file.read()
            tmp.write(content)

        # Get duration of uploaded file using pydub
        from pydub import AudioSegment
        audio_segment = AudioSegment.from_file(tmp_path)
        upload_duration = len(audio_segment) / 1000.0

        # Get duration of original job audio to validate match
        original_duration = await _get_audio_duration_ffprobe_signed(job_id, job, storage)
        if original_duration is not None:
            diff = abs(upload_duration - original_duration)
            if diff > 0.5:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Duration mismatch: uploaded file is {upload_duration:.1f}s "
                        f"but original audio is {original_duration:.1f}s. "
                        f"The instrumental must be exactly {original_duration:.1f}s (±0.5s)."
                    ),
                )

        # Convert to FLAC for consistency with the rest of the pipeline
        # (GCE encoding worker searches for *.flac patterns)
        flac_path = tmp_path.rsplit('.', 1)[0] + '.flac' if '.' in tmp_path else tmp_path + '.flac'
        if suffix != '.flac':
            audio_segment.export(flac_path, format='flac')
        else:
            flac_path = tmp_path  # Already FLAC, no conversion needed

        # Upload to GCS (always as .flac)
        output_path = f"jobs/{job_id}/stems/custom_instrumental.flac"
        storage.upload_file(flac_path, output_path)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job {job_id}: Error processing uploaded instrumental: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process audio file: {str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if flac_path and flac_path != tmp_path and os.path.exists(flac_path):
            os.unlink(flac_path)

    # Record in job file_urls
    job_manager.update_file_url(job_id, 'stems', 'custom_instrumental', output_path)

    logger.info(f"Job {job_id}: Custom instrumental uploaded ({upload_duration:.1f}s) to {output_path}")

    return {
        "status": "success",
        "duration_seconds": upload_duration,
        "message": f"Custom instrumental uploaded ({upload_duration:.1f}s)",
    }


async def _get_audio_duration_ffprobe_signed(job_id: str, job, storage: StorageService) -> Optional[float]:
    """
    Get the duration of the job's original audio using ffprobe on a signed GCS URL.

    Uses ffprobe which reads only the container header - fast even for large files.
    Returns None if the duration cannot be determined (non-fatal, upload proceeds).
    """
    gcs_path = job.input_media_gcs_path
    if not gcs_path:
        return None

    try:
        signed_url = storage.generate_signed_url(gcs_path, expiration_minutes=5)

        def _run_ffprobe() -> float:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', signed_url],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                raise ValueError(f"ffprobe error: {result.stderr[:200]}")
            data = json.loads(result.stdout)
            return float(data['format']['duration'])

        return await asyncio.get_event_loop().run_in_executor(None, _run_ffprobe)

    except Exception as e:
        logger.warning(f"Job {job_id}: Could not determine original audio duration: {e}")
        return None


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

        # If we have audio search download params, retry the download
        elif (job.audio_source_type == 'audio_search' and
              job.source_name and job.source_id):
            logger.info(
                f"Job {job_id}: Has audio search params, retrying download: "
                f"source_name={job.source_name}, source_id={job.source_id}"
            )

            # Import needed services
            from backend.services.audio_search_service import get_audio_search_service
            from backend.services.youtube_download_service import (
                get_youtube_download_service,
                YouTubeDownloadError
            )
            from backend.services.audio_search_service import DownloadError
            import tempfile
            import os

            # Clear error state
            job_manager.update_job(job_id, {
                'error_message': None,
                'error_details': None,
            })

            # Reset to DOWNLOADING_AUDIO state
            if not job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.DOWNLOADING_AUDIO,
                progress=12,
                message=f"Retrying audio download from {job.source_name}"
            ):
                raise HTTPException(
                    status_code=500,
                    detail="Failed to transition job status for retry"
                )

            # Retry download using saved params
            # This is similar to the logic in audio_search.py:_download_and_start_processing
            audio_search_service = get_audio_search_service()

            try:
                if job.source_name == 'YouTube':
                    # Use YouTubeDownloadService
                    youtube_service = get_youtube_download_service()
                    video_id = job.source_id

                    try:
                        audio_gcs_path = await youtube_service.download_by_id(
                            video_id=video_id,
                            job_id=job_id,
                            artist=job.artist,
                            title=job.title,
                        )
                        filename = os.path.basename(audio_gcs_path)
                    except YouTubeDownloadError as e:
                        job_manager.fail_job(job_id, f"Audio download retry failed: YouTube download failed: {e}")
                        raise HTTPException(status_code=500, detail=f"YouTube download failed: {e}")

                elif job.source_name in ['RED', 'OPS', 'Spotify']:
                    # Use audio search service for torrent/Spotify sources
                    if not audio_search_service.is_remote_enabled():
                        job_manager.fail_job(
                            job_id,
                            f"Cannot retry: flacfetch service not configured for {job.source_name}"
                        )
                        raise HTTPException(
                            status_code=400,
                            detail=f"Cannot download from {job.source_name} without remote flacfetch service."
                        )

                    gcs_destination = f"uploads/{job_id}/audio/"

                    try:
                        result = audio_search_service.download_by_id(
                            source_name=job.source_name,
                            source_id=job.source_id,
                            output_dir="",
                            target_file=job.target_file,
                            download_url=job.download_url,
                            gcs_path=gcs_destination,
                        )

                        audio_gcs_path = result.filepath
                        if audio_gcs_path.startswith('gs://'):
                            audio_gcs_path = audio_gcs_path.replace('gs://karaoke-gen-storage/', '', 1)
                        filename = os.path.basename(result.filepath)

                    except DownloadError as e:
                        job_manager.fail_job(job_id, f"Audio download retry failed: {e}")
                        raise HTTPException(status_code=500, detail=f"Download failed: {e}")
                else:
                    job_manager.fail_job(
                        job_id,
                        f"Cannot retry: unknown source type {job.source_name}"
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unknown source type: {job.source_name}"
                    )

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

                # Trigger workers in background
                async def trigger_workers():
                    await asyncio.gather(
                        worker_service.trigger_audio_worker(job_id),
                        worker_service.trigger_lyrics_worker(job_id)
                    )

                background_tasks.add_task(trigger_workers)

                return {
                    "status": "success",
                    "job_id": job_id,
                    "job_status": "downloading",
                    "message": f"Job retry started: re-downloading from {job.source_name}",
                    "retry_stage": "audio_download"
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error retrying audio download for job {job_id}: {e}", exc_info=True)
                job_manager.fail_job(job_id, f"Audio download retry failed: {e}")
                raise HTTPException(status_code=500, detail=f"Download retry failed: {e}")

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
    dropbox_cleaned = False
    if brand_code and dropbox_path:
        try:
            from backend.services.dropbox_service import get_dropbox_service
            dropbox = get_dropbox_service()
            if dropbox.is_configured:
                base_name = f"{job.artist} - {job.title}"
                folder_name = f"{brand_code} - {base_name}"
                full_path = f"{dropbox_path}/{folder_name}"
                success = dropbox.delete_folder(full_path)
                dropbox_cleaned = success
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
    # Use tracked file IDs if available; otherwise fall back to searching by brand code.
    # This handles old jobs where gdrive_files was not persisted (e.g., uploaded before
    # file ID tracking was implemented, or when GDrive upload silently failed and a
    # previous test run left files behind).
    gdrive_files = state_data.get('gdrive_files')
    gdrive_folder_id = getattr(job, 'gdrive_folder_id', None)
    gdrive_cleaned = False

    if gdrive_files or (gdrive_folder_id and brand_code):
        try:
            from backend.services.gdrive_service import get_gdrive_service
            gdrive = get_gdrive_service()
            if gdrive.is_configured:
                file_ids: list = []
                gdrive_method = None  # track how files were located for response

                if gdrive_files and isinstance(gdrive_files, dict):
                    # Fast path: delete using tracked file IDs from state_data
                    file_ids = [v for v in gdrive_files.values() if v]
                    logger.info(
                        f"GDrive cleanup for job {job_id}: "
                        f"using {len(file_ids)} tracked file IDs"
                    )

                if not file_ids and gdrive_folder_id and brand_code:
                    # Fallback: search by brand code prefix in CDG/, MP4/, MP4-720p/ subfolders.
                    # Handles jobs where gdrive_files was not tracked (old jobs, or jobs where
                    # GDrive upload succeeded but file IDs were lost).
                    logger.info(
                        f"GDrive cleanup for job {job_id}: "
                        f"no tracked file IDs, searching by brand_code '{brand_code}'"
                    )
                    file_ids = gdrive.find_files_by_brand_code(gdrive_folder_id, brand_code)
                    gdrive_method = "brand_code_search"

                if file_ids:
                    delete_results = gdrive.delete_files(file_ids)
                    all_success = all(delete_results.values())
                    gdrive_cleaned = all_success
                    results["gdrive"] = {
                        "status": "success" if all_success else "partial",
                        "files": delete_results,
                    }
                    if gdrive_method:
                        results["gdrive"]["method"] = gdrive_method
                else:
                    gdrive_cleaned = True  # Nothing to delete
                    results["gdrive"] = {
                        "status": "skipped",
                        "reason": "no files found to delete",
                    }
            else:
                results["gdrive"] = {
                    "status": "failed",
                    "reason": "Google Drive credentials not configured",
                }
        except Exception as e:
            logger.error(
                f"Error cleaning up Google Drive for job {job_id}: {e}", exc_info=True
            )
            results["gdrive"] = {"status": "error", "error": str(e)}
    else:
        if not gdrive_folder_id:
            results["gdrive"]["reason"] = "no gdrive_folder_id on job and no tracked file IDs"

    # Recycle the brand code so it can be reused — but only after GDrive is confirmed clean.
    # If we recycle before GDrive cleanup, a new job could claim the same brand code and
    # collide with leftover files on GDrive (duplicate brand code in public share).
    if dropbox_cleaned and brand_code and gdrive_cleaned:
        try:
            from backend.services.brand_code_service import (
                BrandCodeService, get_brand_code_service
            )
            prefix, number = BrandCodeService.parse_brand_code(brand_code)
            get_brand_code_service().recycle_brand_code(prefix, number)
            results["dropbox"]["recycled_brand_code"] = True
            logger.info(f"Recycled brand code {brand_code} after full distribution cleanup")
        except (ValueError, Exception) as e:
            logger.warning(f"Failed to recycle brand code {brand_code}: {e}")
            results["dropbox"]["recycled_brand_code"] = False
    elif dropbox_cleaned and brand_code:
        logger.warning(
            f"Brand code {brand_code} NOT recycled: GDrive cleanup did not confirm success "
            f"(gdrive_cleaned={gdrive_cleaned}). Brand code will remain reserved to prevent "
            f"collisions with leftover GDrive files."
        )

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


# ============================================================================
# Guided Flow: Create job from search session
# ============================================================================

class CreateFromSearchRequest(BaseModel):
    """Request to create a job from a completed standalone search session."""
    search_session_id: str = Field(..., description="Session ID returned by /api/audio-search/search-standalone")
    selection_index: int = Field(..., description="Index of the selected audio source from search results")
    artist: str = Field(..., description="Search artist name (from Step 1)")
    title: str = Field(..., description="Search title (from Step 1)")
    display_artist: Optional[str] = Field(None, description="Display artist override for title screens/filenames")
    display_title: Optional[str] = Field(None, description="Display title override for title screens/filenames")
    is_private: bool = Field(False, description="Private track: Dropbox only, no YouTube/GDrive")

    @validator('display_artist', 'display_title')
    def strip_whitespace(cls, v):
        if v is not None:
            return v.strip() or None
        return v


@router.post("/create-from-search", response_model=JobResponse)
async def create_job_from_search(
    request: Request,
    background_tasks: BackgroundTasks,
    body: CreateFromSearchRequest,
    auth_result: AuthResult = Depends(require_auth)
) -> JobResponse:
    """
    Create a karaoke job from a previously completed standalone search session.

    This is the second half of the guided job creation flow:
    1. POST /api/audio-search/search-standalone  → search_session_id + results
    2. User picks a result (Step 2 of wizard)
    3. User confirms job settings (Step 3 of wizard)
    4. POST /api/jobs/create-from-search         → job_id (this endpoint)

    The job is created with all final field values at creation time (including
    is_private and display overrides).  It skips AWAITING_AUDIO_SELECTION and
    goes directly to DOWNLOADING_AUDIO, then triggers workers.

    Credit deduction happens here (inside job_manager.create_job).
    """
    # Lazy imports from audio_search to avoid circular imports at module level
    from backend.api.routes.audio_search import (
        _validate_and_prepare_selection,
        _download_audio_and_trigger_workers,
        extract_request_metadata,
    )
    from backend.services.audio_search_service import get_audio_search_service
    from backend.middleware.tenant import get_tenant_config_from_request

    try:
        user_email = auth_result.user_email
        if not user_email:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Read the session non-destructively for validation.
        # consume_search_session (atomic read+delete) is called later, just before job creation,
        # so that a 4xx validation error leaves the session intact for the user to retry.
        firestore_service = FirestoreService()
        session = firestore_service.get_search_session(body.search_session_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail="Search expired — please search again"
            )

        # Verify the session belongs to the requesting user (or admin)
        if not auth_result.is_admin and session.get('user_email') != user_email:
            raise HTTPException(status_code=403, detail="You don't have permission to use this search session")

        # Verify tenant consistency — session must have been created in the same tenant context
        request_tenant_id = getattr(request.state, 'tenant_id', None)
        if session.get('tenant_id') != request_tenant_id:
            raise HTTPException(status_code=403, detail="You don't have permission to use this search session")

        # Check TTL (belt-and-suspenders — Firestore TTL may not delete immediately)
        ttl_expiry = session.get('ttl_expiry')
        if ttl_expiry:
            if isinstance(ttl_expiry, str):
                try:
                    ttl_expiry = datetime.fromisoformat(ttl_expiry)
                except ValueError:
                    ttl_expiry = None
            if ttl_expiry and datetime.utcnow() > ttl_expiry.replace(tzinfo=None):
                raise HTTPException(
                    status_code=404,
                    detail="Search expired — please search again"
                )

        search_results = session.get('results', [])
        if not search_results:
            raise HTTPException(status_code=400, detail="Search session has no results")

        if body.selection_index < 0 or body.selection_index >= len(search_results):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid selection index {body.selection_index}. Valid range: 0-{len(search_results)-1}"
            )

        # Apply server defaults (same as existing search endpoint)
        settings_obj = get_settings()
        effective_dropbox_path = settings_obj.default_dropbox_path
        effective_gdrive_folder_id = settings_obj.default_gdrive_folder_id
        effective_discord_webhook_url = settings_obj.default_discord_webhook_url
        effective_enable_youtube_upload = settings_obj.default_enable_youtube_upload
        effective_brand_prefix = settings_obj.default_brand_prefix
        effective_youtube_description = settings_obj.default_youtube_description

        # Apply default theme
        theme_service = get_theme_service()
        effective_theme_id = theme_service.get_default_theme_id()
        if effective_theme_id:
            logger.info(f"Applying default theme: {effective_theme_id}")

        from backend.services.job_defaults_service import resolve_cdg_txt_defaults
        resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(effective_theme_id, None, None)

        # Determine display values
        session_artist = session.get('artist', body.artist)
        session_title = session.get('title', body.title)
        effective_display_artist = body.display_artist or session_artist
        effective_display_title = body.display_title or session_title

        tenant_id = session.get('tenant_id')

        request_metadata = extract_request_metadata(request, created_from="guided_flow")

        # All validation passed — atomically consume (read+delete) the session to prevent
        # a second concurrent request from creating a duplicate job.
        consumed_session = firestore_service.consume_search_session(body.search_session_id)
        if not consumed_session:
            # Another concurrent request already consumed the session
            raise HTTPException(
                status_code=404,
                detail="Search expired — please search again"
            )

        # Create the job with all final values
        job_create = JobCreate(
            artist=effective_display_artist,
            title=effective_display_title,
            theme_id=effective_theme_id,
            enable_cdg=resolved_cdg,
            enable_txt=resolved_txt,
            brand_prefix=effective_brand_prefix,
            enable_youtube_upload=effective_enable_youtube_upload,
            youtube_description=effective_youtube_description,
            youtube_description_template=effective_youtube_description,
            discord_webhook_url=effective_discord_webhook_url,
            dropbox_path=effective_dropbox_path,
            gdrive_folder_id=effective_gdrive_folder_id,
            user_email=user_email,
            audio_search_artist=session_artist,
            audio_search_title=session_title,
            request_metadata=request_metadata,
            is_private=body.is_private,
            tenant_id=tenant_id,
        )
        job = job_manager.create_job(job_create, is_admin=auth_result.is_admin)
        job_id = job.job_id

        logger.info(f"Created job {job_id} from search session {body.search_session_id}")

        metrics.record_job_created(job_id, source="guided_search")

        # Apply default theme style to job (copy style_params.json to job folder)
        if effective_theme_id:
            from backend.api.routes.file_upload import _prepare_theme_for_job
            try:
                style_params_path, theme_style_assets, youtube_desc = _prepare_theme_for_job(
                    job_id, effective_theme_id, {}
                )
                theme_update = {
                    'style_params_gcs_path': style_params_path,
                    'style_assets': theme_style_assets,
                }
                if youtube_desc and not effective_youtube_description:
                    theme_update['youtube_description_template'] = youtube_desc
                job_manager.update_job(job_id, theme_update)
            except Exception as e:
                logger.warning(f"Failed to prepare theme '{effective_theme_id}' for job {job_id}: {e}")

        # Store search results and remote_search_id in job state_data
        # (required by _download_audio_and_trigger_workers)
        state_data_update: Dict[str, Any] = {
            'audio_search_results': search_results,
            'audio_search_count': len(search_results),
        }
        if session.get('remote_search_id'):
            state_data_update['remote_search_id'] = session['remote_search_id']
        job_manager.update_job(job_id, {'state_data': state_data_update})

        # Transition to DOWNLOADING_AUDIO (skips AWAITING_AUDIO_SELECTION entirely)
        _validate_and_prepare_selection(
            job_id=job_id,
            selection_index=body.selection_index,
        )

        # Trigger audio download as a background task
        audio_search_service = get_audio_search_service()
        background_tasks.add_task(
            _download_audio_and_trigger_workers,
            job_id,
            body.selection_index,
            audio_search_service,
        )

        return JobResponse(
            status="success",
            job_id=job_id,
            message="Job created successfully. Audio download starting."
        )

    except HTTPException:
        raise
    except (InsufficientCreditsError, RateLimitExceededError):
        raise
    except Exception as e:
        logger.error(f"Error creating job from search: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

