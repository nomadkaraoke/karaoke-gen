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



class TestFileExists:
    """Test DropboxService.file_exists (used by the original-audio backfill)."""

    def test_returns_true_when_metadata_found(self):
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        mock_client = MagicMock()
        service._client = mock_client

        assert service.file_exists("/Karaoke/NOMAD-1 - A - B/A - B (flacfetch).flac") is True
        mock_client.files_get_metadata.assert_called_once_with(
            "/Karaoke/NOMAD-1 - A - B/A - B (flacfetch).flac"
        )

    def test_returns_false_on_not_found(self):
        from dropbox.exceptions import ApiError
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        mock_client = MagicMock()
        # Genuine "not found": is_path() -> get_path().is_not_found() == True
        not_found_err = MagicMock()
        not_found_err.is_path.return_value = True
        not_found_err.get_path.return_value.is_not_found.return_value = True
        mock_client.files_get_metadata.side_effect = ApiError(
            request_id="r", error=not_found_err, user_message_text=None, user_message_locale=None
        )
        service._client = mock_client

        assert service.file_exists("/missing.flac") is False

    def test_reraises_non_not_found_api_error(self):
        """Auth/network/rate-limit errors must NOT be masked as 'file missing'."""
        from dropbox.exceptions import ApiError
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        mock_client = MagicMock()
        other_err = MagicMock()
        other_err.is_path.return_value = False
        mock_client.files_get_metadata.side_effect = ApiError(
            request_id="r", error=other_err, user_message_text=None, user_message_locale=None
        )
        service._client = mock_client

        with pytest.raises(ApiError):
            service.file_exists("/some.flac")

    def test_prepends_leading_slash(self):
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        mock_client = MagicMock()
        service._client = mock_client

        service.file_exists("Karaoke/x.flac")
        mock_client.files_get_metadata.assert_called_once_with("/Karaoke/x.flac")


class TestDropboxLosslessExclusion:
    """2026-09-26 cost cut: Nomad's own Dropbox folders skip the lossless 4K MP4
    (it stays in GCS); tenant folders still get everything."""

    @patch("backend.services.dropbox_service.secretmanager.SecretManagerServiceClient")
    def test_upload_folder_skips_excluded_suffixes(self, mock_sm_client_class, tmp_path):
        from backend.services.dropbox_service import DropboxService

        mock_response = Mock()
        mock_response.payload.data = json.dumps({"access_token": "token"}).encode()
        mock_sm_client_class.return_value.access_secret_version.return_value = mock_response

        (tmp_path / "A - B (Final Karaoke Lossless 4k).mp4").write_text("x")
        (tmp_path / "A - B (Final Karaoke Lossless 4k).mkv").write_text("x")
        (tmp_path / "A - B (Final Karaoke Lossy 4k).mp4").write_text("x")

        service = DropboxService()
        with patch.object(service, "upload_file") as mock_upload:
            service.upload_folder(
                str(tmp_path), "/Uploads/folder",
                exclude_suffixes=(" (Final Karaoke Lossless 4k).mp4",),
            )

        uploaded = sorted(c.args[1].rsplit("/", 1)[1] for c in mock_upload.call_args_list)
        assert uploaded == [
            "A - B (Final Karaoke Lossless 4k).mkv",
            "A - B (Final Karaoke Lossy 4k).mp4",
        ]

    def _settings(self, suffixes=" (Final Karaoke Lossless 4k).mp4"):
        s = Mock()
        s.default_dropbox_path = "/MediaUnsynced/Karaoke/Tracks-Organized"
        s.default_private_dropbox_path = "/MediaUnsynced/Karaoke/Tracks-NonPublished"
        s.dropbox_skip_output_suffixes = suffixes
        return s

    def test_own_folders_skip_lossless_mp4_only_by_default(self):
        from backend.config import Settings
        from backend.services.dropbox_service import dropbox_skip_suffixes_for

        default = Settings().dropbox_skip_output_suffixes
        with patch("backend.config.get_settings", return_value=self._settings(default)):
            for path in ("/MediaUnsynced/Karaoke/Tracks-Organized",
                         "/MediaUnsynced/Karaoke/Tracks-NonPublished/"):
                skip = dropbox_skip_suffixes_for(path)
                assert skip == (" (Final Karaoke Lossless 4k).mp4",)
                # The MKV is promised in the Dropbox folder (completion email, Fiverr bot).
                assert not any(s.endswith(".mkv") for s in skip)

    def test_tenant_folder_gets_everything(self):
        from backend.services.dropbox_service import dropbox_skip_suffixes_for

        with patch("backend.config.get_settings", return_value=self._settings()):
            assert dropbox_skip_suffixes_for("/MediaUnsynced/Karaoke/Tracks-VocalStar") == ()
            assert dropbox_skip_suffixes_for(None) == ()

    def test_multiple_suffixes_and_empty_config(self):
        from backend.services.dropbox_service import dropbox_skip_suffixes_for

        both = " (Final Karaoke Lossless 4k).mp4| (Final Karaoke Lossless 4k).mkv"
        with patch("backend.config.get_settings", return_value=self._settings(both)):
            assert len(dropbox_skip_suffixes_for("/MediaUnsynced/Karaoke/Tracks-Organized")) == 2
        with patch("backend.config.get_settings", return_value=self._settings("")):
            assert dropbox_skip_suffixes_for("/MediaUnsynced/Karaoke/Tracks-Organized") == ()


