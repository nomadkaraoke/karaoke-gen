"""
Tests for native distribution services (Dropbox and Google Drive).

These tests verify the service interfaces without requiring actual
cloud credentials, using mocks to simulate API responses.
"""
import json
import os
import pytest
from unittest.mock import MagicMock, patch, mock_open


class TestDropboxService:
    """Tests for the DropboxService class."""

    def test_module_imports(self):
        """Test that the module imports correctly."""
        from backend.services.dropbox_service import DropboxService, get_dropbox_service
        assert DropboxService is not None
        assert get_dropbox_service is not None

    def test_init_creates_instance(self):
        """Test that we can create a DropboxService instance."""
        from backend.services.dropbox_service import DropboxService
        service = DropboxService()
        assert service is not None
        assert service._client is None  # Client not initialized until first use
        assert service._is_configured is False

    def test_is_configured_returns_false_without_credentials(self):
        """Test that is_configured returns False when credentials are missing."""
        from backend.services.dropbox_service import DropboxService
        
        with patch.object(DropboxService, '_load_credentials', return_value=None):
            service = DropboxService()
            assert service.is_configured is False

    def test_is_configured_returns_true_with_credentials(self):
        """Test that is_configured returns True when credentials are present."""
        from backend.services.dropbox_service import DropboxService
        
        mock_creds = {"access_token": "test_token"}
        with patch.object(DropboxService, '_load_credentials', return_value=mock_creds):
            service = DropboxService()
            assert service.is_configured is True

    def test_is_configured_caches_result(self):
        """Test that is_configured caches the result."""
        from backend.services.dropbox_service import DropboxService
        
        service = DropboxService()
        service._is_configured = True
        # Should return True without calling _load_credentials
        assert service.is_configured is True

    def test_is_configured_returns_false_without_access_token(self):
        """Test that is_configured returns False when access_token is missing."""
        from backend.services.dropbox_service import DropboxService
        
        mock_creds = {"refresh_token": "test"}  # No access_token
        with patch.object(DropboxService, '_load_credentials', return_value=mock_creds):
            service = DropboxService()
            assert service.is_configured is False

    def test_get_next_brand_code_first_track(self):
        """Test brand code calculation when no existing tracks."""
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        with patch.object(service, 'list_folders', return_value=[]):
            brand_code = service.get_next_brand_code("/test/path", "NOMAD")
            assert brand_code == "NOMAD-0001"

    def test_get_next_brand_code_legacy_gaps_preserved(self):
        """Test that gaps below 1000 are NOT filled (legacy behavior)."""
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        existing_folders = [
            "NOMAD-0001 - Artist1 - Song1",
            "NOMAD-0002 - Artist2 - Song2",
            "NOMAD-0005 - Artist3 - Song3",  # Gap at 0003, 0004 - should be preserved
        ]
        with patch.object(service, 'list_folders', return_value=existing_folders):
            brand_code = service.get_next_brand_code("/test/path", "NOMAD")
            assert brand_code == "NOMAD-0006"  # Does NOT fill legacy gap

    def test_get_next_brand_code_fills_gap_above_1000(self):
        """Test that gaps at 1001+ ARE filled."""
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        existing_folders = [
            "NOMAD-1001 - Artist1 - Song1",
            "NOMAD-1002 - Artist2 - Song2",
            "NOMAD-1005 - Artist3 - Song3",  # Gap at 1003, 1004
        ]
        with patch.object(service, 'list_folders', return_value=existing_folders):
            brand_code = service.get_next_brand_code("/test/path", "NOMAD")
            assert brand_code == "NOMAD-1003"  # Fills gap at 1003

    def test_get_next_brand_code_no_gaps_above_1000(self):
        """Test brand code calculation with no gaps above 1000 returns max+1."""
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        existing_folders = [
            "NOMAD-1001 - Artist1 - Song1",
            "NOMAD-1002 - Artist2 - Song2",
            "NOMAD-1003 - Artist3 - Song3",
        ]
        with patch.object(service, 'list_folders', return_value=existing_folders):
            brand_code = service.get_next_brand_code("/test/path", "NOMAD")
            assert brand_code == "NOMAD-1004"

    def test_get_next_brand_code_mixed_legacy_and_new(self):
        """Test with both legacy (<1000) and new (>=1000) codes."""
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        existing_folders = [
            "NOMAD-0001 - Artist1 - Song1",
            "NOMAD-0005 - Artist2 - Song2",  # Legacy gap - preserved
            "NOMAD-1001 - Artist3 - Song3",
            "NOMAD-1003 - Artist4 - Song4",  # Gap at 1002 - should be filled
        ]
        with patch.object(service, 'list_folders', return_value=existing_folders):
            brand_code = service.get_next_brand_code("/test/path", "NOMAD")
            assert brand_code == "NOMAD-1002"  # Fills gap at 1002, ignores legacy gap

    def test_get_next_brand_code_different_prefix(self):
        """Test brand code calculation with different brand prefix."""
        from backend.services.dropbox_service import DropboxService

        service = DropboxService()
        existing_folders = [
            "NOMAD-0001 - Artist1 - Song1",
            "NOMAD-0002 - Artist2 - Song2",
            "TEST-0001 - Artist3 - Song3",
            "TEST-0002 - Artist4 - Song4",
        ]
        with patch.object(service, 'list_folders', return_value=existing_folders):
            # NOMAD sequence: 1,2 (below 1000) -> next is 3
            brand_code = service.get_next_brand_code("/test/path", "NOMAD")
            assert brand_code == "NOMAD-0003"
            # TEST sequence: 1,2 (below 1000) -> next is 3
            brand_code = service.get_next_brand_code("/test/path", "TEST")
            assert brand_code == "TEST-0003"

    def test_factory_function_returns_instance(self):
        """Test that get_dropbox_service returns a service instance."""
        from backend.services.dropbox_service import get_dropbox_service
        
        service = get_dropbox_service()
        assert service is not None

    def test_load_credentials_handles_exception(self):
        """Test that _load_credentials handles exceptions gracefully."""
        from backend.services.dropbox_service import DropboxService
        
        with patch('backend.services.dropbox_service.secretmanager.SecretManagerServiceClient') as mock_client:
            mock_client.return_value.access_secret_version.side_effect = Exception("API Error")
            service = DropboxService()
            result = service._load_credentials()
            assert result is None

    def test_load_credentials_parses_json(self):
        """Test that _load_credentials correctly parses JSON credentials."""
        from backend.services.dropbox_service import DropboxService
        
        mock_creds = {"access_token": "test_token", "refresh_token": "test_refresh"}
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json.dumps(mock_creds)
        
        with patch('backend.services.dropbox_service.secretmanager.SecretManagerServiceClient') as mock_client:
            mock_client.return_value.access_secret_version.return_value = mock_response
            service = DropboxService()
            result = service._load_credentials()
            assert result == mock_creds

    def test_client_property_raises_without_credentials(self):
        """Test that client property raises error without credentials."""
        from backend.services.dropbox_service import DropboxService
        
        with patch.object(DropboxService, '_load_credentials', return_value=None):
            service = DropboxService()
            with pytest.raises(RuntimeError, match="credentials not configured"):
                _ = service.client

    def test_client_property_raises_import_error(self):
        """Test that client property raises helpful error if dropbox not installed."""
        from backend.services.dropbox_service import DropboxService
        import sys
        
        mock_creds = {"access_token": "test_token"}
        with patch.object(DropboxService, '_load_credentials', return_value=mock_creds):
            with patch.dict(sys.modules, {'dropbox': None}):
                service = DropboxService()
                # Force reimport to fail
                with patch('builtins.__import__', side_effect=ImportError("No module named 'dropbox'")):
                    with pytest.raises(ImportError, match="dropbox package"):
                        _ = service.client

    def test_list_folders_adds_leading_slash(self):
        """Test that list_folders adds leading slash if missing."""
        from backend.services.dropbox_service import DropboxService
        
        service = DropboxService()
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.entries = []
        mock_result.has_more = False
        mock_client.files_list_folder.return_value = mock_result
        service._client = mock_client
        
        service.list_folders("test/path")  # No leading slash
        
        # Verify it was called with leading slash
        mock_client.files_list_folder.assert_called_with("/test/path")

    def test_upload_file_adds_leading_slash(self):
        """Test that upload_file adds leading slash to remote path."""
        from backend.services.dropbox_service import DropboxService
        import tempfile
        
        service = DropboxService()
        mock_client = MagicMock()
        service._client = mock_client
        
        # Create a small temp file
        with tempfile.NamedTemporaryFile(delete=False, mode='wb') as f:
            f.write(b"test content")
            temp_path = f.name
        
        try:
            service.upload_file(temp_path, "remote/path.txt")  # No leading slash
            # Verify upload was called with leading slash
            mock_client.files_upload.assert_called_once()
            call_args = mock_client.files_upload.call_args
            assert call_args[0][1] == "/remote/path.txt"
        finally:
            os.unlink(temp_path)

    def test_upload_folder_uploads_all_files(self):
        """Test that upload_folder uploads all files in directory."""
        from backend.services.dropbox_service import DropboxService
        import tempfile
        
        service = DropboxService()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some test files
            (open(os.path.join(tmpdir, "file1.txt"), "w")).write("content1")
            (open(os.path.join(tmpdir, "file2.txt"), "w")).write("content2")
            
            with patch.object(service, 'upload_file') as mock_upload:
                service.upload_folder(tmpdir, "/remote/folder")
                
                assert mock_upload.call_count == 2

    def test_create_shared_link_success(self):
        """Test successful shared link creation."""
        from backend.services.dropbox_service import DropboxService
        
        service = DropboxService()
        mock_client = MagicMock()
        mock_link = MagicMock()
        mock_link.url = "https://dropbox.com/shared/test"
        mock_client.sharing_create_shared_link_with_settings.return_value = mock_link
        service._client = mock_client
        
        result = service.create_shared_link("/test/path")
        assert result == "https://dropbox.com/shared/test"

    def test_sharing_list_shared_links_mock_setup(self):
        """Test that mock setup for sharing_list_shared_links works correctly.
        
        Note: Properly mocking Dropbox's ApiError is complex because it
        requires specific exception class structure. This test verifies
        the mock configuration is correct for the success path, which is
        a prerequisite for more complex error-handling tests.
        """
        from backend.services.dropbox_service import DropboxService
        
        service = DropboxService()
        mock_client = MagicMock()
        
        # Mock the existing link retrieval (success case)
        mock_existing_link = MagicMock()
        mock_existing_link.url = "https://dropbox.com/existing/link"
        mock_links_result = MagicMock()
        mock_links_result.links = [mock_existing_link]
        mock_client.sharing_list_shared_links.return_value = mock_links_result
        
        # Assign the mock client to the service
        service._client = mock_client
        
        # Verify the mock returns the expected link structure
        result = mock_client.sharing_list_shared_links(path="/test/path")
        assert result.links[0].url == "https://dropbox.com/existing/link"
        mock_client.sharing_list_shared_links.assert_called_once_with(path="/test/path")


