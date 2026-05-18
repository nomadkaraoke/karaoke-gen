"""
Unit tests for email service.

Tests the new job completion and action reminder email methods,
as well as CC support.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from backend.services.email_service import (
    EmailService,
    EmailProvider,
    ConsoleEmailProvider,
    PostmarkEmailProvider,
    get_email_service,
)


class TestConsoleEmailProvider:
    """Tests for console email provider."""

    def test_send_email_basic(self):
        """Test basic email logging."""
        provider = ConsoleEmailProvider()

        result = provider.send_email(
            to_email="user@example.com",
            subject="Test Subject",
            html_content="<p>Test content</p>",
        )

        assert result is True

    def test_send_email_with_text_content(self):
        """Test email with text content."""
        provider = ConsoleEmailProvider()

        result = provider.send_email(
            to_email="user@example.com",
            subject="Test Subject",
            html_content="<p>Test</p>",
            text_content="Test plain text",
        )

        assert result is True

    def test_send_email_with_cc(self):
        """Test email with CC recipients."""
        provider = ConsoleEmailProvider()

        result = provider.send_email(
            to_email="user@example.com",
            subject="Test Subject",
            html_content="<p>Test</p>",
            cc_emails=["cc1@example.com", "cc2@example.com"],
        )

        assert result is True


class TestPostmarkEmailProvider:
    """Tests for Postmark email provider."""

    def _mock_response(self, status_code=200, body=None):
        resp = Mock()
        resp.status_code = status_code
        resp.json.return_value = body or {"To": "user@example.com", "MessageID": "abc"}
        resp.text = ""
        return resp

    def test_send_email_success(self):
        provider = PostmarkEmailProvider(
            server_token="test-token",
            from_email="from@example.com",
            from_name="Test Sender",
        )

        with patch("backend.services.email_service.requests.post") as mock_post:
            mock_post.return_value = self._mock_response(200)

            result = provider.send_email(
                to_email="user@example.com",
                subject="Test Subject",
                html_content="<p>Test</p>",
                text_content="Test",
            )

        assert result is True
        mock_post.assert_called_once()
        call = mock_post.call_args
        assert call.kwargs["headers"]["X-Postmark-Server-Token"] == "test-token"
        payload = call.kwargs["json"]
        assert payload["From"] == '"Test Sender" <from@example.com>'
        assert payload["To"] == "user@example.com"
        assert payload["Subject"] == "Test Subject"
        assert payload["HtmlBody"] == "<p>Test</p>"
        assert payload["TextBody"] == "Test"
        assert payload["MessageStream"] == "outbound"

    def test_send_email_with_cc_and_bcc_dedupes(self):
        provider = PostmarkEmailProvider(
            server_token="test-token",
            from_email="from@example.com",
        )

        with patch("backend.services.email_service.requests.post") as mock_post:
            mock_post.return_value = self._mock_response(200)

            result = provider.send_email(
                to_email="user@example.com",
                subject="Test",
                html_content="<p>x</p>",
                cc_emails=["cc1@example.com", "CC1@example.com", "cc2@example.com"],
                bcc_emails=["bcc@example.com"],
            )

        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        assert payload["Cc"] == "cc1@example.com, cc2@example.com"
        assert payload["Bcc"] == "bcc@example.com"

    def test_send_email_with_sender_override(self):
        provider = PostmarkEmailProvider(
            server_token="test-token",
            from_email="default@example.com",
            from_name="Default",
        )

        with patch("backend.services.email_service.requests.post") as mock_post:
            mock_post.return_value = self._mock_response(200)

            provider.send_email(
                to_email="user@example.com",
                subject="Test",
                html_content="<p>x</p>",
                from_email_override="tenant@partner.com",
            )

        payload = mock_post.call_args.kwargs["json"]
        assert payload["From"] == '"Default" <tenant@partner.com>'

    def test_send_email_failure_status(self):
        provider = PostmarkEmailProvider(server_token="t", from_email="f@e.com")

        with patch("backend.services.email_service.requests.post") as mock_post:
            mock_post.return_value = self._mock_response(
                422, body={"ErrorCode": 406, "Message": "Inactive recipient"}
            )

            result = provider.send_email(
                to_email="user@example.com",
                subject="Test",
                html_content="<p>x</p>",
            )

        assert result is False

    def test_send_email_request_exception(self):
        import requests as _requests
        provider = PostmarkEmailProvider(server_token="t", from_email="f@e.com")

        with patch("backend.services.email_service.requests.post") as mock_post:
            mock_post.side_effect = _requests.ConnectionError("network down")

            result = provider.send_email(
                to_email="user@example.com",
                subject="Test",
                html_content="<p>x</p>",
            )

        assert result is False


class TestEmailServiceProviderSelection:
    """Tests for EmailService._get_provider() selection logic."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        monkeypatch.delenv("POSTMARK_SERVER_TOKEN", raising=False)

    def test_postmark_when_token_set(self, monkeypatch):
        monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "pm-token")
        svc = EmailService()
        assert isinstance(svc.provider, PostmarkEmailProvider)
        assert svc.is_configured() is True

    def test_console_when_no_token(self, monkeypatch):
        svc = EmailService()
        assert isinstance(svc.provider, ConsoleEmailProvider)
        assert svc.is_configured() is False