class TestEnsureFolderAndMissingPaths:
    """ensure_folder idempotency + list_folders on a not-yet-created tenant folder."""

    def _service(self):
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        service._client = Mock()
        return service

    def _api_error(self, error):
        from dropbox.exceptions import ApiError

        return ApiError("req_id", error, "message", "headers")

    def test_ensure_folder_creates(self):
        service = self._service()
        assert service.ensure_folder("MediaUnsynced/Karaoke/Tracks-X") is True
        service._client.files_create_folder_v2.assert_called_once_with("/MediaUnsynced/Karaoke/Tracks-X")

    def test_ensure_folder_existing_folder_is_noop(self):
        from dropbox.files import CreateFolderError, WriteConflictError, WriteError

        service = self._service()
        service._client.files_create_folder_v2.side_effect = self._api_error(
            CreateFolderError.path(WriteError.conflict(WriteConflictError.folder))
        )
        assert service.ensure_folder("/Tracks-X") is False

    def test_ensure_folder_file_in_the_way_raises(self):
        from dropbox.exceptions import ApiError
        from dropbox.files import CreateFolderError, WriteConflictError, WriteError

        service = self._service()
        service._client.files_create_folder_v2.side_effect = self._api_error(
            CreateFolderError.path(WriteError.conflict(WriteConflictError.file))
        )
        with pytest.raises(ApiError):
            service.ensure_folder("/Tracks-X")

    def test_list_folders_missing_path_is_empty(self):
        from dropbox.files import ListFolderError, LookupError as DbxLookupError

        service = self._service()
        service._client.files_list_folder.side_effect = self._api_error(
            ListFolderError.path(DbxLookupError.not_found)
        )
        assert service.list_folders("/Tracks-New") == []

    def test_list_folders_other_errors_propagate(self):
        from dropbox.exceptions import ApiError
        from dropbox.files import ListFolderError, LookupError as DbxLookupError

        service = self._service()
        service._client.files_list_folder.side_effect = self._api_error(
            ListFolderError.path(DbxLookupError.restricted_content)
        )
        with pytest.raises(ApiError):
            service.list_folders("/Tracks-New")


