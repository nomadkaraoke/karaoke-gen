"""
Admin API routes for dashboard statistics and management.

Handles:
- Dashboard overview statistics
- System-wide metrics
- Admin-only operations
- Audio search cache management
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, List, Optional, Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.dependencies import require_admin
from backend.services.auth_service import UserType, AuthResult
from backend.services.user_service import get_user_service, UserService, USERS_COLLECTION
from backend.services.job_manager import JobManager
from backend.services.flacfetch_client import get_flacfetch_client, FlacfetchServiceError
from backend.services.storage_service import StorageService
from backend.services.audio_search_service import get_audio_search_service, NoResultsError, AudioSearchError
from backend.models.job import JobStatus
from backend.utils.test_data import is_test_email
from karaoke_gen.utils import sanitize_filename


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


class FileInfo(BaseModel):
    """Information about a single file with signed download URL."""
    name: str
    path: str  # GCS path (gs://bucket/...)
    download_url: str  # Signed URL for download
    category: str  # e.g., "stems", "lyrics", "finals"
    file_key: str  # e.g., "instrumental_clean", "lrc"


class JobFilesResponse(BaseModel):
    """Response containing all files for a job with signed download URLs."""
    job_id: str
    artist: Optional[str]
    title: Optional[str]
    files: List[FileInfo]
    total_files: int


class JobUpdateRequest(BaseModel):
    """Request model for updating job fields."""
    # Editable text fields
    artist: Optional[str] = None
    title: Optional[str] = None
    user_email: Optional[str] = None
    theme_id: Optional[str] = None
    brand_prefix: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    youtube_description: Optional[str] = None
    youtube_description_template: Optional[str] = None
    customer_email: Optional[str] = None
    customer_notes: Optional[str] = None

    # Editable boolean fields
    enable_cdg: Optional[bool] = None
    enable_txt: Optional[bool] = None
    enable_youtube_upload: Optional[bool] = None
    non_interactive: Optional[bool] = None
    prep_only: Optional[bool] = None
    is_private: Optional[bool] = None


class JobUpdateResponse(BaseModel):
    """Response from job update endpoint."""
    status: str
    job_id: str
    updated_fields: List[str]
    message: str


# Fields that are allowed to be updated via PATCH endpoint
EDITABLE_JOB_FIELDS = {
    "artist",
    "title",
    "user_email",
    "theme_id",
    "brand_prefix",
    "discord_webhook_url",
    "youtube_description",
    "youtube_description_template",
    "customer_email",
    "customer_notes",
    "enable_cdg",
    "enable_txt",
    "enable_youtube_upload",
    "non_interactive",
    "prep_only",
    "is_private",
}


# =============================================================================
# Admin Stats Endpoints
# =============================================================================

@router.get("/stats/overview", response_model=AdminStatsOverview)
async def get_admin_stats_overview(
    exclude_test: bool = True,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Get overview statistics for admin dashboard.

    Args:
        exclude_test: If True (default), exclude test data (users with @inbox.testmail.app emails
                     and jobs created by test users) from all counts.

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

    users_collection = db.collection(USERS_COLLECTION)
    jobs_collection = db.collection("jobs")

    # Jobs by status category mapping
    processing_statuses = [
        "downloading", "downloading_audio", "searching_audio", "awaiting_audio_selection",
        "separating_stage1", "separating_stage2", "transcribing", "correcting",
        "generating_screens", "applying_padding", "rendering_video",
        "instrumental_selected", "generating_video", "encoding", "packaging",
        "uploading", "notifying"
    ]

    # Limits for streaming queries - these are safety limits to prevent memory issues
    # If hit, stats may be incomplete so we log a warning
    USERS_STREAM_LIMIT = 2000
    JOBS_STREAM_LIMIT = 10000

    if exclude_test:
        # When excluding test data, we must stream and filter in Python
        # because Firestore doesn't support "not ends with" queries

        # Stream all users and filter
        all_users = []
        users_fetched = 0
        for doc in users_collection.limit(USERS_STREAM_LIMIT).stream():
            users_fetched += 1
            user_data = doc.to_dict()
            email = user_data.get("email", "")
            if not is_test_email(email):
                all_users.append(user_data)

        if users_fetched >= USERS_STREAM_LIMIT:
            logger.warning(f"Users stream hit limit ({USERS_STREAM_LIMIT}), stats may be incomplete")

        # Calculate user stats from filtered list
        total_users = len(all_users)
        active_users_7d = sum(
            1 for u in all_users
            if u.get("last_login_at") and _normalize_datetime(u["last_login_at"]) >= seven_days_ago
        )
        active_users_30d = sum(
            1 for u in all_users
            if u.get("last_login_at") and _normalize_datetime(u["last_login_at"]) >= thirty_days_ago
        )
        # Calculate credits from filtered users
        total_credits_issued_30d = 0
        for user_data in all_users:
            transactions = user_data.get("credit_transactions", [])
            for txn in transactions:
                txn_date = _normalize_datetime(txn.get("created_at"))
                if txn_date and txn_date >= thirty_days_ago:
                    amount = txn.get("amount", 0)
                    if amount > 0:
                        total_credits_issued_30d += amount

        # Stream all jobs and filter by user_email
        all_jobs = []
        jobs_fetched = 0
        for doc in jobs_collection.limit(JOBS_STREAM_LIMIT).stream():
            jobs_fetched += 1
            job_data = doc.to_dict()
            user_email = job_data.get("user_email", "")
            if not is_test_email(user_email):
                all_jobs.append(job_data)

        if jobs_fetched >= JOBS_STREAM_LIMIT:
            logger.warning(f"Jobs stream hit limit ({JOBS_STREAM_LIMIT}), stats may be incomplete")

        # Calculate job stats from filtered list
        total_jobs = len(all_jobs)
        jobs_last_7d = sum(
            1 for j in all_jobs
            if j.get("created_at") and _normalize_datetime(j["created_at"]) >= seven_days_ago
        )
        jobs_last_30d = sum(
            1 for j in all_jobs
            if j.get("created_at") and _normalize_datetime(j["created_at"]) >= thirty_days_ago
        )

        # Jobs by status
        jobs_by_status = JobsByStatusResponse(
            pending=sum(1 for j in all_jobs if j.get("status") == "pending"),
            processing=sum(1 for j in all_jobs if j.get("status") in processing_statuses),
            awaiting_review=sum(1 for j in all_jobs if j.get("status") in ["awaiting_review", "in_review"]),
            awaiting_instrumental=sum(1 for j in all_jobs if j.get("status") == "awaiting_instrumental_selection"),
            complete=sum(1 for j in all_jobs if j.get("status") in ["complete", "prep_complete"]),
            failed=sum(1 for j in all_jobs if j.get("status") == "failed"),
            cancelled=sum(1 for j in all_jobs if j.get("status") == "cancelled"),
        )
    else:
        # When including test data, use efficient aggregation queries
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
        total_users = get_count(users_collection)
        active_users_7d = get_count(
            users_collection.where(filter=FieldFilter("last_login_at", ">=", seven_days_ago))
        )
        active_users_30d = get_count(
            users_collection.where(filter=FieldFilter("last_login_at", ">=", thirty_days_ago))
        )
        # Job statistics
        total_jobs = get_count(jobs_collection)
        jobs_last_7d = get_count(
            jobs_collection.where(filter=FieldFilter("created_at", ">=", seven_days_ago))
        )
        jobs_last_30d = get_count(
            jobs_collection.where(filter=FieldFilter("created_at", ">=", thirty_days_ago))
        )

        # Jobs by status
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
        total_credits_issued_30d = 0
        try:
            users_fetched = 0
            for user_doc in users_collection.limit(USERS_STREAM_LIMIT).stream():
                users_fetched += 1
                user_data = user_doc.to_dict()
                transactions = user_data.get("credit_transactions", [])
                for txn in transactions:
                    txn_date = _normalize_datetime(txn.get("created_at"))
                    if txn_date and txn_date >= thirty_days_ago:
                        amount = txn.get("amount", 0)
                        if amount > 0:
                            total_credits_issued_30d += amount
            if users_fetched >= USERS_STREAM_LIMIT:
                logger.warning(f"Credit calculation hit user limit ({USERS_STREAM_LIMIT}), total may be incomplete")
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
    )


def _normalize_datetime(dt_value) -> Optional[datetime]:
    """Normalize datetime values from Firestore (can be datetime or ISO string)."""
    if dt_value is None:
        return None
    if isinstance(dt_value, datetime):
        return dt_value.replace(tzinfo=None)
    if isinstance(dt_value, str):
        try:
            parsed = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None)
        except Exception:
            return None
    return None


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
    exclude_test: bool = True,
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
        exclude_test: If True (default), exclude jobs from test users
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

        # Filter out test users if exclude_test is True
        if exclude_test and is_test_email(data.get("user_email", "")):
            continue

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


# =============================================================================
# Job Files Endpoint
# =============================================================================

def _extract_files_recursive(
    file_urls: Dict[str, Any],
    storage: StorageService,
    category: str = "",
    expiration_minutes: int = 120,
) -> List[FileInfo]:
    """
    Recursively extract files from nested file_urls structure.

    Only includes entries that are GCS paths (gs://...).
    Skips non-GCS entries like YouTube URLs.

    Args:
        file_urls: Dictionary of file URLs (may be nested)
        storage: StorageService instance for generating signed URLs
        category: Current category name (for nested calls)
        expiration_minutes: How long signed URLs should be valid

    Returns:
        List of FileInfo objects with signed download URLs
    """
    files = []

    for key, value in file_urls.items():
        if isinstance(value, dict):
            # Nested structure - recurse with key as category
            nested_files = _extract_files_recursive(
                value,
                storage,
                category=key if not category else f"{category}.{key}",
                expiration_minutes=expiration_minutes,
            )
            files.extend(nested_files)
        elif isinstance(value, str):
            # Check if it's a GCS path (gs:// URI or relative path starting with "jobs/")
            # Skip non-GCS values like YouTube URLs, video IDs, etc.
            blob_path = None
            if value.startswith("gs://"):
                # Full GCS URI - extract blob path after bucket name
                # Format: gs://bucket-name/path/to/file
                parts = value.replace("gs://", "").split("/", 1)
                if len(parts) > 1:
                    blob_path = parts[1]
            elif value.startswith("jobs/"):
                # Relative path within bucket - use directly
                blob_path = value

            if blob_path:
                try:
                    signed_url = storage.generate_signed_url(blob_path, expiration_minutes=expiration_minutes)
                    # Extract filename from path
                    name = blob_path.split("/")[-1] if "/" in blob_path else blob_path
                    files.append(FileInfo(
                        name=name,
                        path=value,
                        download_url=signed_url,
                        category=category,
                        file_key=key,
                    ))
                except Exception as e:
                    # Log but don't fail - file might not exist
                    logger.warning(f"Failed to generate signed URL for {value}: {e}")

    return files


@router.get("/jobs/{job_id}/files", response_model=JobFilesResponse)
async def get_job_files(
    job_id: str,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """
    Get all files for a job with signed download URLs.

    Returns a list of all files associated with the job, including:
    - Input audio file
    - Stem separation results (vocals, instrumentals, etc.)
    - Lyrics files (LRC, ASS, corrections JSON)
    - Screen files (title, end screens)
    - Video files (with/without vocals)
    - Final output files (various formats)
    - Package files (CDG, TXT zips)

    Each file includes a signed URL that's valid for 2 hours.
    Non-GCS entries (like YouTube URLs) are excluded.

    Requires admin authentication.
    """
    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Extract all files with signed URLs
    try:
        storage = StorageService()
        file_urls = job.file_urls or {}
        files = _extract_files_recursive(file_urls, storage)
    except Exception as e:
        # Log but don't fail - return empty list if file extraction fails
        logger.error(f"Failed to extract files for job {job_id}: {e}")
        files = []

    return JobFilesResponse(
        job_id=job.job_id,
        artist=job.artist,
        title=job.title,
        files=files,
        total_files=len(files),
    )


@router.patch("/jobs/{job_id}", response_model=JobUpdateResponse)
async def update_job(
    job_id: str,
    request: Dict[str, Any],
    auth_data: AuthResult = Depends(require_admin),
):
    """
    Update editable fields of a job (admin only).

    This endpoint allows admins to update certain job fields without
    affecting the job's processing state. It's useful for:
    - Correcting artist/title typos
    - Changing user assignment
    - Updating delivery settings (email, theme, etc.)

    Editable fields:
    - artist, title: Track metadata
    - user_email: Job owner
    - theme_id: Visual theme
    - enable_cdg, enable_txt, enable_youtube_upload: Output options
    - customer_email, customer_notes: Made-for-you order info
    - brand_prefix: Brand code prefix
    - non_interactive, prep_only: Workflow options
    - is_private: Private (non-published) track mode
    - discord_webhook_url: Notification URL
    - youtube_description, youtube_description_template: YouTube settings

    Non-editable fields (will return 400 error):
    - job_id, status, progress: System-managed
    - created_at, updated_at: Timestamps
    - state_data, file_urls, timeline: Processing state
    - worker_logs, worker_ids: Audit/tracking data

    For status changes, use the reset endpoint instead.
    """
    admin_email = auth_data.user_email or "unknown"

    # Check for non-editable fields in request
    non_editable_fields = set(request.keys()) - EDITABLE_JOB_FIELDS
    if non_editable_fields:
        raise HTTPException(
            status_code=400,
            detail=f"The following fields are not editable: {', '.join(sorted(non_editable_fields))}. "
            f"Editable fields are: {', '.join(sorted(EDITABLE_JOB_FIELDS))}"
        )

    # Filter to only include provided fields (non-None values)
    updates = {k: v for k, v in request.items() if v is not None}

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="No valid fields provided for update. "
            f"Editable fields are: {', '.join(sorted(EDITABLE_JOB_FIELDS))}"
        )

    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Check if we're toggling is_private from False to True on a completed job
    # If so, auto-delete existing outputs (YouTube, Dropbox, GDrive)
    toggling_to_private = (
        "is_private" in updates
        and updates["is_private"] is True
        and not getattr(job, 'is_private', False)
    )

    # Perform the update (update_job returns None; exceptions indicate failure)
    try:
        job_manager.update_job(job_id, updates)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to update job. Please try again."
        ) from e

    # Auto-delete outputs when toggling to private on a job with existing outputs
    auto_deleted = False
    if toggling_to_private and job.status in TERMINAL_STATES and not job.outputs_deleted_at:
        state_data = job.state_data or {}
        has_outputs = (
            state_data.get('youtube_url')
            or state_data.get('dropbox_link')
            or state_data.get('gdrive_files')
        )
        if has_outputs:
            try:
                logger.info(
                    f"Auto-deleting outputs for job {job_id} (toggled to private by {admin_email})"
                )
                # Call the delete-outputs endpoint directly (same module)
                await delete_job_outputs(job_id, auth_data)
                auto_deleted = True
            except HTTPException:
                # delete_job_outputs raises HTTPException on validation failures,
                # but we've already validated terminal state + not-already-deleted
                logger.warning(f"Auto-delete outputs skipped for job {job_id} (validation)")
            except Exception as e:
                logger.warning(f"Auto-delete outputs failed for job {job_id}: {e}")

    # Log the admin action
    logger.info(
        f"Admin {admin_email} updated job {job_id}. "
        f"Updated fields: {list(updates.keys())}"
        f"{' (outputs auto-deleted)' if auto_deleted else ''}"
    )

    message = f"Successfully updated {len(updates)} field(s)"
    if auto_deleted:
        message += ". Existing outputs were auto-deleted (job set to private)."

    return JobUpdateResponse(
        status="success",
        job_id=job_id,
        updated_fields=list(updates.keys()),
        message=message,
    )


# =============================================================================
# Job Reset Endpoint
# =============================================================================

class JobResetRequest(BaseModel):
    """Request model for resetting a job to a specific state."""
    target_state: str


class JobResetResponse(BaseModel):
    """Response from job reset endpoint."""
    status: str
    job_id: str
    previous_status: str
    new_status: str
    message: str
    cleared_data: List[str]
    worker_triggered: Optional[bool] = None  # Was worker auto-triggered? (only for instrumental_selected)
    worker_trigger_error: Optional[str] = None  # Error message if trigger failed


class TriggerWorkerRequest(BaseModel):
    """Request to trigger a worker for a job."""
    worker_type: str = "video"  # Currently only video supported


class TriggerWorkerResponse(BaseModel):
    """Response from trigger worker endpoint."""
    status: str
    job_id: str
    worker_type: str
    triggered: bool
    message: str
    error: Optional[str] = None


# States that are allowed as reset targets
ALLOWED_RESET_STATES = {
    "pending",
    "awaiting_audio_selection",
    "awaiting_review",  # Combined review (lyrics + instrumental selection)
    # Note: awaiting_instrumental_selection removed - deprecated by combined review flow (Jan 2026)
    "instrumental_selected",  # Re-run video generation with same settings
}

# State data keys to clear for each reset target
# Keys not in this mapping are preserved
# Note: encoding_progress is included in all states because workers check {worker}_progress.stage
# to determine if they should skip (idempotency). Stale 'complete' markers cause workers to skip.
STATE_DATA_CLEAR_KEYS = {
    "pending": [
        "audio_search_results",
        "audio_search_count",
        "remote_search_id",
        "audio_selection",
        "review_complete",
        "corrected_lyrics",
        "instrumental_selection",
        "video_progress",
        "render_progress",
        "screens_progress",
        "encoding_progress",
        # Parallel processing flags - must clear to allow workers to run again
        "audio_complete",
        "lyrics_complete",
    ],
    "awaiting_audio_selection": [
        "audio_selection",
        "review_complete",
        "corrected_lyrics",
        "instrumental_selection",
        "video_progress",
        "render_progress",
        "screens_progress",
        "encoding_progress",
    ],
    "awaiting_review": [
        # Combined review - user reviews lyrics AND selects instrumental in one step
        "review_complete",
        "corrected_lyrics",
        "instrumental_selection",
        "video_progress",
        "render_progress",
        "screens_progress",
        "encoding_progress",
    ],
    # Note: awaiting_instrumental_selection removed - deprecated by combined review flow (Jan 2026)
    "instrumental_selected": [
        # Clear video/encoding/distribution state to allow re-processing
        # Preserves: instrumental_selection, lyrics_metadata, review data
        "video_progress",
        "render_progress",
        "screens_progress",
        "encoding_progress",
        "distribution",
        "brand_code",
        "youtube_url",
        "youtube_video_id",
        "dropbox_link",
        "gdrive_files",
    ],
}


@router.post("/jobs/{job_id}/reset", response_model=JobResetResponse)
async def reset_job(
    job_id: str,
    request: JobResetRequest,
    auth_data: AuthResult = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Reset a job to a specific state for re-processing (admin only).

    This endpoint allows admins to reset a job back to specific workflow
    checkpoints to re-do parts of the processing. This is useful for:
    - Re-running audio search after flacfetch updates
    - Re-reviewing lyrics after corrections
    - Re-selecting instrumental after hearing the result
    - Restarting a failed job from the beginning

    Allowed target states:
    - pending: Restart from the beginning (clears all processing data)
    - awaiting_audio_selection: Re-select audio source
    - awaiting_review: Re-review lyrics (preserves audio stems)
    - awaiting_instrumental_selection: Re-select instrumental (preserves review)

    State data is cleared based on the target state to ensure a clean
    re-processing from that point forward.
    """
    admin_email = auth_data.user_email or "unknown"
    target_state = request.target_state.lower()

    # Validate target state
    if target_state not in ALLOWED_RESET_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target state '{target_state}'. "
            f"Allowed states are: {', '.join(sorted(ALLOWED_RESET_STATES))}"
        )

    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    previous_status = job.status

    # Build update payload
    updates = {
        "status": target_state,
        "progress": 0,
        "message": f"Job reset to {target_state} by admin",
        "updated_at": datetime.utcnow().isoformat(),
    }

    # Clear state data keys based on target state
    keys_to_clear = STATE_DATA_CLEAR_KEYS.get(target_state, [])
    cleared_keys = []
    current_state_data = job.state_data or {}

    for key in keys_to_clear:
        if key in current_state_data:
            cleared_keys.append(key)

    # Add timeline event
    timeline_event = {
        "status": target_state,
        "timestamp": datetime.utcnow().isoformat(),
        "message": f"Admin reset from {previous_status} to {target_state}",
    }

    # Perform the update with state_data clearing
    # Note: update_job() raises exceptions on failure, returns None on success
    job_manager.update_job(job_id, updates)

    # Clear the state data keys and error state using direct Firestore update
    from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion

    job_ref = user_service.db.collection("jobs").document(job_id)

    # Build the update payload
    clear_updates = {}

    # Clear state_data keys
    if cleared_keys:
        for key in cleared_keys:
            clear_updates[f"state_data.{key}"] = DELETE_FIELD

    # Always clear error state on reset (confusing to have old errors after reset)
    clear_updates["error_message"] = DELETE_FIELD
    clear_updates["error_details"] = DELETE_FIELD

    # Add timeline event
    clear_updates["timeline"] = ArrayUnion([timeline_event])

    # Execute the update
    job_ref.update(clear_updates)

    # Debug logging to verify keys were actually cleared
    logger.info(
        f"Admin {admin_email} reset job {job_id} from {previous_status} to {target_state}. "
        f"Attempted to clear state_data keys: {cleared_keys}"
    )

    # Verify the keys were actually deleted (debug check)
    job_after = job_manager.get_job(job_id)
    if job_after and cleared_keys:
        remaining_keys = [k for k in cleared_keys if k in (job_after.state_data or {})]
        if remaining_keys:
            logger.error(
                f"Admin reset job {job_id}: Some keys were NOT cleared: {remaining_keys}. "
                f"Current state_data keys: {list((job_after.state_data or {}).keys())}"
            )
        else:
            logger.debug(f"Admin reset job {job_id}: Verified all keys were cleared successfully")

    # Trigger video worker for states that should auto-continue
    # instrumental_selected: Re-run video generation with same instrumental selection
    worker_triggered: Optional[bool] = None
    worker_trigger_error: Optional[str] = None
    if target_state == "instrumental_selected":
        try:
            from backend.services.worker_service import get_worker_service
            worker_service = get_worker_service()
            worker_triggered = await worker_service.trigger_video_worker(job_id)
            if worker_triggered:
                logger.info(f"Admin reset: Triggered video worker for job {job_id}")
            else:
                worker_trigger_error = "Worker trigger returned False - check worker service logs"
                logger.warning(f"Admin reset: Failed to trigger video worker for job {job_id}")
        except Exception as e:
            worker_triggered = False
            worker_trigger_error = str(e)
            logger.error(f"Admin reset: Exception triggering video worker for job {job_id}: {e}", exc_info=True)

    message = f"Job reset from {previous_status} to {target_state}"
    if worker_triggered:
        message += " (video worker triggered)"
    elif target_state == "instrumental_selected" and not worker_triggered:
        message += " (WARNING: video worker NOT triggered - use manual trigger)"

    return JobResetResponse(
        status="success",
        job_id=job_id,
        previous_status=previous_status,
        new_status=target_state,
        message=message,
        cleared_data=cleared_keys,
        worker_triggered=worker_triggered,
        worker_trigger_error=worker_trigger_error,
    )


