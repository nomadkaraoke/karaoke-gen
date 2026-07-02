"""
Tests for gdrive_service.py - Google Drive file operations.

These tests mock the Google API client and Secret Manager to verify:
- Credential loading from Secret Manager
- Folder creation and lookup
- File uploads with proper MIME types
- Public share folder structure uploads
"""
import json
import os
import ssl
import pytest
from unittest.mock import Mock, MagicMock, patch


class TestGoogleDriveServiceInit:
    """Test GoogleDriveService initialization."""
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_init_creates_service(self, mock_get_settings):
        """Test initialization creates service with settings."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        
        assert service.settings == mock_settings
        assert service._service is None
        assert service._credentials_data is None
        assert service._loaded is False


class TestLoadCredentials:
    """Test _load_credentials method."""
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_load_credentials_success(self, mock_get_settings):
        """Test successful credential loading from Secret Manager."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "token": "access-token",
            "refresh_token": "refresh-token",
            "client_id": "client-id",
            "client_secret": "client-secret",
        })
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        creds = service._load_credentials()
        
        assert creds is not None
        assert creds["refresh_token"] == "refresh-token"
        mock_settings.get_secret.assert_called_once_with("gdrive-oauth-credentials")
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_load_credentials_fallback_to_youtube(self, mock_get_settings):
        """Test fallback to YouTube credentials when Drive creds not found."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        # First call returns None (Drive creds not found)
        # Second call returns YouTube creds
        mock_settings.get_secret.side_effect = [
            None,
            json.dumps({
                "refresh_token": "youtube-token",
                "client_id": "youtube-id",
                "client_secret": "youtube-secret",
            })
        ]
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        creds = service._load_credentials()
        
        assert creds is not None
        assert creds["refresh_token"] == "youtube-token"
        assert mock_settings.get_secret.call_count == 2
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_load_credentials_not_found(self, mock_get_settings):
        """Test handling when no credentials found."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = None
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        creds = service._load_credentials()
        
        assert creds is None
        assert service._loaded is True
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_load_credentials_missing_required_fields(self, mock_get_settings):
        """Test handling when credentials missing required fields."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "token": "access-token",
            # Missing: refresh_token, client_id, client_secret
        })
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        creds = service._load_credentials()
        
        assert creds is None
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_load_credentials_cached(self, mock_get_settings):
        """Test credentials are cached after first load."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token",
            "client_id": "id",
            "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        
        creds1 = service._load_credentials()
        creds2 = service._load_credentials()
        
        assert creds1 == creds2
        assert mock_settings.get_secret.call_count == 1


class TestIsConfigured:
    """Test is_configured property."""
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_is_configured_true(self, mock_get_settings):
        """Test is_configured returns True when credentials exist."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token",
            "client_id": "id",
            "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        
        assert service.is_configured is True
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_is_configured_false(self, mock_get_settings):
        """Test is_configured returns False when no credentials."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = None
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        
        assert service.is_configured is False


class TestDriveService:
    """Test service property."""
    
    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    @patch("backend.services.gdrive_service.get_settings")
    def test_service_creates_drive_client(
        self, mock_get_settings, mock_creds_class, mock_build
    ):
        """Test service property creates Google Drive API client."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "token": "access-token",
            "refresh_token": "refresh-token",
            "client_id": "client-id",
            "client_secret": "client-secret",
        })
        mock_get_settings.return_value = mock_settings
        
        mock_creds = Mock()
        mock_creds_class.return_value = mock_creds
        
        mock_drive_service = Mock()
        mock_build.return_value = mock_drive_service
        
        service = GoogleDriveService()
        result = service.service
        
        mock_build.assert_called_once_with("drive", "v3", credentials=mock_creds)
        assert result == mock_drive_service
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_service_raises_on_missing_credentials(self, mock_get_settings):
        """Test service raises RuntimeError when credentials missing."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = None
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        
        with pytest.raises(RuntimeError) as exc_info:
            _ = service.service
        
        assert "not configured" in str(exc_info.value)