class TestUploadFolderSkipUnchanged:
    """The video worker's second Dropbox pass (after the orchestrator uploaded the
    same folder) must only send new/changed files, not re-upload everything."""

    def _service(self):
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        service._client = Mock()
        return service

    @staticmethod
    def _file_entry(path_lower, content_hash):
        from dropbox.files import FileMetadata

        entry = Mock(spec=FileMetadata)
        entry.path_lower = path_lower
        entry.content_hash = content_hash
        return entry

    def _listing(self, entries, has_more=False, cursor="c1"):
        result = Mock()
        result.entries = entries
        result.has_more = has_more
        result.cursor = cursor
        return result

    def test_content_hash_matches_dropbox_algorithm(self, tmp_path):
        import hashlib

        from backend.services.dropbox_service import dropbox_content_hash

        block = 4 * 1024 * 1024
        data = b"a" * block + b"b" * 10  # two blocks
        f = tmp_path / "x.bin"
        f.write_bytes(data)
        expected = hashlib.sha256(
            hashlib.sha256(data[:block]).digest() + hashlib.sha256(data[block:]).digest()
        ).hexdigest()
        assert dropbox_content_hash(str(f)) == expected

        empty = tmp_path / "empty.bin"
        empty.write_bytes(b"")
        assert dropbox_content_hash(str(empty)) == hashlib.sha256(b"").hexdigest()

    def test_skips_identical_uploads_new_and_changed(self, tmp_path):
        from backend.services.dropbox_service import dropbox_content_hash

        (tmp_path / "Song (Final Karaoke Lossy 4k).mp4").write_bytes(b"video")
        (tmp_path / "Song (Karaoke).cdg").write_bytes(b"cdg-new")
        (tmp_path / "stems").mkdir()
        (tmp_path / "stems" / "Song (Vocals).flac").write_bytes(b"vocals")

        service = self._service()
        service._client.files_list_folder.return_value = self._listing([
            self._file_entry(
                "/tracks/nomad-1 - song/song (final karaoke lossy 4k).mp4",
                dropbox_content_hash(str(tmp_path / "Song (Final Karaoke Lossy 4k).mp4")),
            ),
            self._file_entry("/tracks/nomad-1 - song/song (karaoke).cdg", "stale-hash"),
        ])

        with patch.object(service, "upload_file") as up:
            service.upload_folder(str(tmp_path), "/Tracks/NOMAD-1 - Song", skip_unchanged=True)

        uploaded = sorted(call.args[1] for call in up.call_args_list)
        assert uploaded == [
            "/Tracks/NOMAD-1 - Song/Song (Karaoke).cdg",          # changed
            "/Tracks/NOMAD-1 - Song/stems/Song (Vocals).flac",    # new
        ]
        service._client.files_list_folder.assert_called_once_with("/Tracks/NOMAD-1 - Song", recursive=True)

    def test_listing_paginates(self, tmp_path):
        from backend.services.dropbox_service import dropbox_content_hash

        (tmp_path / "a.txt").write_bytes(b"a")
        (tmp_path / "b.txt").write_bytes(b"b")
        service = self._service()
        service._client.files_list_folder.return_value = self._listing(
            [self._file_entry("/f/a.txt", dropbox_content_hash(str(tmp_path / "a.txt")))], has_more=True
        )
        service._client.files_list_folder_continue.return_value = self._listing(
            [self._file_entry("/f/b.txt", dropbox_content_hash(str(tmp_path / "b.txt")))]
        )
        with patch.object(service, "upload_file") as up:
            service.upload_folder(str(tmp_path), "/f", skip_unchanged=True)
        up.assert_not_called()

    def test_missing_folder_uploads_everything(self, tmp_path):
        from dropbox.exceptions import ApiError
        from dropbox.files import ListFolderError, LookupError as DbxLookupError

        (tmp_path / "a.txt").write_bytes(b"a")
        service = self._service()
        service._client.files_list_folder.side_effect = ApiError(
            "req", ListFolderError.path(DbxLookupError.not_found), "msg", "hdr"
        )
        with patch.object(service, "upload_file") as up:
            service.upload_folder(str(tmp_path), "/new", skip_unchanged=True)
        up.assert_called_once()

    def test_listing_failure_falls_back_to_full_upload(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"a")
        (tmp_path / "b.txt").write_bytes(b"b")
        service = self._service()
        service._client.files_list_folder.side_effect = RuntimeError("network down")
        with patch.object(service, "upload_file") as up:
            service.upload_folder(str(tmp_path), "/f", skip_unchanged=True)
        assert up.call_count == 2

    def test_default_does_not_list_or_skip(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"a")
        service = self._service()
        with patch.object(service, "upload_file") as up:
            service.upload_folder(str(tmp_path), "/f")
        service._client.files_list_folder.assert_not_called()
        up.assert_called_once()