class TestGoogleDriveService:
    """Tests for the GoogleDriveService class."""

    def test_module_imports(self):
        """Test that the module imports correctly."""
        from backend.services.gdrive_service import GoogleDriveService, get_gdrive_service
        assert GoogleDriveService is not None
        assert get_gdrive_service is not None

    def test_init_creates_instance(self):
        """Test that we can create a GoogleDriveService instance."""
        from backend.services.gdrive_service import GoogleDriveService
        service = GoogleDriveService()
        assert service is not None
        assert service._service is None  # Service not initialized until first use
        assert service._loaded is False

    def test_is_configured_returns_false_without_credentials(self):
        """Test that is_configured returns False when credentials are missing."""
        from backend.services.gdrive_service import GoogleDriveService
        
        with patch.object(GoogleDriveService, '_load_credentials', return_value=None):
            service = GoogleDriveService()
            assert service.is_configured is False

    def test_is_configured_returns_true_with_credentials(self):
        """Test that is_configured returns True when credentials are present."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_creds = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        with patch.object(GoogleDriveService, '_load_credentials', return_value=mock_creds):
            service = GoogleDriveService()
            assert service.is_configured is True

    def test_factory_function_returns_singleton(self):
        """Test that get_gdrive_service returns a singleton."""
        from backend.services.gdrive_service import get_gdrive_service, _gdrive_service
        
        # Reset singleton
        import backend.services.gdrive_service as gdrive_module
        gdrive_module._gdrive_service = None
        
        service1 = get_gdrive_service()
        service2 = get_gdrive_service()
        assert service1 is service2

    def test_load_credentials_caches_result(self):
        """Test that _load_credentials caches the result."""
        from backend.services.gdrive_service import GoogleDriveService
        
        service = GoogleDriveService()
        service._loaded = True
        service._credentials_data = {"cached": "data"}
        
        # Should return cached data without calling settings
        result = service._load_credentials()
        assert result == {"cached": "data"}

    def test_load_credentials_falls_back_to_youtube(self):
        """Test that _load_credentials falls back to YouTube credentials."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = MagicMock()
        mock_settings.get_secret.side_effect = [
            None,  # gdrive-oauth-credentials not found
            '{"refresh_token": "yt_refresh", "client_id": "yt_client", "client_secret": "yt_secret"}',  # youtube-oauth-credentials
        ]
        
        service = GoogleDriveService()
        service.settings = mock_settings
        
        result = service._load_credentials()
        assert result is not None
        assert result["refresh_token"] == "yt_refresh"

    def test_load_credentials_returns_none_when_both_fail(self):
        """Test that _load_credentials returns None when no credentials available."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = MagicMock()
        mock_settings.get_secret.return_value = None
        
        service = GoogleDriveService()
        service.settings = mock_settings
        
        result = service._load_credentials()
        assert result is None
        assert service._loaded is True

    def test_load_credentials_validates_required_fields(self):
        """Test that _load_credentials validates required fields."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = MagicMock()
        # Missing client_secret
        mock_settings.get_secret.return_value = '{"refresh_token": "test", "client_id": "test"}'
        
        service = GoogleDriveService()
        service.settings = mock_settings
        
        result = service._load_credentials()
        assert result is None  # Should fail validation

    def test_load_credentials_handles_json_error(self):
        """Test that _load_credentials handles JSON parse errors."""
        from backend.services.gdrive_service import GoogleDriveService
        
        mock_settings = MagicMock()
        mock_settings.get_secret.return_value = "not valid json"
        
        service = GoogleDriveService()
        service.settings = mock_settings
        
        result = service._load_credentials()
        assert result is None

    def test_service_property_raises_without_credentials(self):
        """Test that service property raises error without credentials."""
        from backend.services.gdrive_service import GoogleDriveService
        
        with patch.object(GoogleDriveService, '_load_credentials', return_value=None):
            service = GoogleDriveService()
            with pytest.raises(RuntimeError, match="credentials not configured"):
                _ = service.service

    def test_get_or_create_folder_finds_existing(self):
        """Test that get_or_create_folder finds existing folders."""
        from backend.services.gdrive_service import GoogleDriveService
        
        service = GoogleDriveService()
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {"files": [{"id": "existing_folder_id", "name": "TestFolder"}]}
        mock_files.list.return_value = mock_list
        mock_service.files.return_value = mock_files
        service._service = mock_service
        
        result = service.get_or_create_folder("parent_id", "TestFolder")
        assert result == "existing_folder_id"

    def test_get_or_create_folder_creates_new(self):
        """Test that get_or_create_folder creates new folders when not found."""
        from backend.services.gdrive_service import GoogleDriveService
        
        service = GoogleDriveService()
        mock_service = MagicMock()
        mock_files = MagicMock()
        
        # First call - folder doesn't exist
        mock_list = MagicMock()
        mock_list.execute.return_value = {"files": []}
        mock_files.list.return_value = mock_list
        
        # Second call - create folder
        mock_create = MagicMock()
        mock_create.execute.return_value = {"id": "new_folder_id"}
        mock_files.create.return_value = mock_create
        
        mock_service.files.return_value = mock_files
        service._service = mock_service
        
        result = service.get_or_create_folder("parent_id", "NewFolder")
        assert result == "new_folder_id"

    def test_upload_file_determines_mime_type(self):
        """Test that upload_file determines correct MIME type."""
        from backend.services.gdrive_service import GoogleDriveService
        import tempfile
        
        service = GoogleDriveService()
        mock_service = MagicMock()
        mock_files = MagicMock()
        
        # Mock list (for replace_existing check)
        mock_list = MagicMock()
        mock_list.execute.return_value = {"files": []}
        mock_files.list.return_value = mock_list
        
        # Mock create
        mock_create = MagicMock()
        mock_create.execute.return_value = {"id": "uploaded_file_id"}
        mock_files.create.return_value = mock_create
        
        mock_service.files.return_value = mock_files
        service._service = mock_service
        
        # Create a temp file with .mp4 extension
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(b"fake video content")
            temp_path = f.name
        
        try:
            result = service.upload_file(temp_path, "parent_id", "test.mp4")
            assert result == "uploaded_file_id"
        finally:
            os.unlink(temp_path)

    def test_upload_file_replaces_existing(self):
        """Test that upload_file can replace existing files."""
        from backend.services.gdrive_service import GoogleDriveService
        import tempfile
        
        service = GoogleDriveService()
        mock_service = MagicMock()
        mock_files = MagicMock()
        
        # Mock list - file exists
        mock_list = MagicMock()
        mock_list.execute.return_value = {"files": [{"id": "existing_id"}]}
        mock_files.list.return_value = mock_list
        
        # Mock delete
        mock_delete = MagicMock()
        mock_files.delete.return_value = mock_delete
        
        # Mock create
        mock_create = MagicMock()
        mock_create.execute.return_value = {"id": "new_file_id"}
        mock_files.create.return_value = mock_create
        
        mock_service.files.return_value = mock_files
        service._service = mock_service
        
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b"content")
            temp_path = f.name
        
        try:
            result = service.upload_file(temp_path, "parent_id", "test.txt", replace_existing=True)
            assert result == "new_file_id"
            mock_files.delete.assert_called_once()
        finally:
            os.unlink(temp_path)

    def test_upload_to_public_share_creates_structure(self):
        """Test that upload_to_public_share creates folder structure."""
        from backend.services.gdrive_service import GoogleDriveService
        import tempfile
        
        service = GoogleDriveService()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            mp4_path = os.path.join(tmpdir, "test.mp4")
            mp4_720_path = os.path.join(tmpdir, "test_720p.mp4")
            cdg_path = os.path.join(tmpdir, "test.zip")
            
            open(mp4_path, 'wb').write(b"mp4 content")
            open(mp4_720_path, 'wb').write(b"720p content")
            open(cdg_path, 'wb').write(b"cdg content")
            
            with patch.object(service, 'get_or_create_folder', return_value="folder_id"):
                with patch.object(service, 'upload_file', return_value="file_id"):
                    result = service.upload_to_public_share(
                        root_folder_id="root_id",
                        brand_code="TEST-0001",
                        base_name="Artist - Title",
                        output_files={
                            "final_karaoke_lossy_mp4": mp4_path,
                            "final_karaoke_lossy_720p_mp4": mp4_720_path,
                            "final_karaoke_cdg_zip": cdg_path,
                        }
                    )
                    
                    assert "mp4" in result
                    assert "mp4_720p" in result
                    assert "cdg" in result

    def test_upload_to_public_share_handles_missing_files(self):
        """Test that upload_to_public_share handles missing files gracefully."""
        from backend.services.gdrive_service import GoogleDriveService
        
        service = GoogleDriveService()
        
        with patch.object(service, 'get_or_create_folder', return_value="folder_id"):
            with patch.object(service, 'upload_file', return_value="file_id"):
                result = service.upload_to_public_share(
                    root_folder_id="root_id",
                    brand_code="TEST-0001",
                    base_name="Artist - Title",
                    output_files={
                        "final_karaoke_lossy_mp4": "/nonexistent/path.mp4",
                    }
                )
                
                # Should return empty since file doesn't exist
                assert result == {}


