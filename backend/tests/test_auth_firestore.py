"""
Tests for AuthService and FirestoreService.

These tests focus on the service initialization and basic operations.
"""
import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch


class TestAuthServiceBasics:
    """Basic tests for AuthService."""
    
    def test_auth_service_can_import(self):
        """Test AuthService can be imported."""
        from backend.services.auth_service import AuthService
        assert AuthService is not None
    
    def test_auth_service_initialization(self):
        """Test AuthService initializes with mocked dependencies."""
        mock_firestore = MagicMock()
        
        with patch('backend.services.auth_service.FirestoreService', return_value=mock_firestore), \
             patch.dict('os.environ', {'ADMIN_TOKENS': 'test-token'}):
            from backend.services.auth_service import AuthService
            service = AuthService()
            assert service is not None
    
    def test_auth_service_has_validate_method(self):
        """Test AuthService has token validation method."""
        from backend.services.auth_service import AuthService
        assert hasattr(AuthService, 'validate_token') or hasattr(AuthService, 'verify_token')


class TestIncrementTokenUsage:
    """Tests for increment_token_usage after credit deduction cleanup."""

    def test_session_token_does_not_deduct_credits(self):
        """
        Verify that increment_token_usage does NOT deduct credits for session tokens.

        Credit deduction is handled by JobManager.create_job(), not here.
        This test ensures the dead code removal didn't break the method.
        """
        mock_firestore = MagicMock()
        # Session token: not in admin_tokens, not in Firestore auth_tokens
        mock_firestore.get_token.return_value = None

        with patch('backend.services.auth_service.FirestoreService', return_value=mock_firestore), \
             patch.dict('os.environ', {'ADMIN_TOKENS': 'admin-token'}):
            from backend.services.auth_service import AuthService
            service = AuthService()

            # Mock validate_token to return valid session user
            with patch.object(service, 'validate_token', return_value=(True, 'stripe', 5, 'OK')):
                result = service.increment_token_usage("session-token-123", "job-abc")

        # Should return True (no-op for session tokens)
        assert result is True
        # Should NOT try to deduct credits via user_service
        # (If it did, it would fail since user_service is not mocked)


class TestFirestoreServiceBasics:
    """Basic tests for FirestoreService."""
    
    def test_firestore_service_can_import(self):
        """Test FirestoreService can be imported."""
        from backend.services.firestore_service import FirestoreService
        assert FirestoreService is not None
    
    def test_firestore_service_initialization(self):
        """Test FirestoreService initializes with mocked client."""
        mock_client = MagicMock()
        
        with patch('backend.services.firestore_service.firestore.Client', return_value=mock_client):
            from backend.services.firestore_service import FirestoreService
            service = FirestoreService()
            assert service is not None
    
    def test_firestore_service_has_crud_methods(self):
        """Test FirestoreService has CRUD methods."""
        from backend.services.firestore_service import FirestoreService
        assert hasattr(FirestoreService, 'create_job')
        assert hasattr(FirestoreService, 'get_job')
        assert hasattr(FirestoreService, 'update_job')
        assert hasattr(FirestoreService, 'delete_job')


class TestStorageServiceBasics:
    """Basic tests for StorageService."""
    
    def test_storage_service_can_import(self):
        """Test StorageService can be imported."""
        from backend.services.storage_service import StorageService
        assert StorageService is not None
    
    def test_storage_service_initialization(self):
        """Test StorageService initializes with mocked client."""
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        
        with patch('backend.services.storage_service.storage.Client', return_value=mock_client):
            from backend.services.storage_service import StorageService
            service = StorageService()
            assert service is not None
    
    def test_storage_service_has_file_methods(self):
        """Test StorageService has file operation methods."""
        from backend.services.storage_service import StorageService
        assert hasattr(StorageService, 'upload_file')
        assert hasattr(StorageService, 'download_file')


class TestWorkerServiceBasics:
    """Basic tests for WorkerService."""
    
    def test_worker_service_can_import(self):
        """Test WorkerService can be imported."""
        from backend.services.worker_service import WorkerService
        assert WorkerService is not None
    
    def test_worker_service_initialization(self):
        """Test WorkerService initializes correctly."""
        from backend.services.worker_service import WorkerService
        service = WorkerService()
        assert service is not None
    
    def test_worker_service_has_trigger_methods(self):
        """Test WorkerService has trigger methods."""
        from backend.services.worker_service import WorkerService
        assert hasattr(WorkerService, 'trigger_audio_worker')
        assert hasattr(WorkerService, 'trigger_lyrics_worker')
        assert hasattr(WorkerService, 'trigger_screens_worker')
        assert hasattr(WorkerService, 'trigger_video_worker')
    
    def test_worker_service_get_base_url(self):
        """Test WorkerService constructs base URL."""
        from backend.services.worker_service import WorkerService
        service = WorkerService()
        url = service._get_base_url()
        assert 'http' in url


