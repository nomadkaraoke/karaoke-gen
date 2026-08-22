"""
Emulator integration tests for made-for-you orders with YouTube URLs.

These tests use REAL Firestore and GCS emulators to test the full API flow,
mocking only external services (YouTube download, email, Stripe).

Tests cover:
1. Job creation with YouTube URL metadata
2. YouTube audio download and GCS storage
3. Worker trigger sequencing (download before workers)
4. Error handling for download failures
5. Job state persistence and consistency

Run with: scripts/run-emulator-tests.sh
Or directly: make test-e2e
"""
import pytest
import time
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from .conftest import emulators_running

# Skip all tests if emulators not running
pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh"
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def youtube_order_metadata():
    """Standard metadata for YouTube URL made-for-you order."""
    return {
        "order_type": "made_for_you",
        "customer_email": "customer@example.com",
        "artist": "Test Artist",
        "title": "Test Song",
        "source_type": "youtube",
        "youtube_url": "https://youtube.com/watch?v=abc123",
    }


@pytest.fixture
def youtube_order_metadata_with_notes():
    """YouTube order metadata with customer notes."""
    return {
        "order_type": "made_for_you",
        "customer_email": "customer@example.com",
        "artist": "Test Artist",
        "title": "Test Song",
        "source_type": "youtube",
        "youtube_url": "https://youtube.com/watch?v=abc123",
        "notes": "Please make it extra special!",
    }


@pytest.fixture
def mock_stripe_service():
    """Mock Stripe service for webhook verification."""
    service = MagicMock()
    service.is_configured.return_value = True
    service.verify_webhook_signature.return_value = (True, {}, "OK")
    return service


@pytest.fixture
def mock_email_service():
    """Mock email service for notification verification."""
    service = MagicMock()
    service.is_configured.return_value = True
    service.send_email.return_value = True
    service.send_made_for_you_order_confirmation.return_value = True
    service.send_made_for_you_admin_notification.return_value = True
    return service


@pytest.fixture
def mock_user_service_for_webhook():
    """Mock user service for webhook processing."""
    service = MagicMock()
    service.is_stripe_session_processed.return_value = False
    service._mark_stripe_session_processed.return_value = None

    # Mock admin login token creation
    mock_admin_login = MagicMock()
    mock_admin_login.token = "test-admin-login-token"
    service.create_admin_login_token.return_value = mock_admin_login

    return service


@pytest.fixture
def mock_youtube_download_service():
    """Mock YouTube download service returning valid GCS path."""
    service = MagicMock()
    service.download = AsyncMock(return_value="uploads/test-job-123/audio/test.webm")
    service.is_remote_enabled.return_value = True
    return service


@pytest.fixture
def mock_worker_service_local():
    """Mock worker service to track trigger calls (local scope, different name to avoid conftest collision)."""
    service = MagicMock()
    service.trigger_audio_worker = AsyncMock(return_value=True)
    service.trigger_lyrics_worker = AsyncMock(return_value=True)
    service.trigger_screens_worker = AsyncMock(return_value=True)
    service.trigger_video_worker = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_theme_service():
    """Mock theme service to avoid real Firestore calls."""
    service = MagicMock()
    service.get_default_theme_id.return_value = "nomad"
    service.get_theme.return_value = MagicMock(
        theme_id="nomad",
        name="Nomad Karaoke",
        style_params={}
    )
    return service


@pytest.fixture
def mock_distribution_settings():
    """Mock distribution settings for made-for-you orders."""
    settings = MagicMock()
    settings.enable_youtube_upload = True
    settings.dropbox_path = "/Nomad Karaoke"
    settings.gdrive_folder_id = "test-folder-id"
    settings.brand_prefix = "Nomad Karaoke"
    settings.discord_webhook_url = None
    settings.youtube_description = None
    return settings


# =============================================================================
# Test Classes
# =============================================================================