class TestEmailServiceJobCompletion:
    """Tests for job completion email method."""

    def test_send_job_completion_basic(self):
        """Test basic job completion email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_job_completion(
            to_email="user@example.com",
            message_content="Your video is ready!",
        )

        assert result is True
        service.provider.send_email.assert_called_once()

    def test_send_job_completion_with_song_info(self):
        """Test job completion email includes song in subject."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_job_completion(
            to_email="user@example.com",
            message_content="Your video is ready!",
            artist="Test Artist",
            title="Test Song",
        )

        call_args = service.provider.send_email.call_args
        subject = call_args.kwargs.get('subject') or call_args[0][1]
        assert "Test Artist" in subject
        assert "Test Song" in subject

    def test_send_job_completion_with_brand_code(self):
        """Test job completion email includes brand code in subject."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_job_completion(
            to_email="user@example.com",
            message_content="Your video is ready!",
            artist="Seether",
            title="Tonight",
            brand_code="NOMAD-1178",
        )

        call_args = service.provider.send_email.call_args
        subject = call_args.kwargs.get('subject') or call_args[0][1]
        # Subject format: "NOMAD-1178: Seether - Tonight (Your karaoke video is ready!)"
        assert subject == "NOMAD-1178: Seether - Tonight (Your karaoke video is ready!)"

    def test_send_job_completion_default_subject(self):
        """Test job completion email default subject without song info."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_job_completion(
            to_email="user@example.com",
            message_content="Your video is ready!",
        )

        call_args = service.provider.send_email.call_args
        subject = call_args.kwargs.get('subject') or call_args[0][1]
        assert "karaoke video is ready" in subject.lower()

    def test_send_job_completion_with_cc(self):
        """Test job completion email with CC to admin."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_job_completion(
            to_email="user@example.com",
            message_content="Your video is ready!",
            cc_admin=True,
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        assert "gen@nomadkaraoke.com" in call_kwargs.get('cc_emails', [])

    def test_send_job_completion_without_cc(self):
        """Test job completion email without CC."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_job_completion(
            to_email="user@example.com",
            message_content="Your video is ready!",
            cc_admin=False,
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        assert call_kwargs.get('cc_emails') is None

    def test_send_job_completion_escapes_html(self):
        """Test that message content is HTML-escaped."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_job_completion(
            to_email="user@example.com",
            message_content="Test <script>alert('xss')</script>",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "<script>" not in html_content
        assert "&lt;script&gt;" in html_content

    def test_send_job_completion_includes_plain_text(self):
        """Test that plain text content is included."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        message = "Your video is ready!"
        service.send_job_completion(
            to_email="user@example.com",
            message_content=message,
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        assert call_kwargs.get('text_content') == message


class TestEmailServiceActionReminder:
    """Tests for action reminder email method."""

    def test_send_action_reminder_lyrics(self):
        """Test lyrics action reminder email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_action_reminder(
            to_email="user@example.com",
            message_content="Please review your lyrics",
            action_type="lyrics",
        )

        assert result is True
        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        assert "lyrics" in subject.lower()

    def test_send_action_reminder_instrumental(self):
        """Test instrumental action reminder email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_action_reminder(
            to_email="user@example.com",
            message_content="Please select instrumental",
            action_type="instrumental",
        )

        assert result is True
        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        assert "instrumental" in subject.lower()

    def test_send_action_reminder_with_song_info(self):
        """Test action reminder includes song in subject."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_action_reminder(
            to_email="user@example.com",
            message_content="Please review",
            action_type="lyrics",
            artist="Test Artist",
            title="Test Song",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        assert "Test Artist" in subject
        assert "Test Song" in subject

    def test_send_action_reminder_unknown_type(self):
        """Test action reminder with unknown type still sends."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_action_reminder(
            to_email="user@example.com",
            message_content="Please take action",
            action_type="unknown",
        )

        assert result is True
        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        assert "Action needed" in subject

    def test_send_action_reminder_escapes_html(self):
        """Test that message content is HTML-escaped."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_action_reminder(
            to_email="user@example.com",
            message_content="Test <script>alert('xss')</script>",
            action_type="lyrics",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "<script>" not in html_content
        assert "&lt;script&gt;" in html_content

    def test_send_action_reminder_no_cc(self):
        """Test that action reminders don't have CC."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_action_reminder(
            to_email="user@example.com",
            message_content="Please review",
            action_type="lyrics",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        # Action reminders should not have CC
        assert call_kwargs.get('cc_emails') is None


