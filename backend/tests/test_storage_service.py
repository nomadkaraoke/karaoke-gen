"""
Tests for storage_service.py - Google Cloud Storage operations.

These tests mock the GCS client to verify:
- Upload operations (file, fileobj, JSON)
- Download operations (file, JSON)
- Signed URL generation
- File operations (delete, list, exists)
"""
import json
import pytest
from unittest.mock import Mock, MagicMock, patch
from io import BytesIO

from backend.services.storage_service import StorageService


class TestStorageServiceInit:
    """Test StorageService initialization."""
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_init_creates_client_and_bucket(self, mock_settings, mock_client_class):
        """Test initialization creates GCS client and gets bucket."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_client = Mock()
        mock_bucket = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        
        mock_client_class.assert_called_once_with(project="test-project")
        mock_client.bucket.assert_called_once_with("test-bucket")
        assert service.client == mock_client
        assert service.bucket == mock_bucket


class TestStorageServiceCopy:
    """Test server-side copy (used to stage the tenant instrumental for the GCE encoder)."""

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_copy_blob(self, mock_settings, mock_client_class):
        mock_settings.gcs_bucket_name = "test-bucket"
        mock_src_blob = Mock()
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_src_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client

        service = StorageService()
        result = service.copy_blob(
            "uploads/job123/audio/existing_instrumental.mp3",
            "jobs/job123/existing_instrumental.mp3",
        )

        mock_bucket.blob.assert_called_once_with("uploads/job123/audio/existing_instrumental.mp3")
        mock_bucket.copy_blob.assert_called_once_with(
            mock_src_blob, mock_bucket, "jobs/job123/existing_instrumental.mp3"
        )
        assert result == "jobs/job123/existing_instrumental.mp3"


class TestStorageServiceUpload:
    """Test upload operations."""

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_upload_file(self, mock_settings, mock_client_class):
        """Test uploading a file to GCS."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        result = service.upload_file("/local/path/file.flac", "uploads/job123/file.flac")
        
        mock_bucket.blob.assert_called_once_with("uploads/job123/file.flac")
        mock_blob.upload_from_filename.assert_called_once_with("/local/path/file.flac")
        assert result == "uploads/job123/file.flac"
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_upload_file_raises_on_error(self, mock_settings, mock_client_class):
        """Test upload_file raises exception on GCS error."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_blob.upload_from_filename.side_effect = Exception("GCS error")
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        
        with pytest.raises(Exception) as exc_info:
            service.upload_file("/local/path/file.flac", "dest/file.flac")
        
        assert "GCS error" in str(exc_info.value)
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_upload_fileobj(self, mock_settings, mock_client_class):
        """Test uploading a file object to GCS."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        file_obj = BytesIO(b"test content")
        
        result = service.upload_fileobj(file_obj, "uploads/test.txt", content_type="text/plain")
        
        mock_bucket.blob.assert_called_once_with("uploads/test.txt")
        assert mock_blob.content_type == "text/plain"
        mock_blob.upload_from_file.assert_called_once_with(file_obj, rewind=True)
        assert result == "uploads/test.txt"
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_upload_json(self, mock_settings, mock_client_class):
        """Test uploading JSON data to GCS."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        data = {"key": "value", "nested": {"data": True}}
        
        result = service.upload_json("data/config.json", data)
        
        mock_bucket.blob.assert_called_once_with("data/config.json")
        assert mock_blob.content_type == "application/json"
        # Check the uploaded content
        call_args = mock_blob.upload_from_string.call_args
        uploaded_content = call_args[0][0]
        assert json.loads(uploaded_content) == data
        assert result == "data/config.json"


class TestStorageServiceDownload:
    """Test download operations."""
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_download_file(self, mock_settings, mock_client_class):
        """Test downloading a file from GCS."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        result = service.download_file("uploads/job123/file.flac", "/local/path/file.flac")
        
        mock_bucket.blob.assert_called_once_with("uploads/job123/file.flac")
        mock_blob.download_to_filename.assert_called_once_with("/local/path/file.flac")
        assert result == "/local/path/file.flac"
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_download_file_raises_on_error(self, mock_settings, mock_client_class):
        """Test download_file raises exception on GCS error."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_blob.download_to_filename.side_effect = Exception("File not found")
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        
        with pytest.raises(Exception) as exc_info:
            service.download_file("missing/file.flac", "/local/path")

        assert "File not found" in str(exc_info.value)

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_download_file_missing_reraises_notfound(self, mock_settings, mock_client_class):
        """A 404 from GCS re-raises NotFound (so callers can map it to HTTP 404)
        and is logged at warning, not error, to avoid tripping the error-monitor."""
        from google.cloud.exceptions import NotFound

        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"

        mock_blob = Mock()
        mock_blob.download_to_filename.side_effect = NotFound("No such object")
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client

        service = StorageService()
        with patch("backend.services.storage_service.logger") as mock_logger:
            with pytest.raises(NotFound):
                service.download_file("jobs/abc/finals/lossy_4k_mp4.mp4", "/local/path")
            mock_logger.error.assert_not_called()
            mock_logger.warning.assert_called_once()

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_download_json(self, mock_settings, mock_client_class):
        """Test downloading and parsing JSON from GCS."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_blob.download_as_text.return_value = '{"key": "value", "count": 42}'
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        result = service.download_json("data/config.json")
        
        assert result == {"key": "value", "count": 42}


