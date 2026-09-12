"""
Tests for DiscordNotificationService.

Tests cover:
- Service initialization
- Webhook URL validation
- Message posting
- Video notification posting
- Dry run mode
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.services.discord_service import (
    DiscordNotificationService,
    get_discord_notification_service,
)


class TestDiscordNotificationServiceInit:
    """Test service initialization."""

    def test_init_with_webhook_url(self):
        """Test initialization with valid webhook URL."""
        url = "https://discord.com/api/webhooks/123456789/abcdef123456"
        service = DiscordNotificationService(webhook_url=url)
        assert service.webhook_url == url
        assert service.is_enabled() is True

    def test_init_without_webhook_url(self):
        """Test initialization without webhook URL."""
        service = DiscordNotificationService()
        assert service.webhook_url is None
        assert service.is_enabled() is False

    def test_init_with_dry_run(self):
        """Test initialization with dry run mode."""
        service = DiscordNotificationService(dry_run=True)
        assert service.dry_run is True

    def test_init_with_invalid_webhook_url_raises(self):
        """Test that invalid webhook URL raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DiscordNotificationService(webhook_url="https://example.com/webhook")
        assert "Invalid Discord webhook URL" in str(exc_info.value)

    def test_init_with_non_discord_url_raises(self):
        """Test that non-Discord URL raises ValueError."""
        with pytest.raises(ValueError):
            DiscordNotificationService(
                webhook_url="https://slack.com/api/webhooks/123"
            )


class TestDiscordNotificationServiceValidation:
    """Test webhook URL validation."""

    def test_validate_valid_webhook_url(self):
        """Test that valid webhook URL passes validation."""
        service = DiscordNotificationService()
        # Should not raise
        service._validate_webhook_url(
            "https://discord.com/api/webhooks/123456789/abcdef"
        )

    def test_validate_invalid_webhook_url(self):
        """Test that invalid webhook URL raises ValueError."""
        service = DiscordNotificationService()
        with pytest.raises(ValueError) as exc_info:
            service._validate_webhook_url("https://example.com/webhook")
        assert "Invalid Discord webhook URL" in str(exc_info.value)

    def test_validate_strips_whitespace(self):
        """Test that whitespace is stripped from URL."""
        service = DiscordNotificationService()
        # Should not raise - whitespace is stripped
        service._validate_webhook_url(
            "  https://discord.com/api/webhooks/123456789/abcdef  "
        )


class TestDiscordNotificationServicePostMessage:
    """Test message posting."""

    @patch("backend.services.discord_service.requests.post")
    def test_post_message_success(self, mock_post):
        """Test successful message posting."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = DiscordNotificationService(webhook_url=url)

        result = service.post_message("Test message")

        assert result is True
        mock_post.assert_called_once_with(
            url,
            json={"content": "Test message", "allowed_mentions": {"parse": []}},
            timeout=30
        )

    @patch("backend.services.discord_service.requests.post")
    def test_post_message_with_custom_webhook(self, mock_post):
        """Test message posting with custom webhook URL."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        service = DiscordNotificationService()
        custom_url = "https://discord.com/api/webhooks/987654321/fedcba"

        result = service.post_message("Test message", webhook_url=custom_url)

        assert result is True
        mock_post.assert_called_once_with(
            custom_url,
            json={"content": "Test message", "allowed_mentions": {"parse": []}},
            timeout=30
        )

    def test_post_message_no_webhook_raises(self):
        """Test that posting without webhook URL raises ValueError."""
        service = DiscordNotificationService()

        with pytest.raises(ValueError) as exc_info:
            service.post_message("Test message")
        assert "No Discord webhook URL provided" in str(exc_info.value)

    def test_post_message_dry_run(self):
        """Test message posting in dry run mode."""
        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = DiscordNotificationService(webhook_url=url, dry_run=True)

        result = service.post_message("Test message")

        assert result is True
        # In dry run mode, no actual request should be made

    @patch("backend.services.discord_service.requests.post")
    def test_post_message_http_error(self, mock_post):
        """Test handling of HTTP errors."""
        import requests as req
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError("404 Not Found")
        mock_post.return_value = mock_response

        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = DiscordNotificationService(webhook_url=url)

        with pytest.raises(req.HTTPError):
            service.post_message("Test message")


class TestDiscordNotificationServiceVideoNotification:
    """Test video notification posting."""

    @patch("backend.services.discord_service.requests.post")
    def test_post_video_notification_success(self, mock_post):
        """Test successful video notification."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = DiscordNotificationService(webhook_url=url)

        result = service.post_video_notification(
            "https://www.youtube.com/watch?v=abc123"
        )

        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "New upload:" in call_args[1]["json"]["content"]
        assert "abc123" in call_args[1]["json"]["content"]

    def test_post_video_notification_no_youtube_url(self):
        """Test video notification with empty YouTube URL."""
        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = DiscordNotificationService(webhook_url=url)

        result = service.post_video_notification("")

        assert result is False

    def test_post_video_notification_none_youtube_url(self):
        """Test video notification with None YouTube URL."""
        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = DiscordNotificationService(webhook_url=url)

        result = service.post_video_notification(None)

        assert result is False

    def test_post_video_notification_dry_run(self):
        """Test video notification in dry run mode."""
        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = DiscordNotificationService(webhook_url=url, dry_run=True)

        result = service.post_video_notification(
            "https://www.youtube.com/watch?v=abc123"
        )

        assert result is True


class TestGetDiscordNotificationService:
    """Test factory function."""

    def test_get_service_creates_instance(self):
        """Test that factory function creates a new instance."""
        import backend.services.discord_service as module
        module._discord_notification_service = None

        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = get_discord_notification_service(webhook_url=url)

        assert service is not None
        assert isinstance(service, DiscordNotificationService)
        assert service.webhook_url == url

    def test_get_service_without_webhook(self):
        """Test factory function without webhook URL."""
        import backend.services.discord_service as module
        module._discord_notification_service = None

        service = get_discord_notification_service()

        assert service is not None
        assert service.webhook_url is None
        assert service.is_enabled() is False

    def test_get_service_with_dry_run(self):
        """Test factory function with dry run mode."""
        import backend.services.discord_service as module
        module._discord_notification_service = None

        url = "https://discord.com/api/webhooks/123456789/abcdef"
        service = get_discord_notification_service(webhook_url=url, dry_run=True)

        assert service.dry_run is True
