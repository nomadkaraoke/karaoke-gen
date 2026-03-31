"""Tests for the /api/health/system-status endpoint."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture
def mock_encoding_status():
    return {
        "configured": True,
        "enabled": True,
        "available": True,
        "status": "ok",
        "active_jobs": 0,
        "queue_length": 0,
        "wheel_version": "0.155.3",
    }


@pytest.fixture
def mock_flacfetch_status():
    return {
        "configured": True,
        "available": True,
        "status": "ok",
        "version": "0.19.1",
    }


@pytest.fixture
def mock_separator_status():
    return {
        "available": True,
        "status": "ok",
        "version": "0.4.3",
    }


@pytest.fixture
def mock_encoding_worker_config():
    """Mock EncodingWorkerConfig dataclass."""
    config = MagicMock()
    config.primary_vm = "encoding-worker-b"
    config.primary_ip = "34.10.189.118"
    config.primary_version = "0.155.3"
    config.primary_deployed_at = "2026-03-31T18:10:00Z"
    config.secondary_vm = "encoding-worker-a"
    config.secondary_ip = "34.57.78.246"
    config.secondary_version = "0.155.2"
    config.secondary_deployed_at = "2026-03-29T14:00:00Z"
    config.last_swap_at = "2026-03-31T18:10:00Z"
    config.deploy_in_progress = False
    config.deploy_in_progress_since = None
    config.last_activity_at = None
    return config


class TestSystemStatusPublic:
    """Tests for unauthenticated /api/health/system-status."""

    @pytest.mark.asyncio
    async def test_returns_all_services(
        self, mock_encoding_status, mock_flacfetch_status, mock_separator_status
    ):
        from backend.api.routes.health import system_status

        with patch("backend.api.routes.health.check_encoding_worker_status", new_callable=AsyncMock, return_value=mock_encoding_status), \
             patch("backend.api.routes.health.check_flacfetch_service_status", new_callable=AsyncMock, return_value=mock_flacfetch_status), \
             patch("backend.api.routes.health.check_audio_separator_status", return_value=mock_separator_status), \
             patch("backend.api.routes.health.VERSION", "0.155.3"), \
             patch("backend.api.routes.health.COMMIT_SHA", "8e2a949a"), \
             patch("backend.api.routes.health.PR_NUMBER", "644"), \
             patch("backend.api.routes.health.PR_TITLE", "fix: blue-green deploy"), \
             patch("backend.api.routes.health.STARTUP_TIME", "2026-03-31T18:10:00Z"):

            result = await system_status(auth=None)

        assert "services" in result
        services = result["services"]

        # All 5 services present
        assert "frontend" in services
        assert "backend" in services
        assert "encoder" in services
        assert "flacfetch" in services
        assert "separator" in services

        # Backend has build metadata
        backend_svc = services["backend"]
        assert backend_svc["version"] == "0.155.3"
        assert backend_svc["commit_sha"] == "8e2a949a"
        assert backend_svc["pr_number"] == "644"

        # Encoder has status
        assert services["encoder"]["status"] == "ok"
        assert services["encoder"]["version"] == "0.155.3"

        # No admin_details without auth
        assert "admin_details" not in services["encoder"]

    @pytest.mark.asyncio
    async def test_offline_service(self, mock_flacfetch_status, mock_separator_status):
        from backend.api.routes.health import system_status

        offline_encoding = {
            "configured": True,
            "enabled": True,
            "available": False,
            "error": "Connection refused",
        }

        with patch("backend.api.routes.health.check_encoding_worker_status", new_callable=AsyncMock, return_value=offline_encoding), \
             patch("backend.api.routes.health.check_flacfetch_service_status", new_callable=AsyncMock, return_value=mock_flacfetch_status), \
             patch("backend.api.routes.health.check_audio_separator_status", return_value=mock_separator_status), \
             patch("backend.api.routes.health.VERSION", "0.155.3"), \
             patch("backend.api.routes.health.COMMIT_SHA", ""), \
             patch("backend.api.routes.health.PR_NUMBER", ""), \
             patch("backend.api.routes.health.PR_TITLE", ""), \
             patch("backend.api.routes.health.STARTUP_TIME", "2026-03-31T18:10:00Z"):

            result = await system_status(auth=None)

        assert result["services"]["encoder"]["status"] == "offline"


class TestSystemStatusAdmin:
    """Tests for admin-authenticated /api/health/system-status."""

    @pytest.mark.asyncio
    async def test_admin_gets_bluegreen_details(
        self, mock_encoding_status, mock_flacfetch_status, mock_separator_status, mock_encoding_worker_config
    ):
        from backend.api.routes.health import system_status
        from backend.services.auth_service import UserType

        mock_auth = ("token123", UserType.ADMIN, -1)
        mock_manager = MagicMock()
        mock_manager.get_config.return_value = mock_encoding_worker_config

        with patch("backend.api.routes.health.check_encoding_worker_status", new_callable=AsyncMock, return_value=mock_encoding_status), \
             patch("backend.api.routes.health.check_flacfetch_service_status", new_callable=AsyncMock, return_value=mock_flacfetch_status), \
             patch("backend.api.routes.health.check_audio_separator_status", return_value=mock_separator_status), \
             patch("backend.api.routes.health.VERSION", "0.155.3"), \
             patch("backend.api.routes.health.COMMIT_SHA", ""), \
             patch("backend.api.routes.health.PR_NUMBER", ""), \
             patch("backend.api.routes.health.PR_TITLE", ""), \
             patch("backend.api.routes.health.STARTUP_TIME", "2026-03-31T18:10:00Z"), \
             patch("backend.api.routes.health._get_encoding_worker_manager", return_value=mock_manager):

            result = await system_status(auth=mock_auth)

        encoder = result["services"]["encoder"]
        assert "admin_details" in encoder
        admin = encoder["admin_details"]
        assert admin["primary_vm"] == "encoding-worker-b"
        assert admin["secondary_vm"] == "encoding-worker-a"
        assert admin["last_swap_at"] == "2026-03-31T18:10:00Z"
        assert admin["deploy_in_progress"] is False