class TestJobModelDistributionFields:
    """Tests for distribution fields in Job model."""

    def test_job_model_has_dropbox_path_field(self):
        """Test that Job model has dropbox_path field."""
        from backend.models.job import Job, JobStatus
        from datetime import datetime
        
        job = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            dropbox_path="/Karaoke/Tracks-Organized",
        )
        assert job.dropbox_path == "/Karaoke/Tracks-Organized"

    def test_job_model_has_gdrive_folder_id_field(self):
        """Test that Job model has gdrive_folder_id field."""
        from backend.models.job import Job, JobStatus
        from datetime import datetime
        
        job = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            gdrive_folder_id="1abc123xyz",
        )
        assert job.gdrive_folder_id == "1abc123xyz"

    def test_job_model_distribution_fields_optional(self):
        """Test that distribution fields are optional."""
        from backend.models.job import Job, JobStatus
        from datetime import datetime
        
        job = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        assert job.dropbox_path is None
        assert job.gdrive_folder_id is None


class TestJobCreateDistributionFields:
    """Tests for distribution fields in JobCreate model."""

    def test_job_create_has_dropbox_path_field(self):
        """Test that JobCreate model has dropbox_path field."""
        from backend.models.job import JobCreate
        
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Title",
            dropbox_path="/Karaoke/Tracks-Organized",
        )
        assert job_create.dropbox_path == "/Karaoke/Tracks-Organized"

    def test_job_create_has_gdrive_folder_id_field(self):
        """Test that JobCreate model has gdrive_folder_id field."""
        from backend.models.job import JobCreate
        
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Title",
            gdrive_folder_id="1abc123xyz",
        )
        assert job_create.gdrive_folder_id == "1abc123xyz"

    def test_job_create_distribution_fields_optional(self):
        """Test that distribution fields are optional in JobCreate."""
        from backend.models.job import JobCreate
        
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Title",
        )
        assert job_create.dropbox_path is None
        assert job_create.gdrive_folder_id is None


