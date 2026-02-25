"""
Tests for private (non-published) tracks feature.

Tests cover:
- Job model is_private field
- Distribution settings overrides for private jobs
- Email template YouTube section removal
- Admin toggle with auto-delete behavior
"""
import pytest
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
from types import SimpleNamespace

from backend.models.job import Job, JobCreate
from backend.services.job_defaults_service import (
    get_effective_distribution_for_job,
    EffectiveDistributionSettings,
)
from backend.services.template_service import TemplateService


class TestJobModelIsPrivate:
    """Tests for is_private field on Job and JobCreate models."""

    def test_job_defaults_to_not_private(self):
        """Job.is_private defaults to False."""
        now = datetime.utcnow().isoformat()
        job = Job(job_id="test-123", status="pending", created_at=now, updated_at=now)
        assert job.is_private is False

    def test_job_can_be_created_as_private(self):
        """Job can be explicitly set as private."""
        now = datetime.utcnow().isoformat()
        job = Job(job_id="test-123", status="pending", is_private=True, created_at=now, updated_at=now)
        assert job.is_private is True

    def test_job_create_defaults_to_not_private(self):
        """JobCreate.is_private defaults to False."""
        job_create = JobCreate(artist="Test", title="Song")
        assert job_create.is_private is False

    def test_job_create_can_be_private(self):
        """JobCreate can be explicitly set as private."""
        job_create = JobCreate(artist="Test", title="Song", is_private=True)
        assert job_create.is_private is True

    def test_create_job_persists_is_private(self):
        """JobManager.create_job should persist is_private from JobCreate to Job."""
        from backend.services.job_manager import JobManager

        manager = JobManager()
        manager.firestore = Mock()
        manager.firestore.create_job = Mock()

        job_create = JobCreate(
            artist="Test",
            title="Song",
            is_private=True,
            theme_id="nomad",
        )

        job = manager.create_job(job_create, is_admin=True)

        assert job.is_private is True
        manager.firestore.create_job.assert_called_once()
        saved_job = manager.firestore.create_job.call_args[0][0]
        assert saved_job.is_private is True

    def test_create_job_defaults_is_private_false(self):
        """JobManager.create_job should default is_private to False."""
        from backend.services.job_manager import JobManager

        manager = JobManager()
        manager.firestore = Mock()
        manager.firestore.create_job = Mock()

        job_create = JobCreate(
            artist="Test",
            title="Song",
            theme_id="nomad",
        )

        job = manager.create_job(job_create, is_admin=True)

        assert job.is_private is False


