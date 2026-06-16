"""Tests for POST /api/catalog/match-judge."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.match_judge.verdict import MatchVerdict, KIND_COSMETIC, KIND_NONE


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-admin-token"}


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from backend.api.routes.catalog import _rate_limit_windows
    _rate_limit_windows.clear()
    yield
    _rate_limit_windows.clear()


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.match_judge.service.judge_match", new_callable=AsyncMock)
def test_match_judge_returns_verdict(mock_judge, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="t@example.com", token_id="t")
    mock_judge.return_value = MatchVerdict(
        kind=KIND_COSMETIC,
        confident=True,
        canonical_artist="Paramore",
        canonical_title="Big Man, Little Dignity",
        engine="catalog",
        reason="casing",
    )

    resp = client.post(
        "/api/catalog/match-judge",
        json={"artist": "paramore", "title": "big man, little dignity"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "cosmetic"
    assert data["confident"] is True
    assert data["canonical_artist"] == "Paramore"
    assert data["canonical_title"] == "Big Man, Little Dignity"
    assert data["engine"] == "catalog"


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.match_judge.service.judge_match", new_callable=AsyncMock)
def test_match_judge_passes_audio_tier(mock_judge, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="t@example.com", token_id="t")
    mock_judge.return_value = MatchVerdict("none", True, "q", "b", engine="ai")

    client.post(
        "/api/catalog/match-judge",
        json={"artist": "q", "title": "b", "audio_confidence_tier": 3},
        headers=auth_headers,
    )
    # judge_match(artist, title, audio_tier=...) — tier forwarded
    _, kwargs = mock_judge.call_args
    assert kwargs.get("audio_tier") == 3 or mock_judge.call_args.args[2] == 3


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.match_judge.service.judge_match", new_callable=AsyncMock)
def test_match_judge_defaults_to_full_stage(mock_judge, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="t@example.com", token_id="t")
    mock_judge.return_value = MatchVerdict("none", True, "q", "b", engine="ai")

    client.post(
        "/api/catalog/match-judge",
        json={"artist": "q", "title": "b"},
        headers=auth_headers,
    )
    _, kwargs = mock_judge.call_args
    assert kwargs.get("stage") == "full"


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.match_judge.service.judge_match", new_callable=AsyncMock)
def test_match_judge_forwards_fast_stage(mock_judge, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="t@example.com", token_id="t")
    mock_judge.return_value = MatchVerdict(
        KIND_NONE, False, "q", "b", engine="catalog", reason="needs ai", needs_ai=True
    )

    resp = client.post(
        "/api/catalog/match-judge",
        json={"artist": "q", "title": "b", "stage": "fast"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["needs_ai"] is True
    _, kwargs = mock_judge.call_args
    assert kwargs.get("stage") == "fast"


def test_match_judge_rejects_unknown_stage(client, auth_headers):
    resp = client.post(
        "/api/catalog/match-judge",
        json={"artist": "q", "title": "b", "stage": "turbo"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_match_judge_validation_error(client, auth_headers):
    resp = client.post(
        "/api/catalog/match-judge",
        json={"artist": "", "title": "Dreams"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
