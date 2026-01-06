"""
Unit tests for template service.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from backend.services.template_service import (
    TemplateService,
    get_template_service,
    DEFAULT_JOB_COMPLETION_TEMPLATE,
    DEFAULT_ACTION_NEEDED_LYRICS_TEMPLATE,
    DEFAULT_ACTION_NEEDED_INSTRUMENTAL_TEMPLATE,
)


class TestTemplateRendering:
    """Tests for template rendering functionality."""

    def test_render_template_basic(self):
        """Test basic template variable replacement."""
        service = TemplateService()
        template = "Hello {name}, your job {job_id} is ready!"
        variables = {"name": "Alice", "job_id": "123"}

        result = service.render_template(template, variables)

        assert result == "Hello Alice, your job 123 is ready!"

    def test_render_template_missing_variables(self):
        """Test that missing variables are replaced with empty strings."""
        service = TemplateService()
        template = "Hello {name}, your {thing} is {status}!"
        variables = {"name": "Bob"}

        result = service.render_template(template, variables)

        assert result == "Hello Bob, your  is !"

    def test_render_template_none_values(self):
        """Test that None values are handled correctly."""
        service = TemplateService()
        template = "Hello {name}!"
        variables = {"name": None}

        result = service.render_template(template, variables)

        assert result == "Hello !"

    def test_render_template_removes_feedback_section_when_no_url(self):
        """Test that feedback section is removed when feedback_url is not provided."""
        service = TemplateService()
        template = """Thanks for your order!

If you have a moment, I'd really appreciate your feedback (takes 2 minutes):
{feedback_url}

Have a great day!"""
        variables = {"feedback_url": None}

        result = service.render_template(template, variables)

        assert "feedback" not in result.lower()
        assert "Thanks for your order!" in result
        assert "Have a great day!" in result

    def test_render_template_keeps_feedback_section_when_url_provided(self):
        """Test that feedback section is kept when feedback_url is provided."""
        service = TemplateService()
        template = """Thanks!

If you have a moment, I'd really appreciate your feedback (takes 2 minutes):
{feedback_url}

