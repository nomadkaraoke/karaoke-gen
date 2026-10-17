"""Tests for POST /api/users/auth/handoff-token (mobile app → signed-in web review).

Composed tests: the real login_code router, real UserService (sessions, magic-link
minting) and the real ``build_review_login_url`` over the in-memory Firestore fake
shared with the kjbox tests. Only the job store is faked.

Single use and expiry go through Firestore transactions the fake can't run, so
they're covered against the real emulator in
``backend/tests/emulator/test_handoff_token_lifecycle.py``.
"""
from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import login_code
from backend.services.user_service import MAGIC_LINKS_COLLECTION, get_user_service
from backend.tests.test_kjbox_partner_api import (  # noqa: F401  (fixtures + fakes)
    FakeDb,
    db,
    user_service,
)

EMAIL = "owner@example.com"


@pytest.fixture
def jobs(monkeypatch, user_service):
    from backend.services import job_manager as jm
    from backend.services import job_notification_service as jns

    store = {}

    class FakeJobManager:
        def get_job(self, job_id):
            return store.get(job_id)

    monkeypatch.setattr(jm, "JobManager", FakeJobManager)
    monkeypatch.setattr(jns, "get_user_service", lambda: user_service)
    monkeypatch.setenv("FRONTEND_URL", "https://gen.test")
    return store


@pytest.fixture
def client(user_service):
    app = FastAPI()
    app.include_router(login_code.router, prefix="/api")
    app.dependency_overrides[get_user_service] = lambda: user_service
    return TestClient(app)


def _job(status, email=EMAIL, job_id="job1234"):
    return SimpleNamespace(job_id=job_id, user_email=email, status=status, state_data={})


def _session(user_service, email=EMAIL):
    user_service.get_or_create_user(email)
    return user_service.create_session(email).token


def _handoff(client, token, job_id="job1234", **body):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/api/users/auth/handoff-token",
        json={"target": "job_review", "job_id": job_id, **body},
        headers=headers,
    )


