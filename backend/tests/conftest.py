"""
Shared pytest fixtures for backend tests.

Provides common mocks and test utilities across all test modules.

NOTE: Module-level mocks are NOT applied here to allow emulator tests to work.
Individual tests must mock dependencies as needed.
"""
# IMPORTANT: Set environment variables FIRST, before ANY imports that might
# trigger backend module loading and Settings singleton creation.
import os

# Set up test environment variables BEFORE importing any backend modules
# This must happen before Settings is instantiated, which occurs on first import
if 'FIRESTORE_EMULATOR_HOST' not in os.environ:
    os.environ.setdefault('ADMIN_TOKENS', 'test-admin-token')
    os.environ.setdefault('GOOGLE_CLOUD_PROJECT', 'test-project')
    os.environ.setdefault('GCS_BUCKET_NAME', 'test-bucket')
    os.environ.setdefault('FIRESTORE_COLLECTION', 'jobs')
    os.environ.setdefault('ENVIRONMENT', 'test')

import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime, UTC
from fastapi.testclient import TestClient


# Mock google.auth.default AND firestore for unit tests if not using emulator
# This prevents DefaultCredentialsError and FirestoreClient initialization during imports
if 'FIRESTORE_EMULATOR_HOST' not in os.environ:
    from unittest.mock import MagicMock
    
    # Mock google.auth.default - prevents credential errors
    try:
        import google.auth
        mock_credentials = MagicMock()
        mock_credentials.token = 'fake-token'
        mock_credentials.valid = True
        mock_credentials.universe_domain = 'googleapis.com'  # Required by google-cloud-storage
        mock_credentials.project_id = 'test-project'
        google.auth.default = MagicMock(return_value=(mock_credentials, 'test-project'))
    except ImportError:
        # If google.auth not installed, that's ok for some tests
        pass
    
    # Mock google.cloud.firestore.Client - prevents Firestore initialization
    # This is CRITICAL: when AuthService is instantiated, it creates FirestoreService
    # which tries to create a real Firestore client. The client returns MagicMocks
    # which fail when used as enum values (e.g., UserType).
    try:
        import google.cloud.firestore as firestore_module
        
        # Create a mock client that returns proper values for get_token
        mock_firestore_client = MagicMock()
        mock_collection = MagicMock()
        mock_doc = MagicMock()
        mock_snapshot = MagicMock()
        
        # For auth token lookups, return None (token not found in Firestore)
        # This forces validation to use admin tokens from environment
        mock_snapshot.exists = False
        mock_snapshot.to_dict.return_value = None
        mock_doc.get.return_value = mock_snapshot
        mock_collection.document.return_value = mock_doc
        mock_firestore_client.collection.return_value = mock_collection
        
        # Replace the Client class
        original_client = firestore_module.Client
        firestore_module.Client = MagicMock(return_value=mock_firestore_client)
    except ImportError:
        pass
    
    # Mock google.cloud.storage.Client - prevents GCS initialization
    try:
        import google.cloud.storage as storage_module
        mock_storage_client = MagicMock()
        mock_bucket = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket
        storage_module.Client = MagicMock(return_value=mock_storage_client)
    except ImportError:
        pass

from backend.models.job import Job, JobStatus, JobCreate


@pytest.fixture(autouse=True)
def mock_auth_dependency(request):
    """Mock the require_auth dependency for all tests using FastAPI's dependency override system."""
    # Skip for emulator tests and integration tests which use real auth
    test_path = str(request.fspath)
    if 'emulator' in test_path or 'integration' in test_path:
        yield
        return

    # Skip for service-only unit tests that don't need the FastAPI app
    # Also skip tests that manage their own auth mocks (e.g., push tests use AuthResult objects)
    service_only_tests = ['test_rate_limit_service', 'test_email_validation_service', 'test_rate_limits_api', 'test_gce_encoding_worker', 'test_push_routes', 'test_push_notification_service']
    if any(test_name in test_path for test_name in service_only_tests):
        yield
        return

    # Skip if FIRESTORE_EMULATOR_HOST is set (running in emulator environment)
    import os
    if os.environ.get('FIRESTORE_EMULATOR_HOST'):
        yield
        return
    
    from backend.services.auth_service import UserType, AuthResult
    from backend.api.dependencies import require_auth, require_admin, require_review_auth
    from backend.main import app

    # Create mock auth functions that return AuthResult objects
    async def mock_require_auth():
        """Mock require_auth to always return valid admin credentials."""
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            remaining_uses=999,
            message="Test admin token",
            is_admin=True
        )

    async def mock_require_admin():
        """Mock require_admin to always return valid admin credentials."""
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            remaining_uses=999,
            message="Test admin token",
            is_admin=True
        )

    async def mock_require_review_auth(job_id: str = "test123"):
        """Mock require_review_auth to always return valid review access."""
        return (job_id, "full")

    # Use FastAPI's dependency override system
    app.dependency_overrides[require_auth] = mock_require_auth
    app.dependency_overrides[require_admin] = mock_require_admin
    app.dependency_overrides[require_review_auth] = mock_require_review_auth
    
    yield
    
    # Clean up after test
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    """Get authentication headers for test requests."""
    return {
        "Authorization": "Bearer test-admin-token",
        "Content-Type": "application/json"
    }


@pytest.fixture
def mock_firestore():
    """Mock Firestore client for unit tests."""
    # Mock at module level for unit tests
    with patch('google.cloud.firestore.Client') as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        yield client


@pytest.fixture
def mock_storage_client():
    """Mock GCS Storage client for unit tests."""
    # Mock at module level for unit tests  
    with patch('google.cloud.storage.Client') as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        yield client


@pytest.fixture
def mock_httpx_client():
    """Mock httpx AsyncClient for worker service."""
    with patch('backend.services.worker_service.httpx.AsyncClient') as mock:
        client = AsyncMock()
        mock.return_value.__aenter__.return_value = client
        yield client


@pytest.fixture
def sample_job():
    """Create a sample Job instance for testing."""
    return Job(
        job_id="test123",
        status=JobStatus.PENDING,
        progress=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        url="https://youtube.com/watch?v=test"
    )


@pytest.fixture
def sample_job_create():
    """Create a sample JobCreate instance for testing."""
    return JobCreate(
        url="https://youtube.com/watch?v=test",
        artist="Test Artist",
        title="Test Song"
    )


@pytest.fixture
def test_client():
    """Create a FastAPI TestClient."""
    # Import here to avoid circular dependency
    from backend.main import app
    return TestClient(app)


def create_mock_job(**kwargs):
    """
    Factory function to create mock Job instances with custom fields.
    
    Args:
        **kwargs: Job fields to override defaults
        
    Returns:
        Job instance with specified fields
    """
    defaults = {
        "job_id": "test123",
        "status": JobStatus.PENDING,
        "progress": 0,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC)
    }
    defaults.update(kwargs)
    return Job(**defaults)