# =============================================================================
# Trigger Worker Endpoint
# =============================================================================

@router.post("/jobs/{job_id}/trigger-worker", response_model=TriggerWorkerResponse)
async def trigger_worker(
    job_id: str,
    request: TriggerWorkerRequest,
    auth_data: AuthResult = Depends(require_admin),
):
    """
    Manually trigger a worker for a job (admin only).

    Use this when:
    - Auto-trigger fails after a reset to instrumental_selected
    - Need to re-run processing without resetting state
    - Debugging worker trigger issues

    Currently supports:
    - video: Triggers the video generation worker

    The job should be in an appropriate state for the worker type:
    - video: Job should be in 'instrumental_selected' status

    Args:
        job_id: Job ID to trigger worker for
        request: Contains worker_type (default: "video")

    Returns:
        Trigger result with success/failure and error details
    """
    admin_email = auth_data.user_email or "unknown"
    worker_type = request.worker_type.lower()

    # Validate worker type
    if worker_type not in ["video"]:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported worker type '{worker_type}'. Supported: video"
        )

    # Get job to validate it exists
    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Warn if job is not in the expected state (but don't block)
    expected_status = "instrumental_selected"
    if job.status != expected_status:
        logger.warning(
            f"Admin {admin_email} triggering {worker_type} worker for job {job_id} "
            f"in unexpected status '{job.status}' (expected '{expected_status}')"
        )

    # Trigger the worker
    triggered = False
    error_msg = None

    try:
        from backend.services.worker_service import get_worker_service
        worker_service = get_worker_service()

        if worker_type == "video":
            triggered = await worker_service.trigger_video_worker(job_id)

        if triggered:
            logger.info(
                f"Admin {admin_email} manually triggered {worker_type} worker for job {job_id}"
            )
            message = f"Successfully triggered {worker_type} worker"
        else:
            error_msg = "Worker service returned False - check service logs"
            logger.warning(
                f"Admin {admin_email} failed to trigger {worker_type} worker for job {job_id}: "
                f"service returned False"
            )
            message = f"Failed to trigger {worker_type} worker"

    except Exception as e:
        triggered = False
        error_msg = str(e)
        logger.error(
            f"Admin {admin_email} exception triggering {worker_type} worker for job {job_id}: {e}",
            exc_info=True
        )
        message = f"Error triggering {worker_type} worker"

    return TriggerWorkerResponse(
        status="success" if triggered else "error",
        job_id=job_id,
        worker_type=worker_type,
        triggered=triggered,
        message=message,
        error=error_msg,
    )


# =============================================================================
# Prepare Review Audio Endpoint
# =============================================================================

class PrepareReviewAudioResponse(BaseModel):
    """Response from prepare-review-audio endpoint."""
    status: str
    job_id: str
    transcoded_files: List[str]
    message: str


@router.post("/jobs/{job_id}/prepare-review-audio", response_model=PrepareReviewAudioResponse)
async def prepare_review_audio(
    job_id: str,
    auth_data: AuthResult = Depends(require_admin),
):
    """
    Transcode review audio to OGG Opus for a job (admin only).

    Use this to backfill transcoded audio for existing jobs that were
    created before eager transcoding was deployed. Transcodes the main
    input audio and all stem files to OGG Opus 128kbps.

    Idempotent: skips files that are already transcoded.
    """
    admin_email = auth_data.user_email or "unknown"

    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    try:
        import asyncio
        from backend.services.audio_transcoding_service import AudioTranscodingService
        transcoding = AudioTranscodingService()
        transcoded = await asyncio.to_thread(transcoding.prepare_review_audio_for_job, job)

        logger.info(
            f"Admin {admin_email} prepared review audio for job {job_id}: "
            f"{len(transcoded)} files transcoded"
        )

        return PrepareReviewAudioResponse(
            status="success",
            job_id=job_id,
            transcoded_files=transcoded,
            message=f"Transcoded {len(transcoded)} files to OGG Opus",
        )

    except Exception as e:
        logger.error(
            f"Admin {admin_email} failed to prepare review audio for job {job_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to prepare review audio: {str(e)}",
        )


# =============================================================================
# Clear Worker State Endpoint
# =============================================================================

class ClearWorkersResponse(BaseModel):
    """Response from clear workers endpoint."""
    status: str
    job_id: str
    message: str
    cleared_keys: List[str]


# All worker progress keys that can be cleared
ALL_WORKER_PROGRESS_KEYS = [
    "audio_progress",
    "lyrics_progress",
    "render_progress",
    "screens_progress",
    "video_progress",
    "encoding_progress",
]


@router.post("/jobs/{job_id}/clear-workers", response_model=ClearWorkersResponse)
async def clear_worker_state(
    job_id: str,
    auth_data: AuthResult = Depends(require_admin),
):
    """
    Clear all worker completion markers to allow re-execution (admin only).

    This is an escape hatch for edge cases where you need workers to run again
    without doing a full status reset. It clears all *_progress keys from state_data.

    Use cases:
    - Worker was interrupted and left stale "running" progress
    - Worker completed but needs to run again (e.g., after code fix)
    - Debugging worker idempotency issues

    Note: This does NOT change the job status. If the job is in a terminal state
    (complete, failed, cancelled), workers may not be triggered automatically.
    Use the reset endpoint to also change status if needed.
    """
    from google.cloud.firestore_v1 import DELETE_FIELD

    admin_email = auth_data.user_email or "unknown"
    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Check which keys actually exist in state_data
    state_data = job.state_data or {}
    keys_to_clear = [k for k in ALL_WORKER_PROGRESS_KEYS if k in state_data]

    if not keys_to_clear:
        return ClearWorkersResponse(
            status="success",
            job_id=job_id,
            message="No worker progress keys found in state_data",
            cleared_keys=[],
        )

    # Clear the worker progress keys
    db = JobManager().firestore.db
    job_ref = db.collection("jobs").document(job_id)
    update_payload = {f"state_data.{key}": DELETE_FIELD for key in keys_to_clear}
    job_ref.update(update_payload)

    logger.info(
        f"Admin {admin_email} cleared worker state for job {job_id}. "
        f"Cleared keys: {keys_to_clear}"
    )

    return ClearWorkersResponse(
        status="success",
        job_id=job_id,
        message=f"Cleared {len(keys_to_clear)} worker progress key(s)",
        cleared_keys=keys_to_clear,
    )


# =============================================================================
# Delete Job Outputs Endpoint
# =============================================================================

class DeleteOutputsResponse(BaseModel):
    """Response from delete job outputs endpoint."""
    status: str
    job_id: str
    message: str
    deleted_services: Dict[str, Any]  # youtube, dropbox, gdrive results
    cleared_state_data: List[str]
    outputs_deleted_at: str


# State data keys to clear when deleting outputs
OUTPUT_STATE_DATA_KEYS = [
    "youtube_url",
    "youtube_video_id",
    "dropbox_link",
    "brand_code",
    "gdrive_files",
]


# Terminal states that allow output deletion
TERMINAL_STATES = {"complete", "prep_complete", "failed", "cancelled"}


@router.post("/jobs/{job_id}/delete-outputs", response_model=DeleteOutputsResponse)
async def delete_job_outputs(
    job_id: str,
    auth_data: AuthResult = Depends(require_admin),
):
    """
    Delete all distributed outputs for a job (admin only).

    This endpoint deletes:
    1. YouTube video (if uploaded)
    2. Dropbox folder (if uploaded)
    3. Google Drive files (if uploaded)
    4. Recycles brand code for reuse (if Dropbox + GDrive cleanup succeeded)

    The job record is preserved with outputs_deleted_at timestamp set.
    State data related to distribution is cleared.

    Use case: Delete outputs for quality issues, then reset job to
    awaiting_review or awaiting_instrumental_selection to re-process.

    Args:
        job_id: Job ID to delete outputs for

    Returns:
        Deletion results for each service
    """
    import re
    from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion

    admin_email = auth_data.user_email or "unknown"
    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Verify job is in a terminal state
    if job.status not in TERMINAL_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Can only delete outputs from jobs in terminal states. "
            f"Current status: {job.status}. Allowed: {', '.join(sorted(TERMINAL_STATES))}"
        )

    # Check if outputs already deleted
    if job.outputs_deleted_at:
        raise HTTPException(
            status_code=400,
            detail=f"Outputs were already deleted at {job.outputs_deleted_at}"
        )

    state_data = job.state_data or {}
    results = {
        "youtube": {"status": "skipped", "reason": "no youtube_url in state_data"},
        "dropbox": {"status": "skipped", "reason": "no brand_code or dropbox_path"},
        "gdrive": {"status": "skipped", "reason": "no gdrive_files in state_data"},
    }

    # Clean up YouTube
    youtube_url = state_data.get('youtube_url')
    if youtube_url:
        try:
            video_id_match = re.search(r'(?:youtu\.be/|youtube\.com/watch\?v=)([^&\s]+)', youtube_url)
            if video_id_match:
                video_id = video_id_match.group(1)

                from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
                from backend.services.youtube_service import get_youtube_service

                youtube_service = get_youtube_service()
                if youtube_service.is_configured:
                    finalise = KaraokeFinalise(
                        dry_run=False,
                        non_interactive=True,
                        user_youtube_credentials=youtube_service.get_credentials_dict()
                    )
                    success = finalise.delete_youtube_video(video_id)
                    results["youtube"] = {
                        "status": "success" if success else "failed",
                        "video_id": video_id
                    }
                else:
                    results["youtube"] = {"status": "skipped", "reason": "YouTube credentials not configured"}
            else:
                results["youtube"] = {"status": "failed", "reason": f"Could not extract video ID from {youtube_url}"}
        except Exception as e:
            logger.error(f"Error deleting YouTube video for job {job_id}: {e}", exc_info=True)
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
                results["dropbox"] = {"status": "skipped", "reason": "Dropbox credentials not configured"}
        except Exception as e:
            logger.error(f"Error deleting Dropbox folder for job {job_id}: {e}", exc_info=True)
            results["dropbox"] = {"status": "error", "error": str(e)}

    # Clean up Google Drive
    gdrive_files = state_data.get('gdrive_files')
    if gdrive_files:
        try:
            from backend.services.gdrive_service import get_gdrive_service
            gdrive = get_gdrive_service()
            if gdrive.is_configured:
                file_ids = list(gdrive_files.values()) if isinstance(gdrive_files, dict) else []
                delete_results = gdrive.delete_files(file_ids)
                all_success = all(delete_results.values())
                results["gdrive"] = {
                    "status": "success" if all_success else "partial",
                    "files": delete_results
                }
            else:
                results["gdrive"] = {"status": "skipped", "reason": "Google Drive credentials not configured"}
        except Exception as e:
            logger.error(f"Error deleting Google Drive files for job {job_id}: {e}", exc_info=True)
            results["gdrive"] = {"status": "error", "error": str(e)}

    # Clean up GCS finals folder - prevents stale files from being picked up during re-encoding
    try:
        from backend.services.storage_service import StorageService
        storage = StorageService()
        finals_prefix = f"jobs/{job_id}/finals/"
        deleted_count = storage.delete_folder(finals_prefix)
        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} files from GCS finals folder for job {job_id}")
        results["gcs_finals"] = {
            "status": "success",
            "deleted_count": deleted_count,
            "path": finals_prefix,
        }
    except Exception as e:
        logger.error(f"Error deleting GCS finals folder for job {job_id}: {e}", exc_info=True)
        results["gcs_finals"] = {"status": "error", "error": str(e)}

    # Recycle brand code if Dropbox and GDrive cleanup both succeeded
    dropbox_success = results["dropbox"]["status"] in ("success", "skipped")
    gdrive_success = results["gdrive"]["status"] in ("success", "skipped")
    if brand_code and dropbox_success and gdrive_success:
        try:
            from backend.services.brand_code_service import BrandCodeService, get_brand_code_service
            prefix, number = BrandCodeService.parse_brand_code(brand_code)
            get_brand_code_service().recycle_brand_code(prefix, number)
            results["brand_code"] = {"status": "recycled", "code": brand_code}
            logger.info(f"Recycled brand code {brand_code} during delete-outputs for job {job_id}")
        except (ValueError, Exception) as e:
            logger.warning(f"Failed to recycle brand code {brand_code}: {e}")
            results["brand_code"] = {"status": "failed", "error": str(e)}
    elif brand_code:
        logger.warning(f"Brand code {brand_code} NOT recycled: cleanup incomplete (dropbox={results['dropbox']['status']}, gdrive={results['gdrive']['status']})")
        results["brand_code"] = {"status": "skipped", "reason": "Dropbox/GDrive cleanup incomplete"}

    # Update job record
    deletion_timestamp = datetime.now(timezone.utc)
    user_service = get_user_service()
    db = user_service.db
    job_ref = db.collection("jobs").document(job_id)

    update_payload = {
        "outputs_deleted_at": deletion_timestamp,
        "outputs_deleted_by": admin_email,
        "updated_at": deletion_timestamp,
    }

    # Clear distribution-related state_data keys
    cleared_keys = []
    for key in OUTPUT_STATE_DATA_KEYS:
        if key in state_data:
            update_payload[f"state_data.{key}"] = DELETE_FIELD
            cleared_keys.append(key)

    # Add timeline event
    timeline_event = {
        "status": job.status,  # Keep current status
        "timestamp": deletion_timestamp.isoformat(),
        "message": f"Outputs deleted by admin ({admin_email})",
    }
    update_payload["timeline"] = ArrayUnion([timeline_event])

    job_ref.update(update_payload)

    # Determine overall status based on per-service results
    error_services = [s for s, r in results.items() if r["status"] == "error"]
    failed_services = [s for s, r in results.items() if r["status"] == "failed"]
    success_services = [s for s, r in results.items() if r["status"] == "success"]

    if error_services:
        overall_status = "partial_success" if success_services else "error"
        error_details = "; ".join(
            f"{s}: {results[s].get('error', 'unknown error')}" for s in error_services
        )
        message = f"Some services failed: {error_details}"
    elif failed_services:
        overall_status = "partial_success" if success_services else "failed"
        message = f"Some deletions failed: {', '.join(failed_services)}"
    else:
        overall_status = "success"
        message = "Outputs deleted successfully"

    brand_code_status = results.get('brand_code', {}).get('status', 'n/a')
    logger.info(
        f"Admin {admin_email} deleted outputs for job {job_id}. "
        f"YouTube: {results['youtube']['status']}, "
        f"Dropbox: {results['dropbox']['status']}, "
        f"GDrive: {results['gdrive']['status']}, "
        f"GCS Finals: {results['gcs_finals']['status']}, "
        f"Brand Code: {brand_code_status}. "
        f"Cleared state_data keys: {cleared_keys}"
    )

    return DeleteOutputsResponse(
        status=overall_status,
        job_id=job_id,
        message=message,
        deleted_services=results,
        cleared_state_data=cleared_keys,
        outputs_deleted_at=deletion_timestamp.isoformat(),
    )


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
        is_private=getattr(job, 'is_private', False),
    )

    # Build subject: "NOMAD-1178: Artist - Title (Your karaoke video is ready!)"
    # Sanitize artist/title to handle Unicode characters (curly quotes, em dashes, etc.)
    # that cause email header encoding issues (MIME headers use latin-1)
    safe_artist = sanitize_filename(job.artist) if job.artist else None
    safe_title = sanitize_filename(job.title) if job.title else None
    if brand_code and safe_artist and safe_title:
        subject = f"{brand_code}: {safe_artist} - {safe_title} (Your karaoke video is ready!)"
    elif safe_artist and safe_title:
        subject = f"{safe_artist} - {safe_title} (Your karaoke video is ready!)"
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
        is_private=getattr(job, 'is_private', False),
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


