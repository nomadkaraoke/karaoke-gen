"""
Job notification service for sending emails on job state changes.

Handles:
- Job completion emails (when job enters COMPLETE status)
- Action reminder emails (when user is idle at blocking states)

This service orchestrates between the template service and email service,
building the complete notification flow for jobs.
"""
import logging
import os
from typing import Optional, Dict, Any

from backend.i18n import get_locale_prefix
from backend.services.email_service import get_email_service
from backend.services.template_service import get_template_service
from backend.services.user_service import get_user_service


logger = logging.getLogger(__name__)


# Environment variable to enable/disable auto emails
ENABLE_AUTO_EMAILS = os.getenv("ENABLE_AUTO_EMAILS", "true").lower() == "true"

# Feedback form URL (configured per environment, empty by default to avoid placeholder in emails)
FEEDBACK_FORM_URL = os.getenv("FEEDBACK_FORM_URL", "")


def _mask_email(email: str) -> str:
    """Mask email for logging to protect PII. Shows first char + domain."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


class JobNotificationService:
    """
    Service for sending job-related email notifications.

    Coordinates between template service (for message rendering) and
    email service (for sending).
    """

    def __init__(self):
        """Initialize notification service."""
        self.email_service = get_email_service()
        self.template_service = get_template_service()
        self.frontend_url = os.getenv("FRONTEND_URL", "https://gen.nomadkaraoke.com")
        self.backend_url = os.getenv("BACKEND_URL", "https://api.nomadkaraoke.com")

    def _get_user_locale(self, user_email: str) -> str:
        """Look up user's locale preference from Firestore, defaulting to 'en'."""
        try:
            user_service = get_user_service()
            user = user_service.get_user(user_email)
            if user and user.locale:
                return user.locale
        except Exception:
            pass  # Fall back to English
        return "en"

    def _build_review_url(self, job_id: str, audio_hash: Optional[str] = None, review_token: Optional[str] = None, locale: str = "en") -> str:
        """Build the lyrics review URL for a job."""
        # Use hash-based routing for static hosting compatibility
        return f"{self.frontend_url}{get_locale_prefix(locale)}/app/jobs#/{job_id}/review"

    def _build_instrumental_url(self, job_id: str, instrumental_token: Optional[str] = None, locale: str = "en") -> str:
        """Build the instrumental selection URL for a job."""
        # Use hash-based routing for static hosting compatibility
        return f"{self.frontend_url}{get_locale_prefix(locale)}/app/jobs#/{job_id}/instrumental"

    def _build_audio_edit_url(self, job_id: str, locale: str = "en") -> str:
        """Build the audio edit URL for a job."""
        return f"{self.frontend_url}{get_locale_prefix(locale)}/app/jobs#/{job_id}/audio-edit"

    async def send_job_completion_email(
        self,
        job_id: str,
        user_email: str,
        user_name: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        youtube_url: Optional[str] = None,
        dropbox_url: Optional[str] = None,
        brand_code: Optional[str] = None,
        is_private: bool = False,
        youtube_queued: bool = False,
    ) -> bool:
        """
        Send job completion email to user.

        Args:
            job_id: Job ID
            user_email: User's email address
            user_name: User's display name (optional)
            artist: Artist name
            title: Song title
            youtube_url: YouTube video URL
            dropbox_url: Dropbox folder URL
            brand_code: Release ID (e.g., "NOMAD-1178")
            is_private: If True, omit YouTube section (private/non-published tracks)
            youtube_queued: If True, YouTube upload was deferred due to quota

        Returns:
            True if email was sent successfully
        """
        if not ENABLE_AUTO_EMAILS:
            logger.info(f"Auto emails disabled, skipping completion email for job {job_id}")
            return False

        if not user_email:
            logger.warning(f"No user email for job {job_id}, skipping completion email")
            return False

        try:
            # Get user locale for email
            user_locale = self._get_user_locale(user_email)

            # Render the completion message using template service
            message_content = self.template_service.render_job_completion(
                name=user_name,
                youtube_url=youtube_url,
                dropbox_url=dropbox_url,
                artist=artist,
                title=title,
                job_id=job_id,
                feedback_url=FEEDBACK_FORM_URL,
                is_private=is_private,
                youtube_queued=youtube_queued,
            )

            # Send the email with CC to admin
            success = self.email_service.send_job_completion(
                to_email=user_email,
                message_content=message_content,
                artist=artist,
                title=title,
                brand_code=brand_code,
                cc_admin=True,
                locale=user_locale,
            )

            if success:
                logger.info(f"Sent completion email for job {job_id} to {_mask_email(user_email)}")
            else:
                logger.error(f"Failed to send completion email for job {job_id}")

            return success

        except Exception as e:
            logger.exception(f"Error sending completion email for job {job_id}: {e}")
            return False

    async def send_youtube_upload_complete_email(
        self,
        job_id: str,
        user_email: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        youtube_url: Optional[str] = None,
        brand_code: Optional[str] = None,
    ) -> bool:
        """
        Send follow-up email when a deferred YouTube upload completes.

        Args:
            job_id: Job ID
            user_email: User's email address
            artist: Artist name
            title: Song title
            youtube_url: YouTube video URL
            brand_code: Release ID

        Returns:
            True if email was sent successfully
        """
        if not ENABLE_AUTO_EMAILS:
            logger.info(f"Auto emails disabled, skipping YouTube notification for job {job_id}")
            return False

        if not user_email:
            logger.warning(f"No user email for job {job_id}, skipping YouTube notification")
            return False

        try:
            # Get user locale for email
            user_locale = self._get_user_locale(user_email)

            message_content = self.template_service.render_youtube_upload_complete(
                artist=artist,
                title=title,
                youtube_url=youtube_url,
            )

            success = self.email_service.send_youtube_upload_complete(
                to_email=user_email,
                message_content=message_content,
                artist=artist,
                title=title,
                brand_code=brand_code,
                locale=user_locale,
            )

            if success:
                logger.info(f"Sent YouTube upload notification for job {job_id} to {_mask_email(user_email)}")
            else:
                logger.error(f"Failed to send YouTube upload notification for job {job_id}")

            return success

        except Exception as e:
            logger.exception(f"Error sending YouTube upload notification for job {job_id}: {e}")
            return False

    async def send_action_reminder_email(
        self,
        job_id: str,
        user_email: str,
        action_type: str,
        user_name: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        audio_hash: Optional[str] = None,
        review_token: Optional[str] = None,
        instrumental_token: Optional[str] = None,
    ) -> bool:
        """
        Send action-needed reminder email to user.

        Args:
            job_id: Job ID
            user_email: User's email address
            action_type: Type of action needed:
                - "lyrics": Combined review (lyrics + instrumental selection) for normal jobs
                - "instrumental": Instrumental-only selection for finalise-only jobs
                - "audio_edit": Audio editing before processing begins
            user_name: User's display name
            artist: Artist name
            title: Song title
            audio_hash: Audio hash for review URL (unused, kept for compatibility)
            review_token: Review token for URL (unused, kept for compatibility)
            instrumental_token: Instrumental token for URL (unused, kept for compatibility)

        Returns:
            True if email was sent successfully
        """
        if not ENABLE_AUTO_EMAILS:
            logger.info(f"Auto emails disabled, skipping reminder for job {job_id}")
            return False

        if not user_email:
            logger.warning(f"No user email for job {job_id}, skipping reminder")
            return False

        try:
            # Get user locale for email
            user_locale = self._get_user_locale(user_email)

            # Render the appropriate template
            if action_type == "lyrics":
                review_url = self._build_review_url(job_id, audio_hash, review_token, locale=user_locale)
                message_content = self.template_service.render_action_needed_lyrics(
                    name=user_name,
                    artist=artist,
                    title=title,
                    review_url=review_url,
                )
            elif action_type == "instrumental":
                instrumental_url = self._build_instrumental_url(job_id, instrumental_token, locale=user_locale)
                message_content = self.template_service.render_action_needed_instrumental(
                    name=user_name,
                    artist=artist,
                    title=title,
                    instrumental_url=instrumental_url,
                )
            elif action_type == "audio_edit":
                audio_edit_url = self._build_audio_edit_url(job_id, locale=user_locale)
                # Reuse the lyrics template but with the audio edit URL and different subject
                message_content = self.template_service.render_action_needed_lyrics(
                    name=user_name,
                    artist=artist,
                    title=title,
                    review_url=audio_edit_url,
                )
            else:
                logger.error(f"Unknown action type: {action_type}")
                return False

            # Send the reminder email (no CC)
            success = self.email_service.send_action_reminder(
                to_email=user_email,
                message_content=message_content,
                action_type=action_type,
                artist=artist,
                title=title,
                locale=user_locale,
            )

            if success:
                logger.info(f"Sent {action_type} reminder for job {job_id} to {_mask_email(user_email)}")
            else:
                logger.error(f"Failed to send {action_type} reminder for job {job_id}")

            return success

        except Exception as e:
            logger.exception(f"Error sending {action_type} reminder for job {job_id}: {e}")
            return False

    def get_completion_message(
        self,
        job_id: str,
        user_name: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        youtube_url: Optional[str] = None,
        dropbox_url: Optional[str] = None,
        is_private: bool = False,
        youtube_queued: bool = False,
    ) -> str:
        """
        Get the rendered completion message for a job (for admin copy functionality).

        Args:
            job_id: Job ID
            user_name: User's display name
            artist: Artist name
            title: Song title
            youtube_url: YouTube video URL
            dropbox_url: Dropbox folder URL
            is_private: If True, omit YouTube section (private/non-published tracks)
            youtube_queued: If True, YouTube upload was deferred

        Returns:
            Rendered message content as plain text
        """
        return self.template_service.render_job_completion(
            name=user_name,
            youtube_url=youtube_url,
            dropbox_url=dropbox_url,
            artist=artist,
            title=title,
            job_id=job_id,
            feedback_url=FEEDBACK_FORM_URL,
            is_private=is_private,
            youtube_queued=youtube_queued,
        )


# Global instance
_job_notification_service: Optional[JobNotificationService] = None


def get_job_notification_service() -> JobNotificationService:
    """Get the global job notification service instance."""
    global _job_notification_service
    if _job_notification_service is None:
        _job_notification_service = JobNotificationService()
    return _job_notification_service
