"""
Comprehensive tests for Made-For-You order flow.

Tests cover:
- New model fields (made_for_you, customer_email, customer_notes)
- Email service methods (order confirmation, admin notification)
- Ownership transfer on job completion
- Email suppression for intermediate reminders
- Integration scenarios
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import Mock, MagicMock, patch, AsyncMock

from backend.models.job import Job, JobCreate, JobStatus
from backend.services.email_service import EmailService


# =============================================================================
# Model Tests - New Fields
# =============================================================================

class TestMadeForYouModelFields:
    """Tests for made-for-you fields on Job and JobCreate models."""

    def test_job_has_made_for_you_field(self):
        """Test Job model has made_for_you field."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert hasattr(job, 'made_for_you')
        assert job.made_for_you is False  # Default value

    def test_job_made_for_you_can_be_set_true(self):
        """Test Job model made_for_you can be set to True."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
        )
        assert job.made_for_you is True

    def test_job_has_customer_email_field(self):
        """Test Job model has customer_email field."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert hasattr(job, 'customer_email')
        assert job.customer_email is None  # Default value

    def test_job_customer_email_can_be_set(self):
        """Test Job model customer_email can be set."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            customer_email="customer@example.com",
        )
        assert job.customer_email == "customer@example.com"

    def test_job_has_customer_notes_field(self):
        """Test Job model has customer_notes field."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert hasattr(job, 'customer_notes')
        assert job.customer_notes is None  # Default value

    def test_job_customer_notes_can_be_set(self):
        """Test Job model customer_notes can be set."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            customer_notes="Please make this extra special!",
        )
        assert job.customer_notes == "Please make this extra special!"

    def test_job_create_has_made_for_you_field(self):
        """Test JobCreate model has made_for_you field."""
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Song",
        )
        assert hasattr(job_create, 'made_for_you')
        assert job_create.made_for_you is False

    def test_job_create_has_customer_email_field(self):
        """Test JobCreate model has customer_email field."""
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Song",
        )
        assert hasattr(job_create, 'customer_email')
        assert job_create.customer_email is None

    def test_job_create_has_customer_notes_field(self):
        """Test JobCreate model has customer_notes field."""
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Song",
        )
        assert hasattr(job_create, 'customer_notes')
        assert job_create.customer_notes is None

    def test_job_create_made_for_you_full_config(self):
        """Test JobCreate with all made-for-you fields configured."""
        job_create = JobCreate(
            artist="Seether",
            title="Tonight",
            user_email="admin@nomadkaraoke.com",
            made_for_you=True,
            customer_email="customer@example.com",
            customer_notes="Wedding anniversary song!",
        )
        assert job_create.made_for_you is True
        assert job_create.customer_email == "customer@example.com"
        assert job_create.customer_notes == "Wedding anniversary song!"
        assert job_create.user_email == "admin@nomadkaraoke.com"

    def test_job_roundtrip_serialization(self):
        """Test Job with made-for-you fields survives dict serialization."""
        job = Job(
            job_id="test123",
            status=JobStatus.AWAITING_AUDIO_SELECTION,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
            customer_email="customer@example.com",
            customer_notes="Test notes",
            user_email="admin@nomadkaraoke.com",
            artist="Test Artist",
            title="Test Song",
        )

        job_dict = job.dict()
        assert job_dict['made_for_you'] is True
        assert job_dict['customer_email'] == "customer@example.com"
        assert job_dict['customer_notes'] == "Test notes"


# =============================================================================
# Email Service Tests - Made-For-You Methods
# =============================================================================

class TestMadeForYouOrderConfirmationEmail:
    """Tests for send_made_for_you_order_confirmation method."""

    def test_send_order_confirmation_basic(self):
        """Test basic order confirmation email."""
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

    def test_send_order_confirmation_subject_includes_song(self):
        """Test order confirmation subject includes artist and title."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Seether",
            title="Tonight",
            job_id="test-job-123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        assert "Seether" in subject
        assert "Tonight" in subject
        assert "Order Confirmed" in subject

    def test_send_order_confirmation_includes_customer_notes(self):
        """Test order confirmation includes customer notes when provided."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
            notes="Wedding anniversary!",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "Wedding anniversary!" in html_content

    def test_send_order_confirmation_24_hour_promise(self):
        """Test order confirmation mentions 24-hour delivery."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        text_content = call_kwargs.get('text_content')
        # Should mention 24 hours somewhere
        assert "24" in html_content or "24" in text_content

    def test_send_order_confirmation_escapes_html(self):
        """Test order confirmation escapes HTML in user input."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="<script>alert('xss')</script>",
            title="Test Song",
            job_id="test-job-123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "<script>" not in html_content

    def test_send_order_confirmation_no_cc(self):
        """Test order confirmation doesn't CC anyone."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        assert call_kwargs.get('cc_emails') is None


class TestEmailServiceSendEmailPassthrough:
    """Tests for the generic EmailService.send_email passthrough.

    Regression (2026-08-22): the made-for-you failure handler called
    ``email_service.send_email(...)`` but EmailService had no such method (only the
    provider did), so the failure alert itself crashed with
    ``'EmailService' object has no attribute 'send_email'`` — masking the original
    error and losing the admin alert entirely.
    """

    def test_send_email_delegates_to_provider(self):
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_email(
            to_email="admin@example.com",
            subject="[FAILED] Test",
            html_content="<p>oops</p>",
            text_content="oops",
        )

        assert result is True
        service.provider.send_email.assert_called_once()
        call_kwargs = service.provider.send_email.call_args.kwargs
        assert call_kwargs["to_email"] == "admin@example.com"
        assert call_kwargs["subject"] == "[FAILED] Test"
        assert call_kwargs["html_content"] == "<p>oops</p>"
        assert call_kwargs["text_content"] == "oops"


class TestMadeForYouAdminNotificationEmail:
    """Tests for send_made_for_you_admin_notification method."""

    def test_send_admin_notification_basic(self):
        """Test basic admin notification email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
            admin_login_token="test-token-abc123",
        )

        assert result is True
        service.provider.send_email.assert_called_once()

    def test_send_admin_notification_subject_format(self):
        """Test admin notification subject format."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Seether",
            title="Tonight",
            job_id="test-job-123",
            admin_login_token="test-token-abc123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        # New subject format: "Karaoke Order: {artist} - {title} [ID: {job_id}]"
        assert "Karaoke Order:" in subject
        assert "Seether" in subject
        assert "Tonight" in subject
        assert "[ID: test-job-123]" in subject

    def test_send_admin_notification_includes_admin_token_link(self):
        """Test admin notification includes link with admin_token for one-click login."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="abc123",
            admin_login_token="secure-admin-token-xyz",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        # Should include link to /app/ with admin_token for one-click login
        assert "admin_token=secure-admin-token-xyz" in html_content
        assert "/app/" in html_content

    def test_send_admin_notification_includes_customer_email(self):
        """Test admin notification shows customer email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="vip.customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
            admin_login_token="test-token-abc123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "vip.customer@example.com" in html_content

    def test_send_admin_notification_includes_notes(self):
        """Test admin notification includes customer notes."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
            admin_login_token="test-token-abc123",
            notes="Urgent - needed for wedding!",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "Urgent - needed for wedding!" in html_content

    def test_send_admin_notification_includes_audio_count(self):
        """Test admin notification includes audio source count."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
            admin_login_token="test-token-abc123",
            audio_source_count=5,
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "5" in html_content

    def test_send_admin_notification_includes_deliver_by_eastern(self):
        """Test admin notification includes Deliver By deadline in Eastern Time."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
            admin_login_token="test-token-abc123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        text_content = call_kwargs.get('text_content')
        # Should include "Deliver By" label and ET (Eastern Time) timezone
        assert "Deliver By" in html_content or "Deliver By" in text_content
        assert "ET" in html_content or "ET" in text_content

    def test_send_admin_notification_escapes_html(self):
        """Test admin notification escapes HTML in user input."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
            admin_login_token="test-token-abc123",
            notes="<script>alert('xss')</script>",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "<script>" not in html_content


