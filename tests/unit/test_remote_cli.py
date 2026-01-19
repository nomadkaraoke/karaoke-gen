"""
Tests for remote_cli.py - the cloud-hosted karaoke generation CLI.

These tests mock network calls and subprocess calls to test the CLI logic
without requiring actual network or GCP connectivity.
"""
import argparse
import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import pytest

from karaoke_gen.utils import remote_cli
from karaoke_gen.utils.remote_cli import (
    Config,
    JobStatus,
    RemoteKaraokeClient,
    JobMonitor,
    check_prerequisites,
    get_auth_token,
    main,
)


# Sample style params JSON for testing
SAMPLE_STYLE_PARAMS = {
    "intro": {
        "background_image": "/path/to/intro_bg.png",
        "font": "/path/to/font.ttf"
    },
    "karaoke": {
        "background_image": "/path/to/karaoke_bg.jpg",
        "font_path": "/path/to/font.ttf"
    },
    "end": {
        "background_image": "/path/to/end_bg.png",
        "font": "/path/to/font.ttf"
    },
    "cdg": {
        "font_path": "/path/to/font.ttf",
        "title_screen_background": "/path/to/cdg_title.png"
    }
}


class TestJobStatus:
    """Tests for the JobStatus enum."""
    
    def test_job_status_values(self):
        """Test that all expected status values exist."""
        assert JobStatus.PENDING == "pending"
        assert JobStatus.DOWNLOADING == "downloading"
        assert JobStatus.SEPARATING_STAGE1 == "separating_stage1"
        assert JobStatus.SEPARATING_STAGE2 == "separating_stage2"
        assert JobStatus.AUDIO_COMPLETE == "audio_complete"
        assert JobStatus.TRANSCRIBING == "transcribing"
        assert JobStatus.CORRECTING == "correcting"
        assert JobStatus.LYRICS_COMPLETE == "lyrics_complete"
        assert JobStatus.GENERATING_SCREENS == "generating_screens"
        assert JobStatus.APPLYING_PADDING == "applying_padding"
        assert JobStatus.AWAITING_REVIEW == "awaiting_review"
        assert JobStatus.IN_REVIEW == "in_review"
        assert JobStatus.REVIEW_COMPLETE == "review_complete"
        assert JobStatus.RENDERING_VIDEO == "rendering_video"
        assert JobStatus.AWAITING_INSTRUMENTAL_SELECTION == "awaiting_instrumental_selection"
        assert JobStatus.INSTRUMENTAL_SELECTED == "instrumental_selected"
        assert JobStatus.GENERATING_VIDEO == "generating_video"
        assert JobStatus.ENCODING == "encoding"
        assert JobStatus.PACKAGING == "packaging"
        assert JobStatus.UPLOADING == "uploading"
        assert JobStatus.NOTIFYING == "notifying"
        assert JobStatus.COMPLETE == "complete"
        assert JobStatus.PREP_COMPLETE == "prep_complete"  # Batch 6: Prep-only jobs stop here
        assert JobStatus.FAILED == "failed"
        assert JobStatus.CANCELLED == "cancelled"
        assert JobStatus.ERROR == "error"
    
    def test_job_status_is_string(self):
        """Test that JobStatus values are strings."""
        assert isinstance(JobStatus.PENDING.value, str)
        assert JobStatus.PENDING == "pending"


class TestConfig:
    """Tests for the Config dataclass."""
    
    def test_config_creation(self):
        """Test creating a Config instance."""
        config = Config(
            service_url="https://example.com",
            review_ui_url="https://review.example.com",
            poll_interval=10,
            output_dir="/tmp/output",
            auth_token="test-token"
        )
        assert config.service_url == "https://example.com"
        assert config.review_ui_url == "https://review.example.com"
        assert config.poll_interval == 10
        assert config.output_dir == "/tmp/output"
        assert config.auth_token == "test-token"
    
    def test_config_optional_auth_token(self):
        """Test that auth_token is optional."""
        config = Config(
            service_url="https://example.com",
            review_ui_url="https://review.example.com",
            poll_interval=5,
            output_dir="/tmp"
        )
        assert config.auth_token is None