class TestEmailServiceConfiguration:
    """Tests for email service configuration."""

    def test_uses_postmark_when_configured(self):
        """Test that Postmark is used when server token is set."""
        with patch.dict('os.environ', {'POSTMARK_SERVER_TOKEN': 'test-token'}):
            service = EmailService()
            assert isinstance(service.provider, PostmarkEmailProvider)

    def test_uses_console_when_not_configured(self):
        """Test that console is used when no token."""
        import os
        original = os.environ.pop('POSTMARK_SERVER_TOKEN', None)
        try:
            service = EmailService()
            assert isinstance(service.provider, ConsoleEmailProvider)
        finally:
            if original:
                os.environ['POSTMARK_SERVER_TOKEN'] = original

    def test_is_configured_true_for_postmark(self):
        """Test is_configured returns True for Postmark."""
        service = EmailService()
        service.provider = PostmarkEmailProvider("token", "from@example.com")
        assert service.is_configured() is True

    def test_is_configured_false_for_console(self):
        """Test is_configured returns False for console."""
        service = EmailService()
        service.provider = ConsoleEmailProvider()
        assert service.is_configured() is False


class TestGlobalInstance:
    """Tests for global instance management."""

    def test_get_email_service_returns_same_instance(self):
        """Test that get_email_service returns singleton."""
        # Reset global
        import backend.services.email_service as es
        es._email_service = None

        service1 = get_email_service()
        service2 = get_email_service()

        assert service1 is service2


class TestCCFunctionality:
    """Integration tests for CC functionality across providers."""

    def test_console_provider_logs_cc(self, caplog):
        """Test that console provider logs CC recipients."""
        import logging
        caplog.set_level(logging.INFO)

        provider = ConsoleEmailProvider()
        provider.send_email(
            to_email="user@example.com",
            subject="Test",
            html_content="<p>Test</p>",
            cc_emails=["cc1@example.com", "cc2@example.com"],
        )

        # Check that CC was logged
        assert "cc1@example.com" in caplog.text or "CC:" in caplog.text

class TestMadeForYouOrderConfirmation:
    """Tests for send_made_for_you_order_confirmation method."""

    def test_send_order_confirmation_success(self):
        """Test successful order confirmation email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
        )

        assert result is True
        service.provider.send_email.assert_called_once()
        # Verify BCC is set
        call_kwargs = service.provider.send_email.call_args.kwargs
        assert call_kwargs.get('bcc_emails') == ["done@nomadkaraoke.com"]

    def test_send_order_confirmation_subject_format(self):
        """Test order confirmation has correct subject format."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Seether",
            title="Tonight",
            job_id="test-job-456",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        assert "Order Confirmed" in subject
        assert "Seether" in subject
        assert "Tonight" in subject

    def test_send_order_confirmation_with_notes(self):
        """Test order confirmation includes notes."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-789",
            notes="Wedding anniversary!",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "Wedding anniversary!" in html_content

    def test_send_order_confirmation_includes_delivery_promise(self):
        """Test order confirmation mentions delivery timeframe."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-abc",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content', '')
        text_content = call_kwargs.get('text_content', '')
        # Should mention delivery timeline
        assert "24" in html_content or "24" in text_content

    def test_send_order_confirmation_failure(self):
        """Test handling of email send failure."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = False

        result = service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-def",
        )

        assert result is False


class TestMadeForYouAdminNotification:
    """Tests for send_made_for_you_admin_notification method."""

    def test_send_admin_notification_success(self):
        """Test successful admin notification email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job-123",
            admin_login_token="test-token-abc",
        )

        assert result is True
        service.provider.send_email.assert_called_once()

    def test_send_admin_notification_subject_format(self):
        """Test admin notification has correct subject format."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Seether",
            title="Tonight",
            job_id="job-123",
            admin_login_token="test-token-abc",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        # Subject format: "Karaoke Order: {artist} - {title} [ID: {job_id}]"
        assert "Karaoke Order" in subject
        assert "Seether" in subject
        assert "Tonight" in subject

    def test_send_admin_notification_includes_customer_email(self):
        """Test admin notification includes customer email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="vip@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job-123",
            admin_login_token="test-token-abc",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "vip@example.com" in html_content

    def test_send_admin_notification_includes_job_link(self):
        """Test admin notification includes job link."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="abc-def-123",
            admin_login_token="test-token-abc",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "abc-def-123" in html_content

    def test_send_admin_notification_includes_audio_count(self):
        """Test admin notification includes audio source count."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job-123",
            admin_login_token="test-token-abc",
            audio_source_count=5,
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "5" in html_content

    def test_send_admin_notification_includes_notes(self):
        """Test admin notification includes customer notes."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job-123",
            admin_login_token="test-token-abc",
            notes="Rush order please!",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "Rush order please!" in html_content

    def test_send_admin_notification_failure(self):
        """Test handling of email send failure."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = False

        result = service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job-123",
            admin_login_token="test-token-abc",
        )

        assert result is False