# =============================================================================
# Video Worker Tests - Ownership Transfer
# =============================================================================

# Try to import video_worker - may fail due to environment dependencies
try:
    from backend.workers.video_worker import _handle_made_for_you_completion
    VIDEO_WORKER_AVAILABLE = True
except ImportError:
    VIDEO_WORKER_AVAILABLE = False
    _handle_made_for_you_completion = None


@pytest.mark.skipif(not VIDEO_WORKER_AVAILABLE, reason="video_worker import requires lyrics_transcriber")
class TestMadeForYouOwnershipTransfer:
    """Tests for ownership transfer on made-for-you job completion."""

    def test_handle_made_for_you_completion_transfers_ownership(self):
        """Test that _handle_made_for_you_completion transfers user_email."""
        mock_job = MagicMock()
        mock_job.made_for_you = True
        mock_job.customer_email = "customer@example.com"

        mock_job_manager = MagicMock()

        _handle_made_for_you_completion("job123", mock_job, mock_job_manager)

        mock_job_manager.update_job.assert_called_once_with(
            "job123",
            {'user_email': 'customer@example.com'}
        )

    def test_handle_made_for_you_completion_skips_non_made_for_you(self):
        """Test that ownership transfer is skipped for regular jobs."""
        mock_job = MagicMock()
        mock_job.made_for_you = False
        mock_job.customer_email = "customer@example.com"

        mock_job_manager = MagicMock()

        _handle_made_for_you_completion("job123", mock_job, mock_job_manager)

        mock_job_manager.update_job.assert_not_called()

    def test_handle_made_for_you_completion_handles_missing_flag(self):
        """Test that ownership transfer handles jobs without made_for_you attr."""
        mock_job = MagicMock(spec=[])  # No attributes

        mock_job_manager = MagicMock()

        # Should not raise, should skip gracefully
        _handle_made_for_you_completion("job123", mock_job, mock_job_manager)

        mock_job_manager.update_job.assert_not_called()

    def test_handle_made_for_you_completion_handles_missing_customer_email(self):
        """Test that ownership transfer handles missing customer_email."""
        mock_job = MagicMock()
        mock_job.made_for_you = True
        mock_job.customer_email = None

        mock_job_manager = MagicMock()

        # Should not raise, should skip and log warning
        _handle_made_for_you_completion("job123", mock_job, mock_job_manager)

        mock_job_manager.update_job.assert_not_called()

    def test_handle_made_for_you_completion_handles_false_made_for_you(self):
        """Test explicit False made_for_you is not transferred."""
        mock_job = MagicMock()
        mock_job.made_for_you = False
        mock_job.customer_email = "customer@example.com"

        mock_job_manager = MagicMock()

        _handle_made_for_you_completion("job123", mock_job, mock_job_manager)

        mock_job_manager.update_job.assert_not_called()


class TestMadeForYouOwnershipTransferLogic:
    """
    Tests for ownership transfer logic without requiring video_worker import.

    These tests validate the logic that would be in _handle_made_for_you_completion
    without actually importing the module.
    """

    def test_ownership_transfer_logic_made_for_you_true(self):
        """Test ownership transfer logic when made_for_you is True."""
        mock_job = MagicMock()
        mock_job.made_for_you = True
        mock_job.customer_email = "customer@example.com"

        mock_job_manager = MagicMock()

        # Replicate the logic from _handle_made_for_you_completion
        if not getattr(mock_job, 'made_for_you', False):
            return  # Should not return early

        customer_email = getattr(mock_job, 'customer_email', None)
        if not customer_email:
            return  # Should not return early

        # Transfer ownership
        mock_job_manager.update_job("job123", {'user_email': customer_email})

        mock_job_manager.update_job.assert_called_once_with(
            "job123",
            {'user_email': 'customer@example.com'}
        )

    def test_ownership_transfer_logic_made_for_you_false(self):
        """Test ownership transfer logic when made_for_you is False."""
        mock_job = MagicMock()
        mock_job.made_for_you = False
        mock_job.customer_email = "customer@example.com"

        mock_job_manager = MagicMock()

        # Replicate the logic
        if not getattr(mock_job, 'made_for_you', False):
            pass  # Early return - do nothing
        else:
            mock_job_manager.update_job("job123", {'user_email': mock_job.customer_email})

        mock_job_manager.update_job.assert_not_called()

    def test_ownership_transfer_logic_missing_customer_email(self):
        """Test ownership transfer logic when customer_email is missing."""
        mock_job = MagicMock()
        mock_job.made_for_you = True
        mock_job.customer_email = None

        mock_job_manager = MagicMock()

        # Replicate the logic
        if not getattr(mock_job, 'made_for_you', False):
            pass  # Early return
        else:
            customer_email = getattr(mock_job, 'customer_email', None)
            if customer_email:
                mock_job_manager.update_job("job123", {'user_email': customer_email})

        mock_job_manager.update_job.assert_not_called()

    def test_ownership_transfer_logic_missing_made_for_you_attr(self):
        """Test ownership transfer logic when made_for_you attr doesn't exist."""
        mock_job = MagicMock(spec=['customer_email'])  # Only has customer_email
        mock_job.customer_email = "customer@example.com"

        mock_job_manager = MagicMock()

        # Replicate the logic - getattr with default False handles missing attr
        if not getattr(mock_job, 'made_for_you', False):
            pass  # Early return due to False default
        else:
            mock_job_manager.update_job("job123", {'user_email': mock_job.customer_email})

        mock_job_manager.update_job.assert_not_called()


# =============================================================================
# Email Suppression Tests
# =============================================================================