class TestRemoteKaraokeClient:
    """Tests for the RemoteKaraokeClient class."""
    
    @pytest.fixture
    def config(self):
        """Create a test config."""
        return Config(
            service_url="https://api.example.com",
            review_ui_url="https://review.example.com",
            poll_interval=5,
            output_dir="/tmp/output",
            auth_token="test-token"
        )
    
    @pytest.fixture
    def logger(self):
        """Create a test logger."""
        return logging.getLogger("test")
    
    @pytest.fixture
    def client(self, config, logger):
        """Create a test client."""
        return RemoteKaraokeClient(config, logger)
    
    def test_client_creation(self, config, logger):
        """Test creating a client."""
        client = RemoteKaraokeClient(config, logger)
        assert client.config == config
        assert client.logger == logger
    
    def test_setup_auth_with_token(self, config, logger):
        """Test that auth header is set when token is provided."""
        client = RemoteKaraokeClient(config, logger)
        assert 'Authorization' in client.session.headers
        assert client.session.headers['Authorization'] == 'Bearer test-token'
    
    def test_setup_auth_without_token(self, logger):
        """Test that no auth header is set when no token is provided."""
        config = Config(
            service_url="https://api.example.com",
            review_ui_url="https://review.example.com",
            poll_interval=5,
            output_dir="/tmp"
        )
        client = RemoteKaraokeClient(config, logger)
        assert 'Authorization' not in client.session.headers
    
    def test_allowed_extensions(self, client):
        """Test that allowed extensions are defined."""
        assert '.mp3' in client.ALLOWED_AUDIO_EXTENSIONS
        assert '.wav' in client.ALLOWED_AUDIO_EXTENSIONS
        assert '.flac' in client.ALLOWED_AUDIO_EXTENSIONS
        assert '.png' in client.ALLOWED_IMAGE_EXTENSIONS
        assert '.jpg' in client.ALLOWED_IMAGE_EXTENSIONS
        assert '.ttf' in client.ALLOWED_FONT_EXTENSIONS
        assert '.otf' in client.ALLOWED_FONT_EXTENSIONS
    
    @patch('subprocess.run')
    def test_get_auth_token_from_gcloud_success(self, mock_run, client):
        """Test getting auth token from gcloud."""
        mock_run.return_value = MagicMock(
            stdout="test-identity-token\n",
            returncode=0
        )
        token = client._get_auth_token_from_gcloud()
        assert token == "test-identity-token"
        mock_run.assert_called_once()
    
    @patch('subprocess.run')
    def test_get_auth_token_from_gcloud_failure(self, mock_run, client):
        """Test handling gcloud auth failure."""
        from subprocess import CalledProcessError
        mock_run.side_effect = CalledProcessError(1, 'gcloud')
        token = client._get_auth_token_from_gcloud()
        assert token is None
    
    @patch('subprocess.run')
    def test_get_auth_token_gcloud_not_found(self, mock_run, client):
        """Test handling missing gcloud CLI."""
        mock_run.side_effect = FileNotFoundError()
        token = client._get_auth_token_from_gcloud()
        assert token is None
    
    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_refresh_auth_success_gcloud(self, mock_run, client):
        """Test refreshing auth token via gcloud when no env token is set."""
        mock_run.return_value = MagicMock(
            stdout="new-token\n",
            returncode=0
        )
        result = client.refresh_auth()
        assert result is True
        assert client.config.auth_token == "new-token"
        assert client.session.headers['Authorization'] == 'Bearer new-token'
    
    @patch.dict(os.environ, {'KARAOKE_GEN_AUTH_TOKEN': 'static-admin-token'})
    def test_refresh_auth_keeps_env_token(self, client):
        """Test that refresh_auth keeps static env token instead of calling gcloud."""
        original_token = client.config.auth_token
        result = client.refresh_auth()
        assert result is True
        # Token should NOT be changed when env var is set
        assert client.config.auth_token == original_token
    
    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_refresh_auth_failure(self, mock_run, client):
        """Test handling auth refresh failure."""
        mock_run.side_effect = FileNotFoundError()
        result = client.refresh_auth()
        assert result is False
    
    def test_parse_style_params_with_files(self, client, tmp_path):
        """Test parsing style params JSON with file references."""
        # Create temp files
        font_file = tmp_path / "font.ttf"
        font_file.write_bytes(b"font data")
        bg_file = tmp_path / "bg.png"
        bg_file.write_bytes(b"image data")
        
        style_params = {
            "intro": {
                "background_image": str(bg_file),
                "font": str(font_file)
            }
        }
        
        style_file = tmp_path / "style_params.json"
        style_file.write_text(json.dumps(style_params))
        
        assets = client._parse_style_params(str(style_file))
        
        assert "style_intro_background" in assets
        assert "style_font" in assets
        assert assets["style_intro_background"] == str(bg_file)
        assert assets["style_font"] == str(font_file)
    
    def test_parse_style_params_nonexistent_file(self, client, tmp_path):
        """Test parsing style params with nonexistent referenced files."""
        style_params = {
            "intro": {
                "background_image": "/nonexistent/file.png"
            }
        }
        
        style_file = tmp_path / "style_params.json"
        style_file.write_text(json.dumps(style_params))
        
        assets = client._parse_style_params(str(style_file))
        
        # Should not include nonexistent files
        assert "style_intro_background" not in assets
    
    def test_parse_style_params_invalid_json(self, client, tmp_path):
        """Test handling invalid JSON in style params."""
        style_file = tmp_path / "style_params.json"
        style_file.write_text("not valid json")
        
        assets = client._parse_style_params(str(style_file))
        assert assets == {}
    
    def test_parse_style_params_file_not_found(self, client):
        """Test handling nonexistent style params file."""
        assets = client._parse_style_params("/nonexistent/style.json")
        assert assets == {}
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_get_job_success(self, mock_request, client):
        """Test getting job status."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "job-123",
            "status": "complete",
            "artist": "Test Artist",
            "title": "Test Title"
        }
        mock_request.return_value = mock_response
        
        result = client.get_job("job-123")
        
        assert result["id"] == "job-123"
        assert result["status"] == "complete"
        mock_request.assert_called_once_with('GET', '/api/jobs/job-123')
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_get_job_not_found(self, mock_request, client):
        """Test handling job not found."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response
        
        with pytest.raises(ValueError, match="Job not found"):
            client.get_job("nonexistent-job")
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_get_job_error(self, mock_request, client):
        """Test handling job API error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_request.return_value = mock_response
        
        with pytest.raises(RuntimeError, match="Error getting job"):
            client.get_job("job-123")
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_list_jobs_success(self, mock_request, client):
        """Test listing jobs successfully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"job_id": "job-1", "status": "complete", "artist": "Artist 1", "title": "Title 1"},
            {"job_id": "job-2", "status": "processing", "artist": "Artist 2", "title": "Title 2"},
        ]
        mock_request.return_value = mock_response
        
        result = client.list_jobs()
        
        assert len(result) == 2
        assert result[0]["job_id"] == "job-1"
        mock_request.assert_called_once_with('GET', '/api/jobs', params={'limit': 100})
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_list_jobs_with_status_filter(self, mock_request, client):
        """Test listing jobs with status filter."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_request.return_value = mock_response
        
        client.list_jobs(status="complete", limit=50)
        
        mock_request.assert_called_once_with('GET', '/api/jobs', params={'limit': 50, 'status': 'complete'})
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_list_jobs_error(self, mock_request, client):
        """Test handling list jobs error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_request.return_value = mock_response
        
        with pytest.raises(RuntimeError, match="Error listing jobs"):
            client.list_jobs()
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_cancel_job_success(self, mock_request, client):
        """Test cancelling a job successfully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "cancelled"}
        mock_request.return_value = mock_response
        
        result = client.cancel_job("job-123")
        
        assert result["status"] == "cancelled"
        mock_request.assert_called_once_with(
            'POST',
            '/api/jobs/job-123/cancel',
            json={'reason': 'User requested'}
        )
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_cancel_job_not_found(self, mock_request, client):
        """Test cancelling non-existent job."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response
        
        with pytest.raises(ValueError, match="Job not found"):
            client.cancel_job("nonexistent-job")
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_cancel_job_invalid_state(self, mock_request, client):
        """Test cancelling job in invalid state."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"detail": "Job already complete"}
        mock_request.return_value = mock_response
        
        with pytest.raises(RuntimeError, match="Cannot cancel job"):
            client.cancel_job("job-123")
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_delete_job_success(self, mock_request, client):
        """Test deleting a job successfully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "deleted"}
        mock_request.return_value = mock_response
        
        result = client.delete_job("job-123")
        
        assert result["status"] == "deleted"
        mock_request.assert_called_once_with(
            'DELETE',
            '/api/jobs/job-123',
            params={'delete_files': 'true'}
        )
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_delete_job_keep_files(self, mock_request, client):
        """Test deleting a job but keeping files."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "deleted"}
        mock_request.return_value = mock_response
        
        client.delete_job("job-123", delete_files=False)
        
        mock_request.assert_called_once_with(
            'DELETE',
            '/api/jobs/job-123',
            params={'delete_files': 'false'}
        )
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_delete_job_not_found(self, mock_request, client):
        """Test deleting non-existent job."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response

        with pytest.raises(ValueError, match="Job not found"):
            client.delete_job("nonexistent-job")

    @patch.object(RemoteKaraokeClient, '_request')
    def test_retry_job_success(self, mock_request, client):
        """Test retrying a failed job successfully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "job_status": "instrumental_selected",
            "retry_stage": "video_generation"
        }
        mock_request.return_value = mock_response

        result = client.retry_job("job-123")

        assert result["status"] == "success"
        assert result["retry_stage"] == "video_generation"
        mock_request.assert_called_once_with('POST', '/api/jobs/job-123/retry')

    @patch.object(RemoteKaraokeClient, '_request')
    def test_retry_job_not_found(self, mock_request, client):
        """Test retrying non-existent job."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response

        with pytest.raises(ValueError, match="Job not found"):
            client.retry_job("nonexistent-job")

    @patch.object(RemoteKaraokeClient, '_request')
    def test_retry_job_not_failed(self, mock_request, client):
        """Test retrying a job that isn't in failed state."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "detail": "Only failed jobs can be retried"
        }
        mock_request.return_value = mock_response

        with pytest.raises(RuntimeError, match="Cannot retry job"):
            client.retry_job("job-123")

    @patch.object(RemoteKaraokeClient, '_request')
    def test_get_instrumental_options(self, mock_request, client):
        """Test getting instrumental options."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "clean": {"url": "https://example.com/clean.mp3"},
            "with_backing": {"url": "https://example.com/backing.mp3"}
        }
        mock_request.return_value = mock_response
        
        result = client.get_instrumental_options("job-123")
        
        assert "clean" in result
        assert "with_backing" in result
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_select_instrumental(self, mock_request, client):
        """Test selecting instrumental."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success"}
        mock_request.return_value = mock_response
        
        result = client.select_instrumental("job-123", "clean")
        
        assert result["status"] == "success"
        mock_request.assert_called_once_with(
            'POST',
            '/api/jobs/job-123/select-instrumental',
            json={'selection': 'clean'}
        )
    
    @patch('subprocess.run')
    def test_download_file_via_gsutil_success(self, mock_run, client):
        """Test successful file download."""
        mock_run.return_value = MagicMock(returncode=0)
        
        result = client.download_file_via_gsutil("path/to/file.mp4", "/local/file.mp4")
        
        assert result is True
        mock_run.assert_called_once()
    
    @patch('subprocess.run')
    def test_download_file_via_gsutil_failure(self, mock_run, client):
        """Test failed file download."""
        mock_run.return_value = MagicMock(returncode=1)
        
        result = client.download_file_via_gsutil("path/to/file.mp4", "/local/file.mp4")
        
        assert result is False
    
    @patch('subprocess.run')
    def test_download_file_gsutil_not_found(self, mock_run, client):
        """Test handling missing gsutil."""
        mock_run.side_effect = FileNotFoundError()
        
        result = client.download_file_via_gsutil("path/to/file.mp4", "/local/file.mp4")
        
        assert result is False
    
    def test_download_file_via_url_uses_session_with_auth(self, client, tmp_path):
        """Test that download_file_via_url uses session (which has auth headers).
        
        This test verifies the fix for the bug where downloads failed because
        the auth header wasn't included in requests.
        """
        # Setup: mock the session.get method
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'test content']
        client.session.get = MagicMock(return_value=mock_response)
        
        local_path = str(tmp_path / "test.mp4")
        
        # Call the method with a relative URL
        result = client.download_file_via_url("/api/jobs/123/download/finals/mp4", local_path)
        
        assert result is True
        # CRITICAL: Verify session.get was called (not requests.get directly)
        client.session.get.assert_called_once()
        # Verify the URL was properly constructed with service URL from config
        call_args = client.session.get.call_args
        expected_url = f"{client.config.service_url}/api/jobs/123/download/finals/mp4"
        assert expected_url == call_args[0][0]
        # Verify the file was written
        assert (tmp_path / "test.mp4").exists()
    
    def test_download_file_via_url_absolute_url(self, client, tmp_path):
        """Test download_file_via_url with absolute URL (e.g., signed GCS URL)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'gcs content']
        client.session.get = MagicMock(return_value=mock_response)
        
        local_path = str(tmp_path / "test.mp4")
        absolute_url = "https://storage.googleapis.com/bucket/path/file.mp4?signed=xyz"
        
        result = client.download_file_via_url(absolute_url, local_path)
        
        assert result is True
        # Verify the absolute URL was used as-is
        call_args = client.session.get.call_args
        assert absolute_url == call_args[0][0]
    
    def test_download_file_via_url_failure_returns_false(self, client, tmp_path):
        """Test that download failures return False without raising."""
        mock_response = MagicMock()
        mock_response.status_code = 403  # Forbidden - simulates auth failure
        client.session.get = MagicMock(return_value=mock_response)
        
        local_path = str(tmp_path / "test.mp4")
        
        result = client.download_file_via_url("/api/jobs/123/download", local_path)
        
        assert result is False
        assert not (tmp_path / "test.mp4").exists()
    
    def test_download_file_via_url_network_error(self, client, tmp_path):
        """Test that network errors return False without raising."""
        client.session.get = MagicMock(side_effect=Exception("Network error"))
        
        local_path = str(tmp_path / "test.mp4")
        
        result = client.download_file_via_url("/api/jobs/123/download", local_path)
        
        assert result is False
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_submit_job_file_not_found(self, mock_request, client):
        """Test submitting job with nonexistent file."""
        with pytest.raises(FileNotFoundError):
            client.submit_job("/nonexistent/file.mp3", "Artist", "Title")
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_submit_job_unsupported_format(self, mock_request, client, tmp_path):
        """Test submitting job with unsupported file format."""
        # Create a file with unsupported extension
        test_file = tmp_path / "test.exe"
        test_file.write_bytes(b"test")
        
        with pytest.raises(ValueError, match="Unsupported file type"):
            client.submit_job(str(test_file), "Artist", "Title")
    
    @patch.object(RemoteKaraokeClient, '_request')
    @patch.object(RemoteKaraokeClient, '_upload_file_to_signed_url')
    def test_submit_job_success(self, mock_upload, mock_request, client, tmp_path):
        """Test successful job submission via signed URL flow."""
        # Create test audio file
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"audio data")

        # Mock create job response (first API call)
        create_response = MagicMock()
        create_response.status_code = 200
        create_response.json.return_value = {
            "status": "success",
            "job_id": "new-job-123",
            "upload_urls": [
                {
                    "file_type": "audio",
                    "gcs_path": "uploads/new-job-123/audio/test.mp3",
                    "upload_url": "https://storage.googleapis.com/signed-url",
                    "content_type": "audio/mpeg"
                }
            ]
        }
        
        # Mock uploads complete response (second API call)
        complete_response = MagicMock()
        complete_response.status_code = 200
        complete_response.json.return_value = {
            "status": "success",
            "job_id": "new-job-123",
            "message": "Processing started"
        }
        
        mock_request.side_effect = [create_response, complete_response]
        mock_upload.return_value = True

        result = client.submit_job(str(audio_file), "Test Artist", "Test Title")

        assert result["status"] == "success"
        assert result["job_id"] == "new-job-123"
        # Verify the signed URL upload was called
        mock_upload.assert_called_once()

    @patch.object(RemoteKaraokeClient, '_upload_file_to_signed_url')
    @patch.object(RemoteKaraokeClient, '_request')
    def test_submit_job_with_style_params(self, mock_request, mock_upload, client, tmp_path):
        """Test job submission with style params via signed URL flow."""
        # Create test files
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"audio data")

        style_file = tmp_path / "style_params.json"
        style_file.write_text(json.dumps({"intro": {}}))

        # Mock create job response (first API call)
        create_response = MagicMock()
        create_response.status_code = 200
        create_response.json.return_value = {
            "status": "success",
            "job_id": "new-job-456",
            "upload_urls": [
                {
                    "file_type": "audio",
                    "gcs_path": "uploads/new-job-456/audio/test.mp3",
                    "upload_url": "https://storage.googleapis.com/signed-url-1",
                    "content_type": "audio/mpeg"
                },
                {
                    "file_type": "style_params",
                    "gcs_path": "uploads/new-job-456/style/style_params.json",
                    "upload_url": "https://storage.googleapis.com/signed-url-2",
                    "content_type": "application/json"
                }
            ]
        }
        
        # Mock uploads complete response (second API call)
        complete_response = MagicMock()
        complete_response.status_code = 200
        complete_response.json.return_value = {
            "status": "success",
            "job_id": "new-job-456",
            "message": "Processing started",
            "style_assets": ["style_params"]
        }
        
        mock_request.side_effect = [create_response, complete_response]
        mock_upload.return_value = True

        result = client.submit_job(
            str(audio_file),
            "Test Artist",
            "Test Title",
            style_params_path=str(style_file),
            enable_cdg=True,
            enable_txt=True,
            brand_prefix="TEST",
            discord_webhook_url="https://discord.webhook/url"
        )

        assert result["status"] == "success"
        # Verify uploads were called for both files
        assert mock_upload.call_count == 2
    
    @patch.object(RemoteKaraokeClient, '_upload_file_to_signed_url')
    @patch.object(RemoteKaraokeClient, '_request')
    def test_submit_job_with_existing_instrumental(self, mock_request, mock_upload, client, tmp_path):
        """Test job submission with existing instrumental via signed URL flow (Batch 3)."""
        # Create test files
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"audio data")
        
        instrumental_file = tmp_path / "instrumental.flac"
        instrumental_file.write_bytes(b"instrumental data")
        
        # Mock create job response (first API call)
        create_response = MagicMock()
        create_response.status_code = 200
        create_response.json.return_value = {
            "status": "success",
            "job_id": "job-with-instrumental-123",
            "upload_urls": [
                {
                    "file_type": "audio",
                    "gcs_path": "uploads/job-with-instrumental-123/audio/test.mp3",
                    "upload_url": "https://storage.googleapis.com/signed-url-audio",
                    "content_type": "audio/mpeg"
                },
                {
                    "file_type": "existing_instrumental",
                    "gcs_path": "uploads/job-with-instrumental-123/audio/existing_instrumental.flac",
                    "upload_url": "https://storage.googleapis.com/signed-url-instrumental",
                    "content_type": "audio/flac"
                }
            ]
        }
        
        # Mock uploads complete response (second API call)
        complete_response = MagicMock()
        complete_response.status_code = 200
        complete_response.json.return_value = {
            "status": "success",
            "job_id": "job-with-instrumental-123",
            "message": "Processing started"
        }
        
        mock_request.side_effect = [create_response, complete_response]
        mock_upload.return_value = True
        
        result = client.submit_job(
            str(audio_file),
            "Test Artist",
            "Test Title",
            existing_instrumental=str(instrumental_file)
        )
        
        assert result["status"] == "success"
        assert result["job_id"] == "job-with-instrumental-123"
        # Verify uploads were called for both audio and instrumental
        assert mock_upload.call_count == 2
    
    @patch.object(RemoteKaraokeClient, '_upload_file_to_signed_url')
    @patch.object(RemoteKaraokeClient, '_request')
    def test_submit_job_existing_instrumental_nonexistent_file(self, mock_request, mock_upload, client, tmp_path):
        """Test that nonexistent instrumental file is silently ignored."""
        # Create test audio file
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"audio data")
        
        # Mock create job response (instrumental not included since file doesn't exist)
        create_response = MagicMock()
        create_response.status_code = 200
        create_response.json.return_value = {
            "status": "success",
            "job_id": "job-no-instrumental-456",
            "upload_urls": [
                {
                    "file_type": "audio",
                    "gcs_path": "uploads/job-no-instrumental-456/audio/test.mp3",
                    "upload_url": "https://storage.googleapis.com/signed-url-audio",
                    "content_type": "audio/mpeg"
                }
            ]
        }
        
        # Mock uploads complete response
        complete_response = MagicMock()
        complete_response.status_code = 200
        complete_response.json.return_value = {
            "status": "success",
            "job_id": "job-no-instrumental-456",
            "message": "Processing started"
        }
        
        mock_request.side_effect = [create_response, complete_response]
        mock_upload.return_value = True
        
        # Pass nonexistent instrumental path - should be silently ignored
        result = client.submit_job(
            str(audio_file),
            "Test Artist",
            "Test Title",
            existing_instrumental="/nonexistent/instrumental.flac"
        )
        
        assert result["status"] == "success"
        # Only audio should be uploaded (instrumental file doesn't exist)
        assert mock_upload.call_count == 1
    
    @patch.object(RemoteKaraokeClient, '_request')
    def test_submit_job_api_error(self, mock_request, client, tmp_path):
        """Test handling API error during job creation."""
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"audio data")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "Server error"}
        mock_request.return_value = mock_response

        with pytest.raises(RuntimeError, match="Error creating job"):
            client.submit_job(str(audio_file), "Artist", "Title")


