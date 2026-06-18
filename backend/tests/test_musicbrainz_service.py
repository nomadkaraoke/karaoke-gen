"""Tests for the MusicBrainz lookup service (Bulk Mode album lookup).

HTTP is mocked; fixtures mirror real API shapes captured 2026-06.
"""

import pytest

from backend.services.musicbrainz_service import (
    MusicBrainzService,
    classify_extra,
)


class TestClassifyExtra:
    def test_plain_studio_track_is_not_extra(self):
        assert classify_extra("One More Time") == (False, "")

    def test_live_in_parens_is_extra(self):
        is_extra, reason = classify_extra("Bohemian Rhapsody (Live at Wembley)")
        assert is_extra is True
        assert "live" in reason.lower()

    def test_remix_after_dash_is_extra(self):
        is_extra, _ = classify_extra("Get Lucky - Daft Punk Remix")
        assert is_extra is True

    def test_bonus_disambiguation_is_extra(self):
        is_extra, _ = classify_extra("Some Song", disambiguation="bonus track")
        assert is_extra is True

    def test_word_in_normal_title_not_flagged(self):
        # "Alive" contains "live" but not as a standalone qualifier keyword
        assert classify_extra("Alive") == (False, "")
        # "Living on a Prayer" should not be flagged
        assert classify_extra("Living on a Prayer") == (False, "")


class TestPickCanonical:
    def test_prefers_modal_track_count_then_earliest(self):
        editions = [
            {"release_mbid": "deluxe", "status": "Official", "date": "2011", "country": "US", "track_count": 20},
            {"release_mbid": "std-2001", "status": "Official", "date": "2001-03-12", "country": "XE", "track_count": 14},
            {"release_mbid": "std-2005", "status": "Official", "date": "2005", "country": "US", "track_count": 14},
            {"release_mbid": "promo", "status": "Promotion", "date": "2000", "country": "US", "track_count": 14},
        ]
        chosen = MusicBrainzService.pick_canonical_edition(editions)
        # modal count is 14 (std-2001, std-2005, promo-but-not-official filtered);
        # earliest official among 14-track -> std-2001
        assert chosen["release_mbid"] == "std-2001"

    def test_empty_returns_none(self):
        assert MusicBrainzService.pick_canonical_edition([]) is None


@pytest.mark.asyncio
class TestServiceCalls:
    async def _svc_with(self, monkeypatch, responses):
        """Return a service whose _get returns canned responses keyed by path prefix."""
        svc = MusicBrainzService()

        async def fake_get(path, params):
            for key, val in responses.items():
                if path.startswith(key):
                    return val
            raise AssertionError(f"unexpected path {path}")

        monkeypatch.setattr(svc, "_get", fake_get)
        return svc

    async def test_search_artists_parses(self, monkeypatch):
        svc = await self._svc_with(
            monkeypatch,
            {
                "artist": {
                    "artists": [
                        {"id": "abc", "name": "Daft Punk", "disambiguation": "", "type": "Group", "country": "FR", "score": 100}
                    ]
                }
            },
        )
        out = await svc.search_artists("Daft Punk")
        assert out[0]["mbid"] == "abc"
        assert out[0]["name"] == "Daft Punk"

    async def test_album_tracklist_resolves_canonical_and_flags_extras(self, monkeypatch):
        editions = {
            "release": {
                "releases": [
                    {"id": "rel-std", "title": "Discovery", "status": "Official", "date": "2001-03-12", "country": "XE", "media": [{"track-count": 3}]},
                    {"id": "rel-deluxe", "title": "Discovery (Deluxe)", "status": "Official", "date": "2021", "country": "US", "media": [{"track-count": 5}]},
                ]
            }
        }
        tracklist = {
            "release/rel-std": {
                "id": "rel-std",
                "title": "Discovery",
                "date": "2001-03-12",
                "country": "XE",
                "media": [
                    {
                        "format": "CD",
                        "track-count": 3,
                        "tracks": [
                            {"position": 1, "title": "One More Time", "length": 320840, "recording": {"id": "r1", "title": "One More Time", "disambiguation": "", "length": 320840}},
                            {"position": 2, "title": "Aerodynamic", "length": 207533, "recording": {"id": "r2", "title": "Aerodynamic", "disambiguation": "", "length": 207533}},
                            {"position": 3, "title": "Aerodynamic (Live)", "length": 210000, "recording": {"id": "r3", "title": "Aerodynamic", "disambiguation": "live", "length": 210000}},
                        ],
                    }
                ],
            }
        }
        # _list_editions calls _get("release", {release-group...}); tracklist calls _get("release/<id>", ...)
        # Order matters: "release/rel-std" must match before "release".
        svc = MusicBrainzService()

        async def fake_get(path, params):
            if path.startswith("release/"):
                return tracklist[path]
            if path == "release":
                return editions["release"]
            raise AssertionError(path)

        monkeypatch.setattr(svc, "_get", fake_get)

        result = await svc.get_album_tracklist("rg-discovery")
        assert result["canonical_release_mbid"] == "rel-std"  # modal 3-track studio edition
        assert len(result["tracks"]) == 3
        assert result["tracks"][0]["is_extra"] is False
        assert result["tracks"][2]["is_extra"] is True  # the (Live) track
        assert len(result["editions"]) == 2