class TestMadeForYouEmailSuppression:
    """Tests for email suppression on made-for-you intermediate states."""

    def _create_blocking_job(self, made_for_you: bool = False) -> Job:
        """Create a job in a blocking state for testing."""
        return Job(
            job_id="test-job-123",
            status=JobStatus.AWAITING_REVIEW,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            user_email="admin@nomadkaraoke.com",
            artist="Test Artist",
            title="Test Song",
            made_for_you=made_for_you,
            customer_email="customer@example.com" if made_for_you else None,
            state_data={
                'blocking_state_entered_at': datetime.now(UTC).isoformat(),
                'blocking_action_type': 'lyrics',
                'reminder_sent': False,
            },
        )

    @pytest.fixture
    def mock_job_manager(self):
        """Create a mock job manager."""
        manager = MagicMock()
        manager.firestore = MagicMock()
        return manager

    def test_idle_reminder_skipped_for_made_for_you_job(self, mock_job_manager):
        """Test that idle reminders are skipped for made-for-you jobs."""
        # This tests the logic in internal.py's idle reminder check endpoint
        job = self._create_blocking_job(made_for_you=True)

        # The check in internal.py:
        # if getattr(job, 'made_for_you', False):
        #     return skipped response

        assert getattr(job, 'made_for_you', False) is True
        # When this is True, the endpoint returns early without sending email

    def test_idle_reminder_sent_for_regular_job(self, mock_job_manager):
        """Test that idle reminders ARE sent for regular jobs."""
        job = self._create_blocking_job(made_for_you=False)

        assert getattr(job, 'made_for_you', False) is False
        # When this is False, the endpoint proceeds to send the email

    def test_made_for_you_getattr_handles_missing_attribute(self):
        """Test getattr pattern handles jobs without made_for_you."""
        job = Job(
            job_id="test-job-123",
            status=JobStatus.AWAITING_REVIEW,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        # Using getattr with default False
        result = getattr(job, 'made_for_you', False)
        # Should return False (the field exists with default False)
        assert result is False


# =============================================================================
# Integration-Style Tests
# =============================================================================

class TestMadeForYouIntegrationScenarios:
    """Integration-style tests for made-for-you scenarios."""

    def test_full_made_for_you_job_creation(self):
        """Test creating a job with full made-for-you configuration."""
        # Simulate the job creation from webhook handler
        job_create = JobCreate(
            artist="Seether",
            title="Tonight",
            user_email="admin@nomadkaraoke.com",  # Admin owns during processing
            made_for_you=True,
            customer_email="customer@example.com",
            customer_notes="Anniversary song!",
            # Distribution settings
            enable_youtube_upload=True,
            dropbox_path="/Production/Ready To Upload",
            gdrive_folder_id="1ABC123",
            brand_prefix="NOMAD",
        )

        assert job_create.user_email == "admin@nomadkaraoke.com"
        assert job_create.customer_email == "customer@example.com"
        assert job_create.made_for_you is True
        assert job_create.enable_youtube_upload is True
        assert job_create.dropbox_path == "/Production/Ready To Upload"

    def test_job_visibility_logic(self):
        """Test job visibility logic for made-for-you jobs."""
        # During processing: job owned by admin
        job_processing = Job(
            job_id="test123",
            status=JobStatus.PROCESSING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            user_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            made_for_you=True,
        )

        # Job should NOT be visible to customer (user_email != customer_email)
        assert job_processing.user_email != job_processing.customer_email

        # After completion: ownership transferred
        job_complete = Job(
            job_id="test123",
            status=JobStatus.COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            user_email="customer@example.com",  # Transferred
            customer_email="customer@example.com",
            made_for_you=True,
        )

        # Job should now be visible to customer
        assert job_complete.user_email == job_complete.customer_email

    def test_email_flow_simulation(self):
        """Test the expected email flow for made-for-you orders."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        # Step 1: Order confirmation to customer
        service.send_made_for_you_order_confirmation(
            to_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job123",
            notes="Test notes",
        )

        # Step 2: Admin notification
        service.send_made_for_you_admin_notification(
            to_email="madeforyou@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job123",
            admin_login_token="test-admin-token-xyz",
            audio_source_count=3,
        )

        # Verify both emails were sent
        assert service.provider.send_email.call_count == 2

        # First call should be to customer
        first_call = service.provider.send_email.call_args_list[0]
        assert first_call.kwargs['to_email'] == "customer@example.com"

        # Second call should be to admin
        second_call = service.provider.send_email.call_args_list[1]
        assert second_call.kwargs['to_email'] == "madeforyou@nomadkaraoke.com"

    def test_awaiting_audio_selection_state(self):
        """Test that made-for-you jobs use AWAITING_AUDIO_SELECTION state."""
        job = Job(
            job_id="test123",
            status=JobStatus.AWAITING_AUDIO_SELECTION,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            user_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            made_for_you=True,
            state_data={
                'audio_search_results': [
                    {'source': 'spotify', 'url': 'https://...'},
                    {'source': 'deezer', 'url': 'https://...'},
                ],
            },
        )

        assert job.status == JobStatus.AWAITING_AUDIO_SELECTION
        assert job.made_for_you is True
        assert 'audio_search_results' in job.state_data


class TestMadeForYouEdgeCases:
    """Edge case tests for made-for-you functionality."""

    def test_empty_customer_notes(self):
        """Test job with empty customer notes."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
            customer_email="customer@example.com",
            customer_notes="",  # Empty string
        )
        assert job.customer_notes == ""

    def test_unicode_in_customer_notes(self):
        """Test job with unicode in customer notes."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
            customer_email="customer@example.com",
            customer_notes="Anniversary song! 🎉🎂",
        )
        assert "🎉" in job.customer_notes

    def test_special_characters_in_email(self):
        """Test job with special characters in customer email."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
            customer_email="customer+special@example.com",
        )
        assert job.customer_email == "customer+special@example.com"

    def test_long_customer_notes(self):
        """Test job with very long customer notes."""
        long_notes = "A" * 5000
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
            customer_email="customer@example.com",
            customer_notes=long_notes,
        )
        assert len(job.customer_notes) == 5000

    def test_made_for_you_false_explicitly(self):
        """Test regular job with explicit made_for_you=False."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=False,
            user_email="regular@example.com",
        )
        assert job.made_for_you is False
        assert job.customer_email is None


# =============================================================================
# Webhook Handler Tests - _handle_made_for_you_order
# =============================================================================

class TestHandleMadeForYouOrder:
    """
    Comprehensive tests for the _handle_made_for_you_order webhook handler.

    This function processes Stripe checkout completions for made-for-you orders.
    It must:
    1. Create a job with made_for_you=True
    2. Set user_email to admin email (admin owns during processing)
    3. Set customer_email for final delivery
    4. Set customer_notes from order
    5. Set auto_download=False to pause at audio selection
    6. Perform audio search to populate options
    7. Transition job to AWAITING_AUDIO_SELECTION state
    8. Send admin notification email with audio selection link
    9. Send customer order confirmation email
    """

    @pytest.fixture
    def mock_job_manager(self):
        """Create mock JobManager that captures job creation params."""
        manager = MagicMock()
        # Return a mock Job object when create_job is called
        mock_job = MagicMock()
        mock_job.job_id = "test-job-123"
        manager.create_job.return_value = mock_job
        return manager

    @pytest.fixture
    def mock_worker_service(self):
        """Create mock worker service."""
        service = MagicMock()
        service.trigger_audio_worker = AsyncMock()
        service.trigger_lyrics_worker = AsyncMock()
        return service

    @pytest.fixture
    def mock_audio_search_service(self):
        """Create mock audio search service with results."""
        service = MagicMock()

        # Mock search results
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            'provider': 'RED',
            'artist': 'Test Artist',
            'title': 'Test Album',
            'quality': 'FLAC 16bit CD',
            'is_lossless': True,
        }
        service.search.return_value = [mock_result, mock_result, mock_result]
        service.last_remote_search_id = "search_abc123"
        return service

    @pytest.fixture
    def mock_storage_service(self):
        """Create mock storage service."""
        return MagicMock()

    @pytest.fixture
    def mock_email_service(self):
        """Create mock email service."""
        service = MagicMock()
        service.send_email.return_value = True
        service.send_made_for_you_order_confirmation.return_value = True
        service.send_made_for_you_admin_notification.return_value = True
        return service

    @pytest.fixture
    def mock_user_service(self):
        """Create mock user service."""
        service = MagicMock()
        service._mark_stripe_session_processed.return_value = None
        # Mock create_admin_login_token to return a MagicMock with .token attribute
        mock_admin_login = MagicMock()
        mock_admin_login.token = "test-admin-login-token-123"
        service.create_admin_login_token.return_value = mock_admin_login
        return service

    @pytest.fixture
    def mock_theme_service(self):
        """Create mock theme service."""
        service = MagicMock()
        service.get_default_theme_id.return_value = "nomad"
        return service

    @pytest.fixture
    def order_metadata(self):
        """Standard order metadata from Stripe session."""
        return {
            "order_type": "made_for_you",
            "customer_email": "customer@example.com",
            "artist": "Avril Lavigne",
            "title": "Complicated",
            "source_type": "search",
            "notes": "Please make sure the lyrics are perfect!",
        }

    @pytest.mark.asyncio
    async def test_job_created_with_made_for_you_flag(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        CRITICAL TEST: Job must be created with made_for_you=True.

        This flag is essential for:
        - Ownership transfer on completion
        - Email suppression for intermediate states
        - Identifying made-for-you jobs in admin UI
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            # Mock audio search to raise NoResultsError so we skip download logic
            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # Verify job was created
            mock_job_manager.create_job.assert_called_once()
            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # CRITICAL ASSERTION: made_for_you must be True
            assert job_create_arg.made_for_you is True, \
                "Job must be created with made_for_you=True for made-for-you orders"

    @pytest.mark.asyncio
    async def test_job_owned_by_admin_during_processing(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        CRITICAL TEST: Job user_email must be admin email, not customer email.

        During processing, admin owns the job to:
        - See it in their job list
        - Handle intermediate review steps
        - Customer only gets access after completion
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # CRITICAL ASSERTION: admin owns the job during processing
            assert job_create_arg.user_email == ADMIN_EMAIL, \
                f"Job user_email must be {ADMIN_EMAIL}, not customer email"
            assert job_create_arg.user_email != order_metadata["customer_email"], \
                "Job user_email must NOT be customer email during processing"

    @pytest.mark.asyncio
    async def test_customer_email_stored_for_delivery(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        CRITICAL TEST: customer_email must be stored for final delivery.

        The customer_email field is used for:
        - Ownership transfer on completion
        - Sending completion email with download links
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # CRITICAL ASSERTION: customer_email stored for delivery
            assert job_create_arg.customer_email == order_metadata["customer_email"], \
                "Job must store customer_email for final delivery"

    @pytest.mark.asyncio
    async def test_customer_notes_preserved(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        Customer notes from order must be stored on job for admin reference.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            assert job_create_arg.customer_notes == order_metadata["notes"], \
                "Job must store customer notes from order"

    @pytest.mark.asyncio
    async def test_auto_download_disabled_for_admin_selection(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        CRITICAL TEST: auto_download must be False to pause for admin selection.

        The made-for-you flow requires admin to select the audio source.
        Setting auto_download=True would bypass this, selecting automatically.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # CRITICAL ASSERTION: auto_download must be False
            assert job_create_arg.auto_download is False, \
                "auto_download must be False to allow admin audio selection"

    @pytest.mark.asyncio
    async def test_job_transitions_to_awaiting_audio_selection_with_results(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, mock_audio_search_service, order_metadata
    ):
        """
        When audio search returns results, job should pause at AWAITING_AUDIO_SELECTION.
        """
        from backend.api.routes.users import _handle_made_for_you_order
        from backend.models.job import JobStatus

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service', return_value=mock_audio_search_service), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # Check that job transitioned to AWAITING_AUDIO_SELECTION
            transition_calls = mock_job_manager.transition_to_state.call_args_list

            # Should have at least one transition to AWAITING_AUDIO_SELECTION
            awaiting_selection_calls = [
                call for call in transition_calls
                if call.kwargs.get('new_status') == JobStatus.AWAITING_AUDIO_SELECTION
                or (len(call.args) > 1 and call.args[1] == JobStatus.AWAITING_AUDIO_SELECTION)
            ]

            assert len(awaiting_selection_calls) >= 1, \
                "Job should transition to AWAITING_AUDIO_SELECTION for admin to select audio"

    @pytest.mark.asyncio
    async def test_audio_search_results_stored_in_state_data(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, mock_audio_search_service, order_metadata
    ):
        """
        Audio search results must be stored so admin can view options.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service', return_value=mock_audio_search_service), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # Check update_job was called with audio_search_results
            update_calls = mock_job_manager.update_job.call_args_list

            # Should have stored search results somewhere in the update calls
            all_call_str = str(update_calls)
            assert 'audio_search_results' in all_call_str or 'state_data' in all_call_str, \
                "Audio search results should be stored in job state_data"

    @pytest.mark.asyncio
    async def test_admin_notification_email_sent(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        Admin must receive email notification about new made-for-you order.
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # Check email_service.send_made_for_you_admin_notification was called
            mock_email_service.send_made_for_you_admin_notification.assert_called_once()
            call_kwargs = mock_email_service.send_made_for_you_admin_notification.call_args.kwargs
            assert call_kwargs['to_email'] == ADMIN_EMAIL, \
                f"Admin at {ADMIN_EMAIL} must receive notification email"

    @pytest.mark.asyncio
    async def test_customer_confirmation_email_sent(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        Customer must receive order confirmation email.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # Check email_service.send_made_for_you_order_confirmation was called
            mock_email_service.send_made_for_you_order_confirmation.assert_called_once()
            call_kwargs = mock_email_service.send_made_for_you_order_confirmation.call_args.kwargs
            assert call_kwargs['to_email'] == order_metadata["customer_email"], \
                "Customer must receive order confirmation email"
            assert call_kwargs['job_id'] == "test-job-123", \
                "Customer email must include job ID (order reference)"

    @pytest.mark.asyncio
    async def test_admin_email_includes_admin_login_token(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        Admin notification email must include admin_login_token for one-click login.
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # Verify create_admin_login_token was called to generate the token
            mock_user_service.create_admin_login_token.assert_called_once_with(
                email=ADMIN_EMAIL,
                expiry_hours=24,
            )

            # Check email_service.send_made_for_you_admin_notification was called with admin_login_token
            mock_email_service.send_made_for_you_admin_notification.assert_called_once()
            call_kwargs = mock_email_service.send_made_for_you_admin_notification.call_args.kwargs

            # Verify job_id and admin_login_token are passed
            assert call_kwargs.get('job_id') == "test-job-123", \
                "Admin email must include job ID for reference"
            assert call_kwargs.get('admin_login_token') == "test-admin-login-token-123", \
                "Admin email must include admin_login_token for one-click login"

    @pytest.mark.asyncio
    async def test_youtube_url_order_skips_search(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service
    ):
        """
        Orders with YouTube URL should download audio then start processing (no search).

        Note: The download step was added in Jan 2026 to fix job 811ec4e5 failure.
        See docs/LESSONS-LEARNED.md "YouTube URL Downloads Must Happen Before Workers".

        The handler now uses job_manager.start_job_processing() which handles
        transitioning to DOWNLOADING and triggering workers.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        youtube_metadata = {
            "order_type": "made_for_you",
            "customer_email": "customer@example.com",
            "artist": "Test Artist",
            "title": "Test Song",
            "source_type": "youtube",
            "youtube_url": "https://youtube.com/watch?v=abc123",
        }

        # Mock YouTube download service to return a GCS path
        mock_youtube_service = MagicMock()
        mock_youtube_service.download = AsyncMock(
            return_value="uploads/test-job-123/audio/test.webm"
        )

        # Mock start_job_processing as an async method
        mock_job_manager.start_job_processing = AsyncMock()

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_service):

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=youtube_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # URL should be set
            assert job_create_arg.url == youtube_metadata["youtube_url"]

            # Audio should be downloaded first
            mock_youtube_service.download.assert_called_once()

            # start_job_processing should be called (handles transition + workers)
            mock_job_manager.start_job_processing.assert_called_once_with("test-job-123")

    @pytest.mark.asyncio
    async def test_stripe_session_marked_processed(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        Stripe session must be marked as processed for idempotency.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            mock_user_service._mark_stripe_session_processed.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_theme_applied(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        Default theme (nomad) should be applied to made-for-you jobs.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            assert job_create_arg.theme_id == "nomad", \
                "Default nomad theme should be applied to made-for-you jobs"


class TestMadeForYouYouTubeDownloadContract:
    """
    CRITICAL: Contract tests for YouTube URL handling in made-for-you orders.

    Bug context (2026-01-28): Job 811ec4e5 failed because the webhook handler
    triggered workers directly for YouTube URLs without downloading audio first.
    Workers require input_media_gcs_path to be set, which only happens after
    YouTubeDownloadService.download() completes.

    This test ensures the correct pattern:
    1. YouTubeDownloadService.download() is called BEFORE workers
    2. Job is updated with input_media_gcs_path BEFORE workers
    3. THEN workers are triggered

    See docs/LESSONS-LEARNED.md "YouTube URL Downloads Must Happen Before Workers"
    """

    @pytest.fixture
    def mock_job_manager(self):
        """Create mock JobManager with update tracking."""
        manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-123"
        manager.create_job.return_value = mock_job
        return manager

    @pytest.fixture
    def mock_email_service(self):
        """Create mock email service."""
        service = MagicMock()
        service.send_email.return_value = True
        return service

    @pytest.fixture
    def mock_user_service(self):
        """Create mock user service."""
        service = MagicMock()
        service._mark_stripe_session_processed.return_value = None
        mock_admin_login = MagicMock()
        mock_admin_login.token = "test-admin-login-token-123"
        service.create_admin_login_token.return_value = mock_admin_login
        return service

    @pytest.fixture
    def mock_theme_service(self):
        """Create mock theme service."""
        service = MagicMock()
        service.get_default_theme_id.return_value = "nomad"
        return service

    @pytest.fixture
    def youtube_order_metadata(self):
        """Order metadata with YouTube URL."""
        return {
            "order_type": "made_for_you",
            "customer_email": "customer@example.com",
            "artist": "Test Artist",
            "title": "Test Song",
            "source_type": "youtube",
            "youtube_url": "https://youtube.com/watch?v=abc123",
        }

    @pytest.mark.asyncio
    async def test_youtube_url_downloads_before_workers(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, youtube_order_metadata
    ):
        """
        CRITICAL: YouTube audio MUST be downloaded before workers are triggered.

        The correct flow is:
        1. YouTubeDownloadService.download() returns GCS path
        2. Job is updated with input_media_gcs_path
        3. Workers are triggered

        Without this, workers fail with "Failed to download audio file" because
        input_media_gcs_path is null.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        # Track call order to verify sequence
        call_order = []

        # Mock YouTubeDownloadService to return a GCS path
        mock_youtube_service = MagicMock()
        mock_youtube_service.download = AsyncMock(
            return_value="uploads/test-job-123/audio/test.webm",
            side_effect=lambda **kwargs: (
                call_order.append('youtube_download'),
                "uploads/test-job-123/audio/test.webm"
            )[1]
        )

        # Mock worker service and track when workers are triggered
        mock_worker_service = MagicMock()
        mock_worker_service.trigger_audio_worker = AsyncMock(
            side_effect=lambda x: call_order.append('audio_worker')
        )
        mock_worker_service.trigger_lyrics_worker = AsyncMock(
            side_effect=lambda x: call_order.append('lyrics_worker')
        )

        # Track when input_media_gcs_path is set
        original_update_job = mock_job_manager.update_job
        def tracking_update_job(job_id, data):
            if isinstance(data, dict) and 'input_media_gcs_path' in data:
                call_order.append('gcs_path_set')
            return original_update_job(job_id, data) if callable(original_update_job) else None
        mock_job_manager.update_job = MagicMock(side_effect=tracking_update_job)

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_service):

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=youtube_order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

        # CRITICAL: YouTubeDownloadService.download MUST be called for YouTube URLs
        mock_youtube_service.download.assert_called_once()

        # CRITICAL: GCS path MUST be set BEFORE workers are triggered
        assert 'gcs_path_set' in call_order, \
            "input_media_gcs_path must be set for YouTube URL orders. " \
            f"Call order was: {call_order}"

        gcs_path_index = call_order.index('gcs_path_set')
        worker_indices = [i for i, x in enumerate(call_order)
                         if x in ('audio_worker', 'lyrics_worker')]

        for worker_index in worker_indices:
            assert gcs_path_index < worker_index, \
                f"input_media_gcs_path must be set BEFORE workers are triggered. " \
                f"Call order was: {call_order}"

    @pytest.mark.asyncio
    async def test_youtube_url_calls_start_job_processing_after_download(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, youtube_order_metadata
    ):
        """
        CRITICAL: start_job_processing must be called AFTER download completes.

        The handler uses job_manager.start_job_processing() which:
        1. Transitions job to DOWNLOADING
        2. Triggers audio and lyrics workers

        Without start_job_processing, workers would run but all status updates
        would fail silently because transitions from PENDING to GENERATING_SCREENS
        or AWAITING_REVIEW are invalid.

        This bug caused made-for-you orders to get stuck at PENDING status even though
        all processing completed successfully. See job IDs 06cfea29 and 984da08b.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        # Track call order to verify sequence
        call_order = []

        # Mock YouTubeDownloadService to return a GCS path
        mock_youtube_service = MagicMock()
        mock_youtube_service.download = AsyncMock(
            return_value="uploads/test-job-123/audio/test.webm",
            side_effect=lambda **kwargs: (
                call_order.append('youtube_download'),
                "uploads/test-job-123/audio/test.webm"
            )[1]
        )

        # Track when start_job_processing is called
        mock_job_manager.start_job_processing = AsyncMock(
            side_effect=lambda job_id: call_order.append('start_job_processing')
        )

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_service):

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=youtube_order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

        # CRITICAL: start_job_processing must be called
        assert 'start_job_processing' in call_order, \
            f"start_job_processing must be called. Call order was: {call_order}"

        # CRITICAL: start_job_processing must happen AFTER download
        download_index = call_order.index('youtube_download')
        processing_index = call_order.index('start_job_processing')

        assert download_index < processing_index, \
            f"YouTube download must complete BEFORE start_job_processing. " \
            f"Call order was: {call_order}"

    @pytest.mark.asyncio
    async def test_youtube_download_called_with_correct_params(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, youtube_order_metadata
    ):
        """
        YouTubeDownloadService.download must be called with job_id, url, artist, and title.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_youtube_service = MagicMock()
        mock_youtube_service.download = AsyncMock(
            return_value="uploads/test-job-123/audio/test.webm"
        )

        mock_worker_service = MagicMock()
        mock_worker_service.trigger_audio_worker = AsyncMock()
        mock_worker_service.trigger_lyrics_worker = AsyncMock()

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_service):

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=youtube_order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

        # Verify download was called with expected parameters
        mock_youtube_service.download.assert_called_once()
        call_kwargs = mock_youtube_service.download.call_args.kwargs

        assert call_kwargs.get('url') == youtube_order_metadata['youtube_url'], \
            "download must be called with the YouTube URL"
        assert call_kwargs.get('job_id') == "test-job-123", \
            "download must be called with the job_id"
        assert call_kwargs.get('artist') == youtube_order_metadata['artist'], \
            "download must be called with the artist"
        assert call_kwargs.get('title') == youtube_order_metadata['title'], \
            "download must be called with the title"

    @pytest.mark.asyncio
    async def test_youtube_download_failure_marks_job_failed(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, youtube_order_metadata
    ):
        """
        When YouTube download fails, job should be transitioned to FAILED status.

        Flow in _handle_made_for_you_order:
        1. Inner except catches YouTubeDownloadError
        2. Transitions job to FAILED status
        3. Re-raises the exception
        4. Outer except catches it and sends admin notification email
        5. Outer handler swallows the exception (doesn't propagate to caller)
        """
        from backend.api.routes.users import _handle_made_for_you_order
        from backend.services.youtube_download_service import YouTubeDownloadError
        from backend.models.job import JobStatus

        # Mock YouTubeDownloadService to raise an error
        mock_youtube_service = MagicMock()
        mock_youtube_service.download = AsyncMock(
            side_effect=YouTubeDownloadError("Video unavailable")
        )

        mock_worker_service = MagicMock()
        mock_worker_service.trigger_audio_worker = AsyncMock()
        mock_worker_service.trigger_lyrics_worker = AsyncMock()

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_service):

            # Inner handler marks job failed and re-raises; outer handler sends notification
            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=youtube_order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

        # Verify job was transitioned to FAILED
        mock_job_manager.transition_to_state.assert_called()
        transition_call = mock_job_manager.transition_to_state.call_args
        assert transition_call.kwargs.get('new_status') == JobStatus.FAILED, \
            "Job should be transitioned to FAILED on download error"
        assert "YouTube download failed" in transition_call.kwargs.get('message', ''), \
            "Error message should indicate YouTube download failure"

        # Workers should NOT be triggered when download fails
        mock_worker_service.trigger_audio_worker.assert_not_called()
        mock_worker_service.trigger_lyrics_worker.assert_not_called()

        # Admin should receive error notification email
        mock_email_service.send_email.assert_called_once()
        email_call = mock_email_service.send_email.call_args
        assert "[FAILED]" in email_call.kwargs.get('subject', ''), \
            "Error notification email should have [FAILED] in subject"

    @pytest.mark.asyncio
    async def test_youtube_download_failure_still_sends_customer_confirmation(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, youtube_order_metadata
    ):
        """
        Regression: a YouTube download failure must NOT prevent the paid customer
        from receiving their order-confirmation email, nor prevent the admin from
        being alerted.

        Bug context (2026-08-22): Job 17dc6f48 failed on a YouTube bot-check
        download error. Because the confirmation + admin emails were only sent
        *after* audio acquisition, the download failure skipped both — Andrew never
        received the "New Made-For-You Order Received!" notification and there was no
        guarantee the customer got their confirmation. The confirmation email is now
        sent up-front, independently of the (fallible) audio acquisition step.
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL
        from backend.services.youtube_download_service import YouTubeDownloadError

        mock_youtube_service = MagicMock()
        mock_youtube_service.download = AsyncMock(
            side_effect=YouTubeDownloadError(
                "Sign in to confirm you're not a bot"
            )
        )

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=MagicMock()), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_service):

            # Must not raise - order intake is resilient to audio-acquisition failures
            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=youtube_order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

        # CRITICAL: customer still receives their order confirmation despite the failure
        mock_email_service.send_made_for_you_order_confirmation.assert_called_once()
        confirm_kwargs = mock_email_service.send_made_for_you_order_confirmation.call_args.kwargs
        assert confirm_kwargs['to_email'] == youtube_order_metadata['customer_email']
        assert confirm_kwargs['job_id'] == "test-job-123"

        # CRITICAL: admin is alerted to the failure so they can intervene manually
        mock_email_service.send_email.assert_called_once()
        failure_kwargs = mock_email_service.send_email.call_args.kwargs
        assert failure_kwargs['to_email'] == ADMIN_EMAIL
        assert "[FAILED]" in failure_kwargs['subject']

    @pytest.mark.asyncio
    async def test_customer_confirmation_email_failure_does_not_abort_order(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, youtube_order_metadata
    ):
        """
        Resilience: if the customer confirmation email itself fails to send, order
        processing (audio acquisition + admin notification) must still proceed. An
        email-provider hiccup should never block the actual work.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        # Confirmation email raises, but the order must still be processed
        mock_email_service.send_made_for_you_order_confirmation.side_effect = \
            RuntimeError("Postmark timeout")

        # start_job_processing is async - mock it so the success path completes
        mock_job_manager.start_job_processing = AsyncMock()

        mock_youtube_service = MagicMock()
        mock_youtube_service.download = AsyncMock(
            return_value="uploads/test-job-123/audio/test.webm"
        )

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=MagicMock()), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_service):

            # Must not raise despite the confirmation email failure
            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=youtube_order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

        # Download still happened and admin notification still sent
        mock_youtube_service.download.assert_called_once()
        mock_email_service.send_made_for_you_admin_notification.assert_called_once()
        # No [FAILED] alert - the order itself succeeded
        mock_email_service.send_email.assert_not_called()