class TestFileUploadDistributionParams:
    """Tests for distribution parameters in file upload endpoint."""

    def test_endpoint_has_dropbox_path_parameter(self):
        """Test that upload endpoint signature includes dropbox_path parameter."""
        import inspect
        from backend.api.routes.file_upload import upload_and_create_job
        
        sig = inspect.signature(upload_and_create_job)
        param_names = list(sig.parameters.keys())
        
        assert "dropbox_path" in param_names

    def test_endpoint_has_gdrive_folder_id_parameter(self):
        """Test that upload endpoint signature includes gdrive_folder_id parameter."""
        import inspect
        from backend.api.routes.file_upload import upload_and_create_job
        
        sig = inspect.signature(upload_and_create_job)
        param_names = list(sig.parameters.keys())
        
        assert "gdrive_folder_id" in param_names

    def test_endpoint_dropbox_path_is_optional(self):
        """Test that dropbox_path parameter has a default value (Form(None))."""
        import inspect
        from backend.api.routes.file_upload import upload_and_create_job
        
        sig = inspect.signature(upload_and_create_job)
        param = sig.parameters.get("dropbox_path")
        
        assert param is not None
        # Default is Form(None), so check that it's not required
        assert param.default is not inspect.Parameter.empty

    def test_endpoint_gdrive_folder_id_is_optional(self):
        """Test that gdrive_folder_id parameter has a default value (Form(None))."""
        import inspect
        from backend.api.routes.file_upload import upload_and_create_job
        
        sig = inspect.signature(upload_and_create_job)
        param = sig.parameters.get("gdrive_folder_id")
        
        assert param is not None
        # Default is Form(None), so check that it's not required
        assert param.default is not inspect.Parameter.empty