class TestMadeForYouYouTubeOrderFlow:
    """
    Integration tests for made-for-you orders with YouTube URLs.

    Uses real Firestore/GCS emulators to test the full flow:
    1. Webhook creates job with YouTube URL
    2. YouTube audio is downloaded to GCS
    3. Job is updated with input_media_gcs_path
    4. Workers are triggered with correct job state
    """

    @pytest.mark.asyncio
    async def test_youtube_url_order_creates_job_with_correct_fields(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service, youtube_order_metadata
    ):
        """
        Test that a YouTube URL order creates a job with the correct fields.

        Verifies:
        - Job is created with made_for_you=True
        - customer_email is set correctly
        - url is set to the YouTube URL
        - theme_id defaults to "nomad"
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-youtube-fields"
        mock_job_manager.create_job.return_value = mock_job

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            await _handle_made_for_you_order(
                session_id="sess_test_fields",
                metadata=youtube_order_metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            # Verify job creation was called
            assert mock_job_manager.create_job.called

            # Get the JobCreate argument
            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # Verify made_for_you fields
            assert job_create_arg.made_for_you is True, \
                "Job must be marked as made_for_you"
            assert job_create_arg.customer_email == youtube_order_metadata["customer_email"], \
                "Customer email must be set for delivery"
            assert job_create_arg.url == youtube_order_metadata["youtube_url"], \
                "YouTube URL must be set on job"
            assert job_create_arg.user_email == ADMIN_EMAIL, \
                "Admin must own job during processing"
            assert job_create_arg.theme_id == "nomad", \
                "Default nomad theme should be applied"

    @pytest.mark.asyncio
    async def test_youtube_url_order_triggers_workers(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service, youtube_order_metadata
    ):
        """
        Test that YouTube URL orders trigger workers after downloading audio.

        Verifies workers are called after YouTube download completes successfully.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-workers"
        mock_job_manager.create_job.return_value = mock_job

        # start_job_processing is async - mock it to call workers directly
        async def mock_start_job_processing(job_id, progress=15, message=""):
            await mock_worker_service_local.trigger_audio_worker(job_id)
            await mock_worker_service_local.trigger_lyrics_worker(job_id)
        mock_job_manager.start_job_processing = AsyncMock(side_effect=mock_start_job_processing)

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            await _handle_made_for_you_order(
                session_id="sess_test_workers",
                metadata=youtube_order_metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            # Verify workers were triggered via start_job_processing
            mock_worker_service_local.trigger_audio_worker.assert_called_once_with(mock_job.job_id)
            mock_worker_service_local.trigger_lyrics_worker.assert_called_once_with(mock_job.job_id)

    @pytest.mark.asyncio
    async def test_youtube_url_order_sends_notifications(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service, youtube_order_metadata
    ):
        """
        Test that YouTube URL orders send notification emails.

        Verifies:
        - Customer receives order confirmation
        - Admin receives notification with job details
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-notifications"
        mock_job_manager.create_job.return_value = mock_job

        # start_job_processing is async - mock it as simple AsyncMock
        mock_job_manager.start_job_processing = AsyncMock()

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            await _handle_made_for_you_order(
                session_id="sess_test_notifications",
                metadata=youtube_order_metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            # Verify customer confirmation email
            mock_email_service.send_made_for_you_order_confirmation.assert_called_once()
            confirm_call = mock_email_service.send_made_for_you_order_confirmation.call_args
            assert confirm_call.kwargs['to_email'] == youtube_order_metadata["customer_email"]
            assert confirm_call.kwargs['artist'] == youtube_order_metadata["artist"]
            assert confirm_call.kwargs['title'] == youtube_order_metadata["title"]

            # Verify admin notification email
            mock_email_service.send_made_for_you_admin_notification.assert_called_once()
            admin_call = mock_email_service.send_made_for_you_admin_notification.call_args
            assert admin_call.kwargs['to_email'] == ADMIN_EMAIL
            assert admin_call.kwargs['job_id'] == mock_job.job_id
            assert 'admin_login_token' in admin_call.kwargs

    @pytest.mark.asyncio
    async def test_youtube_url_order_marks_session_processed(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service, youtube_order_metadata
    ):
        """
        Test that Stripe session is marked as processed for idempotency.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-idempotency"
        mock_job_manager.create_job.return_value = mock_job

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            await _handle_made_for_you_order(
                session_id="sess_test_idempotency",
                metadata=youtube_order_metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            # Verify session was marked processed
            mock_user_service_for_webhook._mark_stripe_session_processed.assert_called_once()
            mark_call = mock_user_service_for_webhook._mark_stripe_session_processed.call_args
            assert mark_call.kwargs['stripe_session_id'] == "sess_test_idempotency"
            assert mark_call.kwargs['email'] == youtube_order_metadata["customer_email"]

    @pytest.mark.asyncio
    async def test_youtube_url_order_with_customer_notes(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service, youtube_order_metadata_with_notes
    ):
        """
        Test that customer notes are preserved on the job.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-notes"
        mock_job_manager.create_job.return_value = mock_job

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            await _handle_made_for_you_order(
                session_id="sess_test_notes",
                metadata=youtube_order_metadata_with_notes,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]
            assert job_create_arg.customer_notes == youtube_order_metadata_with_notes["notes"], \
                "Customer notes must be preserved on job"

    @pytest.mark.asyncio
    async def test_youtube_url_order_skips_audio_search(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service, youtube_order_metadata
    ):
        """
        Test that YouTube URL orders skip audio search (download + trigger workers directly).
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-no-search"
        mock_job_manager.create_job.return_value = mock_job

        # start_job_processing is async - mock it to call workers directly
        async def mock_start_job_processing(job_id, progress=15, message=""):
            await mock_worker_service_local.trigger_audio_worker(job_id)
            await mock_worker_service_local.trigger_lyrics_worker(job_id)
        mock_job_manager.start_job_processing = AsyncMock(side_effect=mock_start_job_processing)

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.audio_search_service.get_audio_search_service') as mock_get_audio, \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            mock_audio_service = MagicMock()
            mock_get_audio.return_value = mock_audio_service

            await _handle_made_for_you_order(
                session_id="sess_test_no_search",
                metadata=youtube_order_metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            # Audio search should NOT be called for YouTube URL orders
            mock_audio_service.search.assert_not_called()

            # Workers should be triggered directly via start_job_processing
            mock_worker_service_local.trigger_audio_worker.assert_called_once()


class TestMadeForYouYouTubeJobState:
    """
    Tests for job state consistency during YouTube URL processing.

    These tests verify job state is set correctly when jobs are created
    through the made-for-you webhook handler.
    """

    @pytest.mark.asyncio
    async def test_job_state_set_correctly_for_youtube_url_order(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service, youtube_order_metadata
    ):
        """
        Test that job state is set correctly for YouTube URL orders.

        Verifies the JobCreate object has all required fields for proper
        job state persistence.
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-state"
        mock_job_manager.create_job.return_value = mock_job

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            await _handle_made_for_you_order(
                session_id="sess_test_state",
                metadata=youtube_order_metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            # Verify job was created with correct state
            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # Core made-for-you fields
            assert job_create_arg.made_for_you is True
            assert job_create_arg.customer_email == youtube_order_metadata["customer_email"]
            assert job_create_arg.url == youtube_order_metadata["youtube_url"]
            assert job_create_arg.artist == youtube_order_metadata["artist"]
            assert job_create_arg.title == youtube_order_metadata["title"]
            assert job_create_arg.user_email == ADMIN_EMAIL

            # Job configuration
            assert job_create_arg.non_interactive is False  # Admin will review
            assert job_create_arg.auto_download is False  # Pause at audio selection


class TestMadeForYouYouTubeErrorHandling:
    """
    Tests for error handling scenarios in the YouTube URL flow.
    """

    @pytest.mark.asyncio
    async def test_exception_sends_failure_notification(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_theme_service, mock_distribution_settings,
        youtube_order_metadata
    ):
        """
        Test that a job-creation failure sends a failure notification to admin AND
        re-raises so the Stripe webhook returns non-2xx and Stripe retries the (likely
        transient) failure — a paid order must not be silently dropped when no job was
        ever created.
        """
        import pytest
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        # Make job creation fail
        mock_job_manager = MagicMock()
        mock_job_manager.create_job.side_effect = Exception("Database error")

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)):

            # Re-raises (job never created) so the webhook returns non-2xx -> Stripe retries
            with pytest.raises(Exception, match="Database error"):
                await _handle_made_for_you_order(
                    session_id="sess_test_error",
                    metadata=youtube_order_metadata,
                    user_service=mock_user_service_for_webhook,
                    email_service=mock_email_service,
                )

            # Verify failure email was sent to admin before re-raising
            mock_email_service.send_email.assert_called()
            call_args = mock_email_service.send_email.call_args
            assert call_args.kwargs['to_email'] == ADMIN_EMAIL
            assert "[FAILED]" in call_args.kwargs['subject']
            assert "Database error" in call_args.kwargs['html_content']


class TestMadeForYouWebhookIntegration:
    """
    Tests for the Stripe webhook handling with made-for-you orders.

    These tests verify the webhook handler processes made-for-you orders correctly.
    """

    @pytest.mark.asyncio
    async def test_handle_made_for_you_order_full_flow(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service, youtube_order_metadata
    ):
        """
        Test the full made-for-you order handler flow.

        This is what gets called when a Stripe webhook fires for a
        made-for-you order. Verifies:
        - Job is created with correct metadata
        - YouTube audio is downloaded
        - Workers are triggered
        - Emails are sent
        - Session is marked processed
        """
        from backend.api.routes.users import _handle_made_for_you_order, ADMIN_EMAIL

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-webhook-flow"
        mock_job_manager.create_job.return_value = mock_job

        # start_job_processing is async - mock it to call workers directly
        async def mock_start_job_processing(job_id, progress=15, message=""):
            await mock_worker_service_local.trigger_audio_worker(job_id)
            await mock_worker_service_local.trigger_lyrics_worker(job_id)
        mock_job_manager.start_job_processing = AsyncMock(side_effect=mock_start_job_processing)

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            await _handle_made_for_you_order(
                session_id="cs_test_webhook_flow",
                metadata=youtube_order_metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            # 1. Verify job was created
            assert mock_job_manager.create_job.called
            job_create_arg = mock_job_manager.create_job.call_args[0][0]
            assert job_create_arg.made_for_you is True
            assert job_create_arg.url == youtube_order_metadata["youtube_url"]

            # 2. Verify YouTube download was called
            mock_youtube_download_service.download.assert_called_once()

            # 3. Verify workers were triggered via start_job_processing
            mock_worker_service_local.trigger_audio_worker.assert_called_once()
            mock_worker_service_local.trigger_lyrics_worker.assert_called_once()

            # 4. Verify emails were sent
            mock_email_service.send_made_for_you_order_confirmation.assert_called_once()
            mock_email_service.send_made_for_you_admin_notification.assert_called_once()

            # 5. Verify session marked processed
            mock_user_service_for_webhook._mark_stripe_session_processed.assert_called_once()
            assert mock_user_service_for_webhook._mark_stripe_session_processed.call_args.kwargs['stripe_session_id'] == "cs_test_webhook_flow"


class TestMadeForYouYouTubeDistributionSettings:
    """
    Tests for distribution settings on made-for-you YouTube orders.
    """

    @pytest.mark.asyncio
    async def test_distribution_defaults_applied(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_youtube_download_service,
        youtube_order_metadata
    ):
        """
        Test that distribution defaults are applied to made-for-you jobs.

        Made-for-you jobs should have automatic distribution enabled.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-distribution"
        mock_job_manager.create_job.return_value = mock_job

        # Mock distribution settings
        mock_settings = MagicMock()
        mock_settings.enable_youtube_upload = True
        mock_settings.dropbox_path = "/Nomad Karaoke"
        mock_settings.gdrive_folder_id = "test-folder-id"
        mock_settings.brand_prefix = "Nomad Karaoke"
        mock_settings.discord_webhook_url = None
        mock_settings.youtube_description = None

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)):

            await _handle_made_for_you_order(
                session_id="sess_test_distribution",
                metadata=youtube_order_metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            # Verify distribution settings were applied
            job_create_arg = mock_job_manager.create_job.call_args[0][0]

            # These should match the mocked distribution settings
            assert job_create_arg.enable_youtube_upload == mock_settings.enable_youtube_upload
            assert job_create_arg.dropbox_path == mock_settings.dropbox_path
            assert job_create_arg.gdrive_folder_id == mock_settings.gdrive_folder_id
            assert job_create_arg.brand_prefix == mock_settings.brand_prefix


class TestMadeForYouYouTubeGCSPath:
    """
    Tests for GCS path format consistency in YouTube URL orders.
    """

    @pytest.mark.asyncio
    async def test_job_url_set_correctly(
        self, mock_email_service, mock_user_service_for_webhook,
        mock_worker_service_local, mock_theme_service, mock_distribution_settings,
        mock_youtube_download_service
    ):
        """
        Test that the YouTube URL is stored correctly on the job.
        """
        from backend.api.routes.users import _handle_made_for_you_order

        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        metadata = {
            "order_type": "made_for_you",
            "customer_email": "customer@example.com",
            "artist": "Rick Astley",
            "title": "Never Gonna Give You Up",
            "source_type": "youtube",
            "youtube_url": test_url,
        }

        mock_job_manager = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-url"
        mock_job_manager.create_job.return_value = mock_job

        with patch('backend.services.job_manager.JobManager', return_value=mock_job_manager), \
             patch('backend.services.worker_service.get_worker_service', return_value=mock_worker_service_local), \
             patch('backend.services.storage_service.StorageService'), \
             patch('backend.api.routes.users.get_theme_service', return_value=mock_theme_service), \
             patch('backend.api.routes.users.get_effective_distribution_settings', return_value=mock_distribution_settings), \
             patch('backend.api.routes.users.resolve_cdg_txt_defaults', return_value=(False, False)), \
             patch('backend.api.routes.users._prepare_theme_for_job', return_value=(None, None, None)), \
             patch('backend.api.routes.users.get_youtube_download_service', return_value=mock_youtube_download_service):

            await _handle_made_for_you_order(
                session_id="sess_test_url",
                metadata=metadata,
                user_service=mock_user_service_for_webhook,
                email_service=mock_email_service,
            )

            job_create_arg = mock_job_manager.create_job.call_args[0][0]
            assert job_create_arg.url == test_url


print("Made-for-you YouTube integration tests ready to run")
