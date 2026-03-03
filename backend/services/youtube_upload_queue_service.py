"""
YouTube upload queue service.

When YouTube API quota is insufficient during job completion, uploads are
queued here instead of being silently skipped. A scheduled processor
retries queued uploads when quota is available.
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo

from google.cloud import firestore

from backend.config import settings


logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_QUEUE_COLLECTION = "youtube_upload_queue"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


class YouTubeUploadQueueService:
    """
    Service for managing deferred YouTube uploads.

    When quota is exhausted during job completion, uploads are queued
    and retried by a scheduled processor.
    """

    def __init__(self, db: Optional[firestore.Client] = None):
        if db is None:
            self.db = firestore.Client(project=settings.google_cloud_project)
        else:
            self.db = db

    def queue_upload(
        self,
        job_id: str,
        user_email: str,
        artist: str,
        title: str,
        brand_code: Optional[str],
        reason: str = "quota_exceeded",
    ) -> None:
        """
        Queue a YouTube upload for later processing.

        Args:
            job_id: Job ID to upload
            user_email: User's email address
            artist: Artist name
            title: Song title
            brand_code: Release ID (e.g., "NOMAD-1287")
            reason: Why the upload was deferred
        """
        doc_ref = self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION).document(job_id)
        now = datetime.now(PACIFIC_TZ)

        doc_ref.set({
            "job_id": job_id,
            "status": "queued",
            "reason": reason,
            "user_email": user_email,
            "artist": artist,
            "title": title,
            "brand_code": brand_code,
            "queued_at": now,
            "attempts": 0,
            "max_attempts": 5,
            "last_error": None,
            "youtube_url": None,
            "notification_sent": False,
            "updated_at": now,
        })

        logger.info(
            f"Queued YouTube upload for job {job_id} ({artist} - {title}), "
            f"reason: {reason}"
        )

    def get_queued_uploads(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get queued uploads ordered by queued_at (oldest first).

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of queued upload dicts
        """
        query = (
            self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION)
            .where("status", "==", "queued")
            .order_by("queued_at")
            .limit(limit)
        )

        return [doc.to_dict() for doc in query.stream()]

    def get_all_queue_entries(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get all queue entries (any status) for admin dashboard.

        Args:
            limit: Maximum entries to return

        Returns:
            List of queue entry dicts, newest first
        """
        query = (
            self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION)
            .order_by("queued_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )

        return [doc.to_dict() for doc in query.stream()]

    def mark_processing(self, job_id: str) -> bool:
        """
        Atomically mark a queued upload as processing.

        Uses a transaction to prevent concurrent processing.

        Returns:
            True if successfully claimed, False if already processing/completed
        """
        doc_ref = self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION).document(job_id)

        @firestore.transactional
        def claim_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if not doc.exists:
                return False

            data = doc.to_dict()
            if data.get("status") != "queued":
                return False

            attempts = data.get("attempts", 0)
            max_attempts = data.get("max_attempts", 5)
            if attempts >= max_attempts:
                transaction.update(doc_ref, {
                    "status": "failed",
                    "last_error": f"Max attempts ({max_attempts}) exceeded",
                    "updated_at": datetime.now(PACIFIC_TZ),
                })
                return False

            transaction.update(doc_ref, {
                "status": "processing",
                "attempts": attempts + 1,
                "updated_at": datetime.now(PACIFIC_TZ),
            })
            return True

        transaction = self.db.transaction()
        return claim_in_transaction(transaction, doc_ref)

    def mark_completed(self, job_id: str, youtube_url: str) -> None:
        """Mark a queued upload as completed with the YouTube URL."""
        doc_ref = self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION).document(job_id)
        doc_ref.update({
            "status": "completed",
            "youtube_url": youtube_url,
            "completed_at": datetime.now(PACIFIC_TZ),
            "updated_at": datetime.now(PACIFIC_TZ),
        })
        logger.info(f"YouTube upload completed for queued job {job_id}: {youtube_url}")

    def mark_failed(self, job_id: str, error: str) -> None:
        """
        Mark a queued upload as failed.

        If max attempts haven't been reached, resets to 'queued' for retry.
        Uses a transaction for atomic read-then-write.
        """
        doc_ref = self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION).document(job_id)

        @firestore.transactional
        def update_in_transaction(transaction):
            doc = doc_ref.get(transaction=transaction)

            if not doc.exists:
                return None

            data = doc.to_dict()
            attempts = data.get("attempts", 0)
            max_attempts = data.get("max_attempts", 5)

            new_status = "failed" if attempts >= max_attempts else "queued"

            transaction.update(doc_ref, {
                "status": new_status,
                "last_error": error,
                "updated_at": datetime.now(PACIFIC_TZ),
            })
            return (new_status, attempts, max_attempts)

        transaction = self.db.transaction()
        result = update_in_transaction(transaction)

        if result:
            new_status, attempts, max_attempts = result
            logger.warning(
                f"YouTube upload {'failed permanently' if new_status == 'failed' else 'will retry'} "
                f"for job {job_id} (attempt {attempts}/{max_attempts}): {error}"
            )

    def retry_upload(self, job_id: str) -> bool:
        """
        Admin manual retry: reset a failed upload back to queued.

        Returns:
            True if reset, False if not found or not in failed/queued state
        """
        doc_ref = self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION).document(job_id)
        doc = doc_ref.get()

        if not doc.exists:
            return False

        data = doc.to_dict()
        if data.get("status") not in ("failed", "queued"):
            return False

        doc_ref.update({
            "status": "queued",
            "attempts": 0,
            "last_error": None,
            "updated_at": datetime.now(PACIFIC_TZ),
        })
        logger.info(f"Admin retry: reset YouTube upload for job {job_id} to queued")
        return True

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics for admin dashboard."""
        queued = len(list(
            self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION)
            .where("status", "==", "queued")
            .select([])
            .stream()
        ))
        processing = len(list(
            self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION)
            .where("status", "==", "processing")
            .select([])
            .stream()
        ))
        failed = len(list(
            self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION)
            .where("status", "==", "failed")
            .select([])
            .stream()
        ))
        completed = len(list(
            self.db.collection(YOUTUBE_UPLOAD_QUEUE_COLLECTION)
            .where("status", "==", "completed")
            .select([])
            .stream()
        ))

        return {
            "queued": queued,
            "processing": processing,
            "failed": failed,
            "completed": completed,
            "total": queued + processing + failed + completed,
        }


# Singleton instance
_youtube_upload_queue_service: Optional[YouTubeUploadQueueService] = None


def get_youtube_upload_queue_service() -> YouTubeUploadQueueService:
    """Get the singleton YouTubeUploadQueueService instance."""
    global _youtube_upload_queue_service
    if _youtube_upload_queue_service is None:
        _youtube_upload_queue_service = YouTubeUploadQueueService()
    return _youtube_upload_queue_service
