"""
Unit tests for encoding worker lifecycle endpoints.

Tests the warmup and heartbeat API endpoints.
"""
import logging

import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.routes.encoding_worker import router, get_worker_manager
from backend.api.dependencies import require_admin
from backend.services.encoding_errors import (
    EncodingWorkerCapacityError,
    EncodingWorkerStartError,
)


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

    def test_warmup_503_start_failure_logged_as_warning(self, caplog):
        """A transient 503/start failure must NOT log at ERROR.

        The encoding flow falls back to alternate-zone workers, so this is a
        self-healing event. Logging it at ERROR trips the production error
        monitor (severity>=ERROR) and pages us for a non-incident.
        """
        self.mock_manager.ensure_primary_running.side_effect = (
            EncodingWorkerStartError(
                "VM encoding-worker-b start failed in us-central1-c: "
                "503 — SERVICE UNAVAILABLE",
                vm_name="encoding-worker-b",
                zone="us-central1-c",
            )
        )
        with caplog.at_level(logging.WARNING):
            response = self.client.post("/api/internal/encoding-worker/warmup")
        assert response.status_code == 200
        assert response.json()["started"] is False
        warmup_records = [
            r for r in caplog.records if "warmup failed" in r.getMessage()
        ]
        assert warmup_records, "expected a warmup-failure log record"
        assert all(r.levelno == logging.WARNING for r in warmup_records)
        # And nothing from this endpoint should be at ERROR.
        assert not [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR
            and r.name == "backend.api.routes.encoding_worker"
        ]

    def test_warmup_capacity_error_logged_as_warning(self, caplog):
        """Capacity exhaustion is also self-healing → WARNING, not ERROR."""
        self.mock_manager.ensure_primary_running.side_effect = (
            EncodingWorkerCapacityError(
                "VM encoding-worker-b could not be started in us-central1-c: "
                "ZONE_RESOURCE_POOL_EXHAUSTED",
                vm_name="encoding-worker-b",
                zone="us-central1-c",
                code="ZONE_RESOURCE_POOL_EXHAUSTED",
            )
        )
        with caplog.at_level(logging.WARNING):
            response = self.client.post("/api/internal/encoding-worker/warmup")
        assert response.status_code == 200
        assert not [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR
            and r.name == "backend.api.routes.encoding_worker"
        ]

    def test_warmup_unexpected_error_still_logged_as_error(self, caplog):
        """Genuinely unexpected failures must still surface at ERROR."""
        self.mock_manager.ensure_primary_running.side_effect = RuntimeError(
            "misconfigured service account"
        )
        with caplog.at_level(logging.WARNING):
            response = self.client.post("/api/internal/encoding-worker/warmup")
        assert response.status_code == 200
        error_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR
            and r.name == "backend.api.routes.encoding_worker"
        ]
        assert error_records, "unexpected errors must remain at ERROR severity"


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