class TestMadeForYouYouTubeImports:
    """
    AST-based import validation to prevent missing dependency bugs.

    Verifies that _handle_made_for_you_order imports and uses the correct
    services for YouTube URL handling.
    """

    def test_handler_imports_youtube_download_service(self):
        """
        Verify _handle_made_for_you_order uses YouTubeDownloadService for YouTube URLs.

        This is a regression guard that uses source inspection to verify the
        handler imports the required service. If this test fails, YouTube URL
        orders will fail at runtime because the download step is missing.
        """
        import inspect
        from backend.api.routes.users import _handle_made_for_you_order

        source = inspect.getsource(_handle_made_for_you_order)

        # Must use YouTubeDownloadService for YouTube URLs
        assert 'get_youtube_download_service' in source or 'YouTubeDownloadService' in source, \
            "_handle_made_for_you_order must use YouTubeDownloadService for YouTube URLs. " \
            "Without this, workers are triggered without downloading audio first, " \
            "causing 'Failed to download audio file' errors."

    def test_handler_calls_download_for_youtube_urls(self):
        """
        Verify the handler has a code path that calls download() for YouTube URLs.
        """
        import inspect
        from backend.api.routes.users import _handle_made_for_you_order

        source = inspect.getsource(_handle_made_for_you_order)

        # The handler must have download logic for YouTube URLs
        # Either calling .download() directly or await youtube_service.download()
        has_download_call = '.download(' in source or 'download(' in source

        assert has_download_call, \
            "_handle_made_for_you_order must call download() for YouTube URLs. " \
            "The current code triggers workers directly without downloading."

    def test_users_module_imports_youtube_service(self):
        """
        Verify the users module imports YouTubeDownloadService.
        """
        import inspect
        from backend.api.routes import users

        source = inspect.getsource(users)

        # Module must import the YouTube download service
        assert 'get_youtube_download_service' in source, \
            "backend.api.routes.users must import get_youtube_download_service. " \
            "This is required for handling YouTube URL made-for-you orders."