class TestGetOrCreateFolder:
    """Test get_or_create_folder method."""
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_get_existing_folder(self, mock_get_settings):
        """Test finding an existing folder."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token",
            "client_id": "id",
            "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        
        # Mock the Drive API
        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {
            "files": [{"id": "folder-id-123", "name": "MP4"}]
        }
        mock_files_api.list.return_value = mock_list_result
        
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}
        
        folder_id = service.get_or_create_folder("parent-123", "MP4")
        
        assert folder_id == "folder-id-123"
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_create_new_folder(self, mock_get_settings):
        """Test creating a new folder when it doesn't exist."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token",
            "client_id": "id",
            "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        
        service = GoogleDriveService()
        
        # Mock the Drive API
        mock_files_api = Mock()
        
        # list returns empty (folder doesn't exist)
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {"files": []}
        mock_files_api.list.return_value = mock_list_result
        
        # create returns new folder
        mock_create_result = Mock()
        mock_create_result.execute.return_value = {"id": "new-folder-id"}
        mock_files_api.create.return_value = mock_create_result
        
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}
        
        folder_id = service.get_or_create_folder("parent-123", "NewFolder")
        
        assert folder_id == "new-folder-id"
        mock_files_api.create.assert_called_once()


class TestUploadFile:
    """Test upload_file method."""
    
    @patch("googleapiclient.http.MediaFileUpload")
    @patch("backend.services.gdrive_service.get_settings")
    def test_upload_file_success(self, mock_get_settings, mock_media, tmp_path):
        """Test uploading a file to Drive."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token",
            "client_id": "id",
            "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        
        # Create test file
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"video content")
        
        service = GoogleDriveService()
        
        mock_files_api = Mock()
        
        # list returns empty (no existing file)
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {"files": []}
        mock_files_api.list.return_value = mock_list_result
        
        # create returns new file
        mock_create_result = Mock()
        mock_create_result.execute.return_value = {"id": "file-id-123"}
        mock_files_api.create.return_value = mock_create_result
        
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}
        
        file_id = service.upload_file(str(test_file), "parent-123", "video.mp4")
        
        assert file_id == "file-id-123"
        mock_media.assert_called_once()
        # Verify MIME type was set correctly
        call_kwargs = mock_media.call_args.kwargs
        assert call_kwargs["mimetype"] == "video/mp4"
    
    @patch("googleapiclient.http.MediaFileUpload")
    @patch("backend.services.gdrive_service.get_settings")
    def test_upload_file_replaces_existing(self, mock_get_settings, mock_media, tmp_path):
        """Test upload deletes existing file before upload."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token",
            "client_id": "id",
            "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"content")
        
        service = GoogleDriveService()
        
        mock_files_api = Mock()
        
        # list returns existing file
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {
            "files": [{"id": "existing-file-id"}]
        }
        mock_files_api.list.return_value = mock_list_result
        
        mock_delete_result = Mock()
        mock_delete_result.execute.return_value = {}
        mock_files_api.delete.return_value = mock_delete_result
        
        mock_create_result = Mock()
        mock_create_result.execute.return_value = {"id": "new-file-id"}
        mock_files_api.create.return_value = mock_create_result
        
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}
        
        file_id = service.upload_file(str(test_file), "parent-123", "video.mp4")
        
        # Should have deleted existing file
        mock_files_api.delete.assert_called_once_with(fileId="existing-file-id")
        assert file_id == "new-file-id"


