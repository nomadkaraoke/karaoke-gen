"""
Unit tests for EmailService review reminder and expiry email methods.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch

# Mock Google Cloud before imports
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())


class TestSendReviewReminder:
    """Test send_review_reminder() email method."""

    def _make_service(self):
        """Create an EmailService with a mock provider."""
        with patch('backend.services.email_service.get_settings'):
            from backend.services.email_service import EmailService
            service = EmailService.__new__(EmailService)
            service.provider = Mock()
            service.provider.send_email.return_value = True
            service.frontend_url = "https://gen.nomadkaraoke.com"
            service.buy_url = "https://gen.nomadkaraoke.com"
            service.settings = Mock()
            return service

    def test_subject_with_artist_and_title(self):
        """Subject should include artist and title."""
        service = self._make_service()
        service.send_review_reminder(
            to_email="user@test.com",
            artist="Adele",
            title="Hello",
            job_id="abc123",
        )
        call_args = service.provider.send_email.call_args
        assert "Adele - Hello" in call_args.kwargs["subject"]
        assert "Reminder" in call_args.kwargs["subject"]

    def test_subject_without_artist_and_title(self):
        """Subject should work without artist/title."""
        service = self._make_service()
        service.send_review_reminder(
            to_email="user@test.com",
            job_id="abc123",
        )
        call_args = service.provider.send_email.call_args
        assert "Reminder: Your karaoke video is waiting for review" == call_args.kwargs["subject"]

    def test_content_includes_review_link(self):
        """HTML content should include the review URL."""
        service = self._make_service()
        service.send_review_reminder(
            to_email="user@test.com",
            artist="Adele",
            title="Hello",
            job_id="abc123",
        )
        call_args = service.provider.send_email.call_args
        assert "gen.nomadkaraoke.com/app/jobs#/abc123/review" in call_args.kwargs["html_content"]
        assert "gen.nomadkaraoke.com/app/jobs#/abc123/review" in call_args.kwargs["text_content"]

    def test_content_includes_expiry_warning(self):
        """Content should warn about 24h expiry."""
        service = self._make_service()
        service.send_review_reminder(
            to_email="user@test.com",
            job_id="abc123",
        )
        call_args = service.provider.send_email.call_args
        assert "24 hours" in call_args.kwargs["html_content"]
        assert "expire" in call_args.kwargs["html_content"]

    def test_content_includes_help_offer(self):
        """Content should offer help via reply."""
        service = self._make_service()
        service.send_review_reminder(
            to_email="user@test.com",
            job_id="abc123",
        )
        call_args = service.provider.send_email.call_args
        assert "reply to this email" in call_args.kwargs["html_content"]


class TestSendReviewExpired:
    """Test send_review_expired() email method."""

    def _make_service(self):
        """Create an EmailService with a mock provider."""
        with patch('backend.services.email_service.get_settings'):
            from backend.services.email_service import EmailService
            service = EmailService.__new__(EmailService)
            service.provider = Mock()
            service.provider.send_email.return_value = True
            service.frontend_url = "https://gen.nomadkaraoke.com"
            service.buy_url = "https://gen.nomadkaraoke.com"
            service.settings = Mock()
            return service

    def test_subject_with_artist_and_title(self):
        """Subject should include artist and title."""
        service = self._make_service()
        service.send_review_expired(
            to_email="user@test.com",
            artist="Adele",
            title="Hello",
            credits_balance=2,
        )
        call_args = service.provider.send_email.call_args
        assert "Adele - Hello" in call_args.kwargs["subject"]
        assert "expired" in call_args.kwargs["subject"]

    def test_subject_without_artist_and_title(self):
        """Subject should work without artist/title."""
        service = self._make_service()
        service.send_review_expired(
            to_email="user@test.com",
            credits_balance=1,
        )
        call_args = service.provider.send_email.call_args
        assert "Your karaoke video has expired" == call_args.kwargs["subject"]

    def test_content_includes_refund_message(self):
        """Content should confirm credit refund."""
        service = self._make_service()
        service.send_review_expired(
            to_email="user@test.com",
            credits_balance=3,
        )
        call_args = service.provider.send_email.call_args
        assert "refunded" in call_args.kwargs["html_content"]
        assert "3 credits" in call_args.kwargs["html_content"]

    def test_content_shows_singular_credit(self):
        """Should show 'credit' (singular) for balance of 1."""
        service = self._make_service()
        service.send_review_expired(
            to_email="user@test.com",
            credits_balance=1,
        )
        call_args = service.provider.send_email.call_args
        assert "1 credit" in call_args.kwargs["html_content"]
        # Should NOT say "1 credits"
        assert "1 credits" not in call_args.kwargs["html_content"]

    def test_content_includes_create_link(self):
        """Content should include a link to create a new video."""
        service = self._make_service()
        service.send_review_expired(
            to_email="user@test.com",
            credits_balance=2,
        )
        call_args = service.provider.send_email.call_args
        assert "gen.nomadkaraoke.com/app" in call_args.kwargs["html_content"]
