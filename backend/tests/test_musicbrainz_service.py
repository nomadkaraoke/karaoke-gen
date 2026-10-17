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

    def test_multi_country_release_ranks_by_best_country(self):
        # Mirror editions carry every release-event country; the best-ranked one counts.
        editions = [
            {"release_mbid": "de", "status": "Official", "date": "1976", "countries": ["DE"], "track_count": 10},
            {"release_mbid": "multi", "status": "Official", "date": "1976", "countries": ["DE", "GB"], "track_count": 10},
        ]
        assert MusicBrainzService.pick_canonical_edition(editions, ["GB"])["release_mbid"] == "multi"


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


def _rows_response(fields, rows):
    """Build a BigQuery jobs.query REST response (f/v row encoding)."""
    def cell(v):
        if isinstance(v, list):
            return {"v": [{"v": x} for x in v]}
        return {"v": None if v is None else str(v)}
    return {
        "jobComplete": True,
        "schema": {"fields": fields},
        "rows": [{"f": [cell(r.get(f["name"])) for f in fields]} for r in rows],
    }


class TestNormalizeAndDecode:
    def test_normalize_matches_decide_norm(self):
        from backend.services.musicbrainz_service import normalize_name
        assert normalize_name("AC/DC") == "ac dc"
        assert normalize_name("  Britney   Spears ") == "britney spears"
        # Not accent-folded, like mb_artists.name_normalized.
        assert normalize_name("Beyoncé") == "beyonc"
        assert normalize_name("!!!") == ""

    def test_decode_rows_types(self):
        from backend.services.musicbrainz_service import _decode_rows
        fields = [
            {"name": "s", "type": "STRING", "mode": "NULLABLE"},
            {"name": "n", "type": "INTEGER", "mode": "NULLABLE"},
            {"name": "arr", "type": "STRING", "mode": "REPEATED"},
            {"name": "missing", "type": "INTEGER", "mode": "NULLABLE"},
        ]
        resp = _rows_response(fields, [{"s": "x", "n": 7, "arr": ["A", "B"], "missing": None}])
        assert _decode_rows(resp["schema"], resp["rows"]) == [{"s": "x", "n": 7, "arr": ["A", "B"], "missing": None}]


@pytest.mark.asyncio
class TestServiceCalls:
    def _svc_with(self, monkeypatch, handler):
        """Service whose _query is answered by handler(sql, params) -> rows; records calls."""
        svc = MusicBrainzService()
        calls = []

        async def fake_query(sql, params):
            calls.append((sql, params))
            return handler(sql, params)

        monkeypatch.setattr(svc, "_query", fake_query)
        return svc, calls

    async def test_search_artists_normalizes_and_ranks(self, monkeypatch):
        svc, calls = self._svc_with(monkeypatch, lambda sql, p: [
            {"artist_mbid": "abc", "artist_name": "AC/DC", "disambiguation": None,
             "artist_type": "Group", "area_name": "Australia", "match_rank": 0},
            {"artist_mbid": "def", "artist_name": "AC/DC Jam", "disambiguation": "",
             "artist_type": None, "area_name": None, "match_rank": 1},
        ])
        out = await svc.search_artists("AC/DC")
        assert calls[0][1] == {"q": "ac dc"}
        assert "mb_artists_normalized" in calls[0][0]
        assert out[0] == {"mbid": "abc", "name": "AC/DC", "disambiguation": "", "type": "Group",
                          "country": "Australia", "score": 100}
        assert out[1]["score"] == 90

    async def test_search_artists_blank_query_skips_bigquery(self, monkeypatch):
        svc, calls = self._svc_with(monkeypatch, lambda sql, p: [])
        assert await svc.search_artists("  ?! ") == []
        assert calls == []

    async def test_get_albums_studio_first(self, monkeypatch):
        svc, calls = self._svc_with(monkeypatch, lambda sql, p: [
            {"release_group_mbid": "live", "title": "Alive", "primary_type": "Album",
             "secondary_types": ["Live"], "first_release_date": "1997"},
            {"release_group_mbid": "studio", "title": "Discovery", "primary_type": "Album",
             "secondary_types": [], "first_release_date": "2001-03-12"},
        ])
        out = await svc.get_albums("artist-1")
        assert calls[0][1] == {"artist_mbid": "artist-1"}
        assert [g["release_group_mbid"] for g in out] == ["studio", "live"]
        assert out[0]["is_studio"] is True and out[1]["is_studio"] is False

    async def test_album_tracklist_resolves_canonical_and_flags_extras(self, monkeypatch):
        editions = [
            {"release_mbid": "rel-std", "title": "Discovery", "status": "Official", "release_date": "2001-03-12",
             "countries": ["XE"], "packaging": None, "disambiguation": None, "track_count": 3},
            {"release_mbid": "rel-deluxe", "title": "Discovery (Deluxe)", "status": "Official", "release_date": "2021",
             "countries": ["US"], "packaging": None, "disambiguation": None, "track_count": 5},
        ]
        tracks = [
            {"medium_position": 1, "track_position": 1, "title": "One More Time", "recording_mbid": "r1",
             "recording_disambiguation": "", "length_ms": 320840},
            {"medium_position": 1, "track_position": 2, "title": "Aerodynamic", "recording_mbid": "r2",
             "recording_disambiguation": None, "length_ms": 207533},
            {"medium_position": 1, "track_position": 3, "title": "Aerodynamic", "recording_mbid": "r3",
             "recording_disambiguation": "live", "length_ms": 210000},
        ]

        def handler(sql, params):
            if "mb_release_tracks" in sql:
                assert params == {"rg": "rg-discovery", "r": "rel-std"}
                return tracks
            if "release_mbid = @r" in sql:
                return [{"release_mbid": "rel-std", "title": "Discovery", "release_date": "2001-03-12", "countries": ["XE"]}]
            if "mb_releases" in sql:
                return editions
            raise AssertionError(sql)

        svc, calls = self._svc_with(monkeypatch, handler)
        result = await svc.get_album_tracklist("rg-discovery")
        assert result["canonical_release_mbid"] == "rel-std"  # modal 3-track studio edition
        assert result["country"] == "XE"
        assert len(result["tracks"]) == 3
        assert result["tracks"][0]["is_extra"] is False
        assert result["tracks"][2]["is_extra"] is True  # recording disambiguation "live"
        assert len(result["editions"]) == 2
        # No release-group lookup query: the album's MBID was passed through.
        assert not any("SELECT release_group_mbid FROM" in sql for sql, _ in calls)
        variants = result["variants"]
        assert len(variants) == 2
        assert variants[0]["label"] == "Original"
        assert variants[0]["track_count"] == 3
        assert variants[1]["label"] == "Reissue"
        assert variants[1]["delta_vs_original"] == 2
        assert result["selected_variant_mbid"] == result["canonical_release_mbid"]

    async def test_release_tracklist_without_release_group_looks_it_up(self, monkeypatch):
        def handler(sql, params):
            if "SELECT release_group_mbid FROM" in sql:
                return [{"release_group_mbid": "rg-x"}]
            if "mb_release_tracks" in sql:
                assert params["rg"] == "rg-x"
                return []
            return [{"release_mbid": "rel-x", "title": "X", "release_date": None, "countries": []}]

        svc, _ = self._svc_with(monkeypatch, handler)
        out = await svc.get_release_tracklist("rel-x")
        assert out["release_mbid"] == "rel-x" and out["country"] is None and out["date"] == ""

    async def test_unknown_release_raises(self, monkeypatch):
        svc, _ = self._svc_with(monkeypatch, lambda sql, p: [])
        with pytest.raises(LookupError):
            await svc.get_release_tracklist("nope")