class TestStorageServiceDelete:
    """Test delete operations."""
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_delete_file(self, mock_settings, mock_client_class):
        """Test deleting a single file from GCS."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        result = service.delete_file("uploads/job123/file.flac")

        mock_bucket.blob.assert_called_once_with("uploads/job123/file.flac")
        mock_blob.delete.assert_called_once()
        assert result is True

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_delete_file_missing_raises_by_default(self, mock_settings, mock_client_class):
        """A 404 on delete raises when ignore_missing is not set (default)."""
        from google.cloud.exceptions import NotFound

        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"

        mock_blob = Mock()
        mock_blob.delete.side_effect = NotFound("No such object")
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client

        service = StorageService()
        with pytest.raises(NotFound):
            service.delete_file("jobs/abc/screens/title.mov")

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_delete_file_missing_ignored(self, mock_settings, mock_client_class):
        """With ignore_missing=True, a 404 is a no-op returning False (no raise)."""
        from google.cloud.exceptions import NotFound

        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"

        mock_blob = Mock()
        mock_blob.delete.side_effect = NotFound("No such object")
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client

        service = StorageService()
        result = service.delete_file("jobs/abc/screens/title.mov", ignore_missing=True)
        assert result is False

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_delete_folder(self, mock_settings, mock_client_class):
        """Test deleting all files with a prefix (folder)."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        # Create mock blobs
        mock_blob1 = Mock()
        mock_blob1.name = "uploads/job123/file1.flac"
        mock_blob2 = Mock()
        mock_blob2.name = "uploads/job123/file2.flac"
        mock_blob3 = Mock()
        mock_blob3.name = "uploads/job123/subdir/file3.flac"
        
        mock_bucket = Mock()
        mock_bucket.list_blobs.return_value = [mock_blob1, mock_blob2, mock_blob3]
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        count = service.delete_folder("uploads/job123/")
        
        mock_bucket.list_blobs.assert_called_once_with(prefix="uploads/job123/")
        mock_blob1.delete.assert_called_once()
        mock_blob2.delete.assert_called_once()
        mock_blob3.delete.assert_called_once()
        assert count == 3
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_delete_folder_handles_errors_gracefully(self, mock_settings, mock_client_class):
        """Test delete_folder continues even if some deletes fail."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob1 = Mock()
        mock_blob1.name = "file1.flac"
        mock_blob2 = Mock()
        mock_blob2.name = "file2.flac"
        mock_blob2.delete.side_effect = Exception("Permission denied")
        mock_blob3 = Mock()
        mock_blob3.name = "file3.flac"
        
        mock_bucket = Mock()
        mock_bucket.list_blobs.return_value = [mock_blob1, mock_blob2, mock_blob3]
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        count = service.delete_folder("uploads/")
        
        # Should have deleted 2 files (blob2 failed)
        assert count == 2


class TestStorageServiceFileOperations:
    """Test file listing and existence checks."""
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_list_files(self, mock_settings, mock_client_class):
        """Test listing files with a prefix."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob1 = Mock()
        mock_blob1.name = "uploads/job123/audio.flac"
        mock_blob2 = Mock()
        mock_blob2.name = "uploads/job123/lyrics.txt"
        
        mock_bucket = Mock()
        mock_bucket.list_blobs.return_value = [mock_blob1, mock_blob2]
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        files = service.list_files("uploads/job123/")
        
        assert files == ["uploads/job123/audio.flac", "uploads/job123/lyrics.txt"]
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_file_exists_returns_true(self, mock_settings, mock_client_class):
        """Test file_exists returns True for existing file."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_blob.exists.return_value = True
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        result = service.file_exists("uploads/job123/file.flac")
        
        assert result is True
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_file_exists_returns_false(self, mock_settings, mock_client_class):
        """Test file_exists returns False for missing file."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_blob.exists.return_value = False
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        service = StorageService()
        result = service.file_exists("missing/file.flac")
        
        assert result is False


