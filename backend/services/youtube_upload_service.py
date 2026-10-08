"""
YouTube Upload Service.

Provides video upload functionality for YouTube, extracted from KaraokeFinalise
for use by both the cloud backend (video_worker) and local CLI.

This service handles:
- Video uploads with metadata (title, description, tags)
- Thumbnail uploads
- Duplicate video detection and replacement
- Authentication via pre-stored credentials or client secrets file
"""

import logging
import os
import time
from typing import Optional, Dict, Any, Tuple

from thefuzz import fuzz

logger = logging.getLogger(__name__)


class YouTubeUploadService:
    """
    Service for uploading videos to YouTube.

    Supports two authentication modes:
    1. Pre-stored credentials (for server-side/non-interactive use)
    2. Client secrets file (for interactive CLI use with browser OAuth)
    """

    YOUTUBE_URL_PREFIX = "https://www.youtube.com/watch?v="
    SCOPES = ["https://www.googleapis.com/auth/youtube"]

    # Resumable-upload robustness. Within one upload session, googleapiclient
    # resumes chunks itself on 5xx/429/rate-limit responses (``num_retries``).
    # A 404/410 means YouTube dropped the session entirely ("Gone") — nothing
    # was created, so the only fix is a fresh session from byte 0.
    UPLOAD_NUM_RETRIES = 3
    UPLOAD_SESSION_ATTEMPTS = 3
    UPLOAD_SESSION_LOST_STATUSES = (404, 410)
    UPLOAD_RESTART_BACKOFF_SECONDS = 5

    def __init__(
        self,
        credentials: Optional[Dict[str, Any]] = None,
        client_secrets_file: Optional[str] = None,
        non_interactive: bool = False,
        server_side_mode: bool = False,
        dry_run: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the YouTube upload service.

        Args:
            credentials: Pre-stored OAuth credentials dict with keys:
                - token: Access token
                - refresh_token: Refresh token
                - token_uri: Token endpoint URL
                - client_id: OAuth client ID
                - client_secret: OAuth client secret
                - scopes: List of OAuth scopes
            client_secrets_file: Path to OAuth client secrets JSON file
                (for interactive authentication)
            non_interactive: If True, skip interactive prompts
            server_side_mode: If True, use exact matching for duplicate detection
            dry_run: If True, log actions without performing them
            logger: Optional logger instance
        """
        self.credentials = credentials
        self.client_secrets_file = client_secrets_file
        self.non_interactive = non_interactive
        self.server_side_mode = server_side_mode
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger(__name__)

        self._youtube_service = None
        self._channel_id = None

    def _authenticate(self):
        """
        Authenticate with YouTube API and return service object.

        Returns:
            YouTube API service object

        Raises:
            Exception: If authentication fails or is required in non-interactive mode
        """
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google_auth_oauthlib.flow import InstalledAppFlow
        import pickle

        # Check if we have pre-stored credentials (for non-interactive mode)
        if self.credentials and self.non_interactive:
            try:
                creds = Credentials(
                    token=self.credentials.get('token'),
                    refresh_token=self.credentials.get('refresh_token'),
                    token_uri=self.credentials.get('token_uri'),
                    client_id=self.credentials.get('client_id'),
                    client_secret=self.credentials.get('client_secret'),
                    scopes=self.credentials.get('scopes', self.SCOPES)
                )

                # Refresh token if needed
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())

                youtube = build('youtube', 'v3', credentials=creds)
                self.logger.info("Successfully authenticated with YouTube using pre-stored credentials")
                return youtube

            except Exception as e:
                self.logger.error(f"Failed to authenticate with pre-stored credentials: {str(e)}")
                # Fall through to original authentication if pre-stored credentials fail

        # For non-interactive mode without pre-stored credentials, raise error
        if self.non_interactive:
            raise Exception(
                "YouTube authentication required but running in non-interactive mode. "
                "Please pre-authenticate or disable YouTube upload."
            )

        # Interactive authentication using client secrets file
        if not self.client_secrets_file:
            raise Exception("No YouTube credentials or client secrets file provided")

        # Token file stores the user's access and refresh tokens
        youtube_token_file = "/tmp/karaoke-finalise-youtube-token.pickle"

        creds = None

        # Check if we have saved credentials
        if os.path.exists(youtube_token_file):
            with open(youtube_token_file, "rb") as token:
                creds = pickle.load(token)

        # If there are no valid credentials, let the user log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, scopes=self.SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save the credentials for the next run
            with open(youtube_token_file, "wb") as token:
                pickle.dump(creds, token)

        return build("youtube", "v3", credentials=creds)

    @property
    def youtube_service(self):
        """Lazy-load YouTube service on first access."""
        if self._youtube_service is None:
            self._youtube_service = self._authenticate()
        return self._youtube_service

    def get_channel_id(self) -> Optional[str]:
        """
        Get the authenticated user's YouTube channel ID.

        Returns:
            Channel ID string, or None if not found
        """
        if self._channel_id:
            return self._channel_id

        request = self.youtube_service.channels().list(part="snippet", mine=True)
        response = request.execute()

        if "items" in response and len(response["items"]) > 0:
            self._channel_id = response["items"][0]["id"]
            return self._channel_id

        return None

    def check_duplicate(
        self,
        title: str,
        exact_match: Optional[bool] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Check if a video with the given title already exists on the channel.

        Args:
            title: Video title to search for
            exact_match: If True, require exact title match. If None, uses
                server_side_mode setting (exact in server mode, fuzzy in CLI)

        Returns:
            Tuple of (exists: bool, video_id: Optional[str], video_url: Optional[str])
        """
        channel_id = self.get_channel_id()
        if not channel_id:
            self.logger.warning("Could not get channel ID, skipping duplicate check")
            return False, None, None

        use_exact_match = exact_match if exact_match is not None else self.server_side_mode

        self.logger.info(f"Searching YouTube channel {channel_id} for title: {title}")
        request = self.youtube_service.search().list(
            part="snippet",
            channelId=channel_id,
            q=title,
            type="video",
            maxResults=10
        )
        response = request.execute()

        if "items" not in response or len(response["items"]) == 0:
            self.logger.info(f"No matching video found with title: {title}")
            return False, None, None

        for item in response["items"]:
            # Verify the video actually belongs to our channel
            result_channel_id = item["snippet"]["channelId"]
            if result_channel_id != channel_id:
                self.logger.debug(
                    f"Skipping video from different channel: {item['snippet']['title']} "
                    f"(channel: {result_channel_id})"
                )
                continue

            found_title = item["snippet"]["title"]

            # Determine if this is a match
            if use_exact_match:
                is_match = title.lower() == found_title.lower()
                similarity_score = 100 if is_match else 0
            else:
                similarity_score = fuzz.ratio(title.lower(), found_title.lower())
                is_match = similarity_score >= 70

            if is_match:
                video_id = item["id"]["videoId"]
                video_url = f"{self.YOUTUBE_URL_PREFIX}{video_id}"
                self.logger.info(
                    f"Potential match found on YouTube channel with ID: {video_id} "
                    f"and title: {found_title} (similarity: {similarity_score}%)"
                )

                # In non-interactive mode, return the match directly
                if self.non_interactive:
                    self.logger.info("Non-interactive mode, found a match.")
                    return True, video_id, video_url

                # Interactive confirmation
                confirmation = input(
                    f"Is '{found_title}' the video you are finalising? (y/n): "
                ).strip().lower()
                if confirmation == "y":
                    return True, video_id, video_url

        self.logger.info(f"No matching video found with title: {title}")
        return False, None, None

    def delete_video(self, video_id: str) -> bool:
        """
        Delete a YouTube video by its ID.

        Args:
            video_id: The YouTube video ID to delete

        Returns:
            True if successful, False otherwise
        """
        self.logger.info(f"Deleting YouTube video with ID: {video_id}")

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would delete YouTube video with ID: {video_id}")
            return True

        try:
            self.youtube_service.videos().delete(id=video_id).execute()
            self.logger.info(f"Successfully deleted YouTube video with ID: {video_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete YouTube video with ID {video_id}: {e}")
            return False

    @staticmethod
    def truncate_title(title: str, max_length: int = 95) -> str:
        """
        Truncate title to the nearest whole word within max_length.

        Args:
            title: Title to truncate
            max_length: Maximum length (default 95 for YouTube)

        Returns:
            Truncated title with "..." if needed
        """
        if len(title) <= max_length:
            return title
        truncated_title = title[:max_length].rsplit(" ", 1)[0]
        if len(truncated_title) < max_length:
            truncated_title += " ..."
        return truncated_title

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        thumbnail_path: Optional[str] = None,
        tags: Optional[list] = None,
        category_id: str = "10",  # Music category
        privacy_status: str = "public",
        replace_existing: bool = False,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Upload a video to YouTube with metadata and optional thumbnail.

        Args:
            video_path: Path to the video file to upload
            title: Video title (will be truncated to 95 chars if needed)
            description: Video description
            thumbnail_path: Optional path to thumbnail image
            tags: Optional list of tags/keywords
            category_id: YouTube category ID (default "10" for Music)
            privacy_status: "public", "private", or "unlisted"
            replace_existing: If True, delete existing video with same title

        Returns:
            Tuple of (video_id, video_url) or (None, None) if upload failed/skipped
        """
        from googleapiclient.http import MediaFileUpload

        # Truncate title if needed
        youtube_title = self.truncate_title(title)

        self.logger.info(f"Uploading video to YouTube: {youtube_title}")

        if self.dry_run:
            self.logger.info(
                f"DRY RUN: Would upload {video_path} to YouTube with title: {youtube_title}"
            )
            return "dry_run_video_id", f"{self.YOUTUBE_URL_PREFIX}dry_run_video_id"

        # Check for existing video
        should_replace = True if self.server_side_mode else replace_existing
        exists, existing_id, existing_url = self.check_duplicate(youtube_title)

        if exists:
            if should_replace:
                self.logger.info(f"Video already exists on YouTube, deleting before re-upload: {existing_url}")
                if self.delete_video(existing_id):
                    self.logger.info("Successfully deleted existing video, proceeding with upload")
                else:
                    self.logger.error("Failed to delete existing video, aborting upload")
                    return None, None
            else:
                self.logger.warning(f"Video already exists on YouTube, skipping upload: {existing_url}")
                return existing_id, existing_url

        # Prepare video metadata
        body = {
            "snippet": {
                "title": youtube_title,
                "description": description,
                "tags": tags or [],
                "categoryId": category_id,
            },
            "status": {"privacyStatus": privacy_status},
        }

        # Determine MIME type based on file extension
        ext = os.path.splitext(video_path)[1].lower()
        mime_type = {
            ".mkv": "video/x-matroska",
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".avi": "video/x-msvideo",
        }.get(ext, "video/*")

        # Upload video
        response = self._execute_resumable_insert(video_path, mime_type, body)

        video_id = response.get("id")
        video_url = f"{self.YOUTUBE_URL_PREFIX}{video_id}"
        self.logger.info(f"Uploaded video to YouTube: {video_url}")

        # Upload thumbnail if provided
        if thumbnail_path and os.path.isfile(thumbnail_path):
            try:
                self.logger.info(f"Uploading thumbnail from: {thumbnail_path}")
                media_thumbnail = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
                self.youtube_service.thumbnails().set(
                    videoId=video_id,
                    media_body=media_thumbnail
                ).execute()
                self.logger.info(f"Uploaded thumbnail for video ID {video_id}")
            except Exception as e:
                self.logger.error(f"Failed to upload thumbnail: {e}")
                self.logger.warning(
                    "Video uploaded but thumbnail not set. "
                    "You may need to set it manually on YouTube."
                )
        elif thumbnail_path:
            self.logger.warning(f"Thumbnail file not found, skipping: {thumbnail_path}")

        return video_id, video_url

    def _execute_resumable_insert(self, video_path: str, mime_type: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Run ``videos.insert`` as a resumable upload, restarting if the session is lost."""
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        for attempt in range(1, self.UPLOAD_SESSION_ATTEMPTS + 1):
            media_file = MediaFileUpload(video_path, mimetype=mime_type, resumable=True)
            self.logger.info(f"Uploading video to YouTube (attempt {attempt}/{self.UPLOAD_SESSION_ATTEMPTS})...")
            request = self.youtube_service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media_file
            )
            try:
                return request.execute(num_retries=self.UPLOAD_NUM_RETRIES)
            except HttpError as e:
                status = getattr(e.resp, "status", None)
                if status not in self.UPLOAD_SESSION_LOST_STATUSES or attempt == self.UPLOAD_SESSION_ATTEMPTS:
                    raise
                delay = self.UPLOAD_RESTART_BACKOFF_SECONDS * attempt
                self.logger.warning(
                    f"YouTube upload session lost (HTTP {status}), restarting upload in {delay}s: {e}"
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")  # loop always returns or raises


# Singleton instance and factory function (following existing service pattern)
_youtube_upload_service: Optional[YouTubeUploadService] = None


def get_youtube_upload_service(
    credentials: Optional[Dict[str, Any]] = None,
    client_secrets_file: Optional[str] = None,
    **kwargs
) -> YouTubeUploadService:
    """
    Get a YouTube upload service instance.

    For server-side use, pass credentials from YouTubeService.
    For CLI use, pass client_secrets_file.

    Args:
        credentials: Pre-stored OAuth credentials dict
        client_secrets_file: Path to OAuth client secrets JSON file
        **kwargs: Additional arguments passed to YouTubeUploadService

    Returns:
        YouTubeUploadService instance
    """
    global _youtube_upload_service

    # Create new instance if credentials/settings changed
    if _youtube_upload_service is None or credentials or client_secrets_file:
        _youtube_upload_service = YouTubeUploadService(
            credentials=credentials,
            client_secrets_file=client_secrets_file,
            **kwargs
        )

    return _youtube_upload_service