class TestUploadToPublicShare:
    """Test upload_to_public_share method."""
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_upload_to_public_share(self, mock_get_settings, tmp_path):
        """Test uploading files to public share folder structure."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token",
            "client_id": "id",
            "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        
        # Create test files
        mp4_file = tmp_path / "output.mp4"
        mp4_file.write_bytes(b"4k video")
        mp4_720_file = tmp_path / "output_720p.mp4"
        mp4_720_file.write_bytes(b"720p video")
        cdg_file = tmp_path / "output.zip"
        cdg_file.write_bytes(b"cdg package")
        
        service = GoogleDriveService()

        # Mock methods
        with patch.object(service, "get_or_create_folder") as mock_get_folder, \
             patch.object(service, "upload_file") as mock_upload, \
             patch("backend.services.nomad_master_mirror.NomadMasterMirror") as mock_mirror_cls:
                mock_get_folder.side_effect = [
                    "mp4-folder-id",
                    "mp4-720-folder-id",
                    "cdg-folder-id",
                ]
                mock_upload.side_effect = [
                    "mp4-file-id",
                    "720p-file-id",
                    "cdg-file-id",
                ]

                output_files = {
                    "final_karaoke_lossy_mp4": str(mp4_file),
                    "final_karaoke_lossy_720p_mp4": str(mp4_720_file),
                    "final_karaoke_cdg_zip": str(cdg_file),
                }

                result = service.upload_to_public_share(
                    root_folder_id="root-123",
                    brand_code="NOMAD-1163",
                    base_name="Artist - Title",
                    output_files=output_files,
                )

                # Should have created/found 3 folders
                assert mock_get_folder.call_count == 3

                # Should have uploaded 3 files
                assert mock_upload.call_count == 3

                # Check result
                assert result["mp4"] == "mp4-file-id"
                assert result["mp4_720p"] == "720p-file-id"
                assert result["cdg"] == "cdg-file-id"

                # Nomad brand → the 720p master is fast-synced to the Divebar GCS
                # mirror with the exact "{brand_code} - {base_name}.mp4" object name.
                mock_mirror_cls.return_value.push_720p.assert_called_once_with(
                    str(mp4_720_file), "NOMAD-1163 - Artist - Title.mp4"
                )

    @patch("backend.services.gdrive_service.get_settings")
    def test_public_share_no_fast_sync_for_non_nomad_brand(self, mock_get_settings, tmp_path):
        """Non-Nomad brands (and NOMADNP privates) must NOT touch the divebar mirror."""
        from backend.services.gdrive_service import GoogleDriveService

        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings

        mp4_720_file = tmp_path / "output_720p.mp4"
        mp4_720_file.write_bytes(b"720p video")

        service = GoogleDriveService()
        with patch.object(service, "get_or_create_folder", return_value="f"), \
             patch.object(service, "upload_file", return_value="id"), \
             patch("backend.services.nomad_master_mirror.NomadMasterMirror") as mock_mirror_cls:
            for brand in ("VOCALSTAR-12", "NOMADNP-1163"):
                service.upload_to_public_share(
                    root_folder_id="root-123",
                    brand_code=brand,
                    base_name="Artist - Title",
                    output_files={"final_karaoke_lossy_720p_mp4": str(mp4_720_file)},
                )
            mock_mirror_cls.assert_not_called()

    @patch("backend.services.gdrive_service.get_settings")
    def test_public_share_fast_sync_failure_is_non_fatal(self, mock_get_settings, tmp_path):
        """A mirror failure must never break the (already-succeeded) Drive upload."""
        from backend.services.gdrive_service import GoogleDriveService

        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings

        mp4_720_file = tmp_path / "output_720p.mp4"
        mp4_720_file.write_bytes(b"720p video")

        service = GoogleDriveService()
        warnings: list = []
        with patch.object(service, "get_or_create_folder", return_value="f"), \
             patch.object(service, "upload_file", return_value="720p-file-id"), \
             patch("backend.services.nomad_master_mirror.NomadMasterMirror",
                   side_effect=RuntimeError("gcs down")):
            result = service.upload_to_public_share(
                root_folder_id="root-123",
                brand_code="NOMAD-1163",
                base_name="Artist - Title",
                output_files={"final_karaoke_lossy_720p_mp4": str(mp4_720_file)},
                warnings=warnings,
            )
            # Drive upload still reports success despite the mirror blowing up...
            assert result["mp4_720p"] == "720p-file-id"
            # ...and the failure is surfaced as a distribution warning (admin alert),
            # not silently swallowed.
            assert any("fast-sync" in w for w in warnings)
    
    @patch("backend.services.gdrive_service.get_settings")
    def test_upload_to_public_share_skips_missing_files(
        self, mock_get_settings, tmp_path
    ):
        """Test upload skips files that don't exist."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token",
            "client_id": "id",
            "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        
        # Only create one file
        mp4_file = tmp_path / "output.mp4"
        mp4_file.write_bytes(b"video")
        
        service = GoogleDriveService()
        
        with patch.object(service, "get_or_create_folder") as mock_get_folder:
            with patch.object(service, "upload_file") as mock_upload:
                mock_get_folder.return_value = "folder-id"
                mock_upload.return_value = "file-id"
                
                output_files = {
                    "final_karaoke_lossy_mp4": str(mp4_file),
                    "final_karaoke_lossy_720p_mp4": "/nonexistent/file.mp4",
                    "final_karaoke_cdg_zip": None,
                }
                
                result = service.upload_to_public_share(
                    root_folder_id="root-123",
                    brand_code="CODE",
                    base_name="Name",
                    output_files=output_files,
                )
                
                # Should only upload the one file that exists
                assert mock_upload.call_count == 1
                assert len(result) == 1