def test_handoff_returns_url_for_owned_review_job(client, user_service, jobs, db):
    jobs["job1234"] = _job("awaiting_review")
    resp = _handoff(client, _session(user_service), locale="es")
    assert resp.status_code == 200
    body = resp.json()
    assert 0 < body["expires_in_seconds"] <= 120

    url = urlparse(body["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://gen.test/es/auth/verify"
    query = parse_qs(url.query)
    assert query["handoff"] == ["1"]
    token = query["token"][0]

    link = db.collection(MAGIC_LINKS_COLLECTION).docs[token]
    assert link["email"] == EMAIL
    assert link["purpose"] == "job_review:job1234"
    assert link["used"] is False
    assert link["handoff"] is True
    ttl = (datetime.fromisoformat(link["expires_at"]) - datetime.fromisoformat(link["created_at"])).total_seconds()
    assert 0 < ttl <= 120


def test_handoff_in_review_also_allowed(client, user_service, jobs):
    jobs["job1234"] = _job("in_review")
    assert _handoff(client, _session(user_service)).status_code == 200


@pytest.mark.parametrize("locale", ["fr", "ja", "tl", "ca"])
def test_handoff_uses_any_web_locale(client, user_service, jobs, locale):
    # The web app speaks 33 languages, not just the backend's email locales.
    jobs["job1234"] = _job("awaiting_review")
    url = _handoff(client, _session(user_service), locale=locale).json()["url"]
    assert url.startswith(f"https://gen.test/{locale}/auth/verify?")


def test_web_locales_match_frontend_routing_and_messages():
    import os

    from backend.i18n import WEB_LOCALES

    root = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
    with open(os.path.join(root, "i18n", "routing.ts")) as f:
        routing = f.read()
    block = routing.split("locales:", 1)[1].split("]", 1)[0]
    assert set(WEB_LOCALES) == set(re.findall(r"'([a-z]{2,3})'", block))
    messages = {n[:-5] for n in os.listdir(os.path.join(root, "messages")) if n.endswith(".json")}
    assert set(WEB_LOCALES) == messages


def test_handoff_unknown_locale_falls_back_to_en(client, user_service, jobs):
    jobs["job1234"] = _job("awaiting_review")
    url = _handoff(client, _session(user_service), locale="xx").json()["url"]
    assert url.startswith("https://gen.test/en/auth/verify?")


def test_handoff_404_for_other_users_job(client, user_service, jobs, db):
    jobs["job1234"] = _job("awaiting_review", email="someone@else.com")
    token = _session(user_service)
    for job_id in ("job1234", "missing1"):
        resp = _handoff(client, token, job_id=job_id)
        assert resp.status_code == 404 and resp.json()["detail"] == "not_found"
    assert db.collection(MAGIC_LINKS_COLLECTION).docs == {}


def test_handoff_409_when_not_in_review(client, user_service, jobs):
    jobs["job1234"] = _job("rendering_video")
    resp = _handoff(client, _session(user_service))
    assert resp.status_code == 409 and resp.json()["detail"] == "not_in_review"


def test_handoff_401_without_session(client, jobs):
    jobs["job1234"] = _job("awaiting_review")
    for token in (None, "not-a-session"):
        resp = _handoff(client, token)
        assert resp.status_code == 401 and resp.json()["detail"] == "invalid_session"


def test_handoff_rejects_unknown_target(client, user_service, jobs):
    jobs["job1234"] = _job("awaiting_review")
    resp = client.post(
        "/api/users/auth/handoff-token",
        json={"target": "admin", "job_id": "job1234"},
        headers={"Authorization": f"Bearer {_session(user_service)}"},
    )
    assert resp.status_code == 422


def test_handoff_token_purpose_maps_to_review_redirect():
    from backend.api.routes.users import _magic_link_redirect_path

    assert _magic_link_redirect_path("job_review:job1234") == "/app/jobs#/job1234/review"


def test_router_registered_in_main_app():
    from backend.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/users/auth/handoff-token" in paths


class TestAdminLoginTokenSeconds:
    def test_expiry_seconds_overrides_hours(self, user_service, db):
        tok = user_service.create_admin_login_token(EMAIL, expiry_seconds=90, purpose="job_review:x")
        ttl = (tok.expires_at - tok.created_at).total_seconds()
        assert 89 <= ttl <= 91

    @pytest.mark.parametrize("bad", [0, -5, 3601])
    def test_expiry_seconds_range(self, user_service, bad):
        with pytest.raises(ValueError):
            user_service.create_admin_login_token(EMAIL, expiry_seconds=bad)

    def test_default_hours_unchanged(self, user_service):
        tok = user_service.create_admin_login_token(EMAIL)
        assert round((tok.expires_at - tok.created_at).total_seconds() / 3600) == 24


class TestLinkStatusHandoffFlag:
    """The verify page auto-signs-in only when the URL says handoff=1 AND the
    server says the token really was minted by the handoff endpoint."""

    @pytest.fixture
    def status_client(self, user_service):
        from backend.api.routes import users as users_routes

        app = FastAPI()
        app.include_router(users_routes.router, prefix="/api")
        app.dependency_overrides[get_user_service] = lambda: user_service
        return TestClient(app)

    def _status(self, status_client, token):
        return status_client.get("/api/users/auth/link-status", params={"token": token}).json()

    def test_handoff_minted_token_reports_handoff(self, client, status_client, user_service, jobs):
        jobs["job1234"] = _job("awaiting_review")
        url = _handoff(client, _session(user_service)).json()["url"]
        token = parse_qs(urlparse(url).query)["token"][0]
        assert self._status(status_client, token) == {"status": "valid", "handoff": True}

    def test_other_tokens_never_report_handoff(self, status_client, user_service, jobs):
        from backend.services.job_notification_service import build_review_login_url

        # kjbox's 72-hour review link (same purpose, emailed) and a plain magic link.
        review = build_review_login_url("job1234", EMAIL)
        assert "handoff" not in review
        review_token = parse_qs(urlparse(review).query)["token"][0]
        magic = user_service.create_magic_link(EMAIL).token
        for token in (review_token, magic):
            assert self._status(status_client, token) == {"status": "valid", "handoff": False}

    def test_unknown_token_not_handoff(self, status_client):
        assert self._status(status_client, "nope") == {"status": "invalid", "handoff": False}