class TestJobMonitor:
    """Tests for the JobMonitor class."""
    
    @pytest.fixture
    def config(self):
        return Config(
            service_url="https://api.example.com",
            review_ui_url="https://review.example.com",
            poll_interval=1,
            output_dir="/tmp/output"
        )
    
    @pytest.fixture
    def logger(self):
        return logging.getLogger("test")
    
    @pytest.fixture
    def client(self, config, logger):
        return RemoteKaraokeClient(config, logger)
    
    @pytest.fixture
    def monitor(self, client, config, logger):
        return JobMonitor(client, config, logger)
    
    def test_monitor_creation(self, client, config, logger):
        """Test creating a JobMonitor."""
        monitor = JobMonitor(client, config, logger)
        assert monitor.client == client
        assert monitor.config == config
        assert monitor._review_opened is False
        assert monitor._instrumental_prompted is False
        assert monitor._last_timeline_index == 0
    
    @patch('subprocess.run')
    def test_open_browser_darwin(self, mock_run, monitor):
        """Test opening browser on macOS."""
        with patch('platform.system', return_value='Darwin'):
            monitor.open_browser("https://example.com")
            mock_run.assert_called_with(['open', 'https://example.com'], check=True)
    
    @patch('subprocess.run')
    def test_open_browser_linux(self, mock_run, monitor):
        """Test opening browser on Linux."""
        with patch('platform.system', return_value='Linux'):
            monitor.open_browser("https://example.com")
            mock_run.assert_called()
    
    @patch('webbrowser.open')
    def test_open_browser_windows(self, mock_webbrowser, monitor):
        """Test opening browser on Windows."""
        with patch('platform.system', return_value='Windows'):
            monitor.open_browser("https://example.com")
            mock_webbrowser.assert_called_with("https://example.com")
    
    @patch('subprocess.run')
    def test_open_browser_failure(self, mock_run, monitor):
        """Test handling browser open failure."""
        mock_run.side_effect = Exception("Failed")
        # Should not raise, just log
        with patch('platform.system', return_value='Darwin'):
            monitor.open_browser("https://example.com")
    
    @patch.object(JobMonitor, 'open_browser')
    def test_open_review_ui(self, mock_open_browser, monitor):
        """Test opening review UI."""
        monitor.open_review_ui("job-123")

        mock_open_browser.assert_called_once()
        call_url = mock_open_browser.call_args[0][0]
        # New consolidated frontend URL format
        assert call_url == "https://review.example.com/app/jobs/job-123/review"
    
    def test_log_timeline_updates(self, monitor):
        """Test logging timeline events."""
        job_data = {
            "timeline": [
                {"timestamp": "2024-01-01T10:00:00Z", "status": "pending", "message": "Job created"},
                {"timestamp": "2024-01-01T10:01:00Z", "status": "processing", "message": "Processing", "progress": "50"}
            ]
        }
        
        monitor.log_timeline_updates(job_data)
        
        assert monitor._last_timeline_index == 2
        
        # Calling again with same data should not log again
        monitor.log_timeline_updates(job_data)
        assert monitor._last_timeline_index == 2
    
    def test_log_timeline_updates_new_events(self, monitor):
        """Test that only new events are logged."""
        monitor._last_timeline_index = 1
        
        job_data = {
            "timeline": [
                {"timestamp": "2024-01-01T10:00:00Z", "status": "pending", "message": "Old event"},
                {"timestamp": "2024-01-01T10:01:00Z", "status": "processing", "message": "New event"}
            ]
        }
        
        monitor.log_timeline_updates(job_data)
        
        assert monitor._last_timeline_index == 2