class TestGetEffectiveDistributionForJob:
    """Tests for get_effective_distribution_for_job() helper."""

    @patch('backend.services.job_defaults_service.get_settings')
    def test_private_job_uses_private_dropbox_path(self, mock_settings):
        """Private job should use the private Dropbox path."""
        mock_settings.return_value = SimpleNamespace(
            default_private_dropbox_path="/Tracks-NonPublished",
            default_private_brand_prefix="NOMADNP",
        )
        job = SimpleNamespace(
            is_private=True,
            dropbox_path="/Tracks-Organized",
            brand_prefix="NOMAD",
            enable_youtube_upload=True,
            gdrive_folder_id="some-folder-id",
            discord_webhook_url="https://discord.com/hook",
            youtube_description_template="template",
        )

        dist = get_effective_distribution_for_job(job)

        assert dist.dropbox_path == "/Tracks-NonPublished"
        assert dist.brand_prefix == "NOMADNP"

    @patch('backend.services.job_defaults_service.get_settings')
    def test_private_job_disables_youtube(self, mock_settings):
        """Private job should have YouTube upload disabled."""
        mock_settings.return_value = SimpleNamespace(
            default_private_dropbox_path="/Tracks-NonPublished",
            default_private_brand_prefix="NOMADNP",
        )
        job = SimpleNamespace(
            is_private=True,
            enable_youtube_upload=True,
            discord_webhook_url=None,
        )

        dist = get_effective_distribution_for_job(job)

        assert dist.enable_youtube_upload is False
        assert dist.youtube_description is None

    @patch('backend.services.job_defaults_service.get_settings')
    def test_private_job_disables_gdrive(self, mock_settings):
        """Private job should have Google Drive folder ID set to None."""
        mock_settings.return_value = SimpleNamespace(
            default_private_dropbox_path="/Tracks-NonPublished",
            default_private_brand_prefix="NOMADNP",
        )
        job = SimpleNamespace(
            is_private=True,
            gdrive_folder_id="some-folder-id",
            discord_webhook_url=None,
        )

        dist = get_effective_distribution_for_job(job)

        assert dist.gdrive_folder_id is None

    @patch('backend.services.job_defaults_service.get_settings')
    def test_private_job_preserves_discord(self, mock_settings):
        """Private job should still have Discord webhook preserved."""
        mock_settings.return_value = SimpleNamespace(
            default_private_dropbox_path="/Tracks-NonPublished",
            default_private_brand_prefix="NOMADNP",
        )
        job = SimpleNamespace(
            is_private=True,
            discord_webhook_url="https://discord.com/webhook/123",
        )

        dist = get_effective_distribution_for_job(job)

        assert dist.discord_webhook_url == "https://discord.com/webhook/123"

    def test_non_private_job_uses_job_settings(self):
        """Non-private job should use its own distribution settings."""
        job = SimpleNamespace(
            is_private=False,
            dropbox_path="/Tracks-Organized",
            brand_prefix="NOMAD",
            enable_youtube_upload=True,
            gdrive_folder_id="folder-id",
            discord_webhook_url="https://discord.com/hook",
            youtube_description_template="description",
        )

        dist = get_effective_distribution_for_job(job)

        assert dist.dropbox_path == "/Tracks-Organized"
        assert dist.brand_prefix == "NOMAD"
        assert dist.enable_youtube_upload is True
        assert dist.gdrive_folder_id == "folder-id"
        assert dist.discord_webhook_url == "https://discord.com/hook"
        assert dist.youtube_description == "description"

    def test_job_without_is_private_treated_as_non_private(self):
        """Job object without is_private attribute should be treated as non-private."""
        job = SimpleNamespace(
            dropbox_path="/Tracks-Organized",
            brand_prefix="NOMAD",
            enable_youtube_upload=False,
            gdrive_folder_id=None,
            discord_webhook_url=None,
            youtube_description_template=None,
        )

        dist = get_effective_distribution_for_job(job)

        assert dist.dropbox_path == "/Tracks-Organized"
        assert dist.brand_prefix == "NOMAD"
        assert dist.enable_youtube_upload is False


class TestTemplateServicePrivate:
    """Tests for email template rendering with is_private flag."""

    def test_private_job_removes_youtube_section(self):
        """Private job completion email should not contain YouTube section."""
        service = TemplateService()
        service._storage_client = Mock()
        service._bucket = Mock()
        # Force use of default template
        service._bucket.blob.return_value.exists.return_value = False

        result = service.render_job_completion(
            name="Test User",
            youtube_url=None,
            dropbox_url="https://dropbox.com/link",
            artist="Artist",
            title="Song",
            job_id="job-123",
            is_private=True,
        )

        assert "YouTube" not in result
        assert "youtube_url" not in result
        assert "https://dropbox.com/link" in result

    def test_non_private_job_includes_youtube_section(self):
        """Non-private job completion email should include YouTube section."""
        service = TemplateService()
        service._storage_client = Mock()
        service._bucket = Mock()
        service._bucket.blob.return_value.exists.return_value = False

        result = service.render_job_completion(
            name="Test User",
            youtube_url="https://youtube.com/watch?v=123",
            dropbox_url="https://dropbox.com/link",
            artist="Artist",
            title="Song",
            job_id="job-123",
            is_private=False,
        )

        assert "https://youtube.com/watch?v=123" in result
        assert "YouTube" in result

    def test_private_job_still_has_dropbox_link(self):
        """Private job email should still contain the Dropbox link."""
        service = TemplateService()
        service._storage_client = Mock()
        service._bucket = Mock()
        service._bucket.blob.return_value.exists.return_value = False

        result = service.render_job_completion(
            name="Test User",
            dropbox_url="https://dropbox.com/sh/abc123",
            artist="Artist",
            title="Song",
            job_id="job-123",
            is_private=True,
        )

        assert "https://dropbox.com/sh/abc123" in result
        assert "dropbox" in result.lower()

    def test_private_job_still_has_greeting(self):
        """Private job email should still contain the greeting."""
        service = TemplateService()
        service._storage_client = Mock()
        service._bucket = Mock()
        service._bucket.blob.return_value.exists.return_value = False

        result = service.render_job_completion(
            name="Jane",
            dropbox_url="https://dropbox.com/link",
            is_private=True,
        )

        assert "Hi Jane" in result