# =============================================================================
# User Impersonation
# =============================================================================

class ImpersonateUserResponse(BaseModel):
    """Response from impersonate user endpoint."""
    session_token: str
    user_email: str
    message: str


@router.post("/users/{email}/impersonate", response_model=ImpersonateUserResponse)
async def impersonate_user(
    email: str,
    auth_data: AuthResult = Depends(require_admin),
    user_service: UserService = Depends(get_user_service),
):
    """
    Create a session token to impersonate a user (admin only).

    This allows admins to view the application exactly as a specific user would see it.
    The admin's original session remains valid and can be restored client-side.

    Security:
    - Only admins can impersonate
    - Creates a real session (auditable in Firestore)
    - Impersonation is logged for security audit

    Args:
        email: Email of the user to impersonate

    Returns:
        session_token: A valid session token for the target user
        user_email: The impersonated user's email
        message: Success message
    """
    admin_email = auth_data.user_email or "unknown"
    target_email = email.lower()

    # Cannot impersonate yourself
    if target_email == admin_email.lower():
        raise HTTPException(
            status_code=400,
            detail="Cannot impersonate yourself"
        )

    # Verify target user exists
    target_user = user_service.get_user(target_email)
    if not target_user:
        raise HTTPException(
            status_code=404,
            detail=f"User {target_email} not found"
        )

    # Create a real session for the target user
    session = user_service.create_session(
        user_email=target_email,
        ip_address=None,  # Not tracking IP for impersonation
        user_agent=f"Impersonation by {admin_email}",
    )

    # Log impersonation for audit trail
    logger.info(
        f"IMPERSONATION: Admin {admin_email} started impersonating user {target_email}. "
        f"Session token prefix: {session.token[:12]}..."
    )

    return ImpersonateUserResponse(
        session_token=session.token,
        user_email=target_email,
        message=f"Now impersonating {target_email}",
    )


