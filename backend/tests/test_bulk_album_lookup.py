"""Tests for Bulk Mode album lookup + availability routes and the batch helper."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.api.dependencies import require_auth, AuthResult, UserType


def _auth():
    return AuthResult(
        is_valid=True,
        user_type=UserType.STRIPE,
        remaining_uses=999,
        message="ok",
        user_email="t@example.com",
        is_admin=False,
    )


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[require_auth] = _auth
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestBatchHelper:
    @pytest.mark.asyncio
    async def test_batch_maps_community_results(self, monkeypatch):
        from backend.services import karaokenerds_service as kn

        async def fake_single(artist, title):
            if title == "One More Time":
                return {
                    "has_community": True,
                    "songs": [{"community_tracks": [{"brand_name": "KV"}, {"brand_name": "KaraFun"}]}],
                    "best_youtube_url": None,
                }
            return {"has_community": False, "songs": [], "best_youtube_url": None}

        monkeypatch.setattr(kn, "check_community_versions", fake_single)
        out = await kn.check_community_versions_batch(
            [{"artist": "Daft Punk", "title": "One More Time"},
             {"artist": "Daft Punk", "title": "Aerodynamic"}]
        )
        assert out[0]["available"] is True
        assert out[0]["brands"] == ["KV", "KaraFun"]
        assert out[1]["available"] is False
        assert out[1]["brand_count"] == 0


class TestAvailabilityRoute:
    def test_availability_endpoint(self, client, monkeypatch):
        from backend.services import karaokenerds_service as kn

        async def fake_batch(songs, concurrency=5):
            return [{"artist": s["artist"], "title": s["title"], "available": True,
                     "brands": ["KV"], "brand_count": 1} for s in songs]

        monkeypatch.setattr(kn, "check_community_versions_batch", fake_batch)
        resp = client.post("/api/bulk/availability", json={"tracks": [{"artist": "A", "title": "B"}]})
        assert resp.status_code == 200
        assert resp.json()["results"][0]["available"] is True

    def test_availability_rejects_over_limit(self, client):
        tracks = [{"artist": f"A{i}", "title": f"T{i}"} for i in range(101)]
        resp = client.post("/api/bulk/availability", json={"tracks": tracks})
        assert resp.status_code == 422


class TestAlbumTracklistRoute:
    def test_tracklist_merges_availability(self, client, monkeypatch):
        from backend.services import musicbrainz_service as mbs
        from backend.services import karaokenerds_service as kn

        class FakeMB:
            async def get_album_tracklist(self, rg):
                return {
                    "release_mbid": "rel1",
                    "canonical_release_mbid": "rel1",
                    "title": "Discovery",
                    "date": "2001",
                    "tracks": [
                        {"position": 1, "title": "One More Time", "recording_mbid": "r1",
                         "length_ms": 320840, "is_extra": False, "extra_reason": ""},
                        {"position": 2, "title": "Aerodynamic (Live)", "recording_mbid": "r2",
                         "length_ms": 210000, "is_extra": True, "extra_reason": "live"},
                    ],
                    "editions": [{"release_mbid": "rel1", "title": "Discovery", "status": "Official",
                                  "date": "2001", "country": "XE", "track_count": 2}],
                }

        async def fake_batch(songs, concurrency=5):
            return [{"artist": s["artist"], "title": s["title"],
                     "available": s["title"] == "One More Time",
                     "brands": ["KV"] if s["title"] == "One More Time" else [],
                     "brand_count": 1 if s["title"] == "One More Time" else 0} for s in songs]

        monkeypatch.setattr(mbs, "get_musicbrainz_service", lambda: FakeMB())
        monkeypatch.setattr(kn, "check_community_versions_batch", fake_batch)

        resp = client.get("/api/bulk/album/tracklist", params={"artist": "Daft Punk", "release_group_mbid": "rg1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["canonical_release_mbid"] == "rel1"
        assert data["tracks"][0]["available"] is True
        assert data["tracks"][0]["brands"] == ["KV"]
        assert data["tracks"][1]["is_extra"] is True
        assert data["tracks"][1]["available"] is False

    def test_tracklist_requires_an_id(self, client):
        resp = client.get("/api/bulk/album/tracklist", params={"artist": "X"})
        assert resp.status_code == 422
