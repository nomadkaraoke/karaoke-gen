"""Requests-board publish fan-out.

When a community-pick job goes live on YouTube, its ``song_request`` must advance
to ``published`` and every up-voter (other than the owner, who already got the job
completion email) should get a "your track is live" email.

A job can reach YouTube via **two** paths, and both must trigger this:
  1. the quota-managed ``youtube_upload_queue`` (``youtube_queue_processor``), and
  2. a direct upload during video-worker distribution (``video_worker``), which
     never touches that queue.

Historically only path (1) ran the fan-out, so direct-published community picks
(e.g. the first real user request) stayed stuck at ``in_progress`` and their
board "Recently made" entry never appeared. This shared helper is called from
both paths so the outcome is identical regardless of how the video was published.
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _youtube_url_for_job(job) -> Optional[str]:
    """The published YouTube URL for a job, if any, checking both the state_data
    field (set on the direct-publish path) and the distribution metadata."""
    if job is None:
        return None
    state = getattr(job, "state_data", None) or {}
    url = state.get("youtube_url")
    if url:
        return url
    meta = getattr(job, "processing_metadata", None) or {}
    dist = meta.get("distribution") or {}
    return dist.get("youtube_video_url") or None


async def notify_community_publish(job_id: str, youtube_url: str) -> Optional[str]:
    """Advance a community pick to ``published`` and fan out voter emails.

    Idempotent and best-effort: safe to call from both publish paths and to
    re-run after a partial failure. Marking published is always safe to repeat;
    voter emails are guarded by the request's ``notified_voters`` /
    ``voters_notified`` flags so no one is emailed twice.

    Returns the request id if this was a community pick, else ``None`` (a normal,
    non-community job — no-op).
    """
    try:
        from backend.services.song_request_service import get_song_request_service
        service = get_song_request_service()
        request = service.get_by_job_id(job_id)
        if request is None:
            return None  # Not a community pick — normal job.

        service.mark_published(request.id, youtube_url)

        if request.voters_notified:
            return request.id

        # Email up-voters we haven't already reached (retry-safe): exclude the
        # owner (already got the completion email) and anyone previously notified.
        owner = (request.owner_email or request.submitted_by or "").lower()
        already = {v.lower() for v in (request.notified_voters or [])}
        pending = [
            v for v in service.list_upvoters(request.id)
            if v != owner and v not in already
        ]

        from backend.services.job_notification_service import get_job_notification_service
        notification_service = get_job_notification_service()
        succeeded = 0
        for voter in pending:
            try:
                ok = await notification_service.send_community_track_live_email(
                    to_email=voter,
                    artist=request.artist,
                    title=request.title,
                    youtube_url=youtube_url,
                )
            except Exception:
                logger.exception("Failed community voter email for job %s / %s", job_id, voter)
                continue
            if ok:
                # Record each success immediately so a crash mid-loop never
                # re-emails an already-notified voter on the retry.
                service.add_notified_voters(request.id, [voter])
                succeeded += 1

        # Only flag "fully notified" when every pending voter was reached — a
        # partial failure leaves the flag unset so a re-run retries just the misses.
        if succeeded == len(pending):
            service.mark_voters_notified(request.id)
        logger.info(
            "community pick %s published (job %s): notified %d/%d pending voters",
            request.id, job_id, succeeded, len(pending),
        )
        return request.id
    except Exception as e:
        logger.error("Failed community voter fan-out for job %s: %s", job_id, e)
        return None


async def reconcile_community_publishes() -> Dict[str, Any]:
    """Safety net / backfill: find community picks stuck at ``in_progress`` whose
    job is actually live on YouTube, and run the publish transition for each.

    Covers picks that were published before the fan-out was wired into every
    publish path (and any future path that forgets to call it). Fully idempotent.
    """
    from backend.services.job_manager import JobManager
    from backend.services.song_request_service import get_song_request_service

    service = get_song_request_service()
    job_manager = JobManager()

    scanned = 0
    published = []
    for request in service.list_in_progress():
        scanned += 1
        if not request.job_id:
            continue
        url = _youtube_url_for_job(job_manager.get_job(request.job_id))
        if not url:
            continue
        result_id = await notify_community_publish(request.job_id, url)
        if result_id:
            published.append({"request_id": result_id, "job_id": request.job_id, "youtube_url": url})

    logger.info("community publish reconcile: scanned %d in_progress, published %d", scanned, len(published))
    return {"scanned": scanned, "published": published}
