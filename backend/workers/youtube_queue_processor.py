"""
YouTube upload queue processor.

Processes deferred YouTube uploads when quota is available.
Called by Cloud Scheduler via an internal endpoint (hourly).
"""
import asyncio
import logging
import os
import shutil
import tempfile
from typing import Dict, Any, Optional

from backend.config import get_settings
from backend.services.community_publish import notify_community_publish
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.youtube_quota_service import get_youtube_quota_service
from backend.services.youtube_upload_queue_service import get_youtube_upload_queue_service


logger = logging.getLogger(__name__)

# Uploads attempted per run (quota-bound anyway) vs entries fetched: the fetch
# is larger so entries whose claim is refused (job mid admin re-render) can't
# fill the page and starve everything behind them.
MAX_UPLOADS_PER_RUN = 20
QUEUE_FETCH_LIMIT = 100


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

    # Get queued uploads. Fetch more than we'll upload so entries whose claim is
    # refused (job mid admin re-render) can't fill the page and starve others.
    queued = queue_service.get_queued_uploads(limit=QUEUE_FETCH_LIMIT)
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

    # One batched read of the jobs (for the admin re-render notification
    # choice) instead of a Firestore round-trip per entry.
    jobs = await asyncio.to_thread(_get_jobs_batch, queue_service.db, [e["job_id"] for e in queued])

    processed = 0
    failed = 0
    attempted = 0

    for entry in queued:
        job_id = entry["job_id"]
        if attempted >= MAX_UPLOADS_PER_RUN:
            break

        # Re-check quota before each upload
        allowed, remaining, message = quota_service.check_quota_available()
        if not allowed:
            logger.info(f"YouTube queue processor: quota exhausted after {processed} uploads")
            break

        # Claim the entry. The claim transaction also reads the job and refuses
        # while an admin re-render is active on it (the re-render's claim in turn
        # refuses while an upload is processing), so the two never overlap.
        if not queue_service.mark_processing(job_id):
            logger.info(f"YouTube queue processor: could not claim job {job_id}, skipping")
            continue
        attempted += 1

        uploaded_url = None
        try:
            youtube_url = await _process_single_upload(job_id, entry, quota_service, settings)
            if youtube_url:
                uploaded_url = youtube_url
                # Record completion FIRST: from here on the entry must never go
                # back to "queued" (that would upload the video a second time).
                queue_service.mark_completed(job_id, youtube_url)
                await _after_successful_upload(job_id, entry, youtube_url, jobs.get(job_id), queue_service)
                processed += 1
            else:
                queue_service.mark_failed(job_id, "Upload returned no URL")
                failed += 1

        except Exception as e:
            error_str = str(e)
            logger.exception(f"YouTube queue processor: failed to process job {job_id}: {e}")

            if uploaded_url:
                # The video IS on YouTube; only recording it failed. Never
                # re-queue — flag for attention instead.
                _flag_post_upload_error(queue_service, job_id, uploaded_url, error_str)
                processed += 1
                continue

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


async def _after_successful_upload(job_id: str, entry: Dict[str, Any], youtube_url: str, job, queue_service) -> None:
    """Post-upload side effects. Each is isolated: a failure is logged and the
    entry flagged for attention, never re-queued (the upload already happened)."""
    try:
        # Update job state_data with the YouTube URL
        _update_job_youtube_url(job_id, youtube_url)
    except Exception as e:
        logger.exception(f"YouTube queue processor: failed to record URL for job {job_id}")
        _flag_post_upload_error(queue_service, job_id, youtube_url, f"update job: {e}")

    # Send follow-up email — unless queued by (or processed during) an admin
    # re-render that wasn't meant to notify the customer.
    from backend.services.admin_rerender_service import suppress_customer_notifications
    if entry.get("notify_user", True) and not (job is not None and suppress_customer_notifications(job)):
        await _send_youtube_upload_notification(job_id, entry, youtube_url)
    else:
        logger.info(f"YouTube queue processor: skipping follow-up email for job {job_id} (notifications suppressed)")

    # If this was a requests-board community pick, mark it published and fan out
    # "your track is live" emails to everyone who voted (idempotent: voters
    # already notified are never re-emailed).
    try:
        await notify_community_publish(job_id, youtube_url)
    except Exception as e:
        logger.exception(f"YouTube queue processor: community publish failed for job {job_id}")
        _flag_post_upload_error(queue_service, job_id, youtube_url, f"community publish: {e}")


def _flag_post_upload_error(queue_service, job_id: str, youtube_url: str, error: str) -> None:
    """Mark an uploaded entry as needing attention — terminal, never re-queued."""
    try:
        queue_service.mark_post_upload_error(job_id, youtube_url, error)
    except Exception:
        logger.exception(f"YouTube queue processor: could not flag post-upload error for job {job_id}")


def _get_jobs_batch(db, job_ids) -> Dict[str, Any]:
    """Read the given jobs in one ``get_all`` call; returns ``{job_id: job view}``.

    The view carries only what the notification check needs (status,
    review_token, state_data). Best-effort: on error, returns ``{}`` (the
    authoritative admin re-render check is in the claim transaction).
    """
    from types import SimpleNamespace

    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return {}
    try:
        collection = db.collection(get_settings().firestore_collection)
        refs = [collection.document(job_id) for job_id in unique_ids]
        jobs = {}
        for snapshot in db.get_all(refs):
            if not snapshot.exists:
                continue
            data = snapshot.to_dict() or {}
            jobs[snapshot.id] = SimpleNamespace(
                job_id=snapshot.id,
                status=data.get("status"),
                review_token=data.get("review_token"),
                state_data=data.get("state_data") or {},
            )
        return jobs
    except Exception as e:
        logger.warning(f"YouTube queue processor: batch job read failed: {e}")
        return {}


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
        # Render title + description + tags via the shared renderer (single source
        # of truth, also used by the live upload + bulk-rewrite tool).
        from backend.services.youtube_description import (
            build_youtube_tags,
            build_youtube_title,
            render_youtube_description,
            translated_language_name,
        )

        translation_name = translated_language_name(job.state_data)
        youtube_title = build_youtube_title(artist, title, translation_name)
        if len(youtube_title) > 95:
            youtube_title = youtube_title[:92] + " ..."

        brand_code = entry.get("brand_code") or job.state_data.get("brand_code")

        description = render_youtube_description(
            artist=artist,
            title=title,
            brand_code=brand_code,
            template=settings.default_youtube_description or None,
            translation_language_name=translation_name,
        )

        # Upload
        video_id, video_url = youtube_service.upload_video(
            video_path=video_path,
            title=youtube_title,
            description=description,
            thumbnail_path=thumbnail_path,
            tags=build_youtube_tags(artist, title),
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
    """Update job state_data with the YouTube URL after deferred upload.

    Raises on failure (including a missing job) so the caller records the queue
    entry as completed-with-``needs_attention`` instead of silently losing the URL.
    """
    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise RuntimeError(f"job {job_id} not found while recording YouTube URL {youtube_url}")
    # Atomic per-field writes: this deferred upload can land while other
    # state_data is being written, so rewriting the whole map from a
    # snapshot could clobber a sibling key.
    job_manager.update_job(job_id, {
        "state_data.youtube_url": youtube_url,
        "state_data.youtube_upload_queued": False,  # No longer queued
    })
    logger.info(f"Updated job {job_id} state_data with YouTube URL")


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
