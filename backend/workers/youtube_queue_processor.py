"""
YouTube upload queue processor.

Processes deferred YouTube uploads when quota is available.
Called by Cloud Scheduler via an internal endpoint (hourly).
"""
import logging
import os
import shutil
import tempfile
from typing import Dict, Any, Optional

from backend.config import get_settings
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.youtube_quota_service import get_youtube_quota_service
from backend.services.youtube_upload_queue_service import get_youtube_upload_queue_service


logger = logging.getLogger(__name__)


async def process_youtube_upload_queue() -> Dict[str, Any]:
    """
    Process queued YouTube uploads.

    Checks quota availability, then processes queued uploads one at a time.
    Stops if quota is exhausted during processing.

    Returns:
        Summary dict with counts of processed, failed, and remaining items
    """
    settings = get_settings()
    quota_service = get_youtube_quota_service()
    queue_service = get_youtube_upload_queue_service()

    # Check if any quota is available
    allowed, remaining, message = quota_service.check_quota_available()
    if not allowed:
        logger.info(f"YouTube queue processor: no quota available, skipping. {message}")
        return {
            "status": "skipped",
            "reason": "no_quota",
            "message": message,
            "processed": 0,
            "failed": 0,
            "remaining": len(queue_service.get_queued_uploads()),
        }

    # Get queued uploads
    queued = queue_service.get_queued_uploads(limit=20)
    if not queued:
        logger.info("YouTube queue processor: no uploads queued")
        return {
            "status": "empty",
            "message": "No uploads queued",
            "processed": 0,
            "failed": 0,
            "remaining": 0,
        }

    logger.info(f"YouTube queue processor: processing {len(queued)} queued uploads")

    processed = 0
    failed = 0

    for entry in queued:
        job_id = entry["job_id"]

        # Re-check quota before each upload
        allowed, remaining, message = quota_service.check_quota_available()
        if not allowed:
            logger.info(f"YouTube queue processor: quota exhausted after {processed} uploads")
            break

        # Claim the entry
        if not queue_service.mark_processing(job_id):
            logger.info(f"YouTube queue processor: could not claim job {job_id}, skipping")
            continue

        try:
            youtube_url = await _process_single_upload(job_id, entry, quota_service, settings)
            if youtube_url:
                queue_service.mark_completed(job_id, youtube_url)

                # Update job state_data with the YouTube URL
                _update_job_youtube_url(job_id, youtube_url)

                # Send follow-up email
                await _send_youtube_upload_notification(job_id, entry, youtube_url)

                processed += 1
            else:
                queue_service.mark_failed(job_id, "Upload returned no URL")
                failed += 1

        except Exception as e:
            error_str = str(e)
            logger.exception(f"YouTube queue processor: failed to process job {job_id}: {e}")

            # If quota exceeded, stop processing entirely
            if "quotaExceeded" in error_str:
                queue_service.mark_failed(job_id, f"Quota exceeded: {error_str}")
                logger.warning("YouTube queue processor: quota exceeded, stopping")
                failed += 1
                break

            queue_service.mark_failed(job_id, error_str)
            failed += 1

    remaining_count = len(queue_service.get_queued_uploads())
    logger.info(
        f"YouTube queue processor: done. processed={processed} failed={failed} remaining={remaining_count}"
    )

    return {
        "status": "processed",
        "processed": processed,
        "failed": failed,
        "remaining": remaining_count,
    }