class TestStorageServiceSignedUrls:
    """Test signed URL generation."""

    @pytest.fixture(autouse=True)
    def _no_emulator_env(self, monkeypatch):
        # Other test modules (e.g. test_emulator_integration) export
        # STORAGE_EMULATOR_HOST at import time, which would divert these tests
        # into the emulator URL fallback instead of real signing.
        monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_generate_signed_url(self, mock_settings, mock_client_class):
        """Test generating a signed download URL."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/signed-url"
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        # Mock google.auth.default to return credentials without service_account_email
        with patch("google.auth.default") as mock_auth_default:
            mock_credentials = Mock(spec=[])  # No service_account_email attr
            mock_auth_default.return_value = (mock_credentials, "test-project")
            
            service = StorageService()
            url = service.generate_signed_url("uploads/file.flac", expiration_minutes=30)
        
        assert url == "https://storage.googleapis.com/signed-url"
        mock_blob.generate_signed_url.assert_called_once()
        call_kwargs = mock_blob.generate_signed_url.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["version"] == "v4"
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_generate_signed_upload_url(self, mock_settings, mock_client_class):
        """Test generating a signed upload URL."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/signed-upload-url"
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        with patch("google.auth.default") as mock_auth_default:
            mock_credentials = Mock(spec=[])
            mock_auth_default.return_value = (mock_credentials, "test-project")
            
            service = StorageService()
            url = service.generate_signed_upload_url(
                "uploads/file.flac",
                content_type="audio/flac",
                expiration_minutes=15
            )
        
        assert url == "https://storage.googleapis.com/signed-upload-url"
        call_kwargs = mock_blob.generate_signed_url.call_args.kwargs
        assert call_kwargs["method"] == "PUT"
        assert call_kwargs["headers"] == {"Content-Type": "audio/flac"}
    
    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_signed_url_with_service_account(self, mock_settings, mock_client_class):
        """Test signed URL generation with service account credentials."""
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        
        mock_blob = Mock()
        mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/signed-url"
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket
        mock_client_class.return_value = mock_client
        
        with patch("google.auth.default") as mock_auth_default:
            with patch("google.auth.transport.requests.Request") as mock_request:
                # Mock credentials with service_account_email
                mock_credentials = Mock()
                mock_credentials.service_account_email = "sa@project.iam.gserviceaccount.com"
                mock_credentials.token = "access-token-123"
                mock_auth_default.return_value = (mock_credentials, "test-project")
                
                service = StorageService()
                url = service.generate_signed_url("uploads/file.flac")
        
        # Should have refreshed credentials and used IAM signing
        mock_credentials.refresh.assert_called_once()
        call_kwargs = mock_blob.generate_signed_url.call_args.kwargs
        assert call_kwargs["service_account_email"] == "sa@project.iam.gserviceaccount.com"
        assert call_kwargs["access_token"] == "access-token-123"



class TestStorageServiceEmulatorUrls:
    """Test emulator URL fallback when STORAGE_EMULATOR_HOST is set (local dev)."""

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_get_url_uses_emulator_download_path(self, mock_settings, mock_client_class, monkeypatch):
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        mock_bucket = Mock()
        mock_bucket.name = "test-bucket"
        mock_client_class.return_value.bucket.return_value = mock_bucket
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:4443")

        from backend.services.storage_service import StorageService
        url = StorageService().generate_signed_url("jobs/abc/review-audio/song.ogg")

        assert url == (
            "http://127.0.0.1:4443/download/storage/v1/b/test-bucket/o/"
            "jobs%2Fabc%2Freview-audio%2Fsong.ogg?alt=media"
        )

    @patch("backend.services.storage_service.storage.Client")
    @patch("backend.services.storage_service.settings")
    def test_put_url_uses_emulator_signed_style_path(self, mock_settings, mock_client_class, monkeypatch):
        mock_settings.google_cloud_project = "test-project"
        mock_settings.gcs_bucket_name = "test-bucket"
        mock_bucket = Mock()
        mock_bucket.name = "test-bucket"
        mock_client_class.return_value.bucket.return_value = mock_bucket
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:4443")

        from backend.services.storage_service import StorageService
        url = StorageService().generate_signed_upload_url("uploads/x/file.bin")

        # fake-gcs-server accepts PUTs with (unvalidated) X-Goog signature params
        assert url.startswith("http://127.0.0.1:4443/test-bucket/uploads/x/file.bin?")
        assert "X-Goog-Signature=" in url
