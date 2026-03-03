"""
Tests for catalog API routes — proxy endpoints and community check.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    """Test client with mocked auth."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Headers for authenticated requests."""
    return {"Authorization": "Bearer test-admin-token"}


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limit state between tests."""
    from backend.api.routes.catalog import _rate_limit_windows
    _rate_limit_windows.clear()
    yield
    _rate_limit_windows.clear()


# --- Artist search ---


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.catalog_proxy_service.search_artists", new_callable=AsyncMock)
def test_search_artists_returns_results(mock_search, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="test@example.com", token_id="test")
    mock_search.return_value = [
        {"name": "Fleetwood Mac", "mbid": "abc-123", "popularity": 85},
        {"name": "Fleet Foxes", "mbid": "def-456", "popularity": 72},
    ]

    response = client.get("/api/catalog/artists?q=flee", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Fleetwood Mac"


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.catalog_proxy_service.search_artists", new_callable=AsyncMock)
def test_search_artists_empty_results(mock_search, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="test@example.com", token_id="test")
    mock_search.return_value = []

    response = client.get("/api/catalog/artists?q=zzzzz", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_search_artists_requires_min_query_length(client, auth_headers):
    response = client.get("/api/catalog/artists?q=a", headers=auth_headers)
    assert response.status_code == 422  # Validation error


# --- Track search ---


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.catalog_proxy_service.search_tracks", new_callable=AsyncMock)
def test_search_tracks_returns_results(mock_search, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="test@example.com", token_id="test")
    mock_search.return_value = [
        {"track_name": "Dreams", "artist_name": "Fleetwood Mac", "popularity": 82},
    ]

    response = client.get("/api/catalog/tracks?q=dreams", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["track_name"] == "Dreams"


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.catalog_proxy_service.search_tracks", new_callable=AsyncMock)
def test_search_tracks_with_artist_filter(mock_search, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="test@example.com", token_id="test")
    mock_search.return_value = []

    response = client.get(
        "/api/catalog/tracks?q=dreams&artist=Fleetwood+Mac",
        headers=auth_headers,
    )
    assert response.status_code == 200
    mock_search.assert_called_once_with("dreams", "Fleetwood Mac", 10)


# --- Community check ---


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.karaokenerds_service.check_community_versions", new_callable=AsyncMock)
def test_community_check_found(mock_check, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="test@example.com", token_id="test")
    mock_check.return_value = {
        "has_community": True,
        "songs": [
            {
                "title": "Dreams",
                "artist": "Fleetwood Mac",
                "community_tracks": [
                    {
                        "brand_name": "Karaoke Version",
                        "brand_code": "KV",
                        "youtube_url": "https://www.youtube.com/watch?v=abc",
                        "is_community": True,
                    }
                ],
            }
        ],
        "best_youtube_url": "https://www.youtube.com/watch?v=abc",
    }

    response = client.post(
        "/api/catalog/community-check",
        json={"artist": "Fleetwood Mac", "title": "Dreams"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["has_community"] is True
    assert len(data["songs"]) == 1
    assert data["best_youtube_url"] == "https://www.youtube.com/watch?v=abc"


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.karaokenerds_service.check_community_versions", new_callable=AsyncMock)
def test_community_check_not_found(mock_check, mock_auth, client, auth_headers):
    mock_auth.return_value = MagicMock(user_email="test@example.com", token_id="test")
    mock_check.return_value = {
        "has_community": False,
        "songs": [],
        "best_youtube_url": None,
    }

    response = client.post(
        "/api/catalog/community-check",
        json={"artist": "Unknown Artist", "title": "Unknown Song"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["has_community"] is False


def test_community_check_validation_error(client, auth_headers):
    response = client.post(
        "/api/catalog/community-check",
        json={"artist": "", "title": "Dreams"},
        headers=auth_headers,
    )
    assert response.status_code == 422


# --- Rate limiting ---


@patch("backend.api.routes.catalog.require_auth")
@patch("backend.services.catalog_proxy_service.search_artists", new_callable=AsyncMock)
def test_rate_limit_enforced(mock_search, mock_auth, client, auth_headers):
    """Rate limit allows up to 20 requests per minute, then returns 429."""
    # Use a unique email so other tests' requests don't interfere
    unique_email = "ratelimit-unique-test@test.com"
    mock_auth.return_value = MagicMock(user_email=unique_email, token_id="test")
    mock_search.return_value = []

    # Clear any existing rate limit state for this user
    from backend.api.routes.catalog import _rate_limit_windows
    _rate_limit_windows.pop(unique_email, None)

    # First 20 requests should succeed
    for i in range(20):
        resp = client.get("/api/catalog/artists?q=test", headers=auth_headers)
        assert resp.status_code == 200, f"Request {i+1}/20 failed with {resp.status_code}"

    # 21st request should be rate limited
    resp = client.get("/api/catalog/artists?q=test", headers=auth_headers)
    assert resp.status_code == 429
    assert "Rate limit exceeded" in resp.json()["detail"]

    # Clean up
    _rate_limit_windows.pop(unique_email, None)
