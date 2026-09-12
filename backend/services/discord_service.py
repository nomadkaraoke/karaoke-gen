"""
Discord Notification Service.

Provides Discord webhook notification functionality, extracted from KaraokeFinalise
for use by both the cloud backend (video_worker) and local CLI.

This service handles:
- Posting messages to Discord webhooks
- Video upload notifications
- Validation of webhook URLs
"""

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class DiscordNotificationService:
    """
    Service for sending Discord webhook notifications.

    Supports posting messages to Discord channels via webhooks,
    commonly used to notify when new karaoke videos are uploaded.
    """

    # Discord webhook URL pattern
    WEBHOOK_URL_PATTERN = re.compile(
        r"^https://discord\.com/api/webhooks/\d+/[a-zA-Z0-9_-]+$"
    )

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        dry_run: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the Discord notification service.

        Args:
            webhook_url: Discord webhook URL for posting notifications
            dry_run: If True, log actions without performing them
            logger: Optional logger instance
        """
        self.webhook_url = webhook_url
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger(__name__)

        # Validate webhook URL if provided
        if webhook_url:
            self._validate_webhook_url(webhook_url)

    def _validate_webhook_url(self, url: str) -> None:
        """
        Validate that the webhook URL is a valid Discord webhook URL.

        Args:
            url: URL to validate

        Raises:
            ValueError: If URL is not a valid Discord webhook URL
        """
        url = url.strip()
        if not url.startswith("https://discord.com/api/webhooks/"):
            raise ValueError(
                f"Invalid Discord webhook URL: {url}. "
                "URL must start with 'https://discord.com/api/webhooks/'"
            )

    def post_message(
        self,
        message: str,
        webhook_url: Optional[str] = None,
    ) -> bool:
        """
        Post a message to a Discord channel via webhook.

        Args:
            message: The message content to post
            webhook_url: Optional webhook URL (uses instance URL if not provided)

        Returns:
            True if message was posted successfully, False otherwise

        Raises:
            ValueError: If no webhook URL is available
            requests.HTTPError: If the webhook request fails
        """
        url = webhook_url or self.webhook_url
        if not url:
            raise ValueError("No Discord webhook URL provided")

        url = url.strip()
        self._validate_webhook_url(url)

        if self.dry_run:
            self.logger.info(
                f"DRY RUN: Would post Discord message: '{message}' to {url}"
            )
            return True

        self.logger.info(f"Posting message to Discord webhook...")
        # Disable all mentions: message content can include user-supplied text
        # (artist/title in ops alerts), so an embedded @everyone / <@id> must not
        # trigger a Discord ping. (CWE-74 — output injection into a downstream
        # component.)
        data = {"content": message, "allowed_mentions": {"parse": []}}
        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        self.logger.info("Message posted to Discord successfully")
        return True

    def post_video_notification(
        self,
        youtube_url: str,
        webhook_url: Optional[str] = None,
    ) -> bool:
        """
        Post a notification about a new video upload.

        Args:
            youtube_url: The YouTube URL of the uploaded video
            webhook_url: Optional webhook URL (uses instance URL if not provided)

        Returns:
            True if notification was posted successfully, False otherwise
        """
        if not youtube_url:
            self.logger.info("Skipping Discord notification - no YouTube URL available")
            return False

        message = f"New upload: {youtube_url}"
        return self.post_message(message, webhook_url)

    def is_enabled(self) -> bool:
        """
        Check if Discord notifications are enabled.

        Returns:
            True if a valid webhook URL is configured
        """
        return bool(self.webhook_url)


# Singleton instance and factory function (following existing service pattern)
_discord_notification_service: Optional[DiscordNotificationService] = None


def get_discord_notification_service(
    webhook_url: Optional[str] = None,
    **kwargs
) -> DiscordNotificationService:
    """
    Get a Discord notification service instance.

    Args:
        webhook_url: Discord webhook URL for notifications
        **kwargs: Additional arguments passed to DiscordNotificationService

    Returns:
        DiscordNotificationService instance
    """
    global _discord_notification_service

    # Create new instance if webhook URL changed or not yet created
    if _discord_notification_service is None or webhook_url:
        _discord_notification_service = DiscordNotificationService(
            webhook_url=webhook_url,
            **kwargs
        )

    return _discord_notification_service
