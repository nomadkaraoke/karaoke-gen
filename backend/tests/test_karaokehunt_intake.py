"""Tests for POST /api/karaokehunt/request (retired-app request intake)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import require_admin
from backend.api.routes import karaokehunt
from backend.services.error_monitor.frontend_ingestion import RateLimiter

SECRET = "test-forwarder-secret"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(karaokehunt.router, prefix="/api")
    monkeypatch.setattr(
        karaokehunt, "get_settings",
        lambda: MagicMock(karaokehunt_forwarder_secret=SECRET),
    )
    monkeypatch.setattr(karaokehunt, "_limiter", RateLimiter(max_per_minute=30))
    return TestClient(app)


def _payload(**over):
    payload = {
        "email": "aloy@yahoo.com",
        "artist": "Olivia Rodrigo",
        "title": "good 4 u",
        "input_url": "",
        "model_name": "UVR_MDXNET_KARA_2.onnx",
    }
    payload.update(over)
    return payload


def _headers(secret=SECRET, **extra):
    return {"X-KH-Forwarder-Secret": secret, **extra}


class TestIntakeGate:
    def test_unconfigured_secret_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(
            karaokehunt, "get_settings",
            lambda: MagicMock(karaokehunt_forwarder_secret=""),
        )
        resp = client.post("/api/karaokehunt/request", json=_payload(),
                           headers=_headers())
        assert resp.status_code == 503

    def test_wrong_secret_forbidden(self, client):
        resp = client.post("/api/karaokehunt/request", json=_payload(),
                           headers=_headers(secret="nope"))
        assert resp.status_code == 403

    def test_missing_secret_forbidden(self, client):
        resp = client.post("/api/karaokehunt/request", json=_payload())
        assert resp.status_code == 403

    def test_rate_limited(self, client, monkeypatch):
        monkeypatch.setattr(karaokehunt, "_limiter", RateLimiter(max_per_minute=2))
        with patch.object(karaokehunt.karaokehunt_conversion, "create_intake",
                          return_value={"id": "d", "outcome": "invalid"}):
            codes = [
                client.post("/api/karaokehunt/request", json=_payload(),
                            headers=_headers()).status_code
                for _ in range(3)
            ]
        assert codes == [200, 200, 429]

    def test_rate_limit_keys_on_forwarded_ip(self, client, monkeypatch):
        monkeypatch.setattr(karaokehunt, "_limiter", RateLimiter(max_per_minute=1))
        with patch.object(karaokehunt.karaokehunt_conversion, "create_intake",
                          return_value={"id": "d", "outcome": "invalid"}):
            first = client.post("/api/karaokehunt/request", json=_payload(),
                                headers=_headers(**{"X-KH-Client-IP": "1.1.1.1"}))
            second = client.post("/api/karaokehunt/request", json=_payload(),
                                 headers=_headers(**{"X-KH-Client-IP": "2.2.2.2"}))
        assert first.status_code == 200
        assert second.status_code == 200  # different forwarded IP, fresh bucket

    def test_invalid_json_rejected(self, client):
        resp = client.post("/api/karaokehunt/request", content=b"not json",
                           headers={**_headers(), "Content-Type": "application/json"})
        assert resp.status_code == 400


class TestIntakeProcessing:
    def test_valid_request_processes(self, client):
        with patch.object(karaokehunt.karaokehunt_conversion, "create_intake",
                          return_value={"id": "doc1", "outcome": None}) as create, \
             patch.object(karaokehunt.karaokehunt_conversion, "process_intake",
                          new=AsyncMock(return_value={"status": "job_created"})) as process:
            resp = client.post("/api/karaokehunt/request", json=_payload(),
                               headers=_headers(**{"X-KH-Client-IP": "3.3.3.3"}))

        assert resp.status_code == 200
        assert resp.json() == {"status": "success", "id": "doc1"}
        assert create.call_args.args[0]["email"] == "aloy@yahoo.com"
        assert create.call_args.kwargs["client_ip"] == "3.3.3.3"
        process.assert_awaited_once_with("doc1")

    def test_invalid_payload_logged_but_not_processed(self, client):
        with patch.object(karaokehunt.karaokehunt_conversion, "create_intake",
                          return_value={"id": "doc1", "outcome": "invalid"}), \
             patch.object(karaokehunt.karaokehunt_conversion, "process_intake",
                          new=AsyncMock()) as process:
            resp = client.post("/api/karaokehunt/request",
                               json=_payload(email="garbage"), headers=_headers())

        assert resp.status_code == 200
        process.assert_not_awaited()

    def test_conversion_crash_still_returns_success(self, client):
        db = MagicMock()
        with patch.object(karaokehunt.karaokehunt_conversion, "create_intake",
                          return_value={"id": "doc1", "outcome": None}), \
             patch.object(karaokehunt.karaokehunt_conversion, "process_intake",
                          new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch.object(karaokehunt.karaokehunt_conversion, "_get_db",
                          return_value=db):
            resp = client.post("/api/karaokehunt/request", json=_payload(),
                               headers=_headers())

        assert resp.status_code == 200  # the app must never see a failure
        db.collection.return_value.document.return_value.update.assert_called_once()
        marked = db.collection.return_value.document.return_value.update.call_args.args[0]
        assert marked["outcome"] == "error"


class TestReprocess:
    def test_reprocess_requires_admin(self, client):
        resp = client.post("/api/karaokehunt/internal/reprocess/doc1")
        assert resp.status_code in (401, 403)

    def test_reprocess_forces(self, client):
        app = client.app
        app.dependency_overrides[require_admin] = lambda: MagicMock(is_admin=True)
        try:
            with patch.object(karaokehunt.karaokehunt_conversion, "process_intake",
                              new=AsyncMock(return_value={"status": "job_created"})) as process:
                resp = client.post("/api/karaokehunt/internal/reprocess/doc1")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        process.assert_awaited_once_with("doc1", force=True)