class TestRetryBehavior:
    """Test retry behavior for transient connection errors."""

    @patch("backend.services.gdrive_service.get_settings")
    def test_retries_on_broken_pipe(self, mock_get_settings):
        """Test that BrokenPipeError triggers retry and succeeds on second attempt."""
        from backend.services.gdrive_service import GoogleDriveService

        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings

        service = GoogleDriveService()

        mock_files_api = Mock()
        mock_list_result = Mock()
        # First call raises BrokenPipeError, second succeeds
        mock_list_result.execute.side_effect = [
            BrokenPipeError("[Errno 32] Broken pipe"),
            {"files": [{"id": "folder-123", "name": "MP4"}]},
        ]
        mock_files_api.list.return_value = mock_list_result

        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}

        # Patch _reset_service to clear but re-set the mock (avoid real OAuth)
        def mock_reset():
            service._service = mock_drive

        service._reset_service = mock_reset

        folder_id = service.get_or_create_folder("parent-123", "MP4")
        assert folder_id == "folder-123"
        assert mock_list_result.execute.call_count == 2

    @patch("backend.services.gdrive_service.get_settings")
    def test_gives_up_after_3_attempts(self, mock_get_settings):
        """Test that 3 consecutive failures raises the exception."""
        from backend.services.gdrive_service import GoogleDriveService

        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings

        service = GoogleDriveService()

        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = BrokenPipeError("[Errno 32] Broken pipe")
        mock_files_api.list.return_value = mock_list_result

        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}

        # Patch _reset_service to re-set the mock (avoid real OAuth)
        service._reset_service = lambda: setattr(service, '_service', mock_drive)

        with pytest.raises(BrokenPipeError):
            service.get_or_create_folder("parent-123", "MP4")

        assert mock_list_result.execute.call_count == 3

    @patch("backend.services.gdrive_service.get_settings")
    def test_service_reset_on_retry(self, mock_get_settings):
        """Test that _service is reset to None between retry attempts."""
        from backend.services.gdrive_service import GoogleDriveService

        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings

        service = GoogleDriveService()

        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = [
            BrokenPipeError("[Errno 32] Broken pipe"),
            {"files": [{"id": "folder-123", "name": "MP4"}]},
        ]
        mock_files_api.list.return_value = mock_list_result

        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}

        # Patch _reset_service to track calls but still do the reset
        original_reset = service._reset_service
        reset_calls = []

        def tracked_reset():
            reset_calls.append(True)
            original_reset()
            # Re-set mock service so the retry works
            service._service = mock_drive

        service._reset_service = tracked_reset

        folder_id = service.get_or_create_folder("parent-123", "MP4")
        assert folder_id == "folder-123"
        assert len(reset_calls) == 1  # Reset called once between attempt 1 and 2

    @patch("backend.services.gdrive_service.get_settings")
    def test_retries_on_ssl_error(self, mock_get_settings):
        """Test that SSLError triggers retry."""
        from backend.services.gdrive_service import GoogleDriveService

        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings

        service = GoogleDriveService()

        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = [
            ssl.SSLError("SSL: UNEXPECTED_EOF_WHILE_READING"),
            {"files": [{"id": "folder-123", "name": "MP4"}]},
        ]
        mock_files_api.list.return_value = mock_list_result

        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}

        # Patch _reset_service to re-set the mock (avoid real OAuth)
        service._reset_service = lambda: setattr(service, '_service', mock_drive)

        folder_id = service.get_or_create_folder("parent-123", "MP4")
        assert folder_id == "folder-123"
        assert mock_list_result.execute.call_count == 2

    @patch("backend.services.gdrive_service.get_settings")
    def test_no_retry_on_non_transient_error(self, mock_get_settings):
        """Test that non-transient errors are not retried."""
        from backend.services.gdrive_service import GoogleDriveService

        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings

        service = GoogleDriveService()

        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = ValueError("bad value")
        mock_files_api.list.return_value = mock_list_result

        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}

        with pytest.raises(ValueError):
            service.get_or_create_folder("parent-123", "MP4")

        assert mock_list_result.execute.call_count == 1


