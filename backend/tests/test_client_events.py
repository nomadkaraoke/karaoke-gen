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

    def test_cold_start_waking_event_accepted(self, client, fresh_limiter_and_db):
        resp = client.post(
            "/api/client-events",
            json=_event(type="banner_waking", detail={"stall_ms": 5000, "since_reachable_ms": None}),
        )
        assert resp.status_code == 202
        (doc,) = fresh_limiter_and_db.collection.return_value.add.call_args[0]
        assert doc["type"] == "banner_waking"

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


class TestIdentityAndNewFields:
    """2026-10-03: identity is resolved server-side from the bearer token."""

    def _auth_result(self, email, is_admin=False, valid=True):
        r = MagicMock()
        r.is_valid = valid
        r.user_email = email
        r.is_admin = is_admin
        return r

    def _post_with_token(self, client, auth_result, **overrides):
        svc = MagicMock()
        svc.validate_token_full.return_value = auth_result
        with patch("backend.services.auth_service.get_auth_service", return_value=svc):
            resp = client.post(
                "/api/client-events",
                json=_event(**overrides),
                headers={"Authorization": "Bearer sess-token"},
            )
        return resp, svc

    def _doc(self, db):
        (doc,) = db.collection.return_value.add.call_args[0]
        return doc

    def test_anonymous_has_no_identity(self, client, fresh_limiter_and_db):
        client.post("/api/client-events", json=_event())
        doc = self._doc(fresh_limiter_and_db)
        assert doc["user_email"] is None
        assert doc["is_admin"] is False
        assert doc["source"] == "client"

    def test_client_supplied_email_is_ignored(self, client, fresh_limiter_and_db):
        client.post("/api/client-events", json=_event(user_email="spoof@example.com"))
        assert self._doc(fresh_limiter_and_db)["user_email"] is None

    def test_token_resolves_customer(self, client, fresh_limiter_and_db):
        resp, svc = self._post_with_token(client, self._auth_result("fan@gmail.com"))
        assert resp.status_code == 202
        svc.validate_token_full.assert_called_once_with("sess-token")
        doc = self._doc(fresh_limiter_and_db)
        assert doc["user_email"] == "fan@gmail.com"
        assert (doc["is_admin"], doc["is_internal"], doc["is_test"]) == (False, False, False)

    def test_token_resolves_admin_internal(self, client, fresh_limiter_and_db):
        self._post_with_token(client, self._auth_result("andrew@nomadkaraoke.com", is_admin=True))
        doc = self._doc(fresh_limiter_and_db)
        assert doc["is_admin"] is True
        assert doc["is_internal"] is True

    def test_token_resolves_test_account(self, client, fresh_limiter_and_db):
        self._post_with_token(client, self._auth_result("k1.test-1@inbox.testmail.app"))
        assert self._doc(fresh_limiter_and_db)["is_test"] is True

    def test_invalid_token_is_anonymous_not_error(self, client, fresh_limiter_and_db):
        resp, _ = self._post_with_token(client, self._auth_result(None, valid=False))
        assert resp.status_code == 202
        assert self._doc(fresh_limiter_and_db)["user_email"] is None

    def test_auth_service_crash_is_anonymous_not_error(self, client, fresh_limiter_and_db):
        with patch("backend.services.auth_service.get_auth_service", side_effect=RuntimeError("x")):
            resp = client.post(
                "/api/client-events", json=_event(), headers={"Authorization": "Bearer t"}
            )
        assert resp.status_code == 202
        assert self._doc(fresh_limiter_and_db)["user_email"] is None

    def test_fingerprint_tab_episode_and_tenant_persisted(self, client, fresh_limiter_and_db):
        client.post(
            "/api/client-events",
            json=_event(
                url="https://randy-vild.nomadkaraoke.com/en/app/",
                device_fingerprint="635a6066abc",
                tab_id="tab-1",
                episode_id="ep-1",
            ),
        )
        doc = self._doc(fresh_limiter_and_db)
        assert doc["device_fingerprint"] == "635a6066abc"
        assert doc["tab_id"] == "tab-1"
        assert doc["episode_id"] == "ep-1"
        assert doc["tenant"] == "randy-vild"

    def test_consumer_host_has_no_tenant(self, client, fresh_limiter_and_db):
        client.post("/api/client-events", json=_event())
        assert self._doc(fresh_limiter_and_db)["tenant"] is None

    def test_banner_recovered_accepted(self, client, fresh_limiter_and_db):
        resp = client.post(
            "/api/client-events",
            json=_event(type="banner_recovered", detail={"duration_ms": 31000, "peak_status": "unavailable"}),
        )
        assert resp.status_code == 202
        assert self._doc(fresh_limiter_and_db)["type"] == "banner_recovered"

    def test_server_loop_stall_rejected_from_clients(self, client):
        resp = client.post("/api/client-events", json=_event(type="server_loop_stall"))
        assert resp.status_code == 422


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://randy-vild.nomadkaraoke.com/en/app/", "randy-vild"),
        ("https://vocalstar.nomadkaraoke.com/x", "vocalstar"),
        ("https://gen.nomadkaraoke.com/en/app/", None),
        ("https://nomadkaraoke.com/", None),
        ("https://evil.com/?x=.nomadkaraoke.com", None),
        ("", None),
    ],
)
def test_tenant_from_url(url, expected):
    assert client_events._tenant_from_url(url) == expected
