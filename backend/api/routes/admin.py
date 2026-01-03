"""
Admin API routes for dashboard statistics and management.

Handles:
- Dashboard overview statistics
- System-wide metrics
- Admin-only operations
"""
import logging
from datetime import datetime, timedelta
from typing import Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.api.dependencies import require_admin
from backend.services.auth_service import UserType
from backend.services.user_service import get_user_service, UserService
from backend.services.job_manager import JobManager
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
    users_collection = db.collection("users")

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
