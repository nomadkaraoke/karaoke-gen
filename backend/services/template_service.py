"""
Template service for managing email templates stored in GCS.

Templates are stored in GCS bucket and rendered with job-specific variables.
This allows updating email content without code deployment.

Template locations:
- gs://{bucket}/templates/job-completion.txt - Job completion email (plain text)
- gs://{bucket}/templates/action-needed-lyrics.txt - Combined review reminder (lyrics + instrumental)
- gs://{bucket}/templates/action-needed-instrumental.txt - Instrumental selection reminder (finalise-only jobs)
"""
import logging
import re
from typing import Optional, Dict, Any

from backend.config import get_settings


logger = logging.getLogger(__name__)


# Default templates (fallback if GCS fetch fails)
DEFAULT_JOB_COMPLETION_TEMPLATE = """Hi {name},

Thanks for your order!

Here's the link for the karaoke video published to YouTube:
{youtube_url}

Here's the dropbox folder with all the finished files and source files, including:
- "(Final Karaoke Lossless).mkv": combined karaoke video in 4k H264 with lossless FLAC audio
- "(Final Karaoke).mp4": combined karaoke video with title/end screen in 4k H264/AAC
- "(Final Karaoke 720p).mp4": combined karaoke video in 720p H264/AAC (smaller file for older systems)
- "(With Vocals).mp4": sing along video in 4k H264/AAC with original vocals
- "(Karaoke).mov": karaoke video output from MidiCo (no title/end screen)
- "(Title).mov"/"(End).mov": title card and end screen videos
- "(Final Karaoke CDG).zip": CDG+MP3 format for older/commercial karaoke systems
- "(Final Karaoke TXT).zip": TXT+MP3 format for Power Karaoke
- stems/*.flac: various separated instrumental and vocal audio stems in lossless format
- lyrics/*.txt song lyrics from various sources in plain text format

{dropbox_url}

Let me know if anything isn't perfect and I'll happily tweak / fix, or if you need it in any other format I can probably convert it for you!

If you have a moment, I'd really appreciate your feedback (takes 2 minutes):
{feedback_url}

Thanks again and have a great day!
-Andrew
"""

DEFAULT_ACTION_NEEDED_LYRICS_TEMPLATE = """Hi {name},

Your karaoke video for "{artist} - {title}" is ready for review!

Our system has transcribed and synchronized the lyrics, but they may need some corrections. You'll also be able to select your preferred instrumental track (with or without backing vocals).

Please review your lyrics and select an instrumental:
{review_url}

This usually takes just a few minutes.

Thanks!
-Andrew
"""

DEFAULT_ACTION_NEEDED_INSTRUMENTAL_TEMPLATE = """Hi {name},

Your karaoke video for "{artist} - {title}" is almost done!

We've separated the audio into different versions. Please select which instrumental track you'd like to use for the final video.

Select your instrumental here:
{instrumental_url}

Thanks!
-Andrew
"""


