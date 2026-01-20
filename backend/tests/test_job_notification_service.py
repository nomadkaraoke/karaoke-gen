"""
Unit tests for job notification service.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock

from backend.services.job_notification_service import (
    JobNotificationService,
    get_job_notification_service,
    ENABLE_AUTO_EMAILS,
    FEEDBACK_FORM_URL,
)


class TestURLBuilding:
    """Tests for URL building methods."""

    def test_build_review_url_basic(self):
        """Test basic review URL building."""
        service = JobNotificationService()
        service.frontend_url = "https://gen.nomadkaraoke.com"

        url = service._build_review_url("job-123")

        assert url == "https://gen.nomadkaraoke.com/app/jobs#/job-123/review"

    def test_build_review_url_ignores_legacy_params(self):
        """Test review URL ignores legacy audio_hash and review_token params."""
        service = JobNotificationService()
        service.frontend_url = "https://gen.nomadkaraoke.com"

        # Legacy params are still accepted but not used in the URL
        url = service._build_review_url("job-123", audio_hash="abc123", review_token="token456")

        # URL should be the simple consolidated route
        assert url == "https://gen.nomadkaraoke.com/app/jobs#/job-123/review"
        # No query params
        assert "?" not in url

    def test_build_review_url_preserves_job_id_characters(self):
        """Test that job ID is used directly in URL path."""
        service = JobNotificationService()
        service.frontend_url = "https://gen.nomadkaraoke.com"

        # Note: job IDs with slashes would be unusual in real usage
        url = service._build_review_url("abc-def-123")

        assert url == "https://gen.nomadkaraoke.com/app/jobs#/abc-def-123/review"

    def test_build_instrumental_url_basic(self):
        """Test basic instrumental URL building."""
        service = JobNotificationService()
        service.frontend_url = "https://gen.nomadkaraoke.com"

        url = service._build_instrumental_url("job-123")

        assert url == "https://gen.nomadkaraoke.com/app/jobs#/job-123/instrumental"

    def test_build_instrumental_url_ignores_legacy_params(self):
        """Test instrumental URL ignores legacy instrumental_token param."""
        service = JobNotificationService()
        service.frontend_url = "https://gen.nomadkaraoke.com"

        # Legacy param is still accepted but not used in the URL
        url = service._build_instrumental_url("job-123", instrumental_token="inst-token")

        # URL should be the simple consolidated route
        assert url == "https://gen.nomadkaraoke.com/app/jobs#/job-123/instrumental"
        # No query params
        assert "?" not in url


class TestCompletionEmail:
    """Tests for job completion email sending."""

    @pytest.mark.asyncio
    async def test_send_completion_email_success(self):
        """Test successful completion email sending."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.email_service.send_job_completion.return_value = True
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Test message"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_job_completion_email(
                job_id="job-123",
                user_email="user@example.com",
                user_name="Test User",
                artist="Test Artist",
                title="Test Song",
                youtube_url="https://youtube.com/watch?v=123",
                dropbox_url="https://dropbox.com/folder/abc",
            )

        assert result is True
        service.template_service.render_job_completion.assert_called_once()
        service.email_service.send_job_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_completion_email_disabled(self):
        """Test that completion email is skipped when auto emails are disabled."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.template_service = Mock()

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', False):
            result = await service.send_job_completion_email(
                job_id="job-123",
                user_email="user@example.com",
            )

        assert result is False
        service.email_service.send_job_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_completion_email_no_email(self):
        """Test that completion email is skipped when no user email."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.template_service = Mock()

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_job_completion_email(
                job_id="job-123",
                user_email=None,
            )

        assert result is False
        service.email_service.send_job_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_completion_email_empty_email(self):
        """Test that completion email is skipped when user email is empty."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.template_service = Mock()

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_job_completion_email(
                job_id="job-123",
                user_email="",
            )

        assert result is False
        service.email_service.send_job_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_completion_email_send_failure(self):
        """Test handling of email send failure."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.email_service.send_job_completion.return_value = False
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Test message"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_job_completion_email(
                job_id="job-123",
                user_email="user@example.com",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_send_completion_email_exception(self):
        """Test handling of exceptions during email sending."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.email_service.send_job_completion.side_effect = Exception("Send error")
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Test message"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_job_completion_email(
                job_id="job-123",
                user_email="user@example.com",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_send_completion_email_passes_cc_admin(self):
        """Test that completion email is sent with CC to admin."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.email_service.send_job_completion.return_value = True
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Test message"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            await service.send_job_completion_email(
                job_id="job-123",
                user_email="user@example.com",
            )

        # Verify cc_admin=True was passed
        call_kwargs = service.email_service.send_job_completion.call_args.kwargs
        assert call_kwargs.get('cc_admin') is True