class TestHelperFunctions:
    """Tests for module-level helper functions."""
    
    @patch('subprocess.run')
    def test_check_prerequisites_all_available(self, mock_run):
        """Test checking prerequisites when all tools are available."""
        mock_run.return_value = MagicMock(returncode=0)
        logger = logging.getLogger("test")
        
        result = check_prerequisites(logger)
        
        assert result is True
        assert mock_run.call_count == 2  # gcloud and gsutil
    
    @patch('subprocess.run')
    def test_check_prerequisites_gcloud_missing(self, mock_run):
        """Test handling missing gcloud."""
        mock_run.side_effect = FileNotFoundError()
        logger = logging.getLogger("test")
        
        result = check_prerequisites(logger)
        
        # Should still return True but with warnings
        assert result is True
    
    @patch('subprocess.run')
    def test_get_auth_token_from_env(self, mock_run):
        """Test getting auth token from environment variable."""
        logger = logging.getLogger("test")
        
        with patch.dict(os.environ, {'KARAOKE_GEN_AUTH_TOKEN': 'env-token'}):
            token = get_auth_token(logger)
            assert token == 'env-token'
    
    @patch('subprocess.run')
    def test_get_auth_token_from_gcloud(self, mock_run):
        """Test getting auth token from gcloud."""
        mock_run.return_value = MagicMock(
            stdout="gcloud-token\n",
            returncode=0
        )
        logger = logging.getLogger("test")
        
        # Make sure env var is not set
        with patch.dict(os.environ, {}, clear=True):
            # Clear the env var if it exists
            os.environ.pop('KARAOKE_GEN_AUTH_TOKEN', None)
            token = get_auth_token(logger)
            assert token == "gcloud-token"
    
    @patch('subprocess.run')
    def test_get_auth_token_not_available(self, mock_run):
        """Test when no auth token is available."""
        mock_run.side_effect = FileNotFoundError()
        logger = logging.getLogger("test")
        
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('KARAOKE_GEN_AUTH_TOKEN', None)
            token = get_auth_token(logger)
            assert token is None


