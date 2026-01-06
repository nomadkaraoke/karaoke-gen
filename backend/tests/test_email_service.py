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
    SendGridEmailProvider,
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


class TestSendGridEmailProvider:
    """Tests for SendGrid email provider."""

    def test_send_email_success(self):
        """Test successful email sending via SendGrid."""
        provider = SendGridEmailProvider(
            api_key="test-api-key",
            from_email="from@example.com",
            from_name="Test Sender",
        )

        mock_response = Mock()
        mock_response.status_code = 202

        # Patch at the sendgrid module level since it's imported inline
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            mock_client = Mock()
            mock_client.send.return_value = mock_response
            mock_sg.return_value = mock_client

            result = provider.send_email(
                to_email="user@example.com",
                subject="Test Subject",
                html_content="<p>Test</p>",
            )

        assert result is True
        mock_client.send.assert_called_once()

    def test_send_email_with_cc(self):
        """Test email sending with CC via SendGrid."""
        provider = SendGridEmailProvider(
            api_key="test-api-key",
            from_email="from@example.com",
        )

        mock_response = Mock()
        mock_response.status_code = 202

        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            mock_client = Mock()
            mock_client.send.return_value = mock_response
            mock_sg.return_value = mock_client

            result = provider.send_email(
                to_email="user@example.com",
                subject="Test Subject",
                html_content="<p>Test</p>",
                cc_emails=["cc@example.com"],
            )

        assert result is True

    def test_send_email_failure_status(self):
        """Test email sending failure due to bad status."""
        provider = SendGridEmailProvider(
            api_key="test-api-key",
            from_email="from@example.com",
        )

        mock_response = Mock()
        mock_response.status_code = 400

        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            mock_client = Mock()
            mock_client.send.return_value = mock_response
            mock_sg.return_value = mock_client

            result = provider.send_email(
                to_email="user@example.com",
                subject="Test Subject",
                html_content="<p>Test</p>",
            )

        assert result is False

    def test_send_email_exception(self):
        """Test email sending exception handling."""
        provider = SendGridEmailProvider(
            api_key="test-api-key",
            from_email="from@example.com",
        )

        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            mock_sg.return_value.send.side_effect = Exception("API error")

            result = provider.send_email(
                to_email="user@example.com",
                subject="Test Subject",
                html_content="<p>Test</p>",
            )

        assert result is False


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

    def test_uses_sendgrid_when_configured(self):
        """Test that SendGrid is used when API key is set."""
        with patch.dict('os.environ', {'SENDGRID_API_KEY': 'test-key'}):
            service = EmailService()
            assert isinstance(service.provider, SendGridEmailProvider)

    def test_uses_console_when_not_configured(self):
        """Test that console is used when no API key."""
        with patch.dict('os.environ', {}, clear=True):
            # Remove SENDGRID_API_KEY if it exists
            import os
            original = os.environ.pop('SENDGRID_API_KEY', None)
            try:
                service = EmailService()
                assert isinstance(service.provider, ConsoleEmailProvider)
            finally:
                if original:
                    os.environ['SENDGRID_API_KEY'] = original

    def test_is_configured_true_for_sendgrid(self):
        """Test is_configured returns True for SendGrid."""
        service = EmailService()
        service.provider = SendGridEmailProvider("key", "from@example.com")
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

    def test_sendgrid_provider_adds_cc_recipients(self):
        """Test that SendGrid provider adds CC recipients to message."""
        provider = SendGridEmailProvider(
            api_key="test-api-key",
            from_email="from@example.com",
        )

        mock_response = Mock()
        mock_response.status_code = 202

        # Patch at sendgrid module level since imports are inline
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            mock_client = Mock()
            mock_client.send.return_value = mock_response
            mock_sg.return_value = mock_client

            # We need to capture the Mail object
            with patch('sendgrid.helpers.mail.Mail') as mock_mail_class:
                mock_mail = MagicMock()
                mock_mail_class.return_value = mock_mail

                provider.send_email(
                    to_email="user@example.com",
                    subject="Test",
                    html_content="<p>Test</p>",
                    cc_emails=["cc@example.com"],
                )

                # Verify add_cc was called
                mock_mail.add_cc.assert_called()
