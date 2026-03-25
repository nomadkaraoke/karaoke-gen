"""
Unit tests for encoding worker lifecycle endpoints.

Tests the warmup and heartbeat API endpoints.
"""
import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.routes.encoding_worker import router, get_worker_manager
from backend.api.dependencies import require_admin


def get_mock_admin():
    """Override for require_admin dependency."""
    return ("admin-token", "admin", 0)


class TestWarmupEndpoint:
    def setup_method(self):
        self.mock_manager = MagicMock()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.dependency_overrides[require_admin] = get_mock_admin
        self.app.dependency_overrides[get_worker_manager] = lambda: self.mock_manager
        self.client = TestClient(self.app)

    def test_warmup_starts_stopped_vm(self):
        self.mock_manager.ensure_primary_running.return_value = {
            "started": True,
            "vm_name": "encoding-worker-a",
            "primary_url": "http://34.1.2.3:8080",
        }
        response = self.client.post("/api/internal/encoding-worker/warmup")
        assert response.status_code == 200
        data = response.json()
        assert data["started"] is True
        assert data["vm_name"] == "encoding-worker-a"

    def test_warmup_already_running(self):
        self.mock_manager.ensure_primary_running.return_value = {
            "started": False,
            "vm_name": "encoding-worker-a",
            "primary_url": "http://34.1.2.3:8080",
        }
        response = self.client.post("/api/internal/encoding-worker/warmup")
        assert response.status_code == 200
        assert response.json()["started"] is False

    def test_warmup_handles_error(self):
        self.mock_manager.ensure_primary_running.side_effect = RuntimeError(
            "VM not found"
        )
        response = self.client.post("/api/internal/encoding-worker/warmup")
        assert response.status_code == 200
        assert response.json()["started"] is False
        assert "error" in response.json()


class TestHeartbeatEndpoint:
    def setup_method(self):
        self.mock_manager = MagicMock()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.dependency_overrides[require_admin] = get_mock_admin
        self.app.dependency_overrides[get_worker_manager] = lambda: self.mock_manager
        self.client = TestClient(self.app)

    def test_heartbeat_updates_activity(self):
        response = self.client.post("/api/internal/encoding-worker/heartbeat")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        self.mock_manager.update_activity.assert_called_once()

    def test_heartbeat_handles_error(self):
        self.mock_manager.update_activity.side_effect = RuntimeError(
            "Firestore error"
        )
        response = self.client.post("/api/internal/encoding-worker/heartbeat")
        assert response.status_code == 200
        assert response.json()["status"] == "error"