class TestDeleteFile:
    """Test delete_file and delete_files methods."""

    def _make_service(self, mock_get_settings):
        """Helper: make a GoogleDriveService with fake credentials pre-loaded."""
        from backend.services.gdrive_service import GoogleDriveService
        mock_settings = Mock()
        mock_get_settings.return_value = mock_settings
        service = GoogleDriveService()
        service._loaded = True
        service._credentials_data = {"refresh_token": "token", "client_id": "id", "client_secret": "secret"}
        return service

    @patch("backend.services.gdrive_service.get_settings")
    def test_delete_file_success(self, mock_get_settings):
        """Successful delete returns True."""
        service = self._make_service(mock_get_settings)

        mock_files_api = Mock()
        mock_delete = Mock()
        mock_delete.execute.return_value = None
        mock_files_api.delete.return_value = mock_delete

        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        result = service.delete_file("file-id-123")

        assert result is True
        mock_files_api.delete.assert_called_once_with(fileId="file-id-123")

    @patch("backend.services.gdrive_service.get_settings")
    def test_delete_file_returns_true_on_404(self, mock_get_settings):
        """404 (already deleted) returns True — idempotent cleanup."""
        service = self._make_service(mock_get_settings)

        mock_files_api = Mock()
        mock_delete = Mock()
        not_found = Exception("File not found")
        not_found.resp = Mock()
        not_found.resp.status = 404
        mock_delete.execute.side_effect = not_found
        mock_files_api.delete.return_value = mock_delete

        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        result = service.delete_file("already-gone-id")

        assert result is True

    @patch("backend.services.gdrive_service.get_settings")
    def test_delete_file_returns_false_on_error(self, mock_get_settings):
        """Non-404 errors return False."""
        service = self._make_service(mock_get_settings)

        mock_files_api = Mock()
        mock_delete = Mock()
        mock_delete.execute.side_effect = Exception("Permission denied")
        mock_files_api.delete.return_value = mock_delete

        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        result = service.delete_file("restricted-file")

        assert result is False

    @patch("backend.services.gdrive_service.get_settings")
    def test_delete_files_calls_delete_file_for_each_id(self, mock_get_settings):
        """delete_files returns a per-ID success dict."""
        service = self._make_service(mock_get_settings)

        # Stub delete_file: first ID succeeds, second fails
        results_by_id = {"id-1": True, "id-2": False}
        service.delete_file = lambda fid: results_by_id[fid]

        result = service.delete_files(["id-1", "id-2"])

        assert result == {"id-1": True, "id-2": False}

    @patch("backend.services.gdrive_service.get_settings")
    def test_delete_files_empty_list(self, mock_get_settings):
        """delete_files with empty list returns empty dict."""
        service = self._make_service(mock_get_settings)
        result = service.delete_files([])
        assert result == {}


