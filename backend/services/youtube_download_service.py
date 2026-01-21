"""
YouTube download service for cloud backend.

This service provides a single entry point for all YouTube downloads in the cloud.
It automatically routes downloads through the remote flacfetch service when configured,
avoiding YouTube bot detection issues on Cloud Run IPs.

The flow is:
1. Check if remote flacfetch is configured (FLACFETCH_API_URL)
2. If yes: Use FlacfetchClient.download_by_id() - downloads on VM, uploads to GCS
3. If no: Use local yt_dlp (with warning about bot detection risk)

All entry points (audio search selection, direct URL submission) should use this
service for YouTube downloads to ensure consistent behavior.
"""
import asyncio
import logging
import mimetypes
import os
import re
import tempfile
from typing import Optional

from .flacfetch_client import get_flacfetch_client, FlacfetchServiceError
from .storage_service import StorageService

logger = logging.getLogger(__name__)


class YouTubeDownloadError(Exception):
    """Error downloading from YouTube."""
    pass


class YouTubeDownloadService:
    """
    Single point of entry for all YouTube downloads in the cloud backend.

    When remote flacfetch is configured (FLACFETCH_API_URL), downloads happen
    on the flacfetch VM which has YouTube cookies and avoids bot detection.

    When remote is not configured, falls back to local yt_dlp with a warning
    that downloads may be blocked on Cloud Run IPs.

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
        self._storage_service = StorageService()

        if self._flacfetch_client:
            logger.info("YouTubeDownloadService using REMOTE flacfetch (recommended)")
        else:
            logger.warning(
                "YouTubeDownloadService using LOCAL yt_dlp - "
                "downloads may fail due to YouTube bot detection on Cloud Run IPs"
            )

    def is_remote_enabled(self) -> bool:
        """Check if remote flacfetch is configured."""
        return self._flacfetch_client is not None

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
        video_id = self._extract_video_id(url)
        if not video_id:
            raise YouTubeDownloadError(f"Could not extract video ID from URL: {url}")

        logger.info(f"YouTube download: video_id={video_id}, job_id={job_id}")

        if self._flacfetch_client:
            return await self._download_remote(video_id, job_id, artist, title)
        else:
            logger.warning(
                "Remote flacfetch not configured. Attempting local download, "
                "but this may fail due to YouTube bot detection on Cloud Run IPs."
            )
            return await self._download_local(url, job_id, artist, title)

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

    async def _download_local(
        self,
        url: str,
        job_id: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """
        Download using local yt_dlp (fallback when remote not configured).

        Warning: This may fail on Cloud Run due to YouTube bot detection.
        """
        temp_dir = tempfile.mkdtemp(prefix=f"youtube_{job_id}_")

        try:
            from karaoke_gen.file_handler import FileHandler
            from karaoke_gen.utils import sanitize_filename

            # Create FileHandler
            file_handler = FileHandler(
                logger=logger,
                ffmpeg_base_command="ffmpeg -hide_banner -loglevel error -nostats -y",
                create_track_subfolders=False,
                dry_run=False
            )

            # Build output filename
            safe_artist = sanitize_filename(artist) if artist else "Unknown"
            safe_title = sanitize_filename(title) if title else "Unknown"
            output_filename_no_extension = os.path.join(
                temp_dir, f"{safe_artist} - {safe_title}"
            )

            # Get cookies from environment
            cookies_str = os.environ.get("YOUTUBE_COOKIES")
            if cookies_str:
                logger.info("Using YouTube cookies for local download")

            # Download
            logger.info(f"Local YouTube download: {url}")
            downloaded_file = file_handler.download_video(
                url=url,
                output_filename_no_extension=output_filename_no_extension,
                cookies_str=cookies_str
            )

            if not downloaded_file or not os.path.exists(downloaded_file):
                raise YouTubeDownloadError(
                    f"Local download failed - no file returned. "
                    "This is likely due to YouTube bot detection on Cloud Run IPs. "
                    "Configure FLACFETCH_API_URL for remote downloads."
                )

            logger.info(f"Downloaded video: {downloaded_file}")

            # Convert to WAV for processing
            wav_file = file_handler.convert_to_wav(
                input_filename=downloaded_file,
                output_filename_no_extension=output_filename_no_extension
            )

            if not wav_file or not os.path.exists(wav_file):
                raise YouTubeDownloadError("WAV conversion failed")

            logger.info(f"Converted to WAV: {wav_file}")

            # Upload to GCS
            filename = os.path.basename(wav_file)
            gcs_path = f"uploads/{job_id}/audio/{filename}"

            content_type, _ = mimetypes.guess_type(wav_file)
            with open(wav_file, 'rb') as f:
                self._storage_service.upload_fileobj(
                    f,
                    gcs_path,
                    content_type=content_type or 'audio/wav'
                )

            logger.info(f"Uploaded to GCS: {gcs_path}")
            return gcs_path

        except YouTubeDownloadError:
            raise
        except Exception as e:
            logger.error(f"Local YouTube download error: {e}", exc_info=True)
            raise YouTubeDownloadError(f"Local download failed: {e}") from e
        finally:
            # Cleanup temp directory
            try:
                import shutil
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup temp dir: {cleanup_error}")


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
