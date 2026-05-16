"""
Tests for credential manager and auth endpoints.
"""
import pytest
import json
from unittest.mock import MagicMock, patch
from datetime import datetime

from backend.services.credential_manager import (
    CredentialManager,
    CredentialStatus,
    CredentialCheckResult,
    DeviceAuthInfo,
    _destroy_old_secret_versions,
    get_credential_manager,
)


class TestCredentialStatus:
    """Tests for CredentialStatus enum."""
    
    def test_status_values(self):
        """Test all status values exist."""
        assert CredentialStatus.VALID == "valid"
        assert CredentialStatus.EXPIRED == "expired"
        assert CredentialStatus.INVALID == "invalid"
        assert CredentialStatus.NOT_CONFIGURED == "not_configured"
        assert CredentialStatus.ERROR == "error"


class TestCredentialCheckResult:
    """Tests for CredentialCheckResult dataclass."""
    
    def test_create_result(self):
        """Test creating a check result."""
        result = CredentialCheckResult(
            service="youtube",
            status=CredentialStatus.VALID,
            message="Credentials are valid",
            last_checked=datetime.utcnow()
        )
        
        assert result.service == "youtube"
        assert result.status == CredentialStatus.VALID
        assert result.message == "Credentials are valid"
        assert result.expires_at is None
    
    def test_create_result_with_expiry(self):
        """Test creating a check result with expiry."""
        expiry = datetime.utcnow()
        result = CredentialCheckResult(
            service="gdrive",
            status=CredentialStatus.VALID,
            message="Valid",
            last_checked=datetime.utcnow(),
            expires_at=expiry
        )
        
        assert result.expires_at == expiry


