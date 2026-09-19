"""Tests for POST /api/client-events (degradation telemetry)."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import client_events


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(client_events.router, prefix="/api")
    return TestClient(app)


@pytest.fixture(autouse=True)
def fresh_limiter_and_db(monkeypatch):
    monkeypatch.setattr(client_events, "_limiter", client_events.RateLimiter(max_per_minute=30))
    db = MagicMock()
    monkeypatch.setattr(client_events, "_db_singleton", db)
    return db


def _event(**overrides):
    payload = {
        "type": "banner_unavailable",
        "url": "https://gen.nomadkaraoke.com/app/jobs?token=SECRET#/abc123/review",
        "job_id": "abc123",
        "locale": "en",
        "release": "deadbeef",
        "detail": {"stall_ms": 21000, "probe_ok": False},
    }
    payload.update(overrides)
    return payload


class TestReportClientEvent:
    def test_valid_event_persists_to_firestore(self, client, fresh_limiter_and_db):
        resp = client.post("/api/client-events", json=_event())

        assert resp.status_code == 202
        assert resp.json() == {"status": "recorded"}
        fresh_limiter_and_db.collection.assert_called_once_with("client_events")
        (doc,) = fresh_limiter_and_db.collection.return_value.add.call_args[0]
        assert doc["type"] == "banner_unavailable"
        assert doc["job_id"] == "abc123"
        assert doc["detail"] == {"stall_ms": 21000, "probe_ok": False}
        # Query string (may carry tokens) must be stripped before persisting.
        assert "SECRET" not in doc["url"]

    def test_unknown_event_type_rejected(self, client):
        resp = client.post("/api/client-events", json=_event(type="something_else"))
        assert resp.status_code == 422

    def test_rate_limited(self, client, monkeypatch):
        monkeypatch.setattr(client_events, "_limiter", client_events.RateLimiter(max_per_minute=2))
        assert client.post("/api/client-events", json=_event()).status_code == 202
        assert client.post("/api/client-events", json=_event()).status_code == 202
        assert client.post("/api/client-events", json=_event()).status_code == 429

    def test_bot_user_agent_ignored(self, client, fresh_limiter_and_db):
        resp = client.post(
            "/api/client-events",
            json=_event(),
            headers={"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"},
        )
        assert resp.status_code == 202
        assert resp.json() == {"status": "ignored"}
        fresh_limiter_and_db.collection.assert_not_called()

    def test_firestore_failure_still_returns_202(self, client, fresh_limiter_and_db):
        fresh_limiter_and_db.collection.return_value.add.side_effect = Exception("firestore down")
        resp = client.post("/api/client-events", json=_event())
        assert resp.status_code == 202  # telemetry must never error back to users

    def test_detail_is_capped(self):
        big = {f"key{i}": "x" * 500 for i in range(30)}
        capped = client_events._capped_detail(big)
        assert len(capped) == 12
        assert all(len(v) <= 200 for v in capped.values())
        assert client_events._capped_detail(None) == {}
        assert client_events._capped_detail("not a dict") == {}
