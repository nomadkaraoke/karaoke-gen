"""
YouTube download service for cloud backend.

This service provides a single entry point for all YouTube downloads in the cloud.
flacfetch is the SOLE downloader: downloads are always routed through the remote
flacfetch service, which has YouTube cookies and avoids bot detection on Cloud Run IPs.

The flow is:
1. Check if remote flacfetch is configured (FLACFETCH_API_URL)
2. If yes: Use FlacfetchClient.download_by_id() - downloads on VM, uploads to GCS
3. If no: raise YouTubeDownloadError — there is NO local yt-dlp fallback. We never
   run yt-dlp inside the Cloud Run container (it's blocked by bot detection and a
   silent fallback previously masked a real download bug — 2026-06-08 incident).

All entry points (audio search selection, direct URL submission) should use this
service for YouTube downloads to ensure consistent behavior.
"""
import logging
import re
from typing import Optional

from .flacfetch_client import get_flacfetch_client, FlacfetchServiceError

logger = logging.getLogger(__name__)


class YouTubeDownloadError(Exception):
    """Error downloading from YouTube."""
    pass


class YouTubeDownloadService:
    """
    Single point of entry for all YouTube downloads in the cloud backend.

    When remote flacfetch is configured (FLACFETCH_API_URL), downloads happen
    on the flacfetch VM which has YouTube cookies and avoids bot detection.

    When remote is not configured, this raises YouTubeDownloadError. There is no
    local yt-dlp fallback — flacfetch is the sole downloader.

    Usage:
        service = get_youtube_download_service()
        gcs_path = await service.download(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            job_id="job123",
            artist="Rick Astley",
            title="Never Gonna Give You Up",
        )
    """

    def __init__(self):
        self._flacfetch_client = get_flacfetch_client()

        if self._flacfetch_client:
            logger.info("YouTubeDownloadService using REMOTE flacfetch")
        else:
            logger.warning(
                "YouTubeDownloadService: remote flacfetch NOT configured "
                "(FLACFETCH_API_URL) - URL downloads will fail until it is set"
            )

    def is_remote_enabled(self) -> bool:
        """Check if remote flacfetch is configured."""
        return self._flacfetch_client is not None

    async def check_availability(self, url: str) -> Optional[dict]:
        """
        Check if a YouTube video is available for download.

        Uses the flacfetch /check-youtube endpoint to detect geo-restrictions,
        private/removed videos, etc. before creating a job.

        Returns:
            Check result dict if check succeeds, None if check can't be
            performed (no remote configured, network error, etc.).
            Callers should treat None as "unknown, proceed anyway".
        """
        if not self._flacfetch_client:
            return None

        video_id = self._extract_video_id(url)
        if not video_id:
            return None

        try:
            result = await self._flacfetch_client.check_youtube(url)
            logger.info(
                f"YouTube availability check for {video_id}: "
                f"available={result.get('available')}"
            )
            return result
        except FlacfetchServiceError as e:
            logger.warning(f"YouTube availability check failed for {video_id}, proceeding anyway: {e}")
            return None

    async def download(
        self,
        url: str,
        job_id: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """
        Download YouTube audio and upload to GCS.

        Args:
            url: YouTube URL (any format - watch, youtu.be, shorts, etc.)
            job_id: Job ID for GCS path
            artist: Optional artist name for filename
            title: Optional title for filename

        Returns:
            GCS path (not gs:// prefix, just the path portion)
            Example: "uploads/job123/audio/Artist - Title.flac"

        Raises:
            YouTubeDownloadError: If download fails
        """
        # flacfetch is the SOLE downloader — never run yt-dlp on Cloud Run.
        # If it isn't configured, fail loudly rather than silently falling back
        # to a local download (which is blocked by bot detection on Cloud Run
        # IPs anyway).
        if not self._flacfetch_client:
            raise YouTubeDownloadError(
                "Remote flacfetch is not configured (FLACFETCH_API_URL). "
                "URL downloads require the flacfetch service."
            )

        video_id = self._extract_video_id(url)
        if video_id:
            logger.info(f"URL download (YouTube): video_id={video_id}, job_id={job_id}")
            return await self._download_remote(video_id, job_id, artist, title)

        # Any other yt-dlp-supported site (Facebook, SoundCloud, TikTok, ...).
        logger.info(f"URL download (generic): url={url}, job_id={job_id}")
        return await self._download_remote_url(url, job_id, artist, title)

    async def download_by_id(
        self,
        video_id: str,
        job_id: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """
        Download YouTube audio by video ID.

        Same as download() but takes a video ID directly instead of URL.

        Args:
            video_id: YouTube video ID (e.g., "dQw4w9WgXcQ")
            job_id: Job ID for GCS path
            artist: Optional artist name for filename
            title: Optional title for filename

        Returns:
            GCS path (path portion only, no gs:// prefix)

        Raises:
            YouTubeDownloadError: If download fails
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        return await self.download(url, job_id, artist, title)

    def _extract_video_id(self, url: str) -> Optional[str]:
        """
        Extract YouTube video ID from various URL formats.

        Supports:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/shorts/VIDEO_ID
        - https://www.youtube.com/embed/VIDEO_ID
        - https://youtube.com/v/VIDEO_ID

        Returns:
            Video ID string, or None if extraction fails
        """
        patterns = [
            # Standard watch URL
            r'(?:youtube\.com/watch\?v=|youtube\.com/watch\?.*&v=)([a-zA-Z0-9_-]{11})',
            # Short URL
            r'youtu\.be/([a-zA-Z0-9_-]{11})',
            # Shorts URL
            r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
            # Embed URL
            r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
            # Old-style URL
            r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    async def _download_remote(
        self,
        video_id: str,
        job_id: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """Download using remote flacfetch service."""
        gcs_destination = f"uploads/{job_id}/audio/"

        # Build output filename if artist/title provided
        output_filename = None
        if artist and title:
            from karaoke_gen.utils import sanitize_filename
            safe_artist = sanitize_filename(artist)
            safe_title = sanitize_filename(title)
            output_filename = f"{safe_artist} - {safe_title}"

        logger.info(
            f"Remote YouTube download: video_id={video_id}, "
            f"gcs_path={gcs_destination}, filename={output_filename}"
        )

        try:
            # Start download
            download_id = await self._flacfetch_client.download_by_id(
                source_name="YouTube",
                source_id=video_id,
                output_filename=output_filename,
                gcs_path=gcs_destination,
            )

            logger.info(f"Remote download started: {download_id}")

            # Wait for completion
            def log_progress(status):
                progress = status.get("progress", 0)
                speed = status.get("download_speed_kbps", 0)
                logger.debug(f"Download progress: {progress:.1f}% ({speed:.1f} KB/s)")

            final_status = await self._flacfetch_client.wait_for_download(
                download_id,
                timeout=300,  # 5 minute timeout for YouTube downloads
                progress_callback=log_progress,
            )

            # Extract GCS path from response
            gcs_path = final_status.get("gcs_path")
            if not gcs_path:
                raise YouTubeDownloadError(
                    "Remote download completed but no GCS path returned"
                )

            # Convert gs:// URL to path portion
            if gcs_path.startswith("gs://"):
                parts = gcs_path.replace("gs://", "").split("/", 1)
                if len(parts) == 2:
                    gcs_path = parts[1]

            logger.info(f"Remote YouTube download complete: {gcs_path}")
            return gcs_path

        except FlacfetchServiceError as e:
            raise YouTubeDownloadError(f"Remote download failed: {e}") from e
        except Exception as e:
            logger.error(f"Remote YouTube download error: {e}", exc_info=True)
            raise YouTubeDownloadError(f"Remote download failed: {e}") from e

    async def _download_remote_url(
        self,
        url: str,
        job_id: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """
        Download an arbitrary yt-dlp-supported URL via remote flacfetch.

        Used for non-YouTube sites (Facebook, SoundCloud, TikTok, Vimeo, ...).
        The URL is handed to flacfetch's generic "URL" source, which downloads
        on the VM and uploads to GCS.
        """
        gcs_destination = f"uploads/{job_id}/audio/"

        output_filename = None
        if artist and title:
            from karaoke_gen.utils import sanitize_filename
            output_filename = f"{sanitize_filename(artist)} - {sanitize_filename(title)}"

        logger.info(
            f"Remote URL download: url={url}, "
            f"gcs_path={gcs_destination}, filename={output_filename}"
        )

        try:
            download_id = await self._flacfetch_client.download_by_id(
                source_name="URL",
                source_id=url,
                download_url=url,
                output_filename=output_filename,
                gcs_path=gcs_destination,
            )

            logger.info(f"Remote URL download started: {download_id}")

            def log_progress(status):
                progress = status.get("progress", 0)
                logger.debug(f"Download progress: {progress:.1f}%")

            final_status = await self._flacfetch_client.wait_for_download(
                download_id,
                timeout=300,
                progress_callback=log_progress,
            )

            gcs_path = final_status.get("gcs_path")
            if not gcs_path:
                raise YouTubeDownloadError(
                    "Remote download completed but no GCS path returned"
                )

            if gcs_path.startswith("gs://"):
                parts = gcs_path.replace("gs://", "").split("/", 1)
                if len(parts) == 2:
                    gcs_path = parts[1]

            logger.info(f"Remote URL download complete: {gcs_path}")
            return gcs_path

        except FlacfetchServiceError as e:
            raise YouTubeDownloadError(f"Remote download failed: {e}") from e
        except YouTubeDownloadError:
            raise
        except Exception as e:
            logger.error(f"Remote URL download error: {e}", exc_info=True)
            raise YouTubeDownloadError(f"Remote download failed: {e}") from e


# Singleton instance
_youtube_download_service: Optional[YouTubeDownloadService] = None


def get_youtube_download_service() -> YouTubeDownloadService:
    """Get the singleton YouTubeDownloadService instance."""
    global _youtube_download_service
    if _youtube_download_service is None:
        _youtube_download_service = YouTubeDownloadService()
    return _youtube_download_service


def reset_youtube_download_service():
    """Reset the singleton (for testing)."""
    global _youtube_download_service
    _youtube_download_service = None