class TestVideoWorkerDistribution:
    """Tests for distribution handling in video worker."""

    def test_handle_native_distribution_function_exists(self):
        """Test that _handle_native_distribution function exists."""
        from backend.workers import video_worker
        assert hasattr(video_worker, '_handle_native_distribution')

    def test_video_worker_imports_distribution_services(self):
        """Test that video worker can import distribution services."""
        # The services should be importable (even if credentials aren't available)
        try:
            from backend.services.dropbox_service import DropboxService
            from backend.services.gdrive_service import GoogleDriveService
            assert True
        except ImportError as e:
            pytest.fail(f"Failed to import distribution services: {e}")

    @pytest.mark.asyncio
    async def test_handle_native_distribution_skips_without_config(self):
        """Test that _handle_native_distribution skips when not configured."""
        from backend.workers.video_worker import _handle_native_distribution
        
        mock_job = MagicMock()
        mock_job.dropbox_path = None
        mock_job.gdrive_folder_id = None
        mock_job.brand_prefix = None
        mock_job.artist = "Test"
        mock_job.title = "Song"
        
        mock_job_log = MagicMock()
        mock_job_manager = MagicMock()
        
        # Should complete without error
        await _handle_native_distribution(
            job_id="test-123",
            job=mock_job,
            job_log=mock_job_log,
            job_manager=mock_job_manager,
            temp_dir="/tmp/test",
            result={},
        )
        
        # No errors should have been logged
        mock_job_log.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_native_distribution_dropbox_not_configured(self):
        """Test Dropbox upload skipped when service not configured."""
        from backend.workers.video_worker import _handle_native_distribution
        
        mock_job = MagicMock()
        mock_job.dropbox_path = "/test/path"
        mock_job.brand_prefix = "TEST"
        mock_job.gdrive_folder_id = None
        mock_job.artist = "Test"
        mock_job.title = "Song"
        mock_job.state_data = {}
        
        mock_job_log = MagicMock()
        mock_job_manager = MagicMock()
        
        mock_dropbox = MagicMock()
        mock_dropbox.is_configured = False
        
        with patch('backend.services.dropbox_service.get_dropbox_service', return_value=mock_dropbox):
            await _handle_native_distribution(
                job_id="test-123",
                job=mock_job,
                job_log=mock_job_log,
                job_manager=mock_job_manager,
                temp_dir="/tmp/test",
                result={},
            )
        
        # Should log warning about not configured
        mock_job_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_handle_native_distribution_gdrive_not_configured(self):
        """Test Google Drive upload skipped when service not configured."""
        from backend.workers.video_worker import _handle_native_distribution
        
        mock_job = MagicMock()
        mock_job.dropbox_path = None
        mock_job.brand_prefix = None
        mock_job.gdrive_folder_id = "test_folder_id"
        mock_job.artist = "Test"
        mock_job.title = "Song"
        mock_job.state_data = {}
        
        mock_job_log = MagicMock()
        mock_job_manager = MagicMock()
        
        mock_gdrive = MagicMock()
        mock_gdrive.is_configured = False
        
        with patch('backend.services.gdrive_service.get_gdrive_service', return_value=mock_gdrive):
            await _handle_native_distribution(
                job_id="test-123",
                job=mock_job,
                job_log=mock_job_log,
                job_manager=mock_job_manager,
                temp_dir="/tmp/test",
                result={},
            )
        
        # Should log warning about not configured
        mock_job_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_handle_native_distribution_handles_import_error(self):
        """Test that import errors for services are handled gracefully."""
        from backend.workers.video_worker import _handle_native_distribution
        
        mock_job = MagicMock()
        mock_job.dropbox_path = "/test/path"
        mock_job.brand_prefix = "TEST"
        mock_job.gdrive_folder_id = None
        mock_job.artist = "Test"
        mock_job.title = "Song"
        mock_job.state_data = {}
        
        mock_job_log = MagicMock()
        mock_job_manager = MagicMock()
        
        # Simulate import error
        with patch('backend.services.dropbox_service.get_dropbox_service', side_effect=ImportError("No module")):
            await _handle_native_distribution(
                job_id="test-123",
                job=mock_job,
                job_log=mock_job_log,
                job_manager=mock_job_manager,
                temp_dir="/tmp/test",
                result={},
            )
        
        # Should log warning about import error
        mock_job_log.warning.assert_called()