class TestCredentialManager:
    """Tests for CredentialManager class."""
    
    def test_init(self):
        """Test manager initialization."""
        manager = CredentialManager()
        assert manager._pending_device_auths == {}
    
    def test_check_youtube_not_configured(self):
        """Test YouTube check when not configured."""
        with patch('backend.services.credential_manager.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.get_secret.return_value = None
            mock_get_settings.return_value = mock_settings
            
            manager = CredentialManager()
            result = manager.check_youtube_credentials()
            
            assert result.status == CredentialStatus.NOT_CONFIGURED
            assert "not configured" in result.message.lower()
    
    def test_check_youtube_invalid_json(self):
        """Test YouTube check with invalid JSON."""
        with patch('backend.services.credential_manager.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.get_secret.return_value = "not valid json"
            mock_get_settings.return_value = mock_settings
            
            manager = CredentialManager()
            result = manager.check_youtube_credentials()
            
            assert result.status == CredentialStatus.INVALID
            assert "json" in result.message.lower()
    
    def test_check_youtube_missing_fields(self):
        """Test YouTube check with missing required fields."""
        # Missing refresh_token and client_secret
        creds = json.dumps({"token": "test", "client_id": "test"})
        
        with patch('backend.services.credential_manager.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.get_secret.return_value = creds
            mock_get_settings.return_value = mock_settings
            
            manager = CredentialManager()
            result = manager.check_youtube_credentials()
            
            assert result.status == CredentialStatus.INVALID
            assert "missing" in result.message.lower()
    
    def test_check_gdrive_not_configured(self):
        """Test Google Drive check when not configured."""
        with patch('backend.services.credential_manager.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.get_secret.return_value = None
            mock_get_settings.return_value = mock_settings
            
            manager = CredentialManager()
            result = manager.check_gdrive_credentials()
            
            assert result.status == CredentialStatus.NOT_CONFIGURED
    
    def test_check_dropbox_not_configured(self):
        """Test Dropbox check when not configured."""
        with patch('backend.services.credential_manager.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.get_secret.return_value = None
            mock_get_settings.return_value = mock_settings
            
            manager = CredentialManager()
            result = manager.check_dropbox_credentials()
            
            assert result.status == CredentialStatus.NOT_CONFIGURED
    
    def test_check_dropbox_missing_access_token(self):
        """Test Dropbox check with missing access token."""
        creds = json.dumps({"refresh_token": "test"})  # No access_token
        
        with patch('backend.services.credential_manager.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.get_secret.return_value = creds
            mock_get_settings.return_value = mock_settings
            
            manager = CredentialManager()
            result = manager.check_dropbox_credentials()
            
            assert result.status == CredentialStatus.INVALID
            assert "access_token" in result.message.lower()
    
    def test_check_all_credentials(self):
        """Test checking all credentials."""
        with patch('backend.services.credential_manager.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.get_secret.return_value = None
            mock_get_settings.return_value = mock_settings
            
            manager = CredentialManager()
            results = manager.check_all_credentials()
            
            assert "youtube" in results
            assert "gdrive" in results
            assert "dropbox" in results
            assert all(r.status == CredentialStatus.NOT_CONFIGURED for r in results.values())
    
    def test_test_youtube_api_failure(self):
        """Test YouTube API test failure."""
        manager = CredentialManager()
        
        mock_creds = MagicMock()
        
        with patch('googleapiclient.discovery.build', side_effect=Exception("API Error")):
            result = manager._test_youtube_api(mock_creds)
            
            assert result is False
    
    def test_test_gdrive_api_failure(self):
        """Test Google Drive API test failure."""
        manager = CredentialManager()
        
        mock_creds = MagicMock()
        
        with patch('googleapiclient.discovery.build', side_effect=Exception("API Error")):
            result = manager._test_gdrive_api(mock_creds)
            
            assert result is False
    
    def test_test_dropbox_api_failure(self):
        """Test Dropbox API test failure."""
        manager = CredentialManager()
        
        with patch('dropbox.Dropbox', side_effect=Exception("API Error")):
            result = manager._test_dropbox_api({"access_token": "test"})
            
            assert result is False
    
    def test_send_credential_alert_no_webhook(self):
        """Test alert without webhook URL."""
        manager = CredentialManager()
        
        result = manager.send_credential_alert([], discord_webhook_url=None)
        
        assert result is False
    
    def test_send_credential_alert_success(self):
        """Test successful alert sending."""
        manager = CredentialManager()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        
        invalid_services = [
            CredentialCheckResult(
                service="youtube",
                status=CredentialStatus.INVALID,
                message="Test message",
                last_checked=datetime.utcnow()
            )
        ]
        
        with patch('requests.post', return_value=mock_response) as mock_post:
            result = manager.send_credential_alert(
                invalid_services,
                discord_webhook_url="https://discord.com/webhook/test"
            )
            
            assert result is True
            mock_post.assert_called_once()
    
    def test_start_youtube_device_auth(self):
        """Test starting YouTube device auth flow."""
        manager = CredentialManager()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "device_code": "test_device_code",
            "user_code": "TEST-CODE",
            "verification_uri": "https://google.com/device",
            "expires_in": 1800,
            "interval": 5
        }
        
        with patch('requests.post', return_value=mock_response):
            device_info = manager.start_youtube_device_auth(
                client_id="test_client",
                client_secret="test_secret"
            )
            
            assert device_info.device_code == "test_device_code"
            assert device_info.user_code == "TEST-CODE"
            assert device_info.verification_url == "https://google.com/device"
            
            # Should be stored for polling
            key = f"youtube:{device_info.device_code}"
            assert key in manager._pending_device_auths
    
    def test_poll_device_auth_no_client_creds(self):
        """Test polling when client credentials are not in Secret Manager."""
        manager = CredentialManager()
        
        # Mock get_youtube_client_credentials to return None (no creds)
        with patch.object(manager, 'get_youtube_client_credentials', return_value=None):
            status, data = manager.poll_device_auth("youtube", "some_code")
        
        assert status == "error"
        assert "client credentials not found" in data["message"].lower()
    
    def test_poll_device_auth_expired(self):
        """Test polling with expired device code."""
        manager = CredentialManager()
        
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "expired_token"}
        
        with patch.object(manager, 'get_youtube_client_credentials', 
                          return_value={"client_id": "test", "client_secret": "test"}):
            with patch('requests.post', return_value=mock_response):
                status, data = manager.poll_device_auth("youtube", "expired_code")
        
        assert status == "expired"
        assert "expired" in data["message"].lower()
    
    def test_poll_device_auth_pending(self):
        """Test polling when authorization is pending."""
        manager = CredentialManager()
        
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "authorization_pending"}
        
        with patch.object(manager, 'get_youtube_client_credentials',
                          return_value={"client_id": "test", "client_secret": "test"}):
            with patch('requests.post', return_value=mock_response):
                status, data = manager.poll_device_auth("youtube", "pending_code")
        
        assert status == "pending"
        assert "waiting" in data["message"].lower()
    
    def test_poll_device_auth_success(self):
        """Test polling when authorization completes successfully."""
        manager = CredentialManager()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token"
        }
        
        with patch.object(manager, 'get_youtube_client_credentials',
                          return_value={"client_id": "test_id", "client_secret": "test_secret"}):
            with patch('requests.post', return_value=mock_response):
                with patch.object(manager, '_save_credentials_to_secret', return_value=True):
                    status, data = manager.poll_device_auth("youtube", "completed_code")
        
        assert status == "complete"
        assert data["token"] == "new_access_token"
        assert data["refresh_token"] == "new_refresh_token"
        assert data["client_id"] == "test_id"


class TestGetCredentialManager:
    """Tests for singleton factory function."""
    
    def test_returns_singleton(self):
        """Test that factory returns same instance."""
        # Reset singleton
        import backend.services.credential_manager as module
        module._credential_manager = None
        
        manager1 = get_credential_manager()
        manager2 = get_credential_manager()
        
        assert manager1 is manager2


class TestAuthRoutes:
    """Tests for auth API routes."""
    
    def test_routes_import(self):
        """Test that routes can be imported."""
        from backend.api.routes.auth import router
        assert router is not None
    
    def test_status_endpoint_exists(self):
        """Test that status endpoint is defined."""
        from backend.api.routes.auth import get_credentials_status
        assert get_credentials_status is not None
    
    def test_validate_endpoint_exists(self):
        """Test that validate endpoint is defined."""
        from backend.api.routes.auth import validate_credentials
        assert validate_credentials is not None
    
    def test_device_auth_endpoint_exists(self):
        """Test that device auth endpoints are defined."""
        from backend.api.routes.auth import (
            start_youtube_device_auth,
            poll_youtube_device_auth,
            start_gdrive_device_auth,
            poll_gdrive_device_auth,
        )
        assert start_youtube_device_auth is not None
        assert poll_youtube_device_auth is not None
        assert start_gdrive_device_auth is not None
        assert poll_gdrive_device_auth is not None


class TestFileUploadCredentialValidation:
    """Tests for credential validation in file upload."""

    def test_credential_manager_imported(self):
        """Test that credential manager is imported in file upload."""
        from backend.api.routes.file_upload import get_credential_manager, CredentialStatus
        assert get_credential_manager is not None
        assert CredentialStatus is not None


class TestDestroyOldSecretVersions:
    """Tests for _destroy_old_secret_versions helper."""

    def _make_version(self, name, state, create_time):
        v = MagicMock()
        v.name = name
        v.state = state
        v.create_time = create_time
        return v

    def test_destroys_versions_beyond_keep_count(self):
        """Keep newest N, destroy the rest."""
        client = MagicMock()
        enabled = MagicMock()
        # State enum lookup on the version (v.State.ENABLED) — make .State.ENABLED a sentinel
        # and match it on each version's `.state` field.
        State = MagicMock()
        State.ENABLED = "ENABLED"

        def mk(name, ts):
            v = MagicMock()
            v.name = name
            v.state = State.ENABLED
            v.State = State  # so v.state == v.State.ENABLED resolves correctly
            v.create_time = ts
            return v

        # 7 versions, oldest → newest by create_time
        versions = [mk(f"v{i}", i) for i in range(7)]
        client.list_secret_versions.return_value = versions

        _destroy_old_secret_versions(client, "projects/p/secrets/s", keep=3)

        # Should destroy 4 oldest (v0..v3); keep v4, v5, v6
        destroyed_names = [
            c.kwargs["request"]["name"]
            for c in client.destroy_secret_version.call_args_list
        ]
        assert sorted(destroyed_names) == ["v0", "v1", "v2", "v3"]

    def test_swallows_list_errors(self):
        """List failure must not raise — refresh path must keep working."""
        client = MagicMock()
        client.list_secret_versions.side_effect = RuntimeError("boom")
        _destroy_old_secret_versions(client, "projects/p/secrets/s", keep=5)
        client.destroy_secret_version.assert_not_called()

    def test_swallows_per_version_destroy_errors(self):
        """One bad destroy doesn't abort the rest."""
        client = MagicMock()
        State = MagicMock()
        State.ENABLED = "ENABLED"

        def mk(name, ts):
            v = MagicMock()
            v.name = name
            v.state = State.ENABLED
            v.State = State
            v.create_time = ts
            return v

        versions = [mk(f"v{i}", i) for i in range(7)]
        client.list_secret_versions.return_value = versions
        # First destroy fails, rest succeed
        client.destroy_secret_version.side_effect = [
            RuntimeError("nope"), None, None, None,
        ]

        _destroy_old_secret_versions(client, "projects/p/secrets/s", keep=3)
        # All 4 destroy calls attempted despite first failing
        assert client.destroy_secret_version.call_count == 4

    def test_does_nothing_when_below_keep_count(self):
        """If fewer than `keep` enabled versions exist, destroy nothing."""
        client = MagicMock()
        State = MagicMock()
        State.ENABLED = "ENABLED"

        def mk(name, ts):
            v = MagicMock()
            v.name = name
            v.state = State.ENABLED
            v.State = State
            v.create_time = ts
            return v

        versions = [mk(f"v{i}", i) for i in range(3)]
        client.list_secret_versions.return_value = versions

        _destroy_old_secret_versions(client, "projects/p/secrets/s", keep=5)
        client.destroy_secret_version.assert_not_called()
