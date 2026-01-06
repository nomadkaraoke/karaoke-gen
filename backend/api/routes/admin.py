"""
Admin API routes for dashboard statistics and management.

Handles:
- Dashboard overview statistics
- System-wide metrics
- Admin-only operations
- Audio search cache management
"""
import logging
from datetime import datetime, timedelta
from typing import Tuple, List, Optional, Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.dependencies import require_admin
from backend.services.auth_service import UserType
from backend.services.user_service import get_user_service, UserService, USERS_COLLECTION
from backend.services.job_manager import JobManager
from backend.services.flacfetch_client import get_flacfetch_client, FlacfetchServiceError
from backend.models.job import JobStatus


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


# =============================================================================
# Response Models
# =============================================================================

class JobsByStatusResponse(BaseModel):
    """Breakdown of jobs by status."""
    pending: int = 0
    processing: int = 0
    awaiting_review: int = 0
    awaiting_instrumental: int = 0
    complete: int = 0
    failed: int = 0
    cancelled: int = 0


class AdminStatsOverview(BaseModel):
    """Overview statistics for admin dashboard."""
    total_users: int
    active_users_7d: int
    active_users_30d: int
    total_jobs: int
    jobs_last_7d: int
    jobs_last_30d: int
    jobs_by_status: JobsByStatusResponse
    total_credits_issued_30d: int
    total_beta_testers: int


# =============================================================================
# Admin Stats Endpoints
# =============================================================================