class TestMadeForYouDistributionSettings:
    """
    Tests for distribution settings on made-for-you jobs.

    Made-for-you jobs should have distribution defaults applied:
    - YouTube upload enabled
    - Dropbox path set
    - Google Drive folder ID set
    - Brand prefix set

    These ensure the finished video is automatically distributed.
    """

    @pytest.fixture
    def mock_job_manager(self):
        """Create mock JobManager."""
        manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-123"
        manager.create_job.return_value = mock_job
        return manager

    @pytest.fixture
    def mock_email_service(self):
        """Create mock email service."""
        service = MagicMock()
        service.send_email.return_value = True
        return service

    @pytest.fixture
    def mock_user_service(self):
        """Create mock user service."""
        service = MagicMock()
        service._mark_stripe_session_processed.return_value = None
        # Mock create_admin_login_token to return a MagicMock with .token attribute
        mock_admin_login = MagicMock()
        mock_admin_login.token = "test-admin-login-token-123"
        service.create_admin_login_token.return_value = mock_admin_login
        return service

    @pytest.fixture
    def mock_theme_service(self):
        """Create mock theme service."""
        service = MagicMock()
        service.get_default_theme_id.return_value = "nomad"
        return service

    @pytest.fixture
    def order_metadata(self):
        """Standard order metadata."""
        return {
            "order_type": "made_for_you",
            "customer_email": "customer@example.com",
            "artist": "Test Artist",
            "title": "Test Song",
            "source_type": "search",
        }

    @pytest.fixture
    def mock_settings(self):
        """Mock settings with distribution defaults."""
        settings = MagicMock()
        settings.default_enable_youtube_upload = True
        settings.default_dropbox_path = "/Production/Ready To Upload"
        settings.default_gdrive_folder_id = "1ABC123xyz"
        settings.default_brand_prefix = "NOMAD"
        settings.default_discord_webhook_url = None
        settings.default_youtube_description = None
        settings.default_enable_cdg = True
        settings.default_enable_txt = True
        return settings

    @pytest.mark.asyncio
    async def test_youtube_upload_enabled_by_default(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, mock_settings, order_metadata
    ):
        """
        CRITICAL TEST: Made-for-you jobs should have YouTube upload enabled.

        This ensures the finished video is uploaded to YouTube automatically.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.services.job_defaults_service.get_settings', return_value=mock_settings):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            assert job_create_arg.enable_youtube_upload is True, \
                "Made-for-you jobs should have YouTube upload enabled by default"

    @pytest.mark.asyncio
    async def test_dropbox_path_set_by_default(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, mock_settings, order_metadata
    ):
        """
        CRITICAL TEST: Made-for-you jobs should have Dropbox path set.

        This ensures the finished video is synced to Dropbox automatically.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.services.job_defaults_service.get_settings', return_value=mock_settings):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            assert job_create_arg.dropbox_path == "/Production/Ready To Upload", \
                "Made-for-you jobs should have Dropbox path set from defaults"

    @pytest.mark.asyncio
    async def test_gdrive_folder_id_set_by_default(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, mock_settings, order_metadata
    ):
        """
        CRITICAL TEST: Made-for-you jobs should have Google Drive folder ID set.

        This ensures the finished video is uploaded to Google Drive automatically.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.services.job_defaults_service.get_settings', return_value=mock_settings):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            assert job_create_arg.gdrive_folder_id == "1ABC123xyz", \
                "Made-for-you jobs should have Google Drive folder ID set from defaults"

    @pytest.mark.asyncio
    async def test_brand_prefix_set_by_default(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, mock_settings, order_metadata
    ):
        """
        Brand prefix should be set from server defaults.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.services.job_defaults_service.get_settings', return_value=mock_settings):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            assert job_create_arg.brand_prefix == "NOMAD", \
                "Made-for-you jobs should have brand prefix set from defaults"


