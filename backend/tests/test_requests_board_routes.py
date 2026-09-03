"""Unit tests for the public song-request voting board routes (mocked service)."""
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.api.routes import requests_board
from backend.api.routes.requests_board import optional_user_email
from backend.models.song_request import SongRequest, Vote
from backend.services.song_request_service import RequestNotFound, SubmissionRateLimited


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}


def _req(**kw) -> SongRequest:
    defaults = dict(
        id="r1",
        artist="The Beatles",
        title="Hey Jude",
        artist_raw="beatles",
        title_raw="hey jude",
        dedupe_key="the beatles|hey jude",
        submitted_by="test@example.com",
        source="human",
        status="open",
        vote_count=1,
        created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    defaults.update(kw)
    return SongRequest(**defaults)


@pytest.fixture
def fake_service(monkeypatch):
    svc = MagicMock()
    monkeypatch.setattr(requests_board, "get_song_request_service", lambda: svc)
    return svc


def test_submit_created(client, auth_headers, fake_service):
    fake_service.submit_request = AsyncMock(
        return_value=(_req(vote_count=1), False, "The Beatles", "Hey Jude")
    )
    fake_service.get_daily_vote.return_value = Vote(
        voter_email="test@example.com", voted_date="2026-09-02", request_id="r1", value=1
    )
    resp = client.post(
        "/api/requests-board/requests",
        json={"artist": "beatles", "title": "hey jude"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["canonical_artist"] == "The Beatles"
    assert data["was_corrected"] is True  # "beatles" != "The Beatles"
    assert data["request"]["your_vote"] == 1
    # Never leak the submitter email
    assert "submitted_by" not in data["request"]


def test_submit_dedupes_to_existing(client, auth_headers, fake_service):
    fake_service.submit_request = AsyncMock(
        return_value=(_req(vote_count=2), True, "The Beatles", "Hey Jude")
    )
    fake_service.get_daily_vote.return_value = None
    resp = client.post(
        "/api/requests-board/requests",
        json={"artist": "The Beatles", "title": "Hey Jude"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_exists"
    assert resp.json()["request"]["vote_count"] == 2


def test_submit_rate_limited(client, auth_headers, fake_service):
    fake_service.submit_request = AsyncMock(side_effect=SubmissionRateLimited())
    resp = client.post(
        "/api/requests-board/requests",
        json={"artist": "a", "title": "b"},
        headers=auth_headers,
    )
    assert resp.status_code == 429


def test_vote_ok(client, auth_headers, fake_service):
    fake_service.cast_vote.return_value = Vote(
        voter_email="test@example.com", voted_date="2026-09-02", request_id="r1", value=1
    )
    fake_service.get_request.return_value = _req(vote_count=5)
    fake_service.get_daily_vote.return_value = Vote(
        voter_email="test@example.com", voted_date="2026-09-02", request_id="r1", value=1
    )
    resp = client.post("/api/requests-board/requests/r1/vote", json={"direction": "up"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["vote_count"] == 5
    assert resp.json()["your_vote"] == 1


def test_vote_missing_request(client, auth_headers, fake_service):
    fake_service.cast_vote.side_effect = RequestNotFound("r1")
    resp = client.post("/api/requests-board/requests/r1/vote", json={"direction": "down"}, headers=auth_headers)
    assert resp.status_code == 404


def test_list_public_no_viewer(client, fake_service):
    """GET /requests is public; with no viewer there is no per-user annotation."""
    app.dependency_overrides[optional_user_email] = lambda: None
    try:
        fake_service.list_active.return_value = [_req(id="r1", vote_count=3), _req(id="r2", vote_count=1)]
        fake_service.list_published.return_value = []
        resp = client.get("/api/requests-board/requests")
        assert resp.status_code == 200
        data = resp.json()
        assert [r["id"] for r in data["requests"]] == ["r1", "r2"]
        assert data["voted_today"] is None
        assert all(r["your_vote"] is None for r in data["requests"])
        assert all("submitted_by" not in r for r in data["requests"])
    finally:
        app.dependency_overrides.pop(optional_user_email, None)


def test_list_annotates_viewer_vote(client, fake_service):
    app.dependency_overrides[optional_user_email] = lambda: "test@example.com"
    try:
        fake_service.list_active.return_value = [_req(id="r1"), _req(id="r2")]
        fake_service.list_published.return_value = []
        fake_service.get_daily_vote.return_value = Vote(
            voter_email="test@example.com", voted_date="2026-09-02", request_id="r2", value=-1
        )
        resp = client.get("/api/requests-board/requests")
        data = resp.json()
        assert data["voted_today"] is True
        assert data["your_vote_request_id"] == "r2"
        by_id = {r["id"]: r for r in data["requests"]}
        assert by_id["r2"]["your_vote"] == -1
        assert by_id["r1"]["your_vote"] is None
    finally:
        app.dependency_overrides.pop(optional_user_email, None)


def test_me_status(client, auth_headers, fake_service):
    fake_service.get_daily_vote.return_value = Vote(
        voter_email="test@example.com", voted_date="2026-09-02", request_id="r9", value=1
    )
    resp = client.get("/api/requests-board/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"voted_today": True, "request_id": "r9", "value": 1}