class TestSessionTokenValidation:
    """Tests for session token validation in AuthService.

    PR #120: Fixed authentication to recognize session tokens from magic link auth.
    Previously, validate_token() only checked admin tokens and auth_tokens collection.
    Session tokens (created via magic link) are stored in the sessions collection,
    so they were incorrectly rejected as "Invalid token".
    """

    def test_validate_token_checks_session_on_fallback(self):
        """Test that validate_token falls back to checking session tokens."""
        mock_firestore = MagicMock()
        mock_firestore.get_token.return_value = None  # Token not in auth_tokens

        mock_user = MagicMock()
        mock_user.email = "test@example.com"
        mock_user.credits = 5

        mock_user_service = MagicMock()
        mock_user_service.validate_session.return_value = (True, mock_user, "Valid session")

        with patch('backend.services.auth_service.FirestoreService', return_value=mock_firestore), \
             patch('backend.services.user_service.get_user_service', return_value=mock_user_service), \
             patch.dict('os.environ', {'ADMIN_TOKENS': 'admin-token-123'}):
            # Clear the cached instance to get fresh state
            import backend.services.auth_service
            backend.services.auth_service._auth_service = None
            from backend.services.auth_service import AuthService, UserType

            service = AuthService()
            is_valid, user_type, remaining, msg = service.validate_token("session-token-xyz")

            assert is_valid is True
            assert user_type == UserType.STRIPE
            assert remaining == 5  # User's credits
            mock_user_service.validate_session.assert_called_once_with("session-token-xyz")

    def test_validate_token_returns_invalid_when_session_invalid(self):
        """Test that validate_token returns invalid when session validation fails."""
        mock_firestore = MagicMock()
        mock_firestore.get_token.return_value = None  # Token not in auth_tokens

        mock_user_service = MagicMock()
        mock_user_service.validate_session.return_value = (False, None, "Invalid session")

        with patch('backend.services.auth_service.FirestoreService', return_value=mock_firestore), \
             patch('backend.services.user_service.get_user_service', return_value=mock_user_service), \
             patch.dict('os.environ', {'ADMIN_TOKENS': 'admin-token-123'}):
            import backend.services.auth_service
            backend.services.auth_service._auth_service = None
            from backend.services.auth_service import AuthService, UserType

            service = AuthService()
            is_valid, user_type, remaining, msg = service.validate_token("invalid-session")

            assert is_valid is False
            assert remaining == 0
            assert "Invalid token" in msg

    def test_validate_token_returns_zero_credits_when_user_has_none(self):
        """Test that validate_token handles users with 0 credits correctly."""
        mock_firestore = MagicMock()
        mock_firestore.get_token.return_value = None

        mock_user = MagicMock()
        mock_user.email = "test@example.com"
        mock_user.credits = 0

        mock_user_service = MagicMock()
        mock_user_service.validate_session.return_value = (True, mock_user, "Valid session")

        with patch('backend.services.auth_service.FirestoreService', return_value=mock_firestore), \
             patch('backend.services.user_service.get_user_service', return_value=mock_user_service), \
             patch.dict('os.environ', {'ADMIN_TOKENS': 'admin-token-123'}):
            import backend.services.auth_service
            backend.services.auth_service._auth_service = None
            from backend.services.auth_service import AuthService, UserType

            service = AuthService()
            is_valid, user_type, remaining, msg = service.validate_token("session-token")

            # User is authenticated but has no credits
            assert is_valid is True
            assert user_type == UserType.STRIPE
            assert remaining == 0
            assert "no credits" in msg.lower()


class TestJobManagerBasics:
    """Basic tests for JobManager."""

    def test_job_manager_can_import(self):
        """Test JobManager can be imported."""
        from backend.services.job_manager import JobManager
        assert JobManager is not None
    
    def test_job_manager_initialization(self):
        """Test JobManager initializes with mocked services."""
        mock_firestore = MagicMock()
        mock_storage = MagicMock()
        
        with patch('backend.services.job_manager.FirestoreService', return_value=mock_firestore), \
             patch('backend.services.job_manager.StorageService', return_value=mock_storage):
            from backend.services.job_manager import JobManager
            manager = JobManager()
            assert manager is not None
    
    def test_job_manager_has_crud_methods(self):
        """Test JobManager has CRUD methods."""
        from backend.services.job_manager import JobManager
        assert hasattr(JobManager, 'create_job')
        assert hasattr(JobManager, 'get_job')
        assert hasattr(JobManager, 'delete_job')
        assert hasattr(JobManager, 'list_jobs')

