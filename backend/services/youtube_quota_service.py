"""
YouTube Data API v3 quota tracking service.

Uses GCP Cloud Monitoring as the single source of truth for quota usage,
with a lightweight Firestore pending buffer for the ~7-minute delay before
GCP reflects newly completed uploads.

Formula: current_usage = gcp_reported + pending_buffer
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any
from zoneinfo import ZoneInfo

from google.cloud import firestore

from backend.config import settings


logger = logging.getLogger(__name__)

YOUTUBE_QUOTA_PENDING_COLLECTION = "youtube_quota_pending"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

# Pending entries older than this are ignored (GCP should have caught up)
PENDING_EXPIRY_MINUTES = 10

# In-memory cache for GCP usage to avoid hammering Cloud Monitoring
_gcp_cache: Dict[str, Any] = {
    "value": None,
    "timestamp": 0.0,
}
GCP_CACHE_TTL_SECONDS = 60


def _get_today_date_pt() -> str:
    """Get today's date in Pacific Time as YYYY-MM-DD string."""
    return datetime.now(PACIFIC_TZ).strftime("%Y-%m-%d")


def _seconds_until_midnight_pt() -> int:
    """Calculate seconds until Pacific Time midnight (when YouTube quota resets)."""
    now = datetime.now(PACIFIC_TZ)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = midnight + timedelta(days=1)
    return int((next_midnight - now).total_seconds())