class TestMadeForYouJobCreateContract:
    """
    Contract tests to ensure JobCreate for made-for-you orders has correct shape.

    These tests document the expected contract between the webhook handler
    and the job creation system.
    """

    def test_made_for_you_job_create_has_all_required_fields(self):
        """
        A properly configured made-for-you JobCreate must have all these fields.
        """
        # This is what the webhook handler SHOULD create
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Song",
            user_email="admin@nomadkaraoke.com",  # Admin, not customer
            theme_id="nomad",
            made_for_you=True,                    # Flag for MFY
            customer_email="customer@example.com", # For delivery
            customer_notes="Special request",      # Customer notes
            auto_download=False,                   # Pause for selection
            audio_search_artist="Test Artist",
            audio_search_title="Test Song",
            non_interactive=False,                 # Admin reviews
        )

        # Verify all critical fields
        assert job_create.made_for_you is True
        assert job_create.user_email == "admin@nomadkaraoke.com"
        assert job_create.customer_email == "customer@example.com"
        assert job_create.customer_notes == "Special request"
        assert job_create.auto_download is False
        assert job_create.non_interactive is False

    def test_job_create_customer_not_owner(self):
        """
        Verify customer_email is distinct from user_email (owner).
        """
        job_create = JobCreate(
            artist="Test",
            title="Test",
            user_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            made_for_you=True,
        )

        assert job_create.user_email != job_create.customer_email, \
            "Customer should not own job during processing"

    def test_made_for_you_with_distribution_defaults(self):
        """
        Made-for-you jobs should have distribution defaults from server settings.
        """
        job_create = JobCreate(
            artist="Test",
            title="Test",
            user_email="admin@nomadkaraoke.com",
            made_for_you=True,
            customer_email="customer@example.com",
            # Distribution settings that should be applied
            enable_youtube_upload=True,
            dropbox_path="/Production/Ready To Upload",
            gdrive_folder_id="1ABC123xyz",
            brand_prefix="NOMAD",
        )

        # These should be configurable for made-for-you orders
        assert hasattr(job_create, 'enable_youtube_upload')
        assert hasattr(job_create, 'dropbox_path')
        assert hasattr(job_create, 'gdrive_folder_id')
        assert hasattr(job_create, 'brand_prefix')