@router.get("/stats/overview", response_model=AdminStatsOverview)
async def get_admin_stats_overview(
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Get overview statistics for admin dashboard.

    Includes:
    - User counts (total, active in 7d, active in 30d)
    - Job counts (total, by status, recent)
    - Credit statistics
    - Beta program stats
    """
    from google.cloud.firestore_v1 import FieldFilter
    from google.cloud.firestore_v1 import aggregation

    db = user_service.db
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    # Helper function to get count using aggregation
    def get_count(query) -> int:
        try:
            agg_query = aggregation.AggregationQuery(query)
            agg_query.count(alias="count")
            results = agg_query.get()
            return results[0][0].value if results else 0
        except Exception as e:
            logger.warning(f"Aggregation query failed: {e}")
            return 0

    # User statistics
    users_collection = db.collection(USERS_COLLECTION)

    total_users = get_count(users_collection)

    active_users_7d = get_count(
        users_collection.where(filter=FieldFilter("last_login_at", ">=", seven_days_ago))
    )

    active_users_30d = get_count(
        users_collection.where(filter=FieldFilter("last_login_at", ">=", thirty_days_ago))
    )

    total_beta_testers = get_count(
        users_collection.where(filter=FieldFilter("is_beta_tester", "==", True))
    )

    # Job statistics
    jobs_collection = db.collection("jobs")

    total_jobs = get_count(jobs_collection)

    jobs_last_7d = get_count(
        jobs_collection.where(filter=FieldFilter("created_at", ">=", seven_days_ago))
    )

    jobs_last_30d = get_count(
        jobs_collection.where(filter=FieldFilter("created_at", ">=", thirty_days_ago))
    )

    # Jobs by status - map multiple statuses to simplified categories
    processing_statuses = [
        "downloading", "downloading_audio", "searching_audio", "awaiting_audio_selection",
        "separating_stage1", "separating_stage2", "transcribing", "correcting",
        "generating_screens", "applying_padding", "rendering_video",
        "instrumental_selected", "generating_video", "encoding", "packaging",
        "uploading", "notifying"
    ]

    jobs_by_status = JobsByStatusResponse(
        pending=get_count(
            jobs_collection.where(filter=FieldFilter("status", "==", "pending"))
        ),
        processing=sum(
            get_count(jobs_collection.where(filter=FieldFilter("status", "==", status)))
            for status in processing_statuses
        ),
        awaiting_review=get_count(
            jobs_collection.where(filter=FieldFilter("status", "==", "awaiting_review"))
        ) + get_count(
            jobs_collection.where(filter=FieldFilter("status", "==", "in_review"))
        ),
        awaiting_instrumental=get_count(
            jobs_collection.where(filter=FieldFilter("status", "==", "awaiting_instrumental_selection"))
        ),
        complete=get_count(
            jobs_collection.where(filter=FieldFilter("status", "==", "complete"))
        ) + get_count(
            jobs_collection.where(filter=FieldFilter("status", "==", "prep_complete"))
        ),
        failed=get_count(
            jobs_collection.where(filter=FieldFilter("status", "==", "failed"))
        ),
        cancelled=get_count(
            jobs_collection.where(filter=FieldFilter("status", "==", "cancelled"))
        ),
    )

    # Credit statistics - sum credits added in last 30 days
    # This is more expensive, so we'll just estimate from users
    total_credits_issued_30d = 0
    try:
        # Get all users and sum recent credit transactions
        users_docs = users_collection.limit(500).stream()
        for user_doc in users_docs:
            user_data = user_doc.to_dict()
            transactions = user_data.get("credit_transactions", [])
            for txn in transactions:
                txn_date = txn.get("created_at")
                if txn_date:
                    # Handle both datetime and string formats
                    if isinstance(txn_date, str):
                        try:
                            txn_date = datetime.fromisoformat(txn_date.replace("Z", "+00:00"))
                        except Exception:
                            continue
                    if isinstance(txn_date, datetime):
                        txn_date = txn_date.replace(tzinfo=None)
                    else:
                        continue
                    if txn_date >= thirty_days_ago:
                        amount = txn.get("amount", 0)
                        if amount > 0:  # Only count additions, not deductions
                            total_credits_issued_30d += amount
    except Exception as e:
        logger.warning(f"Error calculating credits: {e}")

    return AdminStatsOverview(
        total_users=total_users,
        active_users_7d=active_users_7d,
        active_users_30d=active_users_30d,
        total_jobs=total_jobs,
        jobs_last_7d=jobs_last_7d,
        jobs_last_30d=jobs_last_30d,
        jobs_by_status=jobs_by_status,
        total_credits_issued_30d=total_credits_issued_30d,
        total_beta_testers=total_beta_testers,
    )


# =============================================================================
# Audio Search Management Models
# =============================================================================

class AudioSearchResultSummary(BaseModel):
    """Summary of a single audio search result."""
    index: int
    provider: str
    artist: str
    title: str
    is_lossless: bool
    quality: Optional[str] = None
    seeders: Optional[int] = None


class AudioSearchJobSummary(BaseModel):
    """Summary of a job with audio search results."""
    job_id: str
    status: str
    user_email: Optional[str] = None
    audio_search_artist: Optional[str] = None
    audio_search_title: Optional[str] = None
    created_at: Optional[datetime] = None
    results_count: int
    results_summary: List[AudioSearchResultSummary]
    has_lossless: bool
    providers: List[str]


class AudioSearchListResponse(BaseModel):
    """Response for listing audio search jobs."""
    jobs: List[AudioSearchJobSummary]
    total: int


class ClearSearchCacheResponse(BaseModel):
    """Response for clearing search cache."""
    status: str
    job_id: str
    message: str
    previous_status: str
    new_status: str
    results_cleared: int
    flacfetch_cache_cleared: bool = False
    flacfetch_error: Optional[str] = None


class ClearAllCacheResponse(BaseModel):
    """Response for clearing all flacfetch cache."""
    status: str
    message: str
    deleted_count: int


class CacheStatsResponse(BaseModel):
    """Response for cache statistics."""
    count: int
    total_size_bytes: int
    oldest_entry: Optional[str] = None
    newest_entry: Optional[str] = None
    configured: bool


# =============================================================================
# Audio Search Management Endpoints
# =============================================================================

@router.get("/audio-searches", response_model=AudioSearchListResponse)
async def list_audio_searches(
    limit: int = 50,
    status_filter: Optional[str] = None,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    List jobs with audio search results.

    Returns jobs that have cached audio search results, useful for:
    - Monitoring search activity
    - Identifying stale cached results
    - Clearing cache for specific jobs

    Args:
        limit: Maximum number of jobs to return (default 50)
        status_filter: Optional filter by job status (e.g., 'awaiting_audio_selection')
    """
    from google.cloud.firestore_v1 import FieldFilter

    db = user_service.db
    jobs_collection = db.collection("jobs")

    # Query jobs - we'll filter for those with audio_search_results in Python
    # since Firestore can't query for existence of nested fields efficiently
    query = jobs_collection.order_by("created_at", direction="DESCENDING").limit(500)

    if status_filter:
        query = jobs_collection.where(
            filter=FieldFilter("status", "==", status_filter)
        ).order_by("created_at", direction="DESCENDING").limit(500)

    jobs_with_searches = []

    for doc in query.stream():
        data = doc.to_dict()
        state_data = data.get("state_data", {})
        audio_results = state_data.get("audio_search_results", [])

        if not audio_results:
            continue

        # Compute has_lossless and providers from ALL results (not just first 10)
        has_lossless = any(r.get("is_lossless", False) for r in audio_results)
        providers = {r.get("provider", "Unknown") for r in audio_results}

        # Build summary from first 10 results only
        results_summary = []
        for r in audio_results[:10]:
            results_summary.append(AudioSearchResultSummary(
                index=r.get("index", 0),
                provider=r.get("provider", "Unknown"),
                artist=r.get("artist", ""),
                title=r.get("title", ""),
                is_lossless=r.get("is_lossless", False),
                quality=r.get("quality"),
                seeders=r.get("seeders"),
            ))

        jobs_with_searches.append(AudioSearchJobSummary(
            job_id=doc.id,
            status=data.get("status", "unknown"),
            user_email=data.get("user_email"),
            audio_search_artist=data.get("audio_search_artist"),
            audio_search_title=data.get("audio_search_title"),
            created_at=data.get("created_at"),
            results_count=len(audio_results),
            results_summary=results_summary,
            has_lossless=has_lossless,
            providers=sorted(providers),
        ))

        if len(jobs_with_searches) >= limit:
            break

    return AudioSearchListResponse(
        jobs=jobs_with_searches,
        total=len(jobs_with_searches),
    )


@router.post("/audio-searches/{job_id}/clear-cache", response_model=ClearSearchCacheResponse)
async def clear_audio_search_cache(
    job_id: str,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Clear the cached audio search results for a job.

    This will:
    1. Remove the cached search results from job.state_data
    2. Reset the job status to 'pending' so a new search can be performed
    3. Clear the flacfetch GCS cache for this artist/title (if available)

    Use this when:
    - Cached results are stale (e.g., flacfetch was updated)
    - User wants to search again with different terms
    - Results appear incomplete or incorrect
    """
    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Get current state
    state_data = job.state_data or {}
    audio_results = state_data.get("audio_search_results", [])
    results_count = len(audio_results)
    previous_status = job.status

    if not audio_results:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} has no cached audio search results"
        )

    # Validate job status - only allow cache clear for appropriate states
    # Don't allow clearing cache for jobs that are actively processing or complete
    forbidden_statuses = {
        "downloading", "downloading_audio", "searching_audio",
        "separating_stage1", "separating_stage2", "transcribing", "correcting",
        "generating_screens", "applying_padding", "rendering_video",
        "generating_video", "encoding", "packaging", "uploading",
        "complete", "prep_complete",
    }
    if previous_status in forbidden_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot clear cache for job in '{previous_status}' state. "
            f"Only jobs in pending, awaiting_audio_selection, failed, or cancelled states can have cache cleared."
        )

    # Clear the cache by removing the keys
    db = user_service.db
    job_ref = db.collection("jobs").document(job_id)

    from google.cloud.firestore_v1 import DELETE_FIELD

    # Update job: clear cache and reset status
    job_ref.update({
        "state_data.audio_search_results": DELETE_FIELD,
        "state_data.audio_search_count": DELETE_FIELD,
        "state_data.remote_search_id": DELETE_FIELD,
        "status": "pending",
        "progress": 0,
        "message": "Audio search cache cleared by admin. Ready for new search.",
        "updated_at": datetime.utcnow(),
    })

    # Also clear flacfetch's GCS cache if we have artist/title
    flacfetch_cache_cleared = False
    flacfetch_error = None
    artist = job.audio_search_artist
    title = job.audio_search_title

    if artist and title:
        flacfetch_client = get_flacfetch_client()
        if flacfetch_client:
            try:
                flacfetch_cache_cleared = await flacfetch_client.clear_search_cache(artist, title)
                logger.info(
                    f"Cleared flacfetch cache for '{artist}' - '{title}': "
                    f"{'deleted' if flacfetch_cache_cleared else 'no entry found'}"
                )
            except FlacfetchServiceError as e:
                flacfetch_error = str(e)
                logger.warning(f"Failed to clear flacfetch cache: {e}")
        else:
            flacfetch_error = "flacfetch client not configured"
            logger.debug("Skipping flacfetch cache clear - client not configured")
    else:
        flacfetch_error = "missing artist or title"
        logger.debug(f"Skipping flacfetch cache clear - missing artist ({artist}) or title ({title})")

    logger.info(
        f"Admin {auth_data[0]} cleared audio search cache for job {job_id}. "
        f"Cleared {results_count} results. Status changed from {previous_status} to pending. "
        f"Flacfetch cache cleared: {flacfetch_cache_cleared}"
    )

    message = f"Cleared {results_count} cached search results. Job reset to pending."
    if flacfetch_cache_cleared:
        message += " Flacfetch cache also cleared."
    elif flacfetch_error:
        message += f" Note: flacfetch cache not cleared ({flacfetch_error})."

    return ClearSearchCacheResponse(
        status="success",
        job_id=job_id,
        message=message,
        previous_status=previous_status,
        new_status="pending",
        results_cleared=results_count,
        flacfetch_cache_cleared=flacfetch_cache_cleared,
        flacfetch_error=flacfetch_error,
    )


@router.post("/jobs/{job_id}/reset-worker-state")
async def reset_worker_state(
    job_id: str,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """
    Reset stale worker progress state for a job.

    This clears the video_progress, render_progress, and screens_progress
    from state_data, allowing workers to be re-triggered.

    Use this when a job is stuck because worker progress shows 'running'
    from a previous failed attempt.
    """
    from backend.services.job_manager import JobManager

    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Reset worker progress states
    job_manager.update_state_data(job_id, 'video_progress', {'stage': 'pending'})
    job_manager.update_state_data(job_id, 'render_progress', {'stage': 'pending'})
    job_manager.update_state_data(job_id, 'screens_progress', {'stage': 'pending'})

    logger.info(f"Admin {auth_data[0]} reset worker state for job {job_id}")

    return {
        "status": "success",
        "job_id": job_id,
        "message": "Worker progress states reset to pending"
    }


@router.delete("/cache", response_model=ClearAllCacheResponse)
async def clear_all_flacfetch_cache(
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """
    Clear the entire flacfetch search cache.

    This will delete all cached search results from flacfetch's GCS cache.
    Use with caution - this will cause all subsequent searches to hit
    the trackers fresh.

    Note: This does NOT clear Firestore job.state_data caches, only the
    flacfetch-side GCS cache.
    """
    flacfetch_client = get_flacfetch_client()
    if not flacfetch_client:
        raise HTTPException(
            status_code=503,
            detail="flacfetch client not configured"
        )

    try:
        deleted_count = await flacfetch_client.clear_all_cache()
        logger.info(
            f"Admin {auth_data[0]} cleared all flacfetch cache. "
            f"Deleted {deleted_count} entries."
        )
        return ClearAllCacheResponse(
            status="success",
            message=f"Cleared {deleted_count} cache entries from flacfetch.",
            deleted_count=deleted_count,
        )
    except FlacfetchServiceError as e:
        logger.error(f"Failed to clear all flacfetch cache: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to clear flacfetch cache: {e}"
        )


@router.get("/cache/stats", response_model=CacheStatsResponse)
async def get_flacfetch_cache_stats(
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """
    Get statistics about the flacfetch search cache.

    Returns information about:
    - Number of cached entries
    - Total size in bytes
    - Oldest and newest cache entries
    - Whether cache is configured
    """
    flacfetch_client = get_flacfetch_client()
    if not flacfetch_client:
        raise HTTPException(
            status_code=503,
            detail="flacfetch client not configured"
        )

    try:
        stats = await flacfetch_client.get_cache_stats()
        return CacheStatsResponse(
            count=stats.get("count", 0),
            total_size_bytes=stats.get("total_size_bytes", 0),
            oldest_entry=stats.get("oldest_entry"),
            newest_entry=stats.get("newest_entry"),
            configured=stats.get("configured", True),
        )
    except FlacfetchServiceError as e:
        logger.error(f"Failed to get flacfetch cache stats: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to get cache stats: {e}"
        )


# =============================================================================
# Job Completion Message Endpoints (for admin copy/send functionality)
# =============================================================================

class CompletionMessageResponse(BaseModel):
    """Response containing the rendered completion message."""
    job_id: str
    message: str
    subject: str
    youtube_url: Optional[str] = None
    dropbox_url: Optional[str] = None


class SendCompletionEmailRequest(BaseModel):
    """Request to send a completion email."""
    to_email: str
    cc_admin: bool = True


class SendCompletionEmailResponse(BaseModel):
    """Response from sending a completion email."""
    success: bool
    job_id: str
    to_email: str
    message: str


@router.get("/jobs/{job_id}/completion-message", response_model=CompletionMessageResponse)
async def get_job_completion_message(
    job_id: str,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """
    Get the rendered completion message for a job.

    Returns the plain text message that would be sent to the user,
    rendered using the job completion template with the job's details.

    This is useful for:
    - Copying the message to clipboard (e.g., for Fiverr)
    - Previewing the email before sending

    Requires admin authentication.
    """
    from backend.services.job_notification_service import get_job_notification_service

    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Get youtube, dropbox URLs, and brand_code from state_data (may be None)
    state_data = job.state_data or {}
    youtube_url = state_data.get('youtube_url')
    dropbox_url = state_data.get('dropbox_link')
    brand_code = state_data.get('brand_code')

    # Render the completion message
    notification_service = get_job_notification_service()
    message = notification_service.get_completion_message(
        job_id=job.job_id,
        user_name=None,  # Use default "there"
        artist=job.artist,
        title=job.title,
        youtube_url=youtube_url,
        dropbox_url=dropbox_url,
    )

    # Build subject: "NOMAD-1178: Artist - Title (Your karaoke video is ready!)"
    if brand_code and job.artist and job.title:
        subject = f"{brand_code}: {job.artist} - {job.title} (Your karaoke video is ready!)"
    elif job.artist and job.title:
        subject = f"{job.artist} - {job.title} (Your karaoke video is ready!)"
    else:
        subject = "Your karaoke video is ready!"

    return CompletionMessageResponse(
        job_id=job_id,
        message=message,
        subject=subject,
        youtube_url=youtube_url,
        dropbox_url=dropbox_url,
    )


@router.post("/jobs/{job_id}/send-completion-email", response_model=SendCompletionEmailResponse)
async def send_job_completion_email(
    job_id: str,
    request: SendCompletionEmailRequest,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """
    Send a completion email for a job to a specified email address.

    This allows admins to manually send (or re-send) completion emails,
    useful for:
    - Sending to customers who didn't have an email on file
    - Re-sending if the original email was lost
    - Sending to alternate email addresses

    Requires admin authentication.
    """
    from backend.services.job_notification_service import get_job_notification_service
    from backend.services.email_service import get_email_service

    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Get youtube, dropbox URLs, and brand_code from state_data (may be None)
    state_data = job.state_data or {}
    youtube_url = state_data.get('youtube_url')
    dropbox_url = state_data.get('dropbox_link')
    brand_code = state_data.get('brand_code')

    # Render the completion message
    notification_service = get_job_notification_service()
    message = notification_service.get_completion_message(
        job_id=job.job_id,
        user_name=None,  # Use default "there"
        artist=job.artist,
        title=job.title,
        youtube_url=youtube_url,
        dropbox_url=dropbox_url,
    )

    # Send the email
    email_service = get_email_service()
    success = email_service.send_job_completion(
        to_email=request.to_email,
        message_content=message,
        artist=job.artist,
        title=job.title,
        brand_code=brand_code,
        cc_admin=request.cc_admin,
    )

    if success:
        logger.info(f"Admin sent completion email for job {job_id} to {request.to_email}")
        return SendCompletionEmailResponse(
            success=True,
            job_id=job_id,
            to_email=request.to_email,
            message=f"Completion email sent to {request.to_email}",
        )
    else:
        logger.error(f"Failed to send completion email for job {job_id}")
        raise HTTPException(
            status_code=500,
            detail="Failed to send email. Check email service configuration."
        )