class TestMain:
    """Tests for the main() function."""
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    def test_main_no_service_url(self, mock_create_parser):
        """Test main exits when no service URL is provided."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = None
        mock_args.log_level = "info"
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        
        result = main()
        
        assert result == 1
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.get_auth_token')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    def test_main_finalise_only_requires_folder_and_args(
        self, mock_check, mock_auth, mock_create_parser
    ):
        """Test main --finalise-only requires folder and artist/title args (Batch 6)."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.log_level = "info"
        mock_args.finalise_only = True
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_args.args = []  # No args provided - should fail
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        mock_auth.return_value = "token"

        result = main()

        # Should fail because no folder/artist/title provided
        assert result == 1

    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.get_auth_token')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    def test_main_edit_lyrics_not_supported(
        self, mock_check, mock_auth, mock_create_parser
    ):
        """Test main rejects --edit-lyrics in remote mode."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = True
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        mock_auth.return_value = "token"

        result = main()

        assert result == 1

    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.get_auth_token')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    def test_main_test_email_not_supported(
        self, mock_check, mock_auth, mock_create_parser
    ):
        """Test main rejects --test_email_template in remote mode."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = True
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        mock_auth.return_value = "token"

        result = main()

        assert result == 1

    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.get_auth_token')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    def test_main_no_args_shows_help(
        self, mock_check, mock_auth, mock_create_parser
    ):
        """Test main shows help when no positional args provided."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None  # Added for retry feature
        mock_args.delete = None
        mock_args.bulk_delete = False  # New attribute for bulk delete mode
        mock_args.filter_environment = None  # New attribute for filtering
        mock_args.filter_client_id = None  # New attribute for filtering
        mock_args.args = []  # No positional args
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        mock_auth.return_value = "token"

        result = main()

        assert result == 1
        mock_parser.print_help.assert_called_once()
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.get_auth_token')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.JobMonitor')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_resume_mode(
        self, mock_client_class, mock_monitor_class, mock_check, mock_auth, mock_create_parser
    ):
        """Test main in resume mode."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = "job-123"
        mock_args.bulk_delete = False  # New attribute
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        mock_auth.return_value = "token"
        
        mock_client = MagicMock()
        mock_client.get_job.return_value = {
            "artist": "Artist",
            "title": "Title",
            "status": "processing"
        }
        mock_client_class.return_value = mock_client
        
        mock_monitor = MagicMock()
        mock_monitor.monitor.return_value = 0
        mock_monitor_class.return_value = mock_monitor
        
        result = main()
        
        assert result == 0
        mock_monitor.monitor.assert_called_once_with("job-123")
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_list_jobs_mode(
        self, mock_client_class, mock_check, mock_create_parser
    ):
        """Test main in list jobs mode."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = True
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False  # New attribute for bulk delete mode
        mock_args.filter_environment = None  # New attribute for filtering
        mock_args.filter_client_id = None  # New attribute for filtering
        mock_args.environment = ""  # Job tracking
        mock_args.client_id = ""  # Job tracking
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser

        mock_client = MagicMock()
        mock_client.list_jobs.return_value = [
            {"job_id": "job-1", "status": "complete", "artist": "Artist 1", "title": "Title 1", "request_metadata": {}},
            {"job_id": "job-2", "status": "processing", "artist": "Artist 2", "title": "Title 2", "request_metadata": {}},
        ]
        mock_client_class.return_value = mock_client
        
        result = main()
        
        assert result == 0
        mock_client.list_jobs.assert_called_once_with(environment=None, client_id=None, limit=100)
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_list_jobs_empty(
        self, mock_client_class, mock_check, mock_create_parser
    ):
        """Test main in list jobs mode with no jobs."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = True
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser

        mock_client = MagicMock()
        mock_client.list_jobs.return_value = []
        mock_client_class.return_value = mock_client
        
        result = main()
        
        assert result == 0
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_cancel_job_mode(
        self, mock_client_class, mock_check, mock_create_parser
    ):
        """Test main in cancel job mode."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = "job-123"
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        
        mock_client = MagicMock()
        mock_client.get_job.return_value = {
            "artist": "Artist",
            "title": "Title",
            "status": "processing"
        }
        mock_client.cancel_job.return_value = {"status": "cancelled"}
        mock_client_class.return_value = mock_client
        
        result = main()
        
        assert result == 0
        mock_client.cancel_job.assert_called_once_with("job-123")

    @patch('karaoke_gen.utils.remote_cli.JobMonitor')
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_retry_job_mode(
        self, mock_client_class, mock_check, mock_create_parser, mock_monitor_class
    ):
        """Test main in retry job mode with a failed job."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = "job-123"
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser

        mock_client = MagicMock()
        mock_client.get_job.return_value = {
            "artist": "Artist",
            "title": "Title",
            "status": "failed",
            "error_message": "Discord webhook failed"
        }
        mock_client.retry_job.return_value = {
            "status": "success",
            "retry_stage": "video_generation"
        }
        mock_client_class.return_value = mock_client

        mock_monitor = MagicMock()
        mock_monitor.monitor.return_value = 0
        mock_monitor_class.return_value = mock_monitor

        result = main()

        assert result == 0
        mock_client.retry_job.assert_called_once_with("job-123")
        mock_monitor.monitor.assert_called_once_with("job-123")

    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_retry_job_not_failed(
        self, mock_client_class, mock_check, mock_create_parser
    ):
        """Test main retry job when job is not in failed state."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = "job-123"
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser

        mock_client = MagicMock()
        mock_client.get_job.return_value = {
            "artist": "Artist",
            "title": "Title",
            "status": "complete"  # Not failed
        }
        mock_client_class.return_value = mock_client

        result = main()

        assert result == 1  # Should fail because job is not in failed state
        mock_client.retry_job.assert_not_called()

    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_retry_job_not_found(
        self, mock_client_class, mock_check, mock_create_parser
    ):
        """Test main retry job when job not found."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = "nonexistent-job"
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser

        mock_client = MagicMock()
        mock_client.get_job.side_effect = ValueError("Job not found")
        mock_client_class.return_value = mock_client

        result = main()

        assert result == 1
        mock_client.retry_job.assert_not_called()

    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_retry_job_api_error(
        self, mock_client_class, mock_check, mock_create_parser
    ):
        """Test main retry job when API returns an error."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = "job-123"
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser

        mock_client = MagicMock()
        mock_client.get_job.return_value = {
            "artist": "Artist",
            "title": "Title",
            "status": "failed",
            "error_message": "Some error"
        }
        mock_client.retry_job.side_effect = RuntimeError("Cannot retry job: No safe checkpoint found")
        mock_client_class.return_value = mock_client

        result = main()

        assert result == 1

    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_cancel_job_not_found(
        self, mock_client_class, mock_check, mock_create_parser
    ):
        """Test main cancel job when job not found."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = "nonexistent"
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        
        mock_client = MagicMock()
        mock_client.get_job.side_effect = ValueError("Job not found: nonexistent")
        mock_client_class.return_value = mock_client
        
        result = main()
        
        assert result == 1
    
    @patch('builtins.input', return_value='y')
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_delete_job_mode(
        self, mock_client_class, mock_check, mock_create_parser, mock_input
    ):
        """Test main in delete job mode with confirmation."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = "job-123"
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_args.yes = False  # Interactive mode
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        
        mock_client = MagicMock()
        mock_client.get_job.return_value = {
            "artist": "Artist",
            "title": "Title",
            "status": "complete"
        }
        mock_client.delete_job.return_value = {"status": "deleted"}
        mock_client_class.return_value = mock_client
        
        result = main()
        
        assert result == 0
        mock_client.delete_job.assert_called_once_with("job-123", delete_files=True)
    
    @patch('builtins.input', return_value='n')
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_delete_job_cancelled(
        self, mock_client_class, mock_check, mock_create_parser, mock_input
    ):
        """Test main delete job when user cancels."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = "job-123"
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_args.yes = False  # Interactive mode
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        
        mock_client = MagicMock()
        mock_client.get_job.return_value = {
            "artist": "Artist",
            "title": "Title",
            "status": "complete"
        }
        mock_client_class.return_value = mock_client
        
        result = main()
        
        assert result == 0
        mock_client.delete_job.assert_not_called()
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.RemoteKaraokeClient')
    def test_main_delete_job_non_interactive(
        self, mock_client_class, mock_check, mock_create_parser
    ):
        """Test main delete job in non-interactive mode (with -y flag)."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = "job-123"
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_args.yes = True  # Non-interactive mode
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser

        mock_client = MagicMock()
        mock_client.get_job.return_value = {
            "artist": "Artist",
            "title": "Title",
            "status": "complete"
        }
        mock_client.delete_job.return_value = {"status": "deleted"}
        mock_client_class.return_value = mock_client
        
        result = main()
        
        assert result == 0
        mock_client.delete_job.assert_called_once_with("job-123", delete_files=True)
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.get_auth_token')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('os.path.isfile')
    @patch('os.path.isdir')
    def test_main_directory_input_not_supported(
        self, mock_isdir, mock_isfile, mock_check, mock_auth, mock_create_parser
    ):
        """Test main rejects directory input."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_args.args = ["/path/to/folder"]
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        mock_auth.return_value = "token"
        
        mock_isfile.return_value = False
        mock_isdir.return_value = True
        
        result = main()
        
        assert result == 1
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.get_auth_token')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.is_url')
    @patch('karaoke_gen.utils.remote_cli.is_file')
    @patch('os.path.isfile')
    @patch('os.path.isdir')
    def test_main_audio_search_not_supported(
        self, mock_isdir, mock_isfile, mock_is_file, mock_is_url, 
        mock_check, mock_auth, mock_create_parser
    ):
        """Test main rejects audio search (artist+title without file)."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_args.args = ["Artist", "Title"]  # Audio search format (not yet supported in remote)
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        mock_auth.return_value = "token"
        
        mock_is_url.return_value = False
        mock_is_file.return_value = False
        mock_isfile.return_value = False
        mock_isdir.return_value = False
        
        result = main()
        
        assert result == 1
    
    @patch('karaoke_gen.utils.remote_cli.create_parser')
    @patch('karaoke_gen.utils.remote_cli.get_auth_token')
    @patch('karaoke_gen.utils.remote_cli.check_prerequisites')
    @patch('karaoke_gen.utils.remote_cli.is_url')
    @patch('karaoke_gen.utils.remote_cli.is_file')
    @patch('os.path.isfile')
    def test_main_missing_artist_title(
        self, mock_isfile, mock_is_file, mock_is_url, 
        mock_check, mock_auth, mock_create_parser
    ):
        """Test main rejects file without artist/title."""
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.service_url = "https://api.example.com"
        mock_args.review_ui_url = "https://review.example.com"
        mock_args.poll_interval = 5
        mock_args.output_dir = "/tmp"
        mock_args.log_level = "info"
        mock_args.finalise_only = False
        mock_args.edit_lyrics = False
        mock_args.test_email_template = False
        mock_args.resume = None
        mock_args.list_jobs = False
        mock_args.cancel = None
        mock_args.retry = None
        mock_args.delete = None
        mock_args.bulk_delete = False
        mock_args.filter_environment = None
        mock_args.filter_client_id = None
        mock_args.environment = ""
        mock_args.client_id = ""
        mock_args.args = ["/path/to/file.mp3"]  # File only, no artist/title
        mock_parser.parse_args.return_value = mock_args
        mock_create_parser.return_value = mock_parser
        mock_auth.return_value = "token"
        
        mock_is_url.return_value = False
        mock_is_file.return_value = True
        mock_isfile.return_value = True
        
        result = main()
        
        assert result == 1


class TestJobMonitorDownloads:
    """Tests for JobMonitor file download functionality."""
    
    @pytest.fixture
    def config(self):
        return Config(
            service_url="https://api.example.com",
            review_ui_url="https://review.example.com",
            poll_interval=1,
            output_dir="/tmp/output"
        )
    
    @pytest.fixture
    def logger(self):
        return logging.getLogger("test")
    
    @pytest.fixture
    def client(self, config, logger):
        return RemoteKaraokeClient(config, logger)
    
    @pytest.fixture
    def monitor(self, client, config, logger):
        return JobMonitor(client, config, logger)
    
    @patch.object(RemoteKaraokeClient, 'download_file_via_gsutil')
    @patch('pathlib.Path.mkdir')
    def test_download_outputs_with_brand_code(self, mock_mkdir, mock_download, monitor, tmp_path):
        """Test downloading outputs with brand code in folder name."""
        monitor.config.output_dir = str(tmp_path)
        mock_download.return_value = True
        
        job_data = {
            "artist": "Test Artist",
            "title": "Test Title",
            "state_data": {"brand_code": "TEST-0001"},
            "file_urls": {
                "finals": {
                    "lossless_4k_mp4": "jobs/123/final.mp4"
                }
            }
        }
        
        monitor.download_outputs("job-123", job_data)
        
        mock_download.assert_called()
    
    @patch.object(RemoteKaraokeClient, 'download_file_via_gsutil')
    @patch('pathlib.Path.mkdir')
    def test_download_outputs_without_brand_code(self, mock_mkdir, mock_download, monitor, tmp_path):
        """Test downloading outputs without brand code."""
        monitor.config.output_dir = str(tmp_path)
        mock_download.return_value = True
        
        job_data = {
            "artist": "Test Artist",
            "title": "Test Title",
            "state_data": {},
            "file_urls": {
                "finals": {
                    "lossy_720p": "jobs/123/final_720p.mp4"
                }
            }
        }
        
        monitor.download_outputs("job-123", job_data)
        
        mock_download.assert_called()
    
    @patch.object(RemoteKaraokeClient, 'download_file_via_gsutil')
    @patch('pathlib.Path.mkdir')
    def test_download_outputs_packages(self, mock_mkdir, mock_download, monitor, tmp_path):
        """Test downloading CDG/TXT packages."""
        monitor.config.output_dir = str(tmp_path)
        mock_download.return_value = True
        
        job_data = {
            "artist": "Test Artist",
            "title": "Test Title",
            "state_data": {},
            "file_urls": {
                "packages": {
                    "cdg_zip": "jobs/123/cdg.zip",
                    "txt_zip": "jobs/123/txt.zip"
                }
            }
        }
        
        monitor.download_outputs("job-123", job_data)
        
        # Should be called twice for the two packages
        assert mock_download.call_count >= 2


class TestJobMonitorHandleReview:
    """Tests for JobMonitor review handling."""
    
    @pytest.fixture
    def config(self):
        return Config(
            service_url="https://api.example.com",
            review_ui_url="https://review.example.com",
            poll_interval=0.1,  # Short for testing
            output_dir="/tmp/output"
        )
    
    @pytest.fixture
    def logger(self):
        return logging.getLogger("test")
    
    @pytest.fixture
    def client(self, config, logger):
        return RemoteKaraokeClient(config, logger)
    
    @pytest.fixture
    def monitor(self, client, config, logger):
        return JobMonitor(client, config, logger)
    
    @patch.object(JobMonitor, 'open_review_ui')
    @patch.object(RemoteKaraokeClient, 'get_job')
    def test_handle_review_waits_for_completion(self, mock_get_job, mock_open_ui, monitor):
        """Test handle_review waits until review is complete."""
        # First call returns in_review, second returns review_complete
        mock_get_job.side_effect = [
            {"status": "in_review"},
            {"status": "review_complete"}
        ]
        
        monitor.handle_review("job-123")
        
        mock_open_ui.assert_called_once_with("job-123")
        assert mock_get_job.call_count == 2


class TestSignedUrlUploadFlow:
    """Tests for the signed URL upload flow."""
    
    @pytest.fixture
    def config(self):
        return Config(
            service_url="https://api.example.com",
            review_ui_url="https://review.example.com",
            poll_interval=5,
            output_dir="/tmp/output"
        )
    
    @pytest.fixture
    def logger(self):
        return logging.getLogger("test")
    
    @pytest.fixture
    def client(self, config, logger):
        return RemoteKaraokeClient(config, logger)
    
    def test_get_content_type_audio(self, client):
        """Test content type detection for audio files."""
        assert client._get_content_type("song.flac") == "audio/flac"
        assert client._get_content_type("song.mp3") == "audio/mpeg"
        assert client._get_content_type("song.wav") == "audio/wav"
        assert client._get_content_type("song.m4a") == "audio/mp4"
        assert client._get_content_type("song.ogg") == "audio/ogg"
        assert client._get_content_type("song.aac") == "audio/aac"
    
    def test_get_content_type_images(self, client):
        """Test content type detection for image files."""
        assert client._get_content_type("bg.png") == "image/png"
        assert client._get_content_type("bg.jpg") == "image/jpeg"
        assert client._get_content_type("bg.jpeg") == "image/jpeg"
        assert client._get_content_type("bg.gif") == "image/gif"
        assert client._get_content_type("bg.webp") == "image/webp"
    
    def test_get_content_type_fonts(self, client):
        """Test content type detection for font files."""
        assert client._get_content_type("font.ttf") == "font/ttf"
        assert client._get_content_type("font.otf") == "font/otf"
        assert client._get_content_type("font.woff") == "font/woff"
        assert client._get_content_type("font.woff2") == "font/woff2"
    
    def test_get_content_type_other(self, client):
        """Test content type detection for other files."""
        assert client._get_content_type("style.json") == "application/json"
        assert client._get_content_type("lyrics.txt") == "text/plain"
        assert client._get_content_type("unknown.xyz") == "application/octet-stream"
    
    @patch('requests.put')
    def test_upload_file_to_signed_url_success(self, mock_put, client):
        """Test successful upload to signed URL."""
        mock_put.return_value = MagicMock(status_code=200)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".flac") as f:
            f.write(b"fake audio data")
            temp_path = f.name
        
        try:
            result = client._upload_file_to_signed_url(
                "https://storage.googleapis.com/signed-url",
                temp_path,
                "audio/flac"
            )
            assert result is True
            mock_put.assert_called_once()
        finally:
            os.unlink(temp_path)
    
    @patch('requests.put')
    def test_upload_file_to_signed_url_failure(self, mock_put, client):
        """Test failed upload to signed URL."""
        mock_put.return_value = MagicMock(status_code=403, text="Forbidden")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".flac") as f:
            f.write(b"fake audio data")
            temp_path = f.name
        
        try:
            result = client._upload_file_to_signed_url(
                "https://storage.googleapis.com/signed-url",
                temp_path,
                "audio/flac"
            )
            assert result is False
        finally:
            os.unlink(temp_path)
    
    @patch.object(RemoteKaraokeClient, '_upload_file_to_signed_url')
    @patch.object(RemoteKaraokeClient, '_request')
    def test_submit_job_uses_signed_url_flow(self, mock_request, mock_upload, client):
        """Test that submit_job uses the signed URL upload flow."""
        # Mock create job response
        create_response = MagicMock()
        create_response.status_code = 200
        create_response.json.return_value = {
            "status": "success",
            "job_id": "test-job-123",
            "upload_urls": [
                {
                    "file_type": "audio",
                    "gcs_path": "uploads/test-job-123/audio/test.flac",
                    "upload_url": "https://storage.googleapis.com/signed-url-1",
                    "content_type": "audio/flac"
                }
            ]
        }
        
        # Mock uploads complete response
        complete_response = MagicMock()
        complete_response.status_code = 200
        complete_response.json.return_value = {
            "status": "success",
            "job_id": "test-job-123",
            "message": "Processing started"
        }
        
        mock_request.side_effect = [create_response, complete_response]
        mock_upload.return_value = True
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".flac") as f:
            f.write(b"fake audio data")
            temp_path = f.name
        
        try:
            result = client.submit_job(
                filepath=temp_path,
                artist="Test Artist",
                title="Test Song"
            )
            
            # Verify the flow: create -> upload -> complete
            assert mock_request.call_count == 2
            
            # First call should be to create-with-upload-urls
            first_call = mock_request.call_args_list[0]
            assert first_call[0][0] == 'POST'
            assert 'create-with-upload-urls' in first_call[0][1]
            
            # Second call should be to uploads-complete
            second_call = mock_request.call_args_list[1]
            assert second_call[0][0] == 'POST'
            assert 'uploads-complete' in second_call[0][1]
            
            # Upload should have been called for the audio file
            mock_upload.assert_called_once()
            
            assert result["status"] == "success"
            assert result["job_id"] == "test-job-123"
        finally:
            os.unlink(temp_path)
    
    def test_client_has_signed_url_methods(self, client):
        """Test that RemoteKaraokeClient has the required signed URL methods."""
        assert hasattr(client, '_upload_file_to_signed_url')
        assert hasattr(client, '_get_content_type')
        assert callable(client._upload_file_to_signed_url)
        assert callable(client._get_content_type)


class TestJobMonitorDownloadProgress:
    """Tests for JobMonitor download progress display."""
    
    @pytest.fixture
    def config(self):
        return Config(
            service_url="https://api.example.com",
            review_ui_url="https://review.example.com",
            poll_interval=1,
            output_dir="/tmp/output"
        )
    
    @pytest.fixture
    def logger(self):
        return logging.getLogger("test")
    
    @pytest.fixture
    def monitor(self, config, logger):
        client = MagicMock()
        return JobMonitor(client, config, logger)
    
    def test_show_download_progress_youtube(self, monitor, caplog):
        """Test that YouTube downloads show simple message."""
        job_data = {
            'state_data': {
                'selected_audio_provider': 'YouTube'
            }
        }
        
        with caplog.at_level(logging.INFO):
            monitor._show_download_progress(job_data)
        
        assert "Downloading from YouTube" in caplog.text
    
    @patch('karaoke_gen.utils.remote_cli.requests.get')
    def test_show_download_progress_torrent_with_active_torrents(self, mock_get, monitor, caplog):
        """Test that active torrents show progress details."""
        job_data = {
            'state_data': {
                'selected_audio_provider': 'RED'
            }
        }
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'dependencies': {
                'transmission': {
                    'available': True,
                    'torrents': [{
                        'progress': 45.5,
                        'peers': 3,
                        'download_speed': 1250.0,
                        'stalled': False
                    }]
                }
            }
        }
        mock_get.return_value = mock_response
        
        with caplog.at_level(logging.INFO):
            monitor._show_download_progress(job_data)
        
        assert "Downloading from RED" in caplog.text
        assert "45.5%" in caplog.text
        assert "1250.0 KB/s" in caplog.text
        assert "3 peers" in caplog.text
    
    @patch('karaoke_gen.utils.remote_cli.requests.get')
    def test_show_download_progress_torrent_stalled(self, mock_get, monitor, caplog):
        """Test that stalled torrents show warning."""
        job_data = {
            'state_data': {
                'selected_audio_provider': 'OPS'
            }
        }
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'dependencies': {
                'transmission': {
                    'available': True,
                    'torrents': [{
                        'progress': 0.0,
                        'peers': 0,
                        'download_speed': 0,
                        'stalled': True
                    }]
                }
            }
        }
        mock_get.return_value = mock_response
        
        with caplog.at_level(logging.INFO):
            monitor._show_download_progress(job_data)
        
        assert "STALLED" in caplog.text
        assert "no peers" in caplog.text
    
    @patch('karaoke_gen.utils.remote_cli.requests.get')
    def test_show_download_progress_no_torrents(self, mock_get, monitor, caplog):
        """Test message when no active torrents."""
        job_data = {
            'state_data': {
                'selected_audio_provider': 'RED'
            }
        }
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'dependencies': {
                'transmission': {
                    'available': True,
                    'torrents': []
                }
            }
        }
        mock_get.return_value = mock_response
        
        with caplog.at_level(logging.INFO):
            monitor._show_download_progress(job_data)
        
        assert "Starting download" in caplog.text
    
    @patch('karaoke_gen.utils.remote_cli.requests.get')
    def test_show_download_progress_transmission_unavailable(self, mock_get, monitor, caplog):
        """Test warning when Transmission not available."""
        job_data = {
            'state_data': {
                'selected_audio_provider': 'RED'
            }
        }
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'dependencies': {
                'transmission': {
                    'available': False
                }
            }
        }
        mock_get.return_value = mock_response
        
        with caplog.at_level(logging.INFO):
            monitor._show_download_progress(job_data)
        
        assert "not available" in caplog.text
    
    @patch('karaoke_gen.utils.remote_cli.requests.get')
    def test_show_download_progress_request_error(self, mock_get, monitor, caplog):
        """Test fallback message on request error."""
        job_data = {
            'state_data': {
                'selected_audio_provider': 'RED'
            }
        }
        
        mock_get.side_effect = Exception("Connection error")
        
        with caplog.at_level(logging.INFO):
            monitor._show_download_progress(job_data)
        
        assert "Downloading audio" in caplog.text
    
    @patch('karaoke_gen.utils.remote_cli.requests.get')
    def test_show_download_progress_http_error(self, mock_get, monitor, caplog):
        """Test fallback on non-200 HTTP response."""
        job_data = {
            'state_data': {
                'selected_audio_provider': 'RED'
            }
        }
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response
        
        with caplog.at_level(logging.INFO):
            monitor._show_download_progress(job_data)
        
        assert "Downloading from RED" in caplog.text
    
    def test_show_download_progress_missing_provider(self, monitor, caplog):
        """Test handling of missing provider in state_data."""
        job_data = {
            'state_data': {}
        }
        
        with caplog.at_level(logging.INFO):
            monitor._show_download_progress(job_data)
        
        # Should use 'unknown' as default
        assert "unknown" in caplog.text.lower() or "Downloading" in caplog.text


class TestCategorizedAudioDisplay:
    """Tests for categorized audio search display (flacfetch PR #4)."""
    
    @pytest.fixture
    def monitor(self):
        """Create a JobMonitor instance for testing."""
        config = Config(
            service_url="http://localhost:8000",
            review_ui_url="http://localhost:3000",
            poll_interval=5,
            output_dir="/tmp/output",
            non_interactive=False,
        )
        client = MagicMock()
        logger = logging.getLogger("test")
        return JobMonitor(client=client, config=config, logger=logger)
    
    def test_convert_to_release_objects_empty_list(self, monitor):
        """Test conversion with empty list."""
        result = monitor._convert_to_release_objects([])
        assert result == []
    
    def test_convert_to_release_objects_valid_dicts(self, monitor):
        """Test conversion with valid Release-compatible dicts."""
        release_dicts = [
            {
                "title": "Test Song",
                "artist": "Test Artist",
                "source_name": "RED",
                "quality": {"format": "FLAC", "media": "CD"},
            },
            {
                "title": "Another Song",
                "artist": "Another Artist",
                "source_name": "YouTube",
                "quality": {"format": "AAC", "media": "WEB"},
            },
        ]
        
        result = monitor._convert_to_release_objects(release_dicts)
        
        # Should successfully convert both
        assert len(result) == 2
        assert result[0].title == "Test Song"
        assert result[1].title == "Another Song"
    
    def test_convert_to_release_objects_handles_invalid(self, monitor):
        """Test conversion skips invalid dicts gracefully."""
        release_dicts = [
            {
                "title": "Valid Song",
                "artist": "Valid Artist",
                "source_name": "RED",
                "quality": {"format": "FLAC", "media": "CD"},
            },
            # This dict is missing required fields - may cause error
            {},
        ]
        
        result = monitor._convert_to_release_objects(release_dicts)
        
        # Should have at least one result (the valid one)
        # Invalid ones should be skipped without raising
        assert len(result) >= 1
    
    def test_find_original_index_by_identity(self, monitor):
        """Test finding index by object identity."""
        from flacfetch import Release
        
        releases = [
            Release.from_dict({
                "title": "Song 1", "artist": "Artist", "source_name": "RED",
                "quality": {"format": "FLAC", "media": "CD"}
            }),
            Release.from_dict({
                "title": "Song 2", "artist": "Artist", "source_name": "OPS",
                "quality": {"format": "FLAC", "media": "CD"}
            }),
        ]
        original_results = [
            {"title": "Song 1", "artist": "Artist", "provider": "RED"},
            {"title": "Song 2", "artist": "Artist", "provider": "OPS"},
        ]
        
        # Find by object identity
        result = monitor._find_original_index(releases[1], original_results, releases)
        assert result == 1
    
    def test_find_original_index_by_url(self, monitor):
        """Test finding index by download_url match."""
        from flacfetch import Release
        
        release = Release.from_dict({
            "title": "Song", "artist": "Artist", "source_name": "RED",
            "quality": {"format": "FLAC", "media": "CD"},
            "download_url": "https://example.com/download/123"
        })
        original_results = [
            {"title": "Wrong", "artist": "Artist", "provider": "RED", "url": "https://other.com"},
            {"title": "Song", "artist": "Artist", "provider": "RED", "url": "https://example.com/download/123"},
        ]
        
        result = monitor._find_original_index(release, original_results, [])
        assert result == 1
    
    def test_find_original_index_by_hash(self, monitor):
        """Test finding index by info_hash match."""
        from flacfetch import Release
        
        release = Release.from_dict({
            "title": "Song", "artist": "Artist", "source_name": "RED",
            "quality": {"format": "FLAC", "media": "CD"},
            "info_hash": "abc123hash"
        })
        original_results = [
            {"title": "Wrong", "artist": "Artist", "provider": "RED", "source_id": "other"},
            {"title": "Song", "artist": "Artist", "provider": "RED", "source_id": "abc123hash"},
        ]
        
        result = monitor._find_original_index(release, original_results, [])
        assert result == 1
    
    def test_find_original_index_by_metadata(self, monitor):
        """Test finding index by title+artist+provider match."""
        from flacfetch import Release
        
        release = Release.from_dict({
            "title": "Unique Title", "artist": "Unique Artist", "source_name": "OPS",
            "quality": {"format": "FLAC", "media": "CD"}
        })
        original_results = [
            {"title": "Other", "artist": "Other", "provider": "RED"},
            {"title": "Unique Title", "artist": "Unique Artist", "provider": "OPS"},
        ]
        
        result = monitor._find_original_index(release, original_results, [])
        assert result == 1
    
    def test_find_original_index_not_found(self, monitor):
        """Test returns -1 when no match found."""
        from flacfetch import Release
        
        release = Release.from_dict({
            "title": "No Match", "artist": "No Artist", "source_name": "Unknown",
            "quality": {"format": "MP3", "media": "WEB"}
        })
        original_results = [
            {"title": "Other", "artist": "Other", "provider": "RED"},
        ]
        
        result = monitor._find_original_index(release, original_results, [])
        assert result == -1
    
    def test_convert_api_result_to_release_dict(self, monitor):
        """Test API result conversion to Release-compatible dict."""
        api_result = {
            "title": "Test Song",
            "artist": "Test Artist", 
            "provider": "RED",
            "url": "https://download.url",
            "source_id": "hash123",
            "seeders": 50,
            "quality": "FLAC 16bit CD",
            "quality_data": {"format": "FLAC", "bit_depth": 16, "media": "CD"},
            "is_lossless": True,
            "year": 2020,
        }
        
        result = monitor._convert_api_result_to_release_dict(api_result)
        
        assert result["title"] == "Test Song"
        assert result["artist"] == "Test Artist"
        assert result["source_name"] == "RED"
        assert result["download_url"] == "https://download.url"
        assert result["seeders"] == 50
        assert result["is_lossless"] == True


class TestSearchAudioWithStyleFiles:
    """Tests for search_audio method with style file upload support."""
    
    @pytest.fixture
    def config(self):
        """Create test config."""
        return Config(
            service_url="http://localhost:8000",
            review_ui_url="http://localhost:3000",
            poll_interval=5,
            output_dir="/tmp/output",
            non_interactive=False,
            auth_token="test-token"
        )
    
    @pytest.fixture
    def logger(self):
        """Create test logger."""
        return logging.getLogger("test-search-audio")
    
    @pytest.fixture
    def client(self, config, logger):
        """Create a RemoteKaraokeClient instance for testing."""
        return RemoteKaraokeClient(config, logger)
    
    @patch('requests.Session.request')
    def test_search_audio_without_style_files(self, mock_request, client):
        """Test search_audio without style files."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "awaiting_selection",
            "job_id": "test-job-123",
            "message": "Found 5 audio sources",
            "results_count": 5,
            "server_version": "0.75.47",
        }
        mock_request.return_value = mock_response
        
        result = client.search_audio("Test Artist", "Test Title")
        
        assert result["job_id"] == "test-job-123"
        assert result["results_count"] == 5
        
        # Verify request was made without style_files
        call_args = mock_request.call_args
        request_json = call_args.kwargs.get('json', {})
        assert "style_files" not in request_json
    
    @patch('requests.put')
    @patch('requests.Session.request')
    @patch('os.path.isfile')
    def test_search_audio_with_style_params(self, mock_isfile, mock_request, mock_put, client):
        """Test search_audio with style_params.json file."""
        # Mock file checks
        mock_isfile.return_value = True
        
        # Mock API response with style upload URLs
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "awaiting_selection",
            "job_id": "test-job-123",
            "message": "Found 5 audio sources",
            "results_count": 5,
            "server_version": "0.75.47",
            "style_upload_urls": [
                {
                    "file_type": "style_params",
                    "gcs_path": "uploads/test-job-123/style/style_params.json",
                    "upload_url": "https://storage.googleapis.com/signed-url-1"
                }
            ]
        }
        mock_request.return_value = mock_response
        
        # Mock successful upload
        mock_put_response = MagicMock()
        mock_put_response.status_code = 200
        mock_put.return_value = mock_put_response
        
        # Create temp style params file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"intro": {}, "karaoke": {}, "end": {}}, f)
            style_file = f.name
        
        try:
            # Mock open for reading the style file for upload
            with patch('builtins.open', mock_open(read_data=b'{"test": "data"}')):
                result = client.search_audio(
                    "Test Artist", 
                    "Test Title",
                    style_params_path=style_file
                )
            
            assert result["job_id"] == "test-job-123"
            
            # Verify style_files was included in request
            call_args = mock_request.call_args
            request_json = call_args.kwargs.get('json', {})
            assert "style_files" in request_json
            assert len(request_json["style_files"]) >= 1
            assert request_json["style_files"][0]["file_type"] == "style_params"
            
            # Verify upload was attempted
            mock_put.assert_called_once()
        finally:
            os.unlink(style_file)
    
    @patch('requests.Session.request')
    def test_search_audio_no_results(self, mock_request, client):
        """Test search_audio when no results are found."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"error": "no_results", "message": "No audio found"}
        mock_response.text = "Not found"
        mock_request.return_value = mock_response
        
        with pytest.raises(ValueError, match="No audio sources found"):
            client.search_audio("Unknown Artist", "Unknown Title")
    
    @patch('requests.Session.request')
    def test_search_audio_server_error(self, mock_request, client):
        """Test search_audio when server returns error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "Internal error"}
        mock_response.text = "Internal Server Error"
        mock_request.return_value = mock_response
        
        with pytest.raises(RuntimeError, match="Error searching for audio"):
            client.search_audio("Test Artist", "Test Title")
    
    @patch('requests.put')
    @patch('requests.Session.request')
    @patch('os.path.isfile')
    def test_search_audio_upload_failure_continues(self, mock_isfile, mock_request, mock_put, client):
        """Test that search_audio continues even if style upload fails."""
        mock_isfile.return_value = True
        
        # Mock API response with style upload URLs
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "awaiting_selection",
            "job_id": "test-job-123",
            "results_count": 5,
            "server_version": "0.75.47",
            "style_upload_urls": [
                {
                    "file_type": "style_params",
                    "gcs_path": "uploads/test-job-123/style/style_params.json",
                    "upload_url": "https://storage.googleapis.com/signed-url-1"
                }
            ]
        }
        mock_request.return_value = mock_response
        
        # Mock failed upload (403)
        mock_put_response = MagicMock()
        mock_put_response.status_code = 403
        mock_put.return_value = mock_put_response
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({}, f)
            style_file = f.name
        
        try:
            with patch('builtins.open', mock_open(read_data=b'{}')):
                # Should not raise, even if upload fails
                result = client.search_audio(
                    "Test Artist", 
                    "Test Title",
                    style_params_path=style_file
                )
            
            assert result["job_id"] == "test-job-123"
        finally:
            os.unlink(style_file)
    
    @patch('requests.Session.request')
    @patch('os.path.isfile')
    def test_search_audio_style_file_not_found(self, mock_isfile, mock_request, client):
        """Test search_audio when style_params file doesn't exist."""
        # First call (for style_params_path check) returns False
        mock_isfile.return_value = False
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "awaiting_selection",
            "job_id": "test-job-123",
            "results_count": 5,
            "server_version": "0.75.47",
        }
        mock_request.return_value = mock_response
        
        # Should succeed but without style files
        result = client.search_audio(
            "Test Artist", 
            "Test Title",
            style_params_path="/nonexistent/style.json"
        )
        
        assert result["job_id"] == "test-job-123"
        
        # Verify no style_files in request
        call_args = mock_request.call_args
        request_json = call_args.kwargs.get('json', {})
        assert "style_files" not in request_json