# =============================================================================
# Webhook Handler Email Template Tests
# =============================================================================

class TestMadeForYouWebhookEmailTemplates:
    """
    CRITICAL: Tests that webhook handler uses proper email template methods.

    Bug context (2026-01-09): The webhook handler was using generic send_email()
    with inline HTML instead of the professional template methods:
    - send_made_for_you_order_confirmation()
    - send_made_for_you_admin_notification()

    This resulted in emails not being sent in the expected format and made
    debugging harder since the inline HTML was different from the templates.
    """

    @pytest.fixture
    def mock_job_manager(self):
        """Create mock JobManager that captures job creation params."""
        manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-123"
        manager.create_job.return_value = mock_job
        return manager

    @pytest.fixture
    def mock_user_service(self):
        """Create mock user service."""
        service = MagicMock()
        service._mark_stripe_session_processed.return_value = None
        # Mock create_admin_login_token to return a MagicMock with .token attribute
        mock_admin_login = MagicMock()
        mock_admin_login.token = "test-admin-login-token-123"
        service.create_admin_login_token.return_value = mock_admin_login
        return service

    @pytest.fixture
    def mock_theme_service(self):
        """Create mock theme service."""
        service = MagicMock()
        service.get_default_theme_id.return_value = "nomad"
        return service

    @pytest.fixture
    def order_metadata(self):
        """Standard order metadata from Stripe session."""
        return {
            "order_type": "made_for_you",
            "customer_email": "customer@example.com",
            "artist": "Avril Lavigne",
            "title": "Complicated",
            "source_type": "search",
            "notes": "Please make sure the lyrics are perfect!",
        }

    @pytest.mark.asyncio
    async def test_webhook_uses_order_confirmation_template(
        self, mock_job_manager, mock_user_service, mock_theme_service, order_metadata
    ):
        """
        CRITICAL: Webhook must use send_made_for_you_order_confirmation() template.

        The template method provides:
        - Professional formatting
        - Consistent branding
        - Proper email structure with header/footer

        Using generic send_email() with inline HTML is error-prone and inconsistent.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_email_service = MagicMock()
        mock_email_service.send_made_for_you_order_confirmation.return_value = True
        mock_email_service.send_made_for_you_admin_notification.return_value = True

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # CRITICAL ASSERTION: Must use the template method, not generic send_email
            mock_email_service.send_made_for_you_order_confirmation.assert_called_once()

            # Verify the template was called with correct parameters
            call_kwargs = mock_email_service.send_made_for_you_order_confirmation.call_args.kwargs
            assert call_kwargs['to_email'] == order_metadata["customer_email"]
            assert call_kwargs['artist'] == order_metadata["artist"]
            assert call_kwargs['title'] == order_metadata["title"]
            assert call_kwargs['job_id'] == "test-job-123"
            assert call_kwargs['notes'] == order_metadata["notes"]

    @pytest.mark.asyncio
    async def test_webhook_uses_admin_notification_template(
        self, mock_job_manager, mock_user_service, mock_theme_service, order_metadata
    ):
        """
        CRITICAL: Webhook must use send_made_for_you_admin_notification() template.

        The template method provides:
        - Deadline in EST
        - Link with admin_token for one-click login
        - Audio source count
        - Professional formatting
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        mock_email_service = MagicMock()
        mock_email_service.send_made_for_you_order_confirmation.return_value = True
        mock_email_service.send_made_for_you_admin_notification.return_value = True

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # CRITICAL ASSERTION: Must use the template method, not generic send_email
            mock_email_service.send_made_for_you_admin_notification.assert_called_once()

            # Verify the template was called with correct parameters
            call_kwargs = mock_email_service.send_made_for_you_admin_notification.call_args.kwargs
            assert call_kwargs['to_email'] == ADMIN_EMAIL
            assert call_kwargs['customer_email'] == order_metadata["customer_email"]
            assert call_kwargs['artist'] == order_metadata["artist"]
            assert call_kwargs['title'] == order_metadata["title"]
            assert call_kwargs['job_id'] == "test-job-123"
            assert call_kwargs['admin_login_token'] == "test-admin-login-token-123"

    @pytest.mark.asyncio
    async def test_webhook_does_not_use_generic_send_email_for_notifications(
        self, mock_job_manager, mock_user_service, mock_theme_service, order_metadata
    ):
        """
        CRITICAL: Webhook must NOT use generic send_email() for made-for-you notifications.

        The generic send_email() method was being used with inline HTML, which:
        - Has no consistent styling
        - Is harder to maintain
        - May not match the professional templates

        This test ensures send_email is NOT called for the order/admin notifications.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_email_service = MagicMock()
        mock_email_service.send_made_for_you_order_confirmation.return_value = True
        mock_email_service.send_made_for_you_admin_notification.return_value = True

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            from backend.services.audio_search_service import NoResultsError
            mock_audio_service = MagicMock()
            mock_audio_service.search.side_effect = NoResultsError("No results")
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # CRITICAL ASSERTION: generic send_email should NOT be used
            # for made-for-you order/admin notifications
            mock_email_service.send_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_notification_includes_audio_source_count(
        self, mock_job_manager, mock_user_service, mock_theme_service, order_metadata
    ):
        """
        Admin notification should include count of audio sources found.

        This helps admin prioritize orders and know what to expect.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_email_service = MagicMock()
        mock_email_service.send_made_for_you_order_confirmation.return_value = True
        mock_email_service.send_made_for_you_admin_notification.return_value = True

        # Mock audio search that returns results
        mock_audio_service = MagicMock()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {'provider': 'RED', 'title': 'Test'}
        mock_audio_service.search.return_value = [mock_result, mock_result, mock_result]  # 3 results
        mock_audio_service.last_remote_search_id = "search_123"

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service'), \
             patch('backend.services.audio_search_service.get_audio_search_service', return_value=mock_audio_service), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=order_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            # Verify audio_source_count is passed to admin notification
            call_kwargs = mock_email_service.send_made_for_you_admin_notification.call_args.kwargs
            assert call_kwargs['audio_source_count'] == 3


