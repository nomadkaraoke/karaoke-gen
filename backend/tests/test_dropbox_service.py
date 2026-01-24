"""
Tests for dropbox_service.py - Dropbox file operations.

These tests mock the Dropbox SDK and Secret Manager to verify:
- Credential loading from Secret Manager
- Folder listing and brand code calculation
- File and folder uploads
- Shared link creation
"""
import json
import os
import pytest
from unittest.mock import Mock, MagicMock, patch


class TestDropboxServiceInit:
    """Test DropboxService initialization."""
    
    def test_init_creates_service(self):
        """Test initialization creates service with no client."""
        from backend.services.dropbox_service import DropboxService
        
        service = DropboxService()
        
        assert service._client is None
        assert service._is_configured is False


class TestLoadCredentials:
    """Test _load_credentials method."""
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_load_credentials_success(self, mock_sm_client_class):
        """Test successful credential loading from Secret Manager."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({
            "access_token": "access-token-123",
            "refresh_token": "refresh-token-456",
            "app_key": "app-key",
            "app_secret": "app-secret",
        }).encode("UTF-8")
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        service = DropboxService()
        creds = service._load_credentials()
        
        assert creds is not None
        assert creds["access_token"] == "access-token-123"
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_load_credentials_failure(self, mock_sm_client_class):
        """Test handling when Secret Manager fails."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_sm_client.access_secret_version.side_effect = Exception("Access denied")
        mock_sm_client_class.return_value = mock_sm_client
        
        service = DropboxService()
        creds = service._load_credentials()
        
        assert creds is None


class TestIsConfigured:
    """Test is_configured property."""
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_is_configured_true(self, mock_sm_client_class):
        """Test is_configured returns True when credentials available."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({
            "access_token": "token"
        }).encode("UTF-8")
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        service = DropboxService()
        
        assert service.is_configured is True
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_is_configured_false_no_token(self, mock_sm_client_class):
        """Test is_configured returns False when no access_token."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({
            "refresh_token": "refresh"  # Missing access_token
        }).encode("UTF-8")
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        service = DropboxService()
        
        assert service.is_configured is False


class TestDropboxClient:
    """Test client property."""
    
    @patch("dropbox.Dropbox")
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_client_creates_dropbox_instance(self, mock_sm_client_class, mock_dropbox_class):
        """Test client property creates Dropbox SDK client."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({
            "access_token": "token",
            "refresh_token": "refresh",
            "app_key": "key",
            "app_secret": "secret",
        }).encode("UTF-8")
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        mock_dropbox = Mock()
        mock_dropbox_class.return_value = mock_dropbox
        
        service = DropboxService()
        client = service.client
        
        mock_dropbox_class.assert_called_once_with(
            oauth2_access_token="token",
            oauth2_refresh_token="refresh",
            app_key="key",
            app_secret="secret",
        )
        assert client == mock_dropbox
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_client_raises_on_missing_credentials(self, mock_sm_client_class):
        """Test client raises RuntimeError when credentials missing."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_sm_client.access_secret_version.side_effect = Exception("Not found")
        mock_sm_client_class.return_value = mock_sm_client
        
        service = DropboxService()
        
        with pytest.raises(RuntimeError) as exc_info:
            _ = service.client
        
        assert "not configured" in str(exc_info.value)


class TestListFolders:
    """Test list_folders method."""
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_list_folders(self, mock_sm_client_class):
        """Test listing folders at a path."""
        from backend.services.dropbox_service import DropboxService
        from dropbox.files import FolderMetadata
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        # Create mock folder entries
        mock_folder1 = Mock(spec=FolderMetadata)
        mock_folder1.name = "NOMAD-0001"
        mock_folder2 = Mock(spec=FolderMetadata)
        mock_folder2.name = "NOMAD-0002"
        mock_file = Mock()  # Not a FolderMetadata
        
        mock_result = Mock()
        mock_result.entries = [mock_folder1, mock_folder2, mock_file]
        mock_result.has_more = False
        
        service = DropboxService()
        # Directly set the client to avoid needing to mock the whole init chain
        mock_dropbox = Mock()
        mock_dropbox.files_list_folder.return_value = mock_result
        service._client = mock_dropbox
        
        folders = service.list_folders("/Karaoke/Tracks")
        
        mock_dropbox.files_list_folder.assert_called_once_with("/Karaoke/Tracks")
        assert folders == ["NOMAD-0001", "NOMAD-0002"]
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_list_folders_adds_leading_slash(self, mock_sm_client_class):
        """Test that path without leading slash gets one added."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        mock_result = Mock()
        mock_result.entries = []
        mock_result.has_more = False
        
        service = DropboxService()
        mock_dropbox = Mock()
        mock_dropbox.files_list_folder.return_value = mock_result
        service._client = mock_dropbox
        
        service.list_folders("path/without/slash")
        
        mock_dropbox.files_list_folder.assert_called_once_with("/path/without/slash")


class TestGetNextBrandCode:
    """Test get_next_brand_code method."""
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_get_next_brand_code(self, mock_sm_client_class):
        """Test calculating next brand code - fills gaps only above 1000."""
        from backend.services.dropbox_service import DropboxService

        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client

        service = DropboxService()

        # Mock list_folders to return existing codes with a gap at 1003
        with patch.object(service, "list_folders") as mock_list:
            mock_list.return_value = [
                "NOMAD-1001",
                "NOMAD-1002",
                "NOMAD-1004",  # Gap at 1003 - should be filled
                "NOMAD-1005",
                "Other Folder",
            ]

            next_code = service.get_next_brand_code("/path", "NOMAD")

            # Should fill the gap at 1003 (gaps >= 1001 are filled)
            assert next_code == "NOMAD-1003"
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_get_next_brand_code_empty_folder(self, mock_sm_client_class):
        """Test brand code calculation with no existing codes."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        service = DropboxService()
        
        with patch.object(service, "list_folders") as mock_list:
            mock_list.return_value = []
            
            next_code = service.get_next_brand_code("/path", "BRAND")
            
            assert next_code == "BRAND-0001"