class TestActionReminderEmail:
    """Tests for action reminder email sending."""

    @pytest.mark.asyncio
    async def test_send_lyrics_reminder_success(self):
        """Test successful lyrics reminder email sending."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.email_service.send_action_reminder.return_value = True
        service.template_service = Mock()
        service.template_service.render_action_needed_lyrics.return_value = "Review your lyrics"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_action_reminder_email(
                job_id="job-123",
                user_email="user@example.com",
                action_type="lyrics",
                artist="Test Artist",
                title="Test Song",
            )

        assert result is True
        service.template_service.render_action_needed_lyrics.assert_called_once()
        service.email_service.send_action_reminder.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_instrumental_reminder_success(self):
        """Test successful instrumental reminder email sending."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.email_service.send_action_reminder.return_value = True
        service.template_service = Mock()
        service.template_service.render_action_needed_instrumental.return_value = "Select instrumental"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_action_reminder_email(
                job_id="job-123",
                user_email="user@example.com",
                action_type="instrumental",
            )

        assert result is True
        service.template_service.render_action_needed_instrumental.assert_called_once()
        service.email_service.send_action_reminder.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_reminder_disabled(self):
        """Test that reminder email is skipped when auto emails are disabled."""
        service = JobNotificationService()
        service.email_service = Mock()

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', False):
            result = await service.send_action_reminder_email(
                job_id="job-123",
                user_email="user@example.com",
                action_type="lyrics",
            )

        assert result is False
        service.email_service.send_action_reminder.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_reminder_no_email(self):
        """Test that reminder email is skipped when no user email."""
        service = JobNotificationService()
        service.email_service = Mock()

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_action_reminder_email(
                job_id="job-123",
                user_email=None,
                action_type="lyrics",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_send_reminder_unknown_action_type(self):
        """Test handling of unknown action type."""
        service = JobNotificationService()
        service.email_service = Mock()
        service.template_service = Mock()

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            result = await service.send_action_reminder_email(
                job_id="job-123",
                user_email="user@example.com",
                action_type="unknown",
            )

        assert result is False
        service.email_service.send_action_reminder.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_lyrics_reminder_includes_review_url(self):
        """Test that lyrics reminder includes correct review URL."""
        service = JobNotificationService()
        service.frontend_url = "https://gen.nomadkaraoke.com"
        service.email_service = Mock()
        service.email_service.send_action_reminder.return_value = True
        service.template_service = Mock()
        service.template_service.render_action_needed_lyrics.return_value = "Review"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            await service.send_action_reminder_email(
                job_id="job-123",
                user_email="user@example.com",
                action_type="lyrics",
            )

        # Verify the review URL was passed to template
        call_kwargs = service.template_service.render_action_needed_lyrics.call_args.kwargs
        review_url = call_kwargs.get('review_url')
        assert review_url == "https://gen.nomadkaraoke.com/app/jobs#/job-123/review"

    @pytest.mark.asyncio
    async def test_send_instrumental_reminder_includes_url(self):
        """Test that instrumental reminder includes correct URL."""
        service = JobNotificationService()
        service.frontend_url = "https://gen.nomadkaraoke.com"
        service.email_service = Mock()
        service.email_service.send_action_reminder.return_value = True
        service.template_service = Mock()
        service.template_service.render_action_needed_instrumental.return_value = "Select"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            await service.send_action_reminder_email(
                job_id="job-123",
                user_email="user@example.com",
                action_type="instrumental",
            )

        # Verify the instrumental URL was passed to template
        call_kwargs = service.template_service.render_action_needed_instrumental.call_args.kwargs
        instrumental_url = call_kwargs.get('instrumental_url')
        assert instrumental_url == "https://gen.nomadkaraoke.com/app/jobs#/job-123/instrumental"


class TestGetCompletionMessage:
    """Tests for get_completion_message method."""

    def test_get_completion_message_basic(self):
        """Test basic completion message retrieval."""
        service = JobNotificationService()
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Your video is ready!"

        result = service.get_completion_message(
            job_id="job-123",
            user_name="Test User",
            artist="Test Artist",
            title="Test Song",
            youtube_url="https://youtube.com/watch?v=123",
            dropbox_url="https://dropbox.com/folder/abc",
        )

        assert result == "Your video is ready!"
        service.template_service.render_job_completion.assert_called_once()

    def test_get_completion_message_includes_feedback_url(self):
        """Test that completion message includes feedback URL."""
        service = JobNotificationService()
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Message"

        service.get_completion_message(job_id="job-123")

        # Verify feedback URL was passed
        call_kwargs = service.template_service.render_job_completion.call_args.kwargs
        assert 'feedback_url' in call_kwargs

    def test_get_completion_message_with_defaults(self):
        """Test completion message with default values."""
        service = JobNotificationService()
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Message"

        service.get_completion_message(job_id="job-123")

        call_kwargs = service.template_service.render_job_completion.call_args.kwargs
        assert call_kwargs.get('job_id') == "job-123"
        # Other params should be None/default
        assert call_kwargs.get('name') is None
        assert call_kwargs.get('artist') is None


class TestGlobalInstance:
    """Tests for global instance management."""

    def test_get_job_notification_service_returns_same_instance(self):
        """Test that get_job_notification_service returns singleton."""
        # Reset global
        import backend.services.job_notification_service as jns
        jns._job_notification_service = None

        service1 = get_job_notification_service()
        service2 = get_job_notification_service()

        assert service1 is service2

    def test_service_initializes_with_dependencies(self):
        """Test that service initializes with email and template services."""
        service = JobNotificationService()

        assert service.email_service is not None
        assert service.template_service is not None
        assert service.frontend_url is not None
        assert service.backend_url is not None