class TemplateService:
    """
    Service for fetching and rendering email templates from GCS.

    Templates support the following variables:
    - {name} - User's display name or "there" if unknown
    - {youtube_url} - YouTube video URL
    - {dropbox_url} - Dropbox folder URL
    - {artist} - Artist name
    - {title} - Song title
    - {job_id} - Job ID
    - {review_url} - Lyrics review URL
    - {instrumental_url} - Instrumental selection URL
    - {feedback_url} - Feedback form URL
    """

    TEMPLATE_PREFIX = "templates/"

    def __init__(self):
        """Initialize template service."""
        self.settings = get_settings()
        self._storage_client = None
        self._bucket = None

    @property
    def storage_client(self):
        """Lazy-initialize storage client."""
        if self._storage_client is None:
            from google.cloud import storage
            self._storage_client = storage.Client(project=self.settings.google_cloud_project)
        return self._storage_client

    @property
    def bucket(self):
        """Get the GCS bucket for templates."""
        if self._bucket is None:
            self._bucket = self.storage_client.bucket(self.settings.gcs_bucket_name)
        return self._bucket

    def _fetch_template_from_gcs(self, template_name: str) -> Optional[str]:
        """
        Fetch a template from GCS.

        Args:
            template_name: Name of template file (e.g., "job-completion.txt")

        Returns:
            Template content as string, or None if not found
        """
        blob_path = f"{self.TEMPLATE_PREFIX}{template_name}"
        try:
            blob = self.bucket.blob(blob_path)
            if blob.exists():
                content = blob.download_as_text()
                logger.debug(f"Fetched template from GCS: {blob_path}")
                return content
            else:
                logger.warning(f"Template not found in GCS: {blob_path}")
                return None
        except Exception as e:
            logger.error(f"Failed to fetch template {blob_path}: {e}")
            return None

    def get_job_completion_template(self) -> str:
        """Get the job completion email template."""
        template = self._fetch_template_from_gcs("job-completion.txt")
        if template is None:
            logger.info("Using default job completion template")
            return DEFAULT_JOB_COMPLETION_TEMPLATE
        return template

    def get_action_needed_lyrics_template(self) -> str:
        """Get the lyrics review reminder template."""
        template = self._fetch_template_from_gcs("action-needed-lyrics.txt")
        if template is None:
            logger.info("Using default lyrics reminder template")
            return DEFAULT_ACTION_NEEDED_LYRICS_TEMPLATE
        return template

    def get_action_needed_instrumental_template(self) -> str:
        """Get the instrumental selection reminder template."""
        template = self._fetch_template_from_gcs("action-needed-instrumental.txt")
        if template is None:
            logger.info("Using default instrumental reminder template")
            return DEFAULT_ACTION_NEEDED_INSTRUMENTAL_TEMPLATE
        return template

    def render_template(self, template: str, variables: Dict[str, Any]) -> str:
        """
        Render a template with the given variables.

        Missing variables are replaced with empty strings.
        Handles conditional sections like feedback URL.

        Args:
            template: Template string with {variable} placeholders
            variables: Dictionary of variable values

        Returns:
            Rendered template string
        """
        result = template

        # Handle feedback URL section - remove if not provided
        if not variables.get("feedback_url"):
            # Remove feedback section (lines containing feedback_url placeholder and surrounding text)
            result = re.sub(
                r'\n*If you have a moment.*?\{feedback_url\}\n*',
                '\n',
                result,
                flags=re.DOTALL
            )
            variables["feedback_url"] = ""

        # Replace all variables
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value) if value else "")

        # Clean up any remaining unreplaced placeholders
        result = re.sub(r'\{[a-z_]+\}', '', result)

        return result.strip()

    def render_job_completion(
        self,
        name: Optional[str] = None,
        youtube_url: Optional[str] = None,
        dropbox_url: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        job_id: Optional[str] = None,
        feedback_url: Optional[str] = None,
    ) -> str:
        """
        Render the job completion email template.

        Args:
            name: User's display name (defaults to "there")
            youtube_url: YouTube video URL
            dropbox_url: Dropbox folder URL
            artist: Artist name
            title: Song title
            job_id: Job ID
            feedback_url: Feedback form URL (optional)

        Returns:
            Rendered email content
        """
        template = self.get_job_completion_template()
        variables = {
            "name": name or "there",
            "youtube_url": youtube_url or "[YouTube URL not available]",
            "dropbox_url": dropbox_url or "[Dropbox URL not available]",
            "artist": artist or "Unknown Artist",
            "title": title or "Unknown Title",
            "job_id": job_id or "",
            "feedback_url": feedback_url,
        }
        return self.render_template(template, variables)

    def render_action_needed_lyrics(
        self,
        name: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        review_url: str = "",
    ) -> str:
        """
        Render the combined review reminder template.

        This is used for the combined lyrics + instrumental review flow.
        Users review lyrics and select their instrumental in a single session.

        Args:
            name: User's display name
            artist: Artist name
            title: Song title
            review_url: Combined review URL

        Returns:
            Rendered email content
        """
        template = self.get_action_needed_lyrics_template()
        variables = {
            "name": name or "there",
            "artist": artist or "Unknown Artist",
            "title": title or "Unknown Title",
            "review_url": review_url,
        }
        return self.render_template(template, variables)

    def render_action_needed_instrumental(
        self,
        name: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        instrumental_url: str = "",
    ) -> str:
        """
        Render the instrumental selection reminder template.

        This is only used for finalise-only jobs where users upload pre-rendered
        video and only need to select the instrumental track. For normal jobs,
        instrumental selection is combined with lyrics review (see render_action_needed_lyrics).

        Args:
            name: User's display name
            artist: Artist name
            title: Song title
            instrumental_url: Instrumental selection URL

        Returns:
            Rendered email content
        """
        template = self.get_action_needed_instrumental_template()
        variables = {
            "name": name or "there",
            "artist": artist or "Unknown Artist",
            "title": title or "Unknown Title",
            "instrumental_url": instrumental_url,
        }
        return self.render_template(template, variables)

    def upload_template(self, template_name: str, content: str) -> bool:
        """
        Upload a template to GCS.

        Args:
            template_name: Name of template file (e.g., "job-completion.txt")
            content: Template content

        Returns:
            True if upload successful
        """
        blob_path = f"{self.TEMPLATE_PREFIX}{template_name}"
        try:
            blob = self.bucket.blob(blob_path)
            blob.upload_from_string(content, content_type="text/plain")
            logger.info(f"Uploaded template to GCS: {blob_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload template {blob_path}: {e}")
            return False


# Global instance
_template_service: Optional[TemplateService] = None


def get_template_service() -> TemplateService:
    """Get the global template service instance."""
    global _template_service
    if _template_service is None:
        _template_service = TemplateService()
    return _template_service