class TestConfigClass:
    """Test Config dataclass functionality."""
    
    def test_config_with_all_fields(self):
        """Test Config creation with all fields."""
        config = Config(
            service_url='https://api.example.com',
            review_ui_url='https://review.example.com',
            poll_interval=10,
            output_dir='/tmp/output',
            auth_token='test-token',
            environment='prod'
        )
        
        assert config.service_url == 'https://api.example.com'
        assert config.review_ui_url == 'https://review.example.com'
        assert config.poll_interval == 10
        assert config.output_dir == '/tmp/output'
        assert config.auth_token == 'test-token'
        assert config.environment == 'prod'
    
    def test_config_with_minimal_fields(self):
        """Test Config creation with minimal fields."""
        config = Config(
            service_url='http://localhost:8000',
            review_ui_url='http://localhost:3000',
            poll_interval=5,
            output_dir='/tmp'
        )
        
        assert config.service_url == 'http://localhost:8000'
        assert config.auth_token is None
        # environment defaults to empty string, not None
        assert config.environment == ''
    
    def test_config_with_client_id(self):
        """Test Config creation with client_id."""
        config = Config(
            service_url='http://localhost:8000',
            review_ui_url='http://localhost:3000',
            poll_interval=5,
            output_dir='/tmp',
            client_id='test-client'
        )
        
        assert config.client_id == 'test-client'
    
    def test_config_non_interactive_mode(self):
        """Test Config with non_interactive mode enabled."""
        config = Config(
            service_url='http://localhost:8000',
            review_ui_url='http://localhost:3000',
            poll_interval=5,
            output_dir='/tmp',
            non_interactive=True
        )
        
        assert config.non_interactive is True


