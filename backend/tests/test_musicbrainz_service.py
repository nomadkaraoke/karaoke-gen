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


class TestBuildVariants:
    """Collapse a release-group's editions into distinct tracklist variants."""

    def test_same_track_count_editions_collapse_to_one_variant(self):
        editions = [
            {"release_mbid": "us76", "status": "Official", "date": "1976", "country": "US", "track_count": 10},
            {"release_mbid": "se76", "status": "Official", "date": "1976", "country": "SE", "track_count": 10},
            {"release_mbid": "nl76", "status": "Official", "date": "1976", "country": "NL", "track_count": 10},
        ]
        variants = MusicBrainzService.build_variants(editions)
        assert len(variants) == 1
        v = variants[0]
        assert v["track_count"] == 10
        assert v["pressing_count"] == 3
        assert v["label"] == "Original"
        assert v["year"] == "1976"
        assert v["delta_vs_original"] == 0
        # earliest date (all 1976) then preferred country US over SE/NL
        assert v["representative_release_mbid"] == "us76"

    def test_distinct_track_counts_form_separate_variants_sorted_original_first(self):
        editions = [
            {"release_mbid": "reissue2014", "status": "Official", "date": "2014", "country": "XW", "track_count": 12},
            {"release_mbid": "orig1976", "status": "Official", "date": "1976", "country": "US", "track_count": 10},
        ]
        variants = MusicBrainzService.build_variants(editions)
        assert [v["representative_release_mbid"] for v in variants] == ["orig1976", "reissue2014"]
        assert variants[0]["label"] == "Original"
        assert variants[0]["delta_vs_original"] == 0
        assert variants[1]["label"] == "Reissue"
        assert variants[1]["delta_vs_original"] == 2
        assert variants[1]["year"] == "2014"

    def test_single_edition_is_one_original_variant(self):
        editions = [{"release_mbid": "only", "status": "Official", "date": "1990", "country": "GB", "track_count": 11}]
        variants = MusicBrainzService.build_variants(editions)
        assert len(variants) == 1
        assert variants[0]["label"] == "Original"
        assert variants[0]["pressing_count"] == 1

    def test_no_editions_no_variants(self):
        assert MusicBrainzService.build_variants([]) == []

    def test_editions_without_tracks_excluded(self):
        editions = [
            {"release_mbid": "real", "status": "Official", "date": "1976", "country": "US", "track_count": 10},
            {"release_mbid": "empty", "status": "Official", "date": "1976", "country": "GB", "track_count": 0},
        ]
        variants = MusicBrainzService.build_variants(editions)
        assert len(variants) == 1
        assert variants[0]["representative_release_mbid"] == "real"

    def test_preferred_countries_override_picks_representative(self):
        editions = [
            {"release_mbid": "us76", "status": "Official", "date": "1976", "country": "US", "track_count": 10},
            {"release_mbid": "jp76", "status": "Official", "date": "1976", "country": "JP", "track_count": 10},
        ]
        variants = MusicBrainzService.build_variants(editions, preferred_countries=["JP"])
        assert variants[0]["representative_release_mbid"] == "jp76"


class TestLocaleCountryPrefs:
    def test_english_locale_prefers_anglophone_countries(self):
        from backend.services.musicbrainz_service import country_prefs_for_locale
        assert country_prefs_for_locale("en") == ["GB", "US", "AU", "CA", "IE", "NZ"]

    def test_region_suffix_is_stripped(self):
        from backend.services.musicbrainz_service import country_prefs_for_locale
        assert country_prefs_for_locale("en-US") == ["GB", "US", "AU", "CA", "IE", "NZ"]

    def test_japanese_locale(self):
        from backend.services.musicbrainz_service import country_prefs_for_locale
        assert country_prefs_for_locale("ja") == ["JP"]

    def test_unknown_and_none_fall_back_to_default(self):
        from backend.services.musicbrainz_service import country_prefs_for_locale
        default = ["XW", "XE", "US", "GB"]
        assert country_prefs_for_locale("zz") == default
        assert country_prefs_for_locale(None) == default
        assert country_prefs_for_locale("") == default


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
        # Variants: 3-track original + 5-track reissue; selected = canonical.
        variants = result["variants"]
        assert len(variants) == 2
        assert variants[0]["label"] == "Original"
        assert variants[0]["track_count"] == 3
        assert variants[1]["label"] == "Reissue"
        assert variants[1]["delta_vs_original"] == 2
        assert result["selected_variant_mbid"] == result["canonical_release_mbid"]


import httpx as _httpx


class _FakeResp:
    def __init__(self, status, json_data=None):
        self.status_code = status
        self._json = json_data
        self.request = _httpx.Request("GET", "https://musicbrainz.org/ws/2/artist")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _httpx.HTTPStatusError(
                f"{self.status_code}", request=self.request, response=self
            )

    def json(self):
        return self._json


def _patch_http(monkeypatch, mbs, svc, responses):
    """Make AsyncClient.get return queued _FakeResp objects; disable throttle/backoff."""
    async def _noop():
        return None
    monkeypatch.setattr(svc, "_throttle", _noop)
    monkeypatch.setattr(mbs, "_RETRY_BACKOFF_S", 0)
    calls = {"n": 0}

    async def fake_get(self, url, **kwargs):
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(_httpx.AsyncClient, "get", fake_get)
    return calls


@pytest.mark.asyncio
class TestRetry:
    async def test_retries_503_then_succeeds(self, monkeypatch):
        from backend.services import musicbrainz_service as mbs
        svc = mbs.MusicBrainzService()
        calls = _patch_http(monkeypatch, mbs, svc, [
            _FakeResp(503),
            _FakeResp(200, {"artists": [{"id": "x", "name": "Queen"}]}),
        ])
        out = await svc.search_artists("Queen")
        assert out[0]["mbid"] == "x"
        assert calls["n"] == 2  # one 503 retry, then success

    async def test_exhausts_retries_raises(self, monkeypatch):
        from backend.services import musicbrainz_service as mbs
        svc = mbs.MusicBrainzService()
        _patch_http(monkeypatch, mbs, svc, [_FakeResp(503)])
        with pytest.raises(_httpx.HTTPStatusError):
            await svc.search_artists("Queen")