class TestNotificationServicePrivate:
    """Tests for notification service passing is_private to template."""

    @pytest.mark.asyncio
    async def test_send_completion_email_passes_is_private(self):
        """send_job_completion_email should pass is_private to template renderer."""
        from backend.services.job_notification_service import JobNotificationService

        service = JobNotificationService()
        service.email_service = Mock()
        service.email_service.send_job_completion.return_value = True
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Test message"

        with patch('backend.services.job_notification_service.ENABLE_AUTO_EMAILS', True):
            await service.send_job_completion_email(
                job_id="job-123",
                user_email="user@example.com",
                is_private=True,
            )

        # Verify is_private=True was passed to template renderer
        call_kwargs = service.template_service.render_job_completion.call_args
        assert call_kwargs[1].get('is_private') is True or \
               (call_kwargs[0] and True in call_kwargs[0])  # positional or keyword

    @pytest.mark.asyncio
    async def test_send_completion_email_defaults_is_private_false(self):
        """send_job_completion_email should default is_private to False."""
        from backend.services.job_notification_service import JobNotificationService

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

        call_kwargs = service.template_service.render_job_completion.call_args
        assert call_kwargs[1].get('is_private') is False

    def test_get_completion_message_passes_is_private(self):
        """get_completion_message should pass is_private to template renderer."""
        from backend.services.job_notification_service import JobNotificationService

        service = JobNotificationService()
        service.template_service = Mock()
        service.template_service.render_job_completion.return_value = "Test message"

        service.get_completion_message(job_id="job-123", is_private=True)

        call_kwargs = service.template_service.render_job_completion.call_args
        assert call_kwargs[1].get('is_private') is True


class TestRequestModelIsPrivate:
    """Tests for is_private on API request models."""

    def test_url_submission_request_has_is_private(self):
        """URLSubmissionRequest should accept is_private field."""
        from backend.models.requests import URLSubmissionRequest
        req = URLSubmissionRequest(url="https://youtube.com/watch?v=123", is_private=True)
        assert req.is_private is True

    def test_url_submission_request_is_private_defaults_none(self):
        """URLSubmissionRequest.is_private should default to None."""
        from backend.models.requests import URLSubmissionRequest
        req = URLSubmissionRequest(url="https://youtube.com/watch?v=123")
        assert req.is_private is None


class TestAdminPrivateToggle:
    """Tests for admin API handling of is_private toggle."""

    def test_is_private_in_editable_fields(self):
        """is_private should be in the EDITABLE_JOB_FIELDS set."""
        from backend.api.routes.admin import EDITABLE_JOB_FIELDS
        assert "is_private" in EDITABLE_JOB_FIELDS

    def test_job_update_request_has_is_private(self):
        """JobUpdateRequest model should accept is_private field."""
        from backend.api.routes.admin import JobUpdateRequest
        req = JobUpdateRequest(is_private=True)
        assert req.is_private is True

    def test_job_update_request_is_private_optional(self):
        """JobUpdateRequest.is_private should be optional."""
        from backend.api.routes.admin import JobUpdateRequest
        req = JobUpdateRequest()
        assert req.is_private is None


class TestOrchestratorConfigPrivate:
    """Tests for orchestrator config with private jobs."""

    @patch('backend.services.job_defaults_service.get_settings')
    def test_private_job_config_disables_youtube_and_gdrive(self, mock_settings):
        """Private job should produce config with no YouTube and no GDrive."""
        mock_settings.return_value = SimpleNamespace(
            default_private_dropbox_path="/Tracks-NonPublished",
            default_private_brand_prefix="NOMADNP",
        )
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job

        job = MagicMock()
        job.job_id = "test-private"
        job.artist = "Artist"
        job.title = "Title"
        job.state_data = {"instrumental_selection": "clean"}
        job.enable_cdg = False
        job.enable_txt = False
        job.enable_youtube_upload = True  # Would normally enable YouTube
        job.is_private = True  # But private overrides it
        job.brand_prefix = "NOMAD"
        job.discord_webhook_url = None
        job.youtube_description_template = "desc"
        job.dropbox_path = "/Tracks-Organized"
        job.gdrive_folder_id = "folder-123"
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = None

        config = create_orchestrator_config_from_job(
            job=job,
            temp_dir="/tmp/test",
            youtube_credentials={"token": "test"},
        )

        assert config.enable_youtube_upload is False
        assert config.gdrive_folder_id is None
        assert config.brand_prefix == "NOMADNP"
        assert config.youtube_description_template is None