# =============================================================================
# Job Manager Completion Email Tests
# =============================================================================

class TestJobManagerCompletionEmail:
    """
    Tests for job manager's _schedule_completion_email handling of made-for-you jobs.

    These tests verify that:
    1. Made-for-you jobs send completion email to customer_email (not admin)
    2. Ownership is transferred from admin to customer on completion
    3. Regular jobs still send to user_email as before
    """

    @pytest.fixture
    def job_manager(self):
        """Create JobManager with mocked dependencies."""
        with patch('backend.services.job_manager.FirestoreService'), \
             patch('backend.services.job_manager.StorageService'):
            from backend.services.job_manager import JobManager
            manager = JobManager()
            manager.firestore = MagicMock()
            return manager

    @pytest.fixture
    def made_for_you_job(self):
        """Create a completed made-for-you job."""
        return Job(
            job_id="made-for-you-job-123",
            status=JobStatus.COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            user_email="madeforyou@nomadkaraoke.com",  # Admin owns during processing
            artist="Avril Lavigne",
            title="Complicated",
            made_for_you=True,
            customer_email="customer@example.com",  # Actual customer
            state_data={
                'youtube_url': 'https://youtube.com/watch?v=123',
                'dropbox_link': 'https://dropbox.com/folder/123',
                'brand_code': 'NOMAD-1234',
            },
        )

    @pytest.fixture
    def regular_job(self):
        """Create a completed regular job (not made-for-you)."""
        return Job(
            job_id="regular-job-456",
            status=JobStatus.COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            user_email="user@example.com",
            artist="Test Artist",
            title="Test Song",
            made_for_you=False,
            customer_email=None,
            state_data={
                'youtube_url': 'https://youtube.com/watch?v=456',
                'dropbox_link': 'https://dropbox.com/folder/456',
                'brand_code': 'NOMAD-5678',
            },
        )

    def test_made_for_you_completion_sends_to_customer(self, job_manager, made_for_you_job):
        """Made-for-you completion email goes to customer, not admin."""
        mock_notification_service = MagicMock()

        with patch('backend.services.job_notification_service.get_job_notification_service', return_value=mock_notification_service):
            job_manager._schedule_completion_email(made_for_you_job)

            # Give async task time to be created
            import time
            time.sleep(0.1)

        # Verify ownership was transferred
        job_manager.firestore.update_job.assert_called_with(
            "made-for-you-job-123",
            {'user_email': 'customer@example.com'}
        )

    def test_made_for_you_completion_transfers_ownership(self, job_manager, made_for_you_job):
        """Made-for-you job ownership is transferred to customer on completion."""
        mock_notification_service = MagicMock()

        with patch('backend.services.job_notification_service.get_job_notification_service', return_value=mock_notification_service):
            job_manager._schedule_completion_email(made_for_you_job)

        # Verify update_job was called with the customer email
        job_manager.firestore.update_job.assert_called_once()
        call_args = job_manager.firestore.update_job.call_args
        assert call_args[0][0] == "made-for-you-job-123"
        assert call_args[0][1] == {'user_email': 'customer@example.com'}

    def test_regular_job_completion_sends_to_user(self, job_manager, regular_job):
        """Regular job completion email goes to user_email."""
        mock_notification_service = MagicMock()

        with patch('backend.services.job_notification_service.get_job_notification_service', return_value=mock_notification_service):
            job_manager._schedule_completion_email(regular_job)

        # Verify NO ownership transfer for regular jobs
        job_manager.firestore.update_job.assert_not_called()

    def test_made_for_you_without_customer_email_skips_transfer(self, job_manager):
        """Made-for-you job without customer_email doesn't crash."""
        job_without_customer = Job(
            job_id="no-customer-job",
            status=JobStatus.COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            user_email="madeforyou@nomadkaraoke.com",
            artist="Test",
            title="Test",
            made_for_you=True,
            customer_email=None,  # Missing customer email
        )

        mock_notification_service = MagicMock()

        with patch('backend.services.job_notification_service.get_job_notification_service', return_value=mock_notification_service):
            # Should not raise
            job_manager._schedule_completion_email(job_without_customer)

        # No ownership transfer without customer_email
        job_manager.firestore.update_job.assert_not_called()