class _FakeResp:
    def __init__(self, status, json_data=None, text=""):
        self.status_code = status
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


@pytest.mark.asyncio
class TestBigQueryTransport:
    def _patch(self, monkeypatch, svc, responses):
        import httpx
        monkeypatch.setattr(svc, "_access_token", lambda: "tok")
        sent = []

        async def fake_post(self, url, json=None, headers=None):
            sent.append({"url": url, "json": json, "headers": headers})
            return responses[min(len(sent) - 1, len(responses) - 1)]

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        return sent

    async def test_posts_parameterized_capped_query_and_caches(self, monkeypatch):
        svc = MusicBrainzService()
        fields = [{"name": "artist_mbid", "type": "STRING", "mode": "NULLABLE"}]
        sent = self._patch(monkeypatch, svc, [_FakeResp(200, _rows_response(fields, [{"artist_mbid": "x"}]))])
        rows = await svc._query("SELECT 1", {"q": "queen", "ids": ["a", "b"]})
        assert rows == [{"artist_mbid": "x"}]
        body = sent[0]["json"]
        assert "bigquery.googleapis.com" in sent[0]["url"]
        assert "musicbrainz.org" not in sent[0]["url"]
        assert sent[0]["headers"]["Authorization"] == "Bearer tok"
        assert body["useLegacySql"] is False and body["parameterMode"] == "NAMED"
        assert int(body["maximumBytesBilled"]) <= 2 * 1024**3
        params = {p["name"]: p for p in body["queryParameters"]}
        assert params["q"]["parameterValue"] == {"value": "queen"}
        assert params["ids"]["parameterType"]["type"] == "ARRAY"
        # Second identical query is served from the in-memory cache.
        await svc._query("SELECT 1", {"q": "queen", "ids": ["a", "b"]})
        assert len(sent) == 1

    async def test_http_error_raises(self, monkeypatch):
        svc = MusicBrainzService()
        self._patch(monkeypatch, svc, [_FakeResp(403, text="Access Denied")])
        with pytest.raises(RuntimeError, match="403"):
            await svc._query("SELECT 1", {})

    async def test_incomplete_job_raises(self, monkeypatch):
        svc = MusicBrainzService()
        self._patch(monkeypatch, svc, [_FakeResp(200, {"jobComplete": False})])
        with pytest.raises(RuntimeError, match="timed out"):
            await svc._query("SELECT 1", {})