async def _process_single_upload(
    job_id: str,
    entry: Dict[str, Any],
    quota_service,
    settings,
) -> Optional[str]:
    """
    Process a single queued YouTube upload.

    Downloads the video from GCS, uploads to YouTube, records quota.

    Returns:
        YouTube URL if successful, None otherwise
    """
    job_manager = JobManager()
    storage = StorageService()

    job = job_manager.get_job(job_id)
    if not job:
        logger.error(f"YouTube queue processor: job {job_id} not found")
        return None

    # Create temp directory for the download
    temp_dir = tempfile.mkdtemp(prefix=f"yt-queue-{job_id[:8]}-")

    try:
        # Find the video file in GCS (prefer MKV, then lossless MP4, then lossy)
        video_path = _download_video_from_gcs(job_id, job, storage, temp_dir)
        if not video_path:
            logger.error(f"YouTube queue processor: no video file found for job {job_id}")
            return None

        # Download thumbnail if available
        thumbnail_path = _download_thumbnail_from_gcs(job_id, job, storage, temp_dir)

        # Build YouTube service with fresh credentials
        youtube_service = _create_youtube_service(settings)
        if not youtube_service:
            logger.error("YouTube queue processor: failed to create YouTube service")
            return None

        # Build metadata
        artist = entry.get("artist", job.artist or "Unknown")
        title = entry.get("title", job.title or "Unknown")
        youtube_title = f"{artist} - {title} (Karaoke)"
        if len(youtube_title) > 95:
            youtube_title = youtube_title[:92] + " ..."

        description = settings.default_youtube_description or ""
        brand_code = entry.get("brand_code") or job.state_data.get("brand_code")
        if brand_code:
            description = f"{description}\n\nBrand Code: {brand_code}".strip()

        # Upload
        video_id, video_url = youtube_service.upload_video(
            video_path=video_path,
            title=youtube_title,
            description=description,
            thumbnail_path=thumbnail_path,
            tags=["karaoke", artist, title],
            replace_existing=True,
        )

        if video_url:
            # Record upload in pending buffer (bridges ~7min GCP monitoring delay)
            quota_service.record_upload(job_id)

            logger.info(f"YouTube queue processor: uploaded job {job_id} -> {video_url}")
            return video_url

        return None

    finally:
        # Cleanup temp directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def _download_video_from_gcs(
    job_id: str, job, storage: StorageService, temp_dir: str
) -> Optional[str]:
    """Download the best available video file from GCS."""
    file_urls = job.file_urls or {}
    finals = file_urls.get("finals", {}) if isinstance(file_urls.get("finals"), dict) else {}

    # Priority order: MKV (FLAC audio) > lossless MP4 > lossy 4K MP4 > lossy 720p MP4
    candidates = [
        (finals.get("lossless_4k_mkv"), "lossless_4k_mkv", ".mkv"),
        (finals.get("lossless_4k_mp4"), "lossless_4k_mp4", ".mp4"),
        (finals.get("lossy_4k_mp4"), "lossy_4k_mp4", ".mp4"),
        (finals.get("lossy_720p_mp4"), "lossy_720p_mp4", ".mp4"),
    ]

    for gcs_path, key, ext in candidates:
        if gcs_path:
            local_path = os.path.join(temp_dir, f"video{ext}")
            try:
                storage.download_file(gcs_path, local_path)
                if os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
                    logger.info(f"Downloaded {key} from GCS for job {job_id}")
                    return local_path
            except Exception as e:
                logger.warning(f"Failed to download {key} for job {job_id}: {e}")

    return None


def _download_thumbnail_from_gcs(
    job_id: str, job, storage: StorageService, temp_dir: str
) -> Optional[str]:
    """Download the thumbnail from GCS if available."""
    file_urls = job.file_urls or {}
    screens = file_urls.get("screens", {}) if isinstance(file_urls.get("screens"), dict) else {}
    thumbnail_url = screens.get("title_jpg")
    if not thumbnail_url:
        return None

    local_path = os.path.join(temp_dir, "thumbnail.jpg")
    try:
        storage.download_file(thumbnail_url, local_path)
        if os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
            return local_path
    except Exception as e:
        logger.warning(f"Failed to download thumbnail for job {job_id}: {e}")

    return None


def _create_youtube_service(settings):
    """Create a YouTube upload service with fresh credentials from Secret Manager."""
    try:
        from backend.services.youtube_upload_service import YouTubeUploadService
        import json

        youtube_creds_json = settings.get_secret("youtube-oauth-credentials")
        if not youtube_creds_json:
            logger.error("YouTube OAuth credentials not found in Secret Manager")
            return None

        credentials = json.loads(youtube_creds_json)
        return YouTubeUploadService(
            credentials=credentials,
            non_interactive=True,
            server_side_mode=True,
            logger=logger,
        )
    except Exception as e:
        logger.exception(f"Failed to create YouTube service: {e}")
        return None


def _update_job_youtube_url(job_id: str, youtube_url: str) -> None:
    """Update job state_data with the YouTube URL after deferred upload."""
    try:
        job_manager = JobManager()
        job = job_manager.get_job(job_id)
        if job:
            state_data = job.state_data or {}
            state_data["youtube_url"] = youtube_url
            state_data["youtube_upload_queued"] = False  # No longer queued
            job_manager.update_job(job_id, {"state_data": state_data})
            logger.info(f"Updated job {job_id} state_data with YouTube URL")
    except Exception as e:
        logger.error(f"Failed to update job {job_id} with YouTube URL: {e}")


async def _send_youtube_upload_notification(
    job_id: str, entry: Dict[str, Any], youtube_url: str
) -> None:
    """Send follow-up email notifying user their YouTube upload is complete."""
    try:
        from backend.services.job_notification_service import get_job_notification_service
        notification_service = get_job_notification_service()

        await notification_service.send_youtube_upload_complete_email(
            job_id=job_id,
            user_email=entry.get("user_email", ""),
            artist=entry.get("artist", ""),
            title=entry.get("title", ""),
            youtube_url=youtube_url,
            brand_code=entry.get("brand_code"),
        )
    except Exception as e:
        logger.error(f"Failed to send YouTube upload notification for job {job_id}: {e}")
        # Don't fail the upload over a notification error - mark it in the queue
        try:
            from backend.services.youtube_upload_queue_service import get_youtube_upload_queue_service
            queue_service = get_youtube_upload_queue_service()
            doc_ref = queue_service.db.collection("youtube_upload_queue").document(job_id)
            doc_ref.update({"notification_sent": False, "notification_error": str(e)})
        except Exception:
            pass