Bye!"""
        variables = {"feedback_url": "https://example.com/feedback"}

        result = service.render_template(template, variables)

        assert "https://example.com/feedback" in result
        assert "feedback" in result.lower()


class TestJobCompletionTemplate:
    """Tests for job completion template rendering."""

    def test_render_job_completion_all_fields(self):
        """Test rendering job completion with all fields."""
        service = TemplateService()

        # Mock _fetch_template_from_gcs to return None (use default)
        with patch.object(service, "_fetch_template_from_gcs", return_value=None):
            result = service.render_job_completion(
                name="Alice",
                youtube_url="https://youtube.com/watch?v=123",
                dropbox_url="https://dropbox.com/folder/abc",
                artist="Test Artist",
                title="Test Song",
                job_id="job-123",
                feedback_url="https://example.com/feedback",
            )

        assert "Alice" in result
        assert "https://youtube.com/watch?v=123" in result
        assert "https://dropbox.com/folder/abc" in result
        assert "https://example.com/feedback" in result

    def test_render_job_completion_defaults(self):
        """Test rendering job completion with default values."""
        service = TemplateService()

        with patch.object(service, "_fetch_template_from_gcs", return_value=None):
            result = service.render_job_completion()

        assert "there" in result  # Default name
        assert "[YouTube URL not available]" in result
        assert "[Dropbox URL not available]" in result

    def test_render_job_completion_no_feedback_url(self):
        """Test rendering job completion without feedback URL removes section."""
        service = TemplateService()

        with patch.object(service, "_fetch_template_from_gcs", return_value=None):
            result = service.render_job_completion(
                name="Bob",
                youtube_url="https://youtube.com/123",
                dropbox_url="https://dropbox.com/abc",
            )

        assert "Bob" in result
        # Feedback section should be removed
        assert "really appreciate your feedback" not in result


class TestActionNeededTemplates:
    """Tests for action-needed template rendering."""

    def test_render_action_needed_lyrics(self):
        """Test rendering lyrics review reminder."""
        service = TemplateService()

        with patch.object(service, "_fetch_template_from_gcs", return_value=None):
            result = service.render_action_needed_lyrics(
                name="Charlie",
                artist="Test Artist",
                title="Test Song",
                review_url="https://example.com/review",
            )

        assert "Charlie" in result
        assert "Test Artist" in result
        assert "Test Song" in result
        assert "https://example.com/review" in result

    def test_render_action_needed_instrumental(self):
        """Test rendering instrumental selection reminder."""
        service = TemplateService()

        with patch.object(service, "_fetch_template_from_gcs", return_value=None):
            result = service.render_action_needed_instrumental(
                name="Diana",
                artist="Test Artist",
                title="Test Song",
                instrumental_url="https://example.com/instrumental",
            )

        assert "Diana" in result
        assert "Test Artist" in result
        assert "Test Song" in result
        assert "https://example.com/instrumental" in result


class TestGCSFetching:
    """Tests for GCS template fetching."""

    def test_fetch_template_from_gcs_success(self):
        """Test successful template fetch from GCS."""
        service = TemplateService()

        # Mock the bucket and blob
        mock_blob = Mock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = "Custom template {name}"

        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob

        with patch.object(service, "_bucket", mock_bucket):
            result = service._fetch_template_from_gcs("test.txt")

        assert result == "Custom template {name}"
        mock_bucket.blob.assert_called_once_with("templates/test.txt")

    def test_fetch_template_from_gcs_not_found(self):
        """Test template fetch when file doesn't exist in GCS."""
        service = TemplateService()

        mock_blob = Mock()
        mock_blob.exists.return_value = False

        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob

        with patch.object(service, "_bucket", mock_bucket):
            result = service._fetch_template_from_gcs("nonexistent.txt")

        assert result is None

    def test_fetch_template_from_gcs_error(self):
        """Test template fetch handles errors gracefully."""
        service = TemplateService()

        mock_bucket = Mock()
        mock_bucket.blob.side_effect = Exception("GCS error")

        with patch.object(service, "_bucket", mock_bucket):
            result = service._fetch_template_from_gcs("test.txt")

        assert result is None

    def test_get_job_completion_template_fallback(self):
        """Test that default template is used when GCS fetch fails."""
        service = TemplateService()

        with patch.object(service, "_fetch_template_from_gcs", return_value=None):
            result = service.get_job_completion_template()

        assert result == DEFAULT_JOB_COMPLETION_TEMPLATE

    def test_get_action_needed_lyrics_template_fallback(self):
        """Test that default lyrics template is used when GCS fetch fails."""
        service = TemplateService()

        with patch.object(service, "_fetch_template_from_gcs", return_value=None):
            result = service.get_action_needed_lyrics_template()

        assert result == DEFAULT_ACTION_NEEDED_LYRICS_TEMPLATE

    def test_get_action_needed_instrumental_template_fallback(self):
        """Test that default instrumental template is used when GCS fetch fails."""
        service = TemplateService()

        with patch.object(service, "_fetch_template_from_gcs", return_value=None):
            result = service.get_action_needed_instrumental_template()

        assert result == DEFAULT_ACTION_NEEDED_INSTRUMENTAL_TEMPLATE


class TestTemplateUpload:
    """Tests for template upload functionality."""

    def test_upload_template_success(self):
        """Test successful template upload."""
        service = TemplateService()

        mock_blob = Mock()
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob

        with patch.object(service, "_bucket", mock_bucket):
            result = service.upload_template("test.txt", "Test content")

        assert result is True
        mock_bucket.blob.assert_called_once_with("templates/test.txt")
        mock_blob.upload_from_string.assert_called_once_with(
            "Test content", content_type="text/plain"
        )

    def test_upload_template_error(self):
        """Test template upload handles errors."""
        service = TemplateService()

        mock_blob = Mock()
        mock_blob.upload_from_string.side_effect = Exception("Upload failed")
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob

        with patch.object(service, "_bucket", mock_bucket):
            result = service.upload_template("test.txt", "Test content")

        assert result is False


class TestGlobalInstance:
    """Tests for global instance management."""

    def test_get_template_service_returns_same_instance(self):
        """Test that get_template_service returns singleton."""
        # Reset global
        import backend.services.template_service as ts
        ts._template_service = None

        service1 = get_template_service()
        service2 = get_template_service()

        assert service1 is service2