# =============================================================================
# Regenerate Screens Endpoint
# =============================================================================

class RegenerateScreensResponse(BaseModel):
    """Response from regenerate screens endpoint."""
    status: str
    job_id: str
    message: str
    previous_screens_deleted: bool
    worker_triggered: bool
    error: Optional[str] = None


# States where regenerate screens is allowed
REGENERATE_SCREENS_ALLOWED_STATES = {
    "complete",
    "failed",
    "awaiting_review",
    "awaiting_instrumental_selection",
    "instrumental_selected",
    "prep_complete",
}


@router.post("/jobs/{job_id}/regenerate-screens", response_model=RegenerateScreensResponse)
async def regenerate_screens(
    job_id: str,
    auth_data: AuthResult = Depends(require_admin),
):
    """
    Regenerate title and end screens with current artist/title metadata (admin only).

    Use this when you've edited the artist or title fields and need the
    title/end screens to reflect the new metadata.

    This endpoint:
    1. Validates the job is in an appropriate state
    2. Deletes existing screen files from GCS
    3. Triggers the screens worker to regenerate with current metadata
    4. Returns immediately (does not wait for completion)

    Monitor progress via the job detail page or logs.

    Allowed states: complete, failed, awaiting_review, awaiting_instrumental_selection,
    instrumental_selected, prep_complete
    """
    admin_email = auth_data.user_email or "unknown"
    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Validate job state
    if job.status not in REGENERATE_SCREENS_ALLOWED_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot regenerate screens for job in '{job.status}' state. "
            f"Allowed states: {', '.join(sorted(REGENERATE_SCREENS_ALLOWED_STATES))}"
        )

    # Validate job has artist and title
    if not job.artist or not job.title:
        raise HTTPException(
            status_code=400,
            detail="Job must have artist and title set to regenerate screens"
        )

    # Check that audio and lyrics processing completed at some point
    state_data = job.state_data or {}
    audio_progress = state_data.get('audio_progress', {})
    lyrics_progress = state_data.get('lyrics_progress', {})

    if audio_progress.get('stage') != 'audio_complete':
        raise HTTPException(
            status_code=400,
            detail="Audio processing must be complete before regenerating screens"
        )

    if lyrics_progress.get('stage') != 'lyrics_complete':
        raise HTTPException(
            status_code=400,
            detail="Lyrics processing must be complete before regenerating screens"
        )

    # Delete existing screen files from GCS
    storage = StorageService()
    screens_deleted = False
    try:
        # Delete screen video and image files
        screen_paths = [
            f"jobs/{job_id}/screens/title.mov",
            f"jobs/{job_id}/screens/title.jpg",
            f"jobs/{job_id}/screens/title.png",
            f"jobs/{job_id}/screens/end.mov",
            f"jobs/{job_id}/screens/end.jpg",
            f"jobs/{job_id}/screens/end.png",
        ]
        for path in screen_paths:
            try:
                storage.delete_file(path)
            except Exception:
                pass  # File may not exist
        screens_deleted = True
        logger.info(f"Admin {admin_email} deleted existing screens for job {job_id}")
    except Exception as e:
        logger.warning(f"Error deleting screens for job {job_id}: {e}")

    # Clear screens_progress to allow worker to run
    from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion
    db = job_manager.firestore.db
    job_ref = db.collection("jobs").document(job_id)

    # Set status to lyrics_complete so screens worker can transition to generating_screens.
    # Store original status so the worker can restore it after regen instead of going to awaiting_review.
    original_status = job.status
    job_ref.update({
        "status": JobStatus.LYRICS_COMPLETE.value,
        "state_data.screens_progress": DELETE_FIELD,
        "state_data.audio_complete": True,
        "state_data.lyrics_complete": True,
        "state_data.regen_restore_status": original_status,
        "timeline": ArrayUnion([{
            "status": original_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": f"Admin {admin_email} triggered screen regeneration (will restore to {original_status})"
        }])
    })

    # Trigger screens worker
    worker_triggered = False
    error_msg = None
    try:
        from backend.services.worker_service import get_worker_service
        worker_service = get_worker_service()
        worker_triggered = await worker_service.trigger_screens_worker(job_id)
        if worker_triggered:
            logger.info(f"Admin {admin_email} triggered screens regeneration for job {job_id}")
        else:
            error_msg = "Worker service returned False - check service logs"
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error triggering screens worker for job {job_id}: {e}", exc_info=True)

    return RegenerateScreensResponse(
        status="success" if worker_triggered else "error",
        job_id=job_id,
        message="Screens regeneration started" if worker_triggered else "Failed to trigger screens worker",
        previous_screens_deleted=screens_deleted,
        worker_triggered=worker_triggered,
        error=error_msg,
    )


