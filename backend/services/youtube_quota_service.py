"""
YouTube Data API v3 quota tracking service.

Tracks quota unit consumption in Firestore with daily documents keyed by
Pacific Time date (YouTube quota resets at midnight PT).

Replaces the old hard-coded upload count limit with unit-based tracking
that reflects actual API quota costs:
- videos.insert: 100 units
- search.list: 100 units
- thumbnails.set: 50 units
- videos.delete: 50 units
"""
import logging
from datetime import datetime
from typing import Tuple, Optional, Dict, Any
from zoneinfo import ZoneInfo

from google.cloud import firestore

from backend.config import settings


logger = logging.getLogger(__name__)

YOUTUBE_QUOTA_COLLECTION = "youtube_quota"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

# Known quota costs per YouTube Data API v3 operation
QUOTA_COSTS = {
    "videos.insert": 100,
    "search.list": 100,
    "thumbnails.set": 50,
    "videos.delete": 50,
    "channels.list": 1,
}


def _get_today_date_pt() -> str:
    """Get today's date in Pacific Time as YYYY-MM-DD string."""
    return datetime.now(PACIFIC_TZ).strftime("%Y-%m-%d")


def _seconds_until_midnight_pt() -> int:
    """Calculate seconds until Pacific Time midnight (when YouTube quota resets)."""
    from datetime import timedelta
    now = datetime.now(PACIFIC_TZ)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = midnight + timedelta(days=1)
    return int((next_midnight - now).total_seconds())


class YouTubeQuotaService:
    """
    Service for tracking YouTube Data API v3 quota consumption.

    Uses Firestore for distributed tracking with daily documents
    that automatically correspond to YouTube's quota reset at midnight PT.
    """

    def __init__(self, db: Optional[firestore.Client] = None):
        if db is None:
            self.db = firestore.Client(project=settings.google_cloud_project)
        else:
            self.db = db

    def check_quota_available(self, estimated_units: Optional[int] = None) -> Tuple[bool, int, str]:
        """
        Check if enough quota is available for an upload.

        Args:
            estimated_units: Estimated units needed. Defaults to settings.youtube_quota_upload_cost.

        Returns:
            Tuple of (allowed, remaining_units, message)
        """
        if estimated_units is None:
            estimated_units = settings.youtube_quota_upload_cost

        limit = settings.youtube_quota_daily_limit
        safety_margin = settings.youtube_quota_safety_margin
        effective_limit = limit - safety_margin

        if limit == 0:
            return True, -1, "No YouTube quota limit configured"

        consumed = self._get_units_consumed_today()
        remaining = max(0, effective_limit - consumed)

        if consumed + estimated_units > effective_limit:
            seconds = _seconds_until_midnight_pt()
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            logger.warning(
                f"YouTube quota insufficient: {consumed}/{effective_limit} units used "
                f"(need {estimated_units} more), resets in {hours}h {minutes}m"
            )
            return (
                False,
                remaining,
                f"YouTube API quota insufficient ({consumed}/{effective_limit} units used, "
                f"need {estimated_units}). Resets in {hours}h {minutes}m."
            )

        return True, remaining, f"{remaining} quota units remaining today"

    def record_operation(
        self,
        job_id: str,
        user_email: str,
        operation: str,
        units: Optional[int] = None,
    ) -> None:
        """
        Record a YouTube API operation for quota tracking.

        Uses Firestore transactions for atomic updates.

        Args:
            job_id: Job ID that triggered the operation
            user_email: User who owns the job
            operation: API operation name (e.g., "videos.insert")
            units: Quota units consumed. If None, looks up from QUOTA_COSTS.
        """
        if units is None:
            units = QUOTA_COSTS.get(operation, 0)
            if units == 0:
                logger.warning(f"Unknown YouTube API operation: {operation}, recording 0 units")

        date_str = _get_today_date_pt()
        doc_ref = self.db.collection(YOUTUBE_QUOTA_COLLECTION).document(date_str)

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                units_consumed = data.get("units_consumed", 0) + units
                operations = data.get("operations", [])
            else:
                units_consumed = units
                operations = []

            operations.append({
                "job_id": job_id,
                "operation": operation,
                "units": units,
                "user_email": user_email,
                "timestamp": datetime.now(PACIFIC_TZ),
            })

            transaction.set(doc_ref, {
                "date_pt": date_str,
                "units_consumed": units_consumed,
                "units_limit": settings.youtube_quota_daily_limit,
                "operations": operations,
                "updated_at": datetime.now(PACIFIC_TZ),
            })

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)
        logger.info(
            f"Recorded YouTube quota: {operation}={units} units for job {job_id} "
            f"(user: {user_email})"
        )

    def get_quota_stats(self) -> Dict[str, Any]:
        """
        Get current quota statistics for admin dashboard.

        Returns:
            Dict with quota usage stats
        """
        date_str = _get_today_date_pt()
        doc_ref = self.db.collection(YOUTUBE_QUOTA_COLLECTION).document(date_str)
        doc = doc_ref.get()

        limit = settings.youtube_quota_daily_limit
        safety_margin = settings.youtube_quota_safety_margin
        effective_limit = limit - safety_margin

        if not doc.exists:
            return {
                "date_pt": date_str,
                "units_consumed": 0,
                "units_limit": limit,
                "effective_limit": effective_limit,
                "units_remaining": effective_limit,
                "upload_cost": settings.youtube_quota_upload_cost,
                "estimated_uploads_remaining": effective_limit // settings.youtube_quota_upload_cost if settings.youtube_quota_upload_cost > 0 else -1,
                "seconds_until_reset": _seconds_until_midnight_pt(),
                "operations_count": 0,
            }

        data = doc.to_dict()
        consumed = data.get("units_consumed", 0)
        remaining = max(0, effective_limit - consumed)

        return {
            "date_pt": date_str,
            "units_consumed": consumed,
            "units_limit": limit,
            "effective_limit": effective_limit,
            "units_remaining": remaining,
            "upload_cost": settings.youtube_quota_upload_cost,
            "estimated_uploads_remaining": remaining // settings.youtube_quota_upload_cost if settings.youtube_quota_upload_cost > 0 else -1,
            "seconds_until_reset": _seconds_until_midnight_pt(),
            "operations_count": len(data.get("operations", [])),
        }

    def _get_units_consumed_today(self) -> int:
        """Get total quota units consumed today (Pacific Time)."""
        date_str = _get_today_date_pt()
        doc_ref = self.db.collection(YOUTUBE_QUOTA_COLLECTION).document(date_str)
        doc = doc_ref.get()

        if not doc.exists:
            return 0

        return doc.to_dict().get("units_consumed", 0)


# Singleton instance
_youtube_quota_service: Optional[YouTubeQuotaService] = None


def get_youtube_quota_service() -> YouTubeQuotaService:
    """Get the singleton YouTubeQuotaService instance."""
    global _youtube_quota_service
    if _youtube_quota_service is None:
        _youtube_quota_service = YouTubeQuotaService()
    return _youtube_quota_service