class TestUploadFile:
    """Test upload_file method."""
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_upload_small_file(self, mock_sm_client_class, tmp_path):
        """Test uploading a small file directly."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Small file content")
        
        service = DropboxService()
        mock_dropbox = Mock()
        service._client = mock_dropbox
        
        service.upload_file(str(test_file), "/Uploads/test.txt")
        
        mock_dropbox.files_upload.assert_called_once()
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_upload_file_adds_leading_slash(self, mock_sm_client_class, tmp_path):
        """Test upload adds leading slash to remote path."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        service = DropboxService()
        mock_dropbox = Mock()
        service._client = mock_dropbox
        
        service.upload_file(str(test_file), "uploads/test.txt")
        
        # Check that the path has leading slash
        call_args = mock_dropbox.files_upload.call_args
        assert call_args[0][1] == "/uploads/test.txt"


class TestUploadFolder:
    """Test upload_folder method."""

    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_upload_folder(self, mock_sm_client_class, tmp_path):
        """Test uploading a folder with multiple files."""
        from backend.services.dropbox_service import DropboxService

        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client

        # Create test folder with files (no subdirs for this test)
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")

        service = DropboxService()

        with patch.object(service, "upload_file") as mock_upload:
            service.upload_folder(str(tmp_path), "/Uploads/folder")

            # Should upload 2 files
            assert mock_upload.call_count == 2

    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_upload_folder_recursive(self, mock_sm_client_class, tmp_path):
        """Test uploading a folder recursively includes subdirectories."""
        from backend.services.dropbox_service import DropboxService

        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client

        # Create test folder with files and subdirectories
        (tmp_path / "root_file.txt").write_text("root content")
        (tmp_path / "stems").mkdir()
        (tmp_path / "stems" / "vocals.flac").write_text("vocals")
        (tmp_path / "stems" / "instrumental.flac").write_text("instrumental")
        (tmp_path / "lyrics").mkdir()
        (tmp_path / "lyrics" / "song.lrc").write_text("lyrics")

        service = DropboxService()

        uploaded_files = []
        def capture_upload(local_path, remote_path):
            uploaded_files.append(remote_path)

        with patch.object(service, "upload_file", side_effect=capture_upload) as mock_upload:
            service.upload_folder(str(tmp_path), "/Uploads/folder")

            # Should upload 4 files (1 root + 2 stems + 1 lyrics)
            assert mock_upload.call_count == 4

            # Check that subdirectory structure is preserved
            assert "/Uploads/folder/root_file.txt" in uploaded_files
            assert "/Uploads/folder/stems/vocals.flac" in uploaded_files
            assert "/Uploads/folder/stems/instrumental.flac" in uploaded_files
            assert "/Uploads/folder/lyrics/song.lrc" in uploaded_files

    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_upload_folder_deeply_nested(self, mock_sm_client_class, tmp_path):
        """Test uploading deeply nested folder structure."""
        from backend.services.dropbox_service import DropboxService

        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client

        # Create deeply nested structure
        (tmp_path / "level1").mkdir()
        (tmp_path / "level1" / "level2").mkdir()
        (tmp_path / "level1" / "level2" / "deep_file.txt").write_text("deep")

        service = DropboxService()

        uploaded_files = []
        def capture_upload(local_path, remote_path):
            uploaded_files.append(remote_path)

        with patch.object(service, "upload_file", side_effect=capture_upload):
            service.upload_folder(str(tmp_path), "/Uploads/folder")

            # Check deeply nested file is uploaded with correct path
            assert "/Uploads/folder/level1/level2/deep_file.txt" in uploaded_files


class TestCreateSharedLink:
    """Test create_shared_link method."""
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_create_shared_link_new(self, mock_sm_client_class):
        """Test creating a new shared link."""
        from backend.services.dropbox_service import DropboxService
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        mock_link = Mock()
        mock_link.url = "https://dropbox.com/s/abc123/file.mp4"
        
        service = DropboxService()
        mock_dropbox = Mock()
        mock_dropbox.sharing_create_shared_link_with_settings.return_value = mock_link
        service._client = mock_dropbox
        
        url = service.create_shared_link("/Uploads/file.mp4")
        
        assert url == "https://dropbox.com/s/abc123/file.mp4"
    
    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_create_shared_link_existing(self, mock_sm_client_class):
        """Test getting existing shared link when one already exists."""
        from backend.services.dropbox_service import DropboxService
        from dropbox.exceptions import ApiError
        
        mock_sm_client = Mock()
        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client.access_secret_version.return_value = mock_response
        mock_sm_client_class.return_value = mock_sm_client
        
        # Simulate "link already exists" error
        mock_error = Mock()
        mock_error.is_shared_link_already_exists.return_value = True
        
        mock_existing_link = Mock()
        mock_existing_link.url = "https://dropbox.com/s/existing/file.mp4"
        
        mock_links_result = Mock()
        mock_links_result.links = [mock_existing_link]
        
        service = DropboxService()
        mock_dropbox = Mock()
        mock_dropbox.sharing_create_shared_link_with_settings.side_effect = \
            ApiError("req_id", mock_error, "message", "headers")
        mock_dropbox.sharing_list_shared_links.return_value = mock_links_result
        service._client = mock_dropbox
        
        url = service.create_shared_link("/Uploads/file.mp4")
        
        assert url == "https://dropbox.com/s/existing/file.mp4"

