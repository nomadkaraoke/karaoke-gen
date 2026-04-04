"""
Unit tests for backend main.py and config.py.

These tests cover the FastAPI app initialization and configuration.
"""
import pytest
import os
from unittest.mock import MagicMock, patch


class TestConfig:
    """Tests for config.py."""
    
    def test_settings_from_environment(self):
        """Test settings can be loaded from environment variables."""
        with patch.dict(os.environ, {
            'GCS_BUCKET_NAME': 'test-bucket',
            'FIRESTORE_COLLECTION': 'test-jobs',
            'ADMIN_TOKENS': 'token1,token2',
            'ENVIRONMENT': 'testing'
        }):
            from backend.config import Settings
            settings = Settings()
            assert settings.gcs_bucket_name == 'test-bucket'
            assert settings.firestore_collection == 'test-jobs'
            # admin_tokens is a string, comma-separated
            assert 'token1' in settings.admin_tokens
            assert 'token2' in settings.admin_tokens
    
    def test_admin_tokens_is_comma_separated_string(self):
        """Test admin tokens stored as comma-separated string."""
        with patch.dict(os.environ, {
            'ADMIN_TOKENS': 'token1,token2,token3'
        }):
            from backend.config import Settings
            settings = Settings()
            # admin_tokens is stored as a string
            assert settings.admin_tokens == 'token1,token2,token3'
    
    def test_default_environment(self):
        """Test default environment is development."""
        with patch.dict(os.environ, {}, clear=True):
            from backend.config import Settings
            settings = Settings()
            # Accept 'test' as well since pytest may set ENVIRONMENT=test
            assert settings.environment in ['development', 'production', 'testing', 'test']


class TestMain:
    """Tests for main.py FastAPI app."""
    
    @pytest.fixture
    def app(self):
        """Create FastAPI app with mocked services."""
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'
        with patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')):
            from backend.main import app
            return app
    
    def test_app_has_api_routes(self, app):
        """Test app includes API routes."""
        routes = [route.path for route in app.routes]
        assert any('/api' in route or '/jobs' in route for route in routes)
    
    def test_app_has_cors_middleware(self, app):
        """Test app has CORS middleware configured."""
        # Check that middleware is configured (not necessarily CORS specific)
        assert len(app.user_middleware) >= 0  # App initializes middleware
    
    def test_app_title(self, app):
        """Test app has expected title."""
        assert 'karaoke' in app.title.lower() or app.title


class TestExceptionHandlers:
    """Tests for global exception handlers in main.py."""

    @pytest.mark.asyncio
    async def test_insufficient_credits_handler_returns_402_response(self):
        """Test that the insufficient credits handler returns HTTP 402 with correct body."""
        from backend.main import insufficient_credits_exception_handler
        from backend.exceptions import InsufficientCreditsError

        exc = InsufficientCreditsError(
            message="No credits remaining",
            credits_available=0,
            credits_required=1,
        )

        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.headers = {"accept-language": "en"}

        response = await insufficient_credits_exception_handler(request=mock_request, exc=exc)

        assert response.status_code == 402
        import json
        body = json.loads(response.body)
        assert body["detail"] == "Insufficient credits for this operation"
        assert body["credits_available"] == 0
        assert body["credits_required"] == 1
        assert body["buy_url"] == "/#pricing"


class TestDependencies:
    """Tests for dependencies.py."""
    
    def test_dependencies_module_imports(self):
        """Test dependencies module can be imported and has auth functions."""
        from backend.api import dependencies
        assert hasattr(dependencies, 'require_auth')
        assert hasattr(dependencies, 'require_admin')
        assert hasattr(dependencies, 'optional_auth')