class YouTubeQuotaService:
    """
    Service for tracking YouTube Data API v3 quota consumption.

    Uses GCP Cloud Monitoring as the primary data source, with a Firestore
    pending buffer for recent uploads not yet reflected in GCP metrics.
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

        gcp_usage = self._get_gcp_usage()
        pending = self._get_pending_units()
        consumed = gcp_usage + pending
        remaining = max(0, effective_limit - consumed)

        if consumed + estimated_units > effective_limit:
            seconds = _seconds_until_midnight_pt()
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            logger.warning(
                f"YouTube quota insufficient: {consumed}/{effective_limit} units used "
                f"(GCP: {gcp_usage}, pending: {pending}, need {estimated_units} more), "
                f"resets in {hours}h {minutes}m"
            )
            return (
                False,
                remaining,
                f"YouTube API quota insufficient ({consumed}/{effective_limit} units used, "
                f"need {estimated_units}). Resets in {hours}h {minutes}m."
            )

        return True, remaining, f"{remaining} quota units remaining today"

    def record_upload(self, job_id: str, units: Optional[int] = None) -> None:
        """
        Record a YouTube upload in the pending buffer.

        Called once after a successful upload. The pending buffer bridges
        the ~7-minute gap before GCP Cloud Monitoring reflects the usage.

        Args:
            job_id: Job ID that triggered the upload
            units: Quota units consumed. Defaults to settings.youtube_quota_upload_cost.
        """
        if units is None:
            units = settings.youtube_quota_upload_cost

        date_str = _get_today_date_pt()
        doc_ref = self.db.collection(YOUTUBE_QUOTA_PENDING_COLLECTION).document(date_str)

        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                pending_uploads = data.get("pending_uploads", [])
            else:
                pending_uploads = []

            pending_uploads.append({
                "job_id": job_id,
                "units": units,
                "recorded_at": datetime.now(PACIFIC_TZ),
            })

            # Recalculate pending_units from non-expired entries
            cutoff = datetime.now(PACIFIC_TZ) - timedelta(minutes=PENDING_EXPIRY_MINUTES)
            active_units = sum(
                e.get("units", 0) for e in pending_uploads
                if e.get("recorded_at") and e["recorded_at"] >= cutoff
            )

            transaction.set(doc_ref, {
                "pending_units": active_units,
                "pending_uploads": pending_uploads,
                "updated_at": datetime.now(PACIFIC_TZ),
            })

        transaction = self.db.transaction()
        update_in_transaction(transaction, doc_ref)
        logger.info(
            f"Recorded YouTube upload pending: {units} units for job {job_id}"
        )

    def get_quota_stats(self) -> Dict[str, Any]:
        """
        Get current quota statistics for admin dashboard.

        Returns:
            Dict with quota usage stats including GCP and pending breakdown.
        """
        date_str = _get_today_date_pt()

        limit = settings.youtube_quota_daily_limit
        safety_margin = settings.youtube_quota_safety_margin
        effective_limit = limit - safety_margin

        gcp_usage = self._get_gcp_usage()
        pending = self._get_pending_units()
        consumed = gcp_usage + pending
        remaining = max(0, effective_limit - consumed)

        upload_cost = settings.youtube_quota_upload_cost
        estimated_uploads = remaining // upload_cost if upload_cost > 0 else -1

        upload_count = self._get_pending_upload_count()

        return {
            "date_pt": date_str,
            "units_consumed": consumed,
            "gcp_usage": gcp_usage,
            "pending_units": pending,
            "units_limit": limit,
            "effective_limit": effective_limit,
            "units_remaining": remaining,
            "upload_cost": upload_cost,
            "estimated_uploads_remaining": estimated_uploads,
            "seconds_until_reset": _seconds_until_midnight_pt(),
            "upload_count": upload_count,
        }

    def _get_gcp_usage(self) -> int:
        """
        Query GCP Cloud Monitoring for today's YouTube API quota usage.

        Uses a 60-second in-memory cache. Falls back to stale cache on error,
        or 0 if no cache exists.
        """
        global _gcp_cache

        now = time.time()
        cache_age = now - _gcp_cache["timestamp"]

        # Return cached value if fresh
        if _gcp_cache["value"] is not None and cache_age < GCP_CACHE_TTL_SECONDS:
            return _gcp_cache["value"]

        try:
            from google.cloud import monitoring_v3
            from google.protobuf.timestamp_pb2 import Timestamp

            client = monitoring_v3.MetricServiceClient()
            project_name = f"projects/{settings.google_cloud_project}"

            # Query from midnight PT today
            now_utc = datetime.now(PACIFIC_TZ).astimezone(ZoneInfo("UTC"))
            midnight_pt = datetime.now(PACIFIC_TZ).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(ZoneInfo("UTC"))

            start_time = Timestamp()
            start_time.FromDatetime(midnight_pt)
            end_time = Timestamp()
            end_time.FromDatetime(now_utc)

            interval = monitoring_v3.TimeInterval(
                start_time=start_time,
                end_time=end_time,
            )

            results = client.list_time_series(
                request={
                    "name": project_name,
                    "filter": (
                        'metric.type = "serviceruntime.googleapis.com/quota/rate/net_usage"'
                        ' AND resource.labels.service = "youtube.googleapis.com"'
                    ),
                    "interval": interval,
                    "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                }
            )

            # Sum all datapoints (no aggregation — raw per-minute values)
            total = 0
            for ts in results:
                for point in ts.points:
                    total += int(point.value.int64_value)

            _gcp_cache["value"] = total
            _gcp_cache["timestamp"] = now
            return total

        except Exception as e:
            logger.warning(f"Failed to query GCP Cloud Monitoring: {e}")
            # Fallback to stale cache if available
            if _gcp_cache["value"] is not None:
                logger.info(f"Using stale GCP cache (age: {cache_age:.0f}s)")
                return _gcp_cache["value"]
            return 0

    def _get_pending_units(self) -> int:
        """
        Get pending quota units from Firestore (entries < PENDING_EXPIRY_MINUTES old).
        """
        date_str = _get_today_date_pt()
        doc_ref = self.db.collection(YOUTUBE_QUOTA_PENDING_COLLECTION).document(date_str)
        doc = doc_ref.get()

        if not doc.exists:
            return 0

        data = doc.to_dict()
        pending_uploads = data.get("pending_uploads", [])
        cutoff = datetime.now(PACIFIC_TZ) - timedelta(minutes=PENDING_EXPIRY_MINUTES)

        total = 0
        for entry in pending_uploads:
            recorded_at = entry.get("recorded_at")
            if recorded_at and recorded_at >= cutoff:
                total += entry.get("units", 0)

        return total

    def _get_pending_upload_count(self) -> int:
        """Get count of pending uploads (non-expired) for today."""
        date_str = _get_today_date_pt()
        doc_ref = self.db.collection(YOUTUBE_QUOTA_PENDING_COLLECTION).document(date_str)
        doc = doc_ref.get()

        if not doc.exists:
            return 0

        data = doc.to_dict()
        pending_uploads = data.get("pending_uploads", [])
        cutoff = datetime.now(PACIFIC_TZ) - timedelta(minutes=PENDING_EXPIRY_MINUTES)

        count = 0
        for entry in pending_uploads:
            recorded_at = entry.get("recorded_at")
            if recorded_at and recorded_at >= cutoff:
                count += 1

        return count


# Singleton instance
_youtube_quota_service: Optional[YouTubeQuotaService] = None


def get_youtube_quota_service() -> YouTubeQuotaService:
    """Get the singleton YouTubeQuotaService instance."""
    global _youtube_quota_service
    if _youtube_quota_service is None:
        _youtube_quota_service = YouTubeQuotaService()
    return _youtube_quota_service
