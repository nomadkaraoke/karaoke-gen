"""
Unit tests for API routes.

These tests use FastAPI TestClient with mocked services to test
route logic without hitting real cloud services.
"""
import pytest
import json
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
from io import BytesIO

from backend.models.job import Job, JobStatus


class TestHealthRoutes:
    """Tests for health.py routes."""
    
    @pytest.fixture
    def client(self):
        """Create test client with mocked dependencies."""
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'
        with patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')):
            from backend.main import app
            return TestClient(app)
    
    def test_health_endpoint_returns_200(self, client, auth_headers):
        """Test /api/health returns 200 OK."""
        response = client.get("/api/health", )
        assert response.status_code == 200
    
    def test_health_endpoint_returns_healthy_status(self, client, auth_headers):
        """Test health endpoint returns healthy status."""
        response = client.get("/api/health", )
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_root_endpoint_returns_200(self, client, auth_headers):
        """Test root endpoint returns 200."""
        response = client.get("/", )
        assert response.status_code == 200

    @patch('backend.api.routes.health.check_flacfetch_service_status')
    def test_flacfetch_health_not_configured(self, mock_check, client):
        """Test /api/health/flacfetch when service is not configured."""
        mock_check.return_value = {"configured": False}
        response = client.get("/api/health/flacfetch")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["status"] == "not_configured"

    @patch('backend.api.routes.health.check_flacfetch_service_status')
    def test_flacfetch_health_offline(self, mock_check, client):
        """Test /api/health/flacfetch when service is offline."""
        mock_check.return_value = {
            "configured": True,
            "available": False,
            "error": "Connection timeout"
        }
        response = client.get("/api/health/flacfetch")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["status"] == "offline"
        assert data["error"] == "Connection timeout"

    @patch('backend.api.routes.health.check_flacfetch_service_status')
    def test_flacfetch_health_ok(self, mock_check, client):
        """Test /api/health/flacfetch when service is healthy."""
        mock_check.return_value = {
            "configured": True,
            "available": True,
            "status": "healthy",
            "version": "1.2.3"
        }
        response = client.get("/api/health/flacfetch")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["status"] == "ok"
        assert data["version"] == "1.2.3"

    @patch('backend.api.routes.health.check_encoding_worker_status')
    def test_encoding_worker_health_not_configured(self, mock_check, client):
        """Test /api/health/encoding-worker when not configured."""
        mock_check.return_value = {"configured": False}
        response = client.get("/api/health/encoding-worker")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["status"] == "not_configured"

    @patch('backend.api.routes.health.check_encoding_worker_status')
    def test_encoding_worker_health_offline(self, mock_check, client):
        """Test /api/health/encoding-worker when offline."""
        mock_check.return_value = {
            "configured": True,
            "available": False,
            "error": "Worker unreachable"
        }
        response = client.get("/api/health/encoding-worker")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["status"] == "offline"
        assert data["error"] == "Worker unreachable"

    @patch('backend.api.routes.health.check_encoding_worker_status')
    def test_encoding_worker_health_ok(self, mock_check, client):
        """Test /api/health/encoding-worker when healthy."""
        mock_check.return_value = {
            "configured": True,
            "available": True,
            "status": "ok",
            "wheel_version": "0.123.0",
            "active_jobs": 2,
            "queue_length": 5
        }
        response = client.get("/api/health/encoding-worker")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["status"] == "ok"
        assert data["version"] == "0.123.0"
        assert data["active_jobs"] == 2
        assert data["queue_length"] == 5


class TestJobRoutes:
    """Tests for jobs.py routes.
    
    Note: These tests verify the route module structure.
    Full integration tests are in test_api_integration.py.
    """
    
    def test_jobs_router_exists(self):
        """Test jobs router can be imported."""
        from backend.api.routes import jobs
        assert hasattr(jobs, 'router')
    
    def test_jobs_router_has_expected_endpoints(self):
        """Test jobs router defines expected endpoints."""
        from backend.api.routes.jobs import router
        routes = [route.path for route in router.routes]
        assert '/jobs' in routes or any('/jobs' in r for r in routes)


class TestInternalRoutes:
    """Tests for internal.py routes.
    
    Note: These tests are minimal because the internal routes
    trigger actual worker processing which requires complex mocking.
    The actual worker logic is tested in test_workers.py.
    """
    
    def test_internal_endpoint_structure(self):
        """Test internal route module has expected endpoints."""
        from backend.api.routes import internal
        # Just verify the module can be imported
        assert hasattr(internal, 'router')


class TestFileUploadRoutes:
    """Tests for file_upload.py routes.
    
    Note: These tests verify the route module structure.
    Full integration tests are in test_api_integration.py.
    """
    
    def test_file_upload_router_exists(self):
        """Test file upload router can be imported."""
        from backend.api.routes import file_upload
        assert hasattr(file_upload, 'router')