# =============================================================================
# Restart Job Endpoint
# =============================================================================

class RestartJobRequest(BaseModel):
    """Request model for restarting a job."""
    preserve_audio_stems: bool = False  # If true, keep existing stems (faster)
    delete_outputs: bool = True  # Delete existing output files from GCS


class RestartJobResponse(BaseModel):
    """Response from restart job endpoint."""
    status: str
    job_id: str
    message: str
    previous_status: str
    new_status: str
    cleared_data: List[str]
    deleted_gcs_paths: List[str]
    workers_triggered: List[str]
    error: Optional[str] = None


# States where restart is allowed
RESTART_ALLOWED_STATES = {
    "pending",
    "complete",
    "failed",
    "awaiting_review",
    "in_review",  # User opened review UI but hasn't completed
    "awaiting_audio_selection",
    "awaiting_instrumental_selection",
    "instrumental_selected",
    "prep_complete",
}


@router.post("/jobs/{job_id}/restart", response_model=RestartJobResponse)
async def restart_job(
    job_id: str,
    request: RestartJobRequest,
    auth_data: AuthResult = Depends(require_admin),
):
    """
    Fully restart a job from the beginning (admin only).

    Unlike reset (which just changes state), restart actually triggers
    the appropriate workers to begin processing again.

    Options:
    - preserve_audio_stems: If True, keeps existing audio separation (faster restart).
      Useful when you just need to regenerate screens with updated metadata.
    - delete_outputs: If True (default), deletes existing output files from GCS.

    When preserve_audio_stems=True:
    - Keeps stems, lyrics, and transcription data
    - Clears screens, video, encoding state
    - Triggers screens worker immediately

    When preserve_audio_stems=False:
    - Clears all processing state
    - For YouTube URL jobs: triggers download then audio/lyrics workers
    - For audio search jobs: transitions to awaiting_audio_selection
    - For file upload jobs: triggers audio/lyrics workers

    Allowed states: pending, complete, failed, awaiting_review, awaiting_audio_selection,
    awaiting_instrumental_selection, instrumental_selected, prep_complete
    """
    admin_email = auth_data.user_email or "unknown"
    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Validate job state
    if job.status not in RESTART_ALLOWED_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot restart job in '{job.status}' state. "
            f"Allowed states: {', '.join(sorted(RESTART_ALLOWED_STATES))}"
        )

    previous_status = job.status
    state_data = job.state_data or {}
    cleared_keys = []
    deleted_paths = []
    workers_triggered = []
    error_msg = None

    from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion
    db = job_manager.firestore.db
    job_ref = db.collection("jobs").document(job_id)

    # Delete output files if requested
    if request.delete_outputs:
        storage = StorageService()
        # Delete screens
        for screen_file in ["title.mov", "title.jpg", "title.png", "end.mov", "end.jpg", "end.png"]:
            try:
                path = f"jobs/{job_id}/screens/{screen_file}"
                storage.delete_file(path)
                deleted_paths.append(path)
            except Exception:
                pass
        # Delete final outputs
        for output_file in ["final_4k.mp4", "final_720p.mp4", "with_vocals.mkv", "preview.mp4"]:
            try:
                path = f"jobs/{job_id}/output/{output_file}"
                storage.delete_file(path)
                deleted_paths.append(path)
            except Exception:
                pass

    if request.preserve_audio_stems:
        # Quick restart: keep stems/lyrics, just regenerate screens and video
        # Validate that audio and lyrics completed
        audio_progress = state_data.get('audio_progress', {})
        lyrics_progress = state_data.get('lyrics_progress', {})

        if audio_progress.get('stage') != 'audio_complete':
            raise HTTPException(
                status_code=400,
                detail="Cannot preserve audio stems - audio processing was not complete"
            )
        if lyrics_progress.get('stage') != 'lyrics_complete':
            raise HTTPException(
                status_code=400,
                detail="Cannot preserve audio stems - lyrics processing was not complete"
            )

        # Clear only screens/video/encoding state
        keys_to_clear = [
            "screens_progress",
            "video_progress",
            "render_progress",
            "encoding_progress",
            "review_complete",
            "instrumental_selection",
        ]

        update_payload = {}
        for key in keys_to_clear:
            if key in state_data:
                update_payload[f"state_data.{key}"] = DELETE_FIELD
                cleared_keys.append(key)

        # Set coordination flags to trigger screens worker
        update_payload["state_data.audio_complete"] = True
        update_payload["state_data.lyrics_complete"] = True
        update_payload["status"] = "downloading"  # Allows transition to generating_screens
        update_payload["message"] = "Restarting with preserved audio stems"
        update_payload["error_message"] = DELETE_FIELD
        update_payload["error_details"] = DELETE_FIELD
        update_payload["timeline"] = ArrayUnion([{
            "status": "downloading",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": f"Admin {admin_email} restarted job (preserving audio stems)"
        }])

        job_ref.update(update_payload)
        new_status = "downloading"

        # Trigger screens worker
        try:
            from backend.services.worker_service import get_worker_service
            worker_service = get_worker_service()
            if await worker_service.trigger_screens_worker(job_id):
                workers_triggered.append("screens")
                logger.info(f"Admin {admin_email} triggered screens worker for restart of job {job_id}")
            else:
                error_msg = "Failed to trigger screens worker"
        except Exception as e:
            error_msg = f"Error triggering screens worker: {e}"
            logger.error(f"Error triggering screens worker for job {job_id}: {e}", exc_info=True)

    else:
        # Full restart: clear everything and start from beginning
        keys_to_clear = [
            "audio_complete",
            "lyrics_complete",
            "audio_progress",
            "lyrics_progress",
            "screens_progress",
            "video_progress",
            "render_progress",
            "encoding_progress",
            "review_complete",
            "corrected_lyrics",
            "instrumental_selection",
            "instrumental_options",
            "lyrics_metadata",
        ]

        update_payload = {}
        for key in keys_to_clear:
            if key in state_data:
                update_payload[f"state_data.{key}"] = DELETE_FIELD
                cleared_keys.append(key)

        update_payload["error_message"] = DELETE_FIELD
        update_payload["error_details"] = DELETE_FIELD

        # Determine what workers to trigger based on job input type
        has_youtube_url = bool(job.url)
        has_uploaded_file = bool(job.input_media_gcs_path)
        has_audio_search = bool(state_data.get('audio_search_results') or state_data.get('audio_selection'))

        if has_youtube_url and not has_audio_search:
            # YouTube URL job - need to re-download and process
            # Clear the downloaded file to force re-download
            update_payload["input_media_gcs_path"] = DELETE_FIELD
            cleared_keys.append("input_media_gcs_path")
            update_payload["status"] = "downloading"
            update_payload["message"] = "Re-downloading audio from YouTube"
            update_payload["timeline"] = ArrayUnion([{
                "status": "downloading",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Admin {admin_email} restarted job - downloading from YouTube"
            }])
            job_ref.update(update_payload)
            new_status = "downloading"

            # Download YouTube audio using YouTubeDownloadService, then trigger processing
            try:
                from backend.services.youtube_download_service import (
                    get_youtube_download_service,
                    YouTubeDownloadError,
                )
                from backend.services.worker_service import get_worker_service

                youtube_service = get_youtube_download_service()
                audio_gcs_path = await youtube_service.download(
                    url=job.url,
                    job_id=job_id,
                    artist=job.artist,
                    title=job.title,
                )

                # Update job with downloaded audio path
                job_manager.update_job(job_id, {'input_media_gcs_path': audio_gcs_path})
                workers_triggered.append("download")
                logger.info(f"YouTube audio re-downloaded to GCS: {audio_gcs_path}")

                # Now trigger audio and lyrics workers
                worker_service = get_worker_service()
                if await worker_service.trigger_audio_worker(job_id):
                    workers_triggered.append("audio")
                if await worker_service.trigger_lyrics_worker(job_id):
                    workers_triggered.append("lyrics")

                if "audio" not in workers_triggered and "lyrics" not in workers_triggered:
                    error_msg = "Audio downloaded but failed to trigger processing workers"
            except YouTubeDownloadError as e:
                error_msg = f"YouTube download failed: {e}"
                logger.error(f"YouTube download failed for job {job_id}: {e}")
                # Update job to failed state
                job_manager.mark_job_failed(job_id, f"YouTube download failed: {e}")
                new_status = "failed"
            except Exception as e:
                error_msg = f"Error during YouTube restart: {e}"
                logger.error(f"Error restarting YouTube job {job_id}: {e}", exc_info=True)

        elif has_audio_search:
            # Audio search job - go back to audio selection
            # Clear audio search results to allow re-search
            for key in ["audio_search_results", "audio_search_count", "remote_search_id", "audio_selection"]:
                if key in state_data:
                    update_payload[f"state_data.{key}"] = DELETE_FIELD
                    cleared_keys.append(key)

            update_payload["input_media_gcs_path"] = DELETE_FIELD
            update_payload["status"] = "awaiting_audio_selection"
            update_payload["message"] = "Ready for audio source selection"
            update_payload["timeline"] = ArrayUnion([{
                "status": "awaiting_audio_selection",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Admin {admin_email} restarted job - select audio source"
            }])
            job_ref.update(update_payload)
            new_status = "awaiting_audio_selection"
            # No workers to trigger - waiting for admin to select audio

        elif has_uploaded_file:
            # File upload job - just trigger audio/lyrics workers
            update_payload["status"] = "downloading"
            update_payload["message"] = "Restarting audio and lyrics processing"
            update_payload["timeline"] = ArrayUnion([{
                "status": "downloading",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Admin {admin_email} restarted job from beginning"
            }])
            job_ref.update(update_payload)
            new_status = "downloading"

            try:
                from backend.services.worker_service import get_worker_service
                worker_service = get_worker_service()
                if await worker_service.trigger_audio_worker(job_id):
                    workers_triggered.append("audio")
                if await worker_service.trigger_lyrics_worker(job_id):
                    workers_triggered.append("lyrics")
                if not workers_triggered:
                    error_msg = "Failed to trigger audio/lyrics workers"
            except Exception as e:
                error_msg = f"Error triggering workers: {e}"
                logger.error(f"Error triggering workers for job {job_id}: {e}", exc_info=True)
        else:
            # Unknown job type - just set to pending
            update_payload["status"] = "pending"
            update_payload["message"] = "Job reset - manual intervention may be needed"
            update_payload["timeline"] = ArrayUnion([{
                "status": "pending",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Admin {admin_email} restarted job (no automatic worker trigger)"
            }])
            job_ref.update(update_payload)
            new_status = "pending"
            error_msg = "Job type unclear - may need manual worker trigger"

    logger.info(
        f"Admin {admin_email} restarted job {job_id}: "
        f"{previous_status} -> {new_status}, cleared: {cleared_keys}, workers: {workers_triggered}"
    )

    return RestartJobResponse(
        status="success" if workers_triggered or new_status == "awaiting_audio_selection" else "partial",
        job_id=job_id,
        message=f"Job restarted from {previous_status} to {new_status}",
        previous_status=previous_status,
        new_status=new_status,
        cleared_data=cleared_keys,
        deleted_gcs_paths=deleted_paths,
        workers_triggered=workers_triggered,
        error=error_msg,
    )