class TestRemoteClientContentType:
    """Test content type detection edge cases."""
    
    @pytest.fixture
    def client(self, config):
        """Create a RemoteKaraokeClient instance for testing."""
        logger = logging.getLogger("test_logger")
        return RemoteKaraokeClient(config, logger)
    
    @pytest.fixture
    def config(self):
        """Provide a valid Config instance."""
        return Config(
            service_url='https://api.nomadkaraoke.com',
            review_ui_url='https://gen.nomadkaraoke.com',
            poll_interval=5,
            output_dir='/tmp/output'
        )
    
    def test_get_content_type_for_wav(self, client):
        """Test content type detection for WAV files."""
        assert client._get_content_type('/path/to/audio.wav') == 'audio/wav'
    
    def test_get_content_type_for_gif(self, client):
        """Test content type detection for GIF files."""
        assert client._get_content_type('/path/to/image.gif') == 'image/gif'
    
    def test_get_content_type_for_webp(self, client):
        """Test content type detection for WebP files."""
        assert client._get_content_type('/path/to/image.webp') == 'image/webp'
    
    def test_get_content_type_for_unknown(self, client):
        """Test content type detection for unknown extensions."""
        assert client._get_content_type('/path/to/file.xyz') == 'application/octet-stream'
    
    def test_get_content_type_for_hidden_file(self, client):
        """Test content type detection for hidden files (no extension)."""
        # Files like '.gitignore' have no extension
        assert client._get_content_type('/path/to/.hidden') == 'application/octet-stream'
    
    def test_get_content_type_for_ogg(self, client):
        """Test content type detection for OGG audio files."""
        assert client._get_content_type('/path/to/audio.ogg') == 'audio/ogg'
    
    def test_get_content_type_for_aac(self, client):
        """Test content type detection for AAC audio files."""
        assert client._get_content_type('/path/to/audio.aac') == 'audio/aac'
    
    def test_get_content_type_for_m4a(self, client):
        """Test content type detection for M4A audio files."""
        assert client._get_content_type('/path/to/audio.m4a') == 'audio/mp4'
    
    def test_get_content_type_case_insensitive(self, client):
        """Test content type detection is case-insensitive for extensions."""
        # Extension lookup should be case-insensitive
        assert client._get_content_type('/path/to/image.PNG') == 'image/png'
        assert client._get_content_type('/path/to/audio.MP3') == 'audio/mpeg'
        assert client._get_content_type('/path/to/audio.FLAC') == 'audio/flac'