class TestFindFilesByBrandCode:
    """Test find_files_by_brand_code method — used as fallback in cleanup-distribution."""

    def _make_service(self, mock_get_settings):
        """Helper: make a GoogleDriveService with fake credentials pre-loaded."""
        from backend.services.gdrive_service import GoogleDriveService
        mock_settings = Mock()
        mock_settings.get_secret.return_value = json.dumps({
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        })
        mock_get_settings.return_value = mock_settings
        service = GoogleDriveService()
        service._loaded = True
        service._credentials_data = {
            "refresh_token": "token", "client_id": "id", "client_secret": "secret",
        }
        return service

    def _attach_mock_drive(self, service, list_side_effects):
        """Attach a mock Drive client whose files().list().execute() returns given values."""
        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = list_side_effects
        mock_files_api.list.return_value = mock_list_result
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive
        return mock_files_api

    @patch("backend.services.gdrive_service.get_settings")
    def test_finds_files_in_all_subfolders(self, mock_get_settings):
        """Test finds matching files across CDG/, MP4/, MP4-720p/ subfolders."""
        service = self._make_service(mock_get_settings)

        mock_files_api = Mock()
        mock_list_result = Mock()
        # Responses: for each subfolder: (1) find subfolder, (2) search for files
        mock_list_result.execute.side_effect = [
            {"files": [{"id": "cdg-folder"}]},           # CDG subfolder found
            {"files": [{"id": "cdg-file-1", "name": "NOMAD-1271 - piri - dog.zip"}]},
            {"files": [{"id": "mp4-folder"}]},            # MP4 subfolder found
            {"files": [{"id": "mp4-file-1", "name": "NOMAD-1271 - piri - dog.mp4"}]},
            {"files": [{"id": "mp4720-folder"}]},         # MP4-720p subfolder found
            {"files": [{"id": "mp4720-file-1", "name": "NOMAD-1271 - piri - dog.mp4"}]},
        ]
        mock_files_api.list.return_value = mock_list_result
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        result = service.find_files_by_brand_code("root-folder", "NOMAD-1271")

        assert len(result) == 3
        assert "cdg-file-1" in result
        assert "mp4-file-1" in result
        assert "mp4720-file-1" in result

    @patch("backend.services.gdrive_service.get_settings")
    def test_returns_empty_when_subfolder_not_found(self, mock_get_settings):
        """Test returns empty list when public share subfolders don't exist."""
        service = self._make_service(mock_get_settings)

        mock_files_api = Mock()
        mock_list_result = Mock()
        # All subfolder lookups return empty
        mock_list_result.execute.return_value = {"files": []}
        mock_files_api.list.return_value = mock_list_result
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        result = service.find_files_by_brand_code("root-folder", "NOMAD-1271")

        assert result == []

    @patch("backend.services.gdrive_service.get_settings")
    def test_filters_by_exact_brand_code_prefix(self, mock_get_settings):
        """Test only returns files whose name starts with '{brand_code} - '."""
        service = self._make_service(mock_get_settings)

        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = [
            {"files": [{"id": "cdg-folder"}]},
            {
                "files": [
                    {"id": "match-id", "name": "NOMAD-1271 - piri - dog.zip"},
                    # This one contains NOMAD-1271 but doesn't START with it — should be excluded
                    {"id": "no-match-id", "name": "BONUS-NOMAD-1271 - other.zip"},
                ]
            },
            {"files": []},  # MP4 subfolder not found
            {"files": []},  # MP4-720p subfolder not found
        ]
        mock_files_api.list.return_value = mock_list_result
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        result = service.find_files_by_brand_code("root-folder", "NOMAD-1271")

        assert result == ["match-id"]

    @patch("backend.services.gdrive_service.get_settings")
    def test_returns_empty_when_no_files_match_brand_code(self, mock_get_settings):
        """Test returns empty list when subfolder exists but has no matching files."""
        service = self._make_service(mock_get_settings)

        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = [
            {"files": [{"id": "cdg-folder"}]},
            {"files": [{"id": "other-id", "name": "NOMAD-1300 - someone else.zip"}]},
            {"files": []},  # MP4 not found
            {"files": []},  # MP4-720p not found
        ]
        mock_files_api.list.return_value = mock_list_result
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        result = service.find_files_by_brand_code("root-folder", "NOMAD-1271")

        assert result == []

    @patch("backend.services.gdrive_service.get_settings")
    def test_raises_after_all_subfolders_when_any_error(self, mock_get_settings):
        """When any subfolder search errors, raises RuntimeError after processing all subfolders.

        This prevents the cleanup endpoint from treating an incomplete search result
        as "GDrive is clean" and incorrectly recycling the brand code.
        """
        service = self._make_service(mock_get_settings)

        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = [
            {"files": [{"id": "cdg-folder"}]},
            Exception("API error"),                        # CDG file search fails
            {"files": [{"id": "mp4-folder"}]},
            {"files": [{"id": "mp4-file-1", "name": "NOMAD-1271 - piri - dog.mp4"}]},
            {"files": [{"id": "mp4720-folder"}]},
            {"files": []},
        ]
        mock_files_api.list.return_value = mock_list_result
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        # Should raise after processing all subfolders — an incomplete search must not
        # be silently treated as "nothing found" by the caller
        with pytest.raises(RuntimeError, match="search incomplete"):
            service.find_files_by_brand_code("root-folder", "NOMAD-1271")

    @patch("backend.services.gdrive_service.get_settings")
    def test_recovers_from_transient_connection_error(self, mock_get_settings):
        """A transient BrokenPipe/SSL EOF within a subfolder search is retried, not fatal.

        Regression for the "GDrive brand_code search incomplete: 2/3 subfolder(s) failed"
        alerts caused by [SSL: UNEXPECTED_EOF_WHILE_READING] / [Errno 32] Broken pipe
        against idle Cloud Run containers.
        """
        service = self._make_service(mock_get_settings)
        # Prevent _reset_service (called in before_sleep) from nulling our mock.
        service._reset_service = Mock()

        mock_files_api = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.side_effect = [
            {"files": [{"id": "cdg-folder"}]},             # CDG subfolder found
            BrokenPipeError("Errno 32 Broken pipe"),       # transient drop mid-search
            {"files": [{"id": "cdg-folder"}]},             # retry: subfolder found again
            {"files": [{"id": "cdg-file-1", "name": "NOMAD-1271 - piri - dog.zip"}]},
            {"files": []},                                 # MP4 not found
            {"files": []},                                 # MP4-720p not found
        ]
        mock_files_api.list.return_value = mock_list_result
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        result = service.find_files_by_brand_code("root-folder", "NOMAD-1271")

        assert result == ["cdg-file-1"]
        service._reset_service.assert_called()  # a retry actually happened

    @patch("backend.services.gdrive_service.get_settings")
    def test_persistent_transient_error_still_raises_incomplete(self, mock_get_settings):
        """If retries are exhausted, the search is still reported as incomplete (not clean)."""
        service = self._make_service(mock_get_settings)
        service._reset_service = Mock()

        mock_files_api = Mock()
        mock_list_result = Mock()
        # Every call drops the connection — retries exhaust for all subfolders.
        mock_list_result.execute.side_effect = BrokenPipeError("Errno 32 Broken pipe")
        mock_files_api.list.return_value = mock_list_result
        mock_drive = Mock()
        mock_drive.files.return_value = mock_files_api
        service._service = mock_drive

        with pytest.raises(RuntimeError, match="search incomplete"):
            service.find_files_by_brand_code("root-folder", "NOMAD-1271")


class TestGetGdriveService:
    """Test get_gdrive_service singleton."""

    @patch("backend.services.gdrive_service.get_settings")
    def test_get_gdrive_service_singleton(self, mock_get_settings):
        """Test get_gdrive_service returns singleton instance."""
        from backend.services.gdrive_service import get_gdrive_service
        import backend.services.gdrive_service as gdrive_module

        # Reset singleton
        gdrive_module._gdrive_service = None

        mock_settings = Mock()
        mock_settings.get_secret.return_value = None
        mock_get_settings.return_value = mock_settings

        service1 = get_gdrive_service()
        service2 = get_gdrive_service()

        assert service1 is service2

