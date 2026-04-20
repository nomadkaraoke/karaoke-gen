"""API tests for POST /api/client-errors."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.client_errors import router as client_errors_router


@pytest.fixture
def client(monkeypatch):
    # reset the module-level limiter between tests
    import backend.api.routes.client_errors as mod
    mod._limiter = mod.RateLimiter(max_per_minute=60)

    fake_adapter = MagicMock()
    fake_adapter.upsert_pattern.return_value = MagicMock(
        pattern_id="deadbeef", is_new=True, previous_status=None
    )
    monkeypatch.setattr(mod, "_get_adapter", lambda: fake_adapter)

    app = FastAPI()
    app.include_router(client_errors_router, prefix="/api")
    client = TestClient(app)
    client.fake_adapter = fake_adapter  # type: ignore[attr-defined]
    return client


def _payload(**overrides):
    base = {
        "message": "TypeError: Cannot read properties of null",
        "stack": "TypeError: Cannot read properties of null\n  at x (app.js:1:2)",
        "url": "https://gen.nomadkaraoke.com/en/app/jobs/#/abc/review",
        "user_agent": "Mozilla/5.0 (Android 14; Mobile; rv:138.0) Gecko/138.0 Firefox/138.0",
        "release": "abc1234",
        "user_email": None,
        "viewport": {"w": 412, "h": 915},
        "locale": "en",
        "source": "window.onerror",
    }
    base.update(overrides)
    return base


def test_accepts_valid_report_and_upserts(client):
    resp = client.post("/api/client-errors", json=_payload())
    assert resp.status_code == 202
    assert resp.json()["pattern_id"] == "deadbeef"
    client.fake_adapter.upsert_pattern.assert_called_once()
    (pd,), _ = client.fake_adapter.upsert_pattern.call_args
    assert pd.service == "frontend"
    assert pd.resource_type == "browser"


def test_rejects_missing_message(client):
    resp = client.post("/api/client-errors", json=_payload(message=""))
    assert resp.status_code == 422


def test_caps_oversized_payload(client):
    huge = "x" * 200_000
    resp = client.post("/api/client-errors", json=_payload(stack=huge))
    # server accepts but truncates — sample_message must be <= MAX
    assert resp.status_code == 202
    (pd,), _ = client.fake_adapter.upsert_pattern.call_args
    from backend.services.error_monitor.frontend_ingestion import (
        MAX_SAMPLE_MESSAGE_CHARS,
    )
    assert len(pd.sample_message) <= MAX_SAMPLE_MESSAGE_CHARS


def test_rate_limit_returns_429(client, monkeypatch):
    import backend.api.routes.client_errors as mod
    mod._limiter = mod.RateLimiter(max_per_minute=2)
    assert client.post("/api/client-errors", json=_payload()).status_code == 202
    assert client.post("/api/client-errors", json=_payload()).status_code == 202
    r3 = client.post("/api/client-errors", json=_payload())
    assert r3.status_code == 429


def test_strips_sensitive_query_string(client):
    resp = client.post(
        "/api/client-errors",
        json=_payload(url="https://gen.nomadkaraoke.com/en/app/jobs/?token=SECRET#/abc/review"),
    )
    assert resp.status_code == 202
    (pd,), _ = client.fake_adapter.upsert_pattern.call_args
    assert "SECRET" not in pd.sample_message
    assert "token=" not in pd.sample_message