# =============================================================================
# Override Audio Source Endpoint
# =============================================================================

class OverrideAudioSourceRequest(BaseModel):
    """Request model for overriding audio source."""
    source_type: str  # "audio_search" - switch to audio search mode


class OverrideAudioSourceResponse(BaseModel):
    """Response from override audio source endpoint."""
    status: str
    job_id: str
    message: str
    previous_source: str  # "youtube", "audio_search", "file_upload"
    new_source: str
    cleared_data: List[str]
    new_status: str
    search_results_count: Optional[int] = None
    error: Optional[str] = None


# States where audio source override is allowed
OVERRIDE_AUDIO_ALLOWED_STATES = {
    "pending",
    "complete",
    "failed",
    "awaiting_review",
    "awaiting_audio_selection",
    "instrumental_selected",
    "prep_complete",
}


@router.post("/jobs/{job_id}/override-audio-source", response_model=OverrideAudioSourceResponse)
async def override_audio_source(
    job_id: str,
    request: OverrideAudioSourceRequest,
    auth_data: AuthResult = Depends(require_admin),
):
    """
    Override the audio source for a job (admin only).

    Use this when a Made-For-You order was submitted with a YouTube URL
    but you want to use audio search to find higher quality audio instead.

    Currently supports switching to audio_search mode, which:
    1. Clears existing audio-related state
    2. Performs an audio search using the job's artist/title
    3. Stores search results in state_data
    4. Transitions job to awaiting_audio_selection with results ready
    5. Admin can then select an audio source from the search results

    Allowed states: pending, complete, failed, awaiting_review, awaiting_audio_selection,
    instrumental_selected, prep_complete
    """
    admin_email = auth_data.user_email or "unknown"
    job_manager = JobManager()
    job = job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Validate job state
    if job.status not in OVERRIDE_AUDIO_ALLOWED_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot override audio source for job in '{job.status}' state. "
            f"Allowed states: {', '.join(sorted(OVERRIDE_AUDIO_ALLOWED_STATES))}"
        )

    # Validate source type
    if request.source_type != "audio_search":
        raise HTTPException(
            status_code=400,
            detail="Only 'audio_search' source type is currently supported"
        )

    # Determine current source type
    state_data = job.state_data or {}
    if state_data.get('audio_selection') or state_data.get('audio_search_results'):
        previous_source = "audio_search"
    elif job.url:
        previous_source = "youtube"
    elif job.input_media_gcs_path:
        previous_source = "file_upload"
    else:
        previous_source = "unknown"

    previous_status = job.status
    cleared_keys = []

    from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion
    db = job_manager.firestore.db
    job_ref = db.collection("jobs").document(job_id)

    # Clear all audio-related state to start fresh with audio search
    keys_to_clear = [
        "audio_complete",
        "lyrics_complete",
        "audio_progress",
        "lyrics_progress",
        "screens_progress",
        "video_progress",
        "render_progress",
        "encoding_progress",
        "review_complete",
        "corrected_lyrics",
        "instrumental_selection",
        "instrumental_options",
        "lyrics_metadata",
        "audio_search_results",
        "audio_search_count",
        "remote_search_id",
        "audio_selection",
    ]

    update_payload = {}
    for key in keys_to_clear:
        if key in state_data:
            update_payload[f"state_data.{key}"] = DELETE_FIELD
            cleared_keys.append(key)

    # Clear job-level audio fields
    update_payload["url"] = DELETE_FIELD  # Clear YouTube URL
    update_payload["input_media_gcs_path"] = DELETE_FIELD  # Clear downloaded file
    update_payload["error_message"] = DELETE_FIELD
    update_payload["error_details"] = DELETE_FIELD

    # Set audio search fields based on current artist/title
    search_artist = job.artist
    search_title = job.title
    update_payload["audio_search_artist"] = search_artist
    update_payload["audio_search_title"] = search_title

    # First transition to searching_audio
    update_payload["status"] = "searching_audio"
    update_payload["message"] = f"Searching for: {search_artist} - {search_title}"
    update_payload["progress"] = 5
    update_payload["timeline"] = ArrayUnion([{
        "status": "searching_audio",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": f"Admin {admin_email} switched audio source from {previous_source} to audio_search"
    }])

    job_ref.update(update_payload)

    # Perform the actual audio search
    search_results_count = None
    error_msg = None
    try:
        audio_search_service = get_audio_search_service()
        search_results = audio_search_service.search(search_artist, search_title)
        search_results_count = len(search_results)

        # Store results in job state_data
        results_dicts = [r.to_dict() for r in search_results]
        state_data_update = {
            'audio_search_results': results_dicts,
            'audio_search_count': search_results_count,
        }
        if audio_search_service.last_remote_search_id:
            state_data_update['remote_search_id'] = audio_search_service.last_remote_search_id

        # Transition to awaiting_audio_selection with results
        new_status = "awaiting_audio_selection"
        job_ref.update({
            "state_data": state_data_update,
            "status": new_status,
            "message": f"Found {search_results_count} audio sources - select one",
            "progress": 10,
        })

        logger.info(
            f"Admin {admin_email} overrode audio source for job {job_id}: "
            f"{previous_source} -> audio_search, found {search_results_count} results"
        )

    except NoResultsError as e:
        new_status = "failed"
        error_msg = f"No audio sources found for: {search_artist} - {search_title}"
        job_ref.update({
            "status": new_status,
            "message": error_msg,
            "error_message": error_msg,
        })
        logger.warning(f"Audio search failed for job {job_id}: {error_msg}")

    except AudioSearchError as e:
        new_status = "failed"
        error_msg = f"Audio search failed: {e}"
        job_ref.update({
            "status": new_status,
            "message": error_msg,
            "error_message": error_msg,
        })
        logger.error(f"Audio search error for job {job_id}: {e}")

    return OverrideAudioSourceResponse(
        status="success" if not error_msg else "error",
        job_id=job_id,
        message=f"Found {search_results_count} audio sources - select one in the admin panel." if not error_msg
        else error_msg,
        previous_source=previous_source,
        new_source="audio_search",
        cleared_data=cleared_keys,
        new_status=new_status,
        search_results_count=search_results_count,
        error=error_msg,
    )


