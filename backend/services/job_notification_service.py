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

from backend.services.email_service import get_email_service
from backend.services.template_service import get_template_service


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

    def _build_review_url(self, job_id: str, audio_hash: Optional[str] = None, review_token: Optional[str] = None) -> str:
        """Build the lyrics review URL for a job."""
        # Use hash-based routing for static hosting compatibility
        return f"{self.frontend_url}/app/jobs#/{job_id}/review"

    def _build_instrumental_url(self, job_id: str, instrumental_token: Optional[str] = None) -> str:
        """Build the instrumental selection URL for a job."""
        # Use hash-based routing for static hosting compatibility
        return f"{self.frontend_url}/app/jobs#/{job_id}/instrumental"

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
            # Render the completion message using template service
            message_content = self.template_service.render_job_completion(
                name=user_name,
                youtube_url=youtube_url,
                dropbox_url=dropbox_url,
                artist=artist,
                title=title,
                job_id=job_id,
                feedback_url=FEEDBACK_FORM_URL,
            )

            # Send the email with CC to admin
            success = self.email_service.send_job_completion(
                to_email=user_email,
                message_content=message_content,
                artist=artist,
                title=title,
                brand_code=brand_code,
                cc_admin=True,
            )

            if success:
                logger.info(f"Sent completion email for job {job_id} to {_mask_email(user_email)}")
            else:
                logger.error(f"Failed to send completion email for job {job_id}")

            return success

        except Exception as e:
            logger.exception(f"Error sending completion email for job {job_id}: {e}")
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
            # Render the appropriate template
            if action_type == "lyrics":
                review_url = self._build_review_url(job_id, audio_hash, review_token)
                message_content = self.template_service.render_action_needed_lyrics(
                    name=user_name,
                    artist=artist,
                    title=title,
                    review_url=review_url,
                )
            elif action_type == "instrumental":
                instrumental_url = self._build_instrumental_url(job_id, instrumental_token)
                message_content = self.template_service.render_action_needed_instrumental(
                    name=user_name,
                    artist=artist,
                    title=title,
                    instrumental_url=instrumental_url,
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
        )


# Global instance
_job_notification_service: Optional[JobNotificationService] = None


def get_job_notification_service() -> JobNotificationService:
    """Get the global job notification service instance."""
    global _job_notification_service
    if _job_notification_service is None:
        _job_notification_service = JobNotificationService()
    return _job_notification_service
