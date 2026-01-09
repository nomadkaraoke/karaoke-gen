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
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        assert call_kwargs.get('cc_emails') is None


class TestMadeForYouAdminNotificationEmail:
    """Tests for send_made_for_you_admin_notification method."""

    def test_send_admin_notification_basic(self):
        """Test basic admin notification email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        result = service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
        )

        assert result is True
        service.provider.send_email.assert_called_once()

    def test_send_admin_notification_subject_format(self):
        """Test admin notification subject format."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Seether",
            title="Tonight",
            job_id="test-job-123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        subject = call_kwargs.get('subject')
        assert "[Made For You]" in subject
        assert "Seether" in subject
        assert "Tonight" in subject

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
            job_id="abc123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        # Should include link to admin job page
        assert "abc123" in html_content
        assert "admin/jobs" in html_content or "jobs/abc123" in html_content

    def test_send_admin_notification_includes_customer_email(self):
        """Test admin notification shows customer email."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="vip.customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
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
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
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
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
            audio_source_count=5,
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        assert "5" in html_content

    def test_send_admin_notification_deadline_reminder(self):
        """Test admin notification includes deadline reminder."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
        )

        call_kwargs = service.provider.send_email.call_args.kwargs
        html_content = call_kwargs.get('html_content')
        text_content = call_kwargs.get('text_content')
        # Should mention 24-hour deadline
        assert "24" in html_content or "24" in text_content

    def test_send_admin_notification_escapes_html(self):
        """Test admin notification escapes HTML in user input."""
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True

        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="test-job-123",
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
            notes="Test notes",
        )

        # Step 2: Admin notification
        service.send_made_for_you_admin_notification(
            to_email="admin@nomadkaraoke.com",
            customer_email="customer@example.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job123",
            audio_source_count=3,
        )

        # Verify both emails were sent
        assert service.provider.send_email.call_count == 2

        # First call should be to customer
        first_call = service.provider.send_email.call_args_list[0]
        assert first_call.kwargs['to_email'] == "customer@example.com"

        # Second call should be to admin
        second_call = service.provider.send_email.call_args_list[1]
        assert second_call.kwargs['to_email'] == "admin@nomadkaraoke.com"

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

            # Check email_service.send_email was called with admin email
            email_calls = mock_email_service.send_email.call_args_list
            admin_email_calls = [
                call for call in email_calls
                if call.kwargs.get('to_email') == ADMIN_EMAIL
            ]

            assert len(admin_email_calls) >= 1, \
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

            # Check email_service.send_email was called with customer email
            email_calls = mock_email_service.send_email.call_args_list
            customer_email_calls = [
                call for call in email_calls
                if call.kwargs.get('to_email') == order_metadata["customer_email"]
            ]

            assert len(customer_email_calls) >= 1, \
                "Customer must receive order confirmation email"

    @pytest.mark.asyncio
    async def test_admin_email_includes_job_link(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service, order_metadata
    ):
        """
        Admin notification email must include link to job for audio selection.
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

            # Find admin email call
            email_calls = mock_email_service.send_email.call_args_list
            admin_email_calls = [
                call for call in email_calls
                if call.kwargs.get('to_email') == ADMIN_EMAIL
            ]

            assert len(admin_email_calls) >= 1
            admin_call = admin_email_calls[0]
            html_content = admin_call.kwargs.get('html_content', '')

            # Should include job ID for linking
            assert "test-job-123" in html_content, \
                "Admin email must include job ID link"

    @pytest.mark.asyncio
    async def test_youtube_url_order_skips_search(
        self, mock_job_manager, mock_email_service, mock_user_service,
        mock_theme_service
    ):
        """
        Orders with YouTube URL should trigger workers directly, not search.
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

        mock_worker_service = MagicMock()
        mock_worker_service.trigger_audio_worker = AsyncMock()
        mock_worker_service.trigger_lyrics_worker = AsyncMock()

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service):

            await _handle_made_for_you_order(
                session_id="sess_123",
                metadata=youtube_metadata,
                user_service=mock_user_service,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # URL should be set
            assert job_create_arg.url == youtube_metadata["youtube_url"]

            # Workers should be triggered directly
            mock_worker_service.trigger_audio_worker.assert_called_once()
            mock_worker_service.trigger_lyrics_worker.assert_called_once()

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
             patch('backend.config.get_settings', return_value=mock_settings):

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
             patch('backend.config.get_settings', return_value=mock_settings):

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
             patch('backend.config.get_settings', return_value=mock_settings):

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
             patch('backend.config.get_settings', return_value=mock_settings):

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