# =============================================================================
# Payment Admin Endpoints
# =============================================================================

class RevenueSummaryResponse(BaseModel):
    """Revenue summary metrics."""
    total_gross: int
    total_fees: int
    total_net: int
    total_refunds: int
    transaction_count: int
    average_order_value: int
    revenue_by_type: Dict[str, int]


class RevenueChartPoint(BaseModel):
    """Single data point for revenue chart."""
    date: str
    gross: int
    net: int
    fees: int
    count: int


class PaymentSummary(BaseModel):
    """Payment record for list view."""
    session_id: str
    amount_total: int = 0
    currency: str = "usd"
    stripe_fee: int = 0
    net_amount: int = 0
    customer_email: str = ""
    customer_name: str = ""
    payment_method_type: str = ""
    card_brand: str = ""
    card_last4: str = ""
    order_type: str = ""
    product_description: str = ""
    status: str = ""
    refund_amount: int = 0
    created_at: Optional[str] = None
    is_test: bool = False

    class Config:
        extra = "allow"


class PaymentListResponse(BaseModel):
    """Response for payment list."""
    payments: List[PaymentSummary]
    total: int
    has_more: bool


class StripeBalanceResponse(BaseModel):
    """Stripe account balance."""
    available: int
    pending: int
    currency: str = "usd"