class TestRemoteClientSessionSetup:
    """Test session setup and configuration."""
    
    @pytest.fixture
    def config_with_auth(self):
        """Provide a Config instance with auth token."""
        return Config(
            service_url='https://api.nomadkaraoke.com',
            review_ui_url='https://gen.nomadkaraoke.com',
            poll_interval=5,
            output_dir='/tmp/output',
            auth_token='test-token-123',
            environment='test',
            client_id='test-client-001'
        )
    
    def test_client_sets_auth_header(self, config_with_auth):
        """Test that client sets Authorization header when auth_token is provided."""
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config_with_auth, logger)
        
        assert 'Authorization' in client.session.headers
        assert client.session.headers['Authorization'] == 'Bearer test-token-123'
    
    def test_client_sets_environment_header(self, config_with_auth):
        """Test that client sets X-Environment header when environment is provided."""
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config_with_auth, logger)
        
        assert 'X-Environment' in client.session.headers
        assert client.session.headers['X-Environment'] == 'test'
    
    def test_client_sets_client_id_header(self, config_with_auth):
        """Test that client sets X-Client-ID header when client_id is provided."""
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config_with_auth, logger)
        
        assert 'X-Client-ID' in client.session.headers
        assert client.session.headers['X-Client-ID'] == 'test-client-001'
    
    def test_client_without_auth(self):
        """Test that client works without auth token."""
        config = Config(
            service_url='http://localhost:8000',
            review_ui_url='http://localhost:3000',
            poll_interval=5,
            output_dir='/tmp'
        )
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config, logger)
        
        # Should not have Authorization header
        assert 'Authorization' not in client.session.headers
    
    def test_client_stores_config_and_logger(self, config_with_auth):
        """Test that client correctly stores config and logger references."""
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config_with_auth, logger)
        
        assert client.config == config_with_auth
        assert client.logger == logger
    
    def test_client_has_session_with_timeout(self, config_with_auth):
        """Test that client creates a session object."""
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config_with_auth, logger)
        
        # Client should have a session attribute
        assert hasattr(client, 'session')
        assert client.session is not None
    
    def test_empty_environment_header_not_set(self):
        """Test that empty environment string doesn't set header."""
        config = Config(
            service_url='http://localhost:8000',
            review_ui_url='http://localhost:3000',
            poll_interval=5,
            output_dir='/tmp',
            environment=''  # Empty string
        )
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config, logger)
        
        # Empty environment should not set the header
        assert 'X-Environment' not in client.session.headers
    
    def test_empty_client_id_header_not_set(self):
        """Test that empty client_id string doesn't set header."""
        config = Config(
            service_url='http://localhost:8000',
            review_ui_url='http://localhost:3000',
            poll_interval=5,
            output_dir='/tmp',
            client_id=''  # Empty string
        )
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config, logger)
        
        # Empty client_id should not set the header
        assert 'X-Client-ID' not in client.session.headers
    
    def test_client_sets_user_agent(self):
        """Test that client sets User-Agent header with version."""
        config = Config(
            service_url='http://localhost:8000',
            review_ui_url='http://localhost:3000',
            poll_interval=5,
            output_dir='/tmp'
        )
        logger = logging.getLogger("test_logger")
        client = RemoteKaraokeClient(config, logger)
        
        # User-Agent should be set
        assert 'User-Agent' in client.session.headers
        assert 'karaoke-gen-remote/' in client.session.headers['User-Agent']