class PayoutItem(BaseModel):
    """Payout record."""
    id: str
    amount: int
    currency: str = "usd"
    status: str = ""
    arrival_date: Optional[int] = None
    created: Optional[int] = None
    description: Optional[str] = None
    method: Optional[str] = None


class DisputeItem(BaseModel):
    """Dispute record."""
    id: str
    amount: int
    currency: str = "usd"
    status: str = ""
    reason: str = ""
    charge_id: Optional[str] = None
    created: Optional[int] = None
    evidence_due_by: Optional[int] = None
    payment_intent_id: Optional[str] = None


class RefundRequest(BaseModel):
    """Request to issue a refund."""
    amount: Optional[int] = None
    reason: str = "requested_by_customer"


class RefundResponse(BaseModel):
    """Response from refund processing."""
    success: bool
    message: str
    session_id: str


class UserPaymentHistoryResponse(BaseModel):
    """Payment history for a user."""
    email: str
    payments: List[PaymentSummary]
    total_spent: int
    total_refunded: int
    net_spent: int
    payment_count: int
    first_payment_at: Optional[str] = None
    last_payment_at: Optional[str] = None


class WebhookEventItem(BaseModel):
    """Webhook event record."""
    event_id: str = ""
    event_type: str = ""
    created_at: Optional[str] = None
    processed_at: Optional[str] = None
    status: str = ""
    error_message: Optional[str] = None
    session_id: Optional[str] = None
    customer_email: Optional[str] = None
    summary: Optional[str] = None


@router.get("/payments/summary", response_model=RevenueSummaryResponse)
async def get_payment_summary(
    days: int = 30,
    exclude_test: bool = True,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """Get revenue summary metrics for the given period."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    result = service.get_revenue_summary(days=days, exclude_test=exclude_test)
    # Remove internal cache key
    result.pop("_key", None)
    return RevenueSummaryResponse(**result)


@router.get("/payments/revenue-chart", response_model=List[RevenueChartPoint])
async def get_revenue_chart(
    days: int = 30,
    group_by: str = "day",
    exclude_test: bool = True,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """Get revenue data grouped by time period for charting."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    return service.get_revenue_by_period(days=days, group_by=group_by, exclude_test=exclude_test)


@router.get("/payments", response_model=PaymentListResponse)
async def list_payments(
    limit: int = 50,
    offset: int = 0,
    order_type: Optional[str] = None,
    status: Optional[str] = None,
    email: Optional[str] = None,
    exclude_test: bool = True,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """List payments with pagination and filters."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    result = service.list_payments(
        limit=limit, offset=offset, order_type=order_type,
        status=status, email=email, exclude_test=exclude_test,
    )
    return PaymentListResponse(**result)


@router.get("/payments/balance", response_model=StripeBalanceResponse)
async def get_stripe_balance(
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """Get current Stripe account balance."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    return StripeBalanceResponse(**service.get_stripe_balance())


@router.get("/payments/payouts", response_model=List[PayoutItem])
async def get_payouts(
    limit: int = 20,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """Get recent payouts."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    return service.get_recent_payouts(limit=limit)


@router.get("/payments/disputes", response_model=List[DisputeItem])
async def get_disputes(
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """Get open and recent disputes."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    return service.get_recent_disputes()


@router.get("/payments/webhook-events", response_model=List[WebhookEventItem])
async def get_webhook_events(
    limit: int = 50,
    event_type: Optional[str] = None,
    status: Optional[str] = None,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """Get recent webhook events for audit trail."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    return service.list_webhook_events(limit=limit, event_type=event_type, status=status)


@router.get("/payments/by-user/{email}")
async def get_user_payments(
    email: str,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """Get all payments for a specific user."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    return service.get_user_payment_history(email)


@router.get("/payments/{session_id}")
async def get_payment_detail(
    session_id: str,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """Get full details for a single payment."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    result = service.get_payment_detail(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Payment not found")
    return result


@router.post("/payments/{session_id}/refund", response_model=RefundResponse)
async def refund_payment(
    session_id: str,
    request: RefundRequest,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """
    Issue a full or partial refund for a payment.

    If amount is not specified, issues a full refund.
    Automatically deducts proportional credits from user.
    """
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()

    # Get payment details first to determine credits to deduct
    payment = service.get_payment_detail(session_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    success, message = service.process_refund(
        session_id=session_id,
        amount=request.amount,
        reason=request.reason,
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    # Deduct proportional credits if this was a credit purchase
    if payment.get("order_type") == "credit_purchase" and payment.get("credits_granted", 0) > 0:
        try:
            original_amount = payment.get("amount_total", 0)
            refund_amount = request.amount or original_amount
            credits_to_deduct = int(
                payment["credits_granted"] * (refund_amount / original_amount)
            )
            if credits_to_deduct > 0:
                user_email = payment.get("customer_email", "")
                user_service = get_user_service()
                user_service.add_credits(
                    email=user_email,
                    amount=-credits_to_deduct,
                    reason=f"refund_{session_id}",
                )
                logger.info(
                    f"Deducted {credits_to_deduct} credits from {user_email} "
                    f"for refund on {session_id}"
                )
        except Exception as e:
            logger.error(f"Failed to deduct credits for refund {session_id}: {e}")

    admin_email = getattr(auth_data, "user_email", None) or "unknown"
    logger.info(f"Admin {admin_email} issued refund for {session_id}: {message}")

    return RefundResponse(success=True, message=message, session_id=session_id)


@router.post("/payments/normalize-emails")
async def normalize_payment_emails(
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """One-time backfill: lowercase all customer_email values in stripe_payments."""
    from backend.services.stripe_admin_service import get_stripe_admin_service
    service = get_stripe_admin_service()
    return service.normalize_customer_emails()


@router.post("/backfill/user-stats")
async def backfill_user_stats(
    auth_data: Tuple[str, UserType, int] = Depends(require_admin),
):
    """One-time backfill: recalculate total_jobs_completed and total_spent for all users."""
    from google.cloud import firestore as firestore_lib
    db = firestore_lib.Client()

    # Get all users
    users_docs = list(db.collection(USERS_COLLECTION).stream())
    updated = 0

    for user_doc in users_docs:
        user_data = user_doc.to_dict()
        email = user_data.get("email", "")
        if not email:
            continue

        # Count completed jobs (try both original email and lowercase)
        completed_jobs = 0
        seen_job_ids = set()
        for email_variant in set([email, email.lower().strip()]):
            jobs_query = db.collection("jobs").where("user_email", "==", email_variant).where(
                "status", "in", ["complete", "prep_complete"]
            )
            for job_doc in jobs_query.stream():
                if job_doc.id not in seen_job_ids:
                    seen_job_ids.add(job_doc.id)
                    completed_jobs += 1

        # Sum payments
        total_spent = 0
        payments_query = db.collection("stripe_payments").where(
            "customer_email", "==", email.lower().strip()
        )
        for pay_doc in payments_query.stream():
            pay_data = pay_doc.to_dict()
            status = pay_data.get("status", "")
            if status not in ("refunded",):
                total_spent += pay_data.get("amount_total", 0)
                total_spent -= pay_data.get("refund_amount", 0)

        # Update user document
        updates = {}
        if completed_jobs != user_data.get("total_jobs_completed", 0):
            updates["total_jobs_completed"] = completed_jobs
        if total_spent != user_data.get("total_spent", 0):
            updates["total_spent"] = total_spent

        if updates:
            user_doc.reference.update(updates)
            updated += 1

    return {"status": "success", "users_checked": len(users_docs), "users_updated": updated}
