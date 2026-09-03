"""Tests for the shared YouTube back-catalogue rewrite logic."""
import pytest

from backend.services import youtube_backfill as bf


def _video(title, description="", category_id="10", vid="vid123"):
    return {"id": vid, "snippet": {"title": title, "description": description, "categoryId": category_id}}


class TestClassify:
    def test_standard_karaoke_title_with_brand_code(self):
        c = bf.classify(_video(
            "Coldplay - Yellow (Karaoke)",
            "Karaoke video created with Nomad Karaoke\n\nBrand Code: NOMAD-1603",
        ))
        assert c["is_karaoke"] and c["eligible"]
        assert c["artist"] == "Coldplay" and c["song_title"] == "Yellow"
        assert c["brand_code"] == "NOMAD-1603"
        assert c["parse_confidence"] == "high"

    def test_official_karaoke_version_suffix(self):
        c = bf.classify(_video("Adele - Hello (Official Karaoke Version)"))
        assert c["eligible"] and c["artist"] == "Adele" and c["song_title"] == "Hello"

    def test_fiverr_era_video_is_eligible(self):
        c = bf.classify(_video(
            "Queen - Bohemian Rhapsody (Karaoke)",
            "This is a karaoke (instrumental) version...\nhttps://www.fiverr.com/share/j2aLvm",
        ))
        assert c["kind"] == "fiverr" and c["eligible"]

    def test_terse_new_template_detected(self):
        c = bf.classify(_video(
            "Taylor Swift - Cruel Summer (Karaoke)",
            "AI-powered vocal separation and synchronized lyrics.\n\nBrand Code: NOMAD-0009",
        ))
        assert c["kind"] == "terse_new" and c["eligible"]
        assert c["brand_code"] == "NOMAD-0009"

    def test_npbrand_code_parsed(self):
        c = bf.classify(_video("Artist - Song (Karaoke)", "Brand Code: NOMADNP-0042"))
        assert c["brand_code"] == "NOMADNP-0042"

    def test_no_brand_code_still_eligible(self):
        c = bf.classify(_video("Oasis - Wonderwall (Karaoke)", "some description"))
        assert c["eligible"] and c["brand_code"] is None

    def test_non_karaoke_tutorial_is_skipped(self):
        c = bf.classify(_video("Channel Trailer", "Welcome to my channel"))
        assert not c["is_karaoke"] and not c["eligible"] and c["kind"] == "non_karaoke"

    def test_karaoke_word_but_unparseable_title_not_eligible(self):
        c = bf.classify(_video("How I Make Karaoke Videos With AI"))
        assert c["is_karaoke"] and not c["eligible"] and c["parse_confidence"] == "none"

    def test_medium_confidence_hyphenated_not_auto_eligible(self):
        c = bf.classify(_video('YouTube "Reused content" appeal - Nomad Karaoke Explanation'))
        assert c["is_karaoke"] and c["parse_confidence"] == "medium" and not c["eligible"]

    def test_endash_separator_supported(self):
        c = bf.classify(_video("Beyoncé – Halo (Karaoke)"))
        assert c["eligible"] and c["artist"] == "Beyoncé" and c["song_title"] == "Halo"


class TestBuildEntries:
    def test_targets_and_skip_and_override(self):
        videos = {
            "a": _video("Coldplay - Yellow (Karaoke)", "Brand Code: NOMAD-1603", vid="a"),
            "b": _video("How I Make Karaoke Videos", vid="b"),  # medium, not eligible
            "c": _video("Some Artist - Some Song (Karaoke)", "old desc", vid="c"),
        }
        order = ["a", "b", "c"]
        entries = bf.build_entries(
            videos, order,
            skip_ids={"c"},                          # skip the otherwise-eligible one
            include_overrides={"b": {"artist": "The Band", "song_title": "The Tune"}},
        )
        by = {e["video_id"]: e for e in entries}
        assert by["a"]["target"] and by["a"]["will_change"]
        # 'b' force-included with overridden artist/title
        assert by["b"]["forced_include"] and by["b"]["target"]
        assert by["b"]["artist"] == "The Band" and by["b"]["song_title"] == "The Tune"
        assert by["b"]["parse_confidence"] == "override"
        # 'c' skipped -> not a target
        assert by["c"]["in_skip_list"] and not by["c"]["target"]

    def test_will_change_false_when_already_current(self):
        from backend.services.youtube_description import render_youtube_description
        # Brand code must be the real 4-digit form so classify re-extracts it and
        # render_for reproduces the identical description.
        current = render_youtube_description(artist="Coldplay", title="Yellow", brand_code="NOMAD-1603")
        v = _video("Coldplay - Yellow (Karaoke)", current, vid="x")
        entries = bf.build_entries({"x": v}, ["x"], skip_ids=set(), include_overrides={})
        assert entries[0]["target"]
        assert entries[0]["brand_code"] == "NOMAD-1603"
        assert entries[0]["will_change"] is False


class TestBuildUpdateSnippet:
    def test_preserves_title_category_and_swaps_description(self):
        entry = bf.classify(_video("Coldplay - Yellow (Karaoke)", "Brand Code: NOMAD-1603"))
        current = {"title": "Coldplay - Yellow (Karaoke)", "categoryId": "10",
                   "tags": ["old", "tags"], "description": "old"}
        body = bf.build_update_snippet(current, entry, enrich_tags=False)
        assert body["title"] == "Coldplay - Yellow (Karaoke)"
        assert body["categoryId"] == "10"
        assert body["tags"] == ["old", "tags"]  # preserved
        assert "nomadkaraoke.com/r/youtube" in body["description"]
        assert "AI" not in body["description"]

    def test_enrich_tags_replaces_tags(self):
        entry = bf.classify(_video("Coldplay - Yellow (Karaoke)"))
        current = {"title": "Coldplay - Yellow (Karaoke)", "categoryId": "10", "tags": ["old"]}
        body = bf.build_update_snippet(current, entry, enrich_tags=True)
        assert "karaoke" in body["tags"]
        assert "Coldplay" in body["tags"]
        assert body["tags"] != ["old"]

    def test_missing_category_defaults_to_music(self):
        entry = bf.classify(_video("Coldplay - Yellow (Karaoke)"))
        body = bf.build_update_snippet({"title": "x"}, entry, enrich_tags=False)
        assert body["categoryId"] == bf.DEFAULT_MUSIC_CATEGORY

    def test_readonly_fields_dropped(self):
        entry = bf.classify(_video("Coldplay - Yellow (Karaoke)"))
        current = {"title": "t", "categoryId": "10", "channelId": "UC...", "publishedAt": "2020"}
        body = bf.build_update_snippet(current, entry, enrich_tags=False)
        assert "channelId" not in body and "publishedAt" not in body


class TestListLoaders:
    def test_skip_ids_parses_comments_and_pipe(self, tmp_path, monkeypatch):
        f = tmp_path / "skip_ids.txt"
        f.write_text("# c\nabc123  # note\nDEF456 | Artist | Title\n\n", encoding="utf-8")
        monkeypatch.setattr(bf, "SKIP_IDS_PATH", f)
        assert bf.load_skip_ids() == {"abc123", "DEF456"}

    def test_include_overrides_bare_and_override(self, tmp_path, monkeypatch):
        f = tmp_path / "include_ids.txt"
        f.write_text("bare1  # x\nov1 | The Band | The Song\n", encoding="utf-8")
        monkeypatch.setattr(bf, "INCLUDE_IDS_PATH", f)
        out = bf.load_include_overrides()
        assert out["bare1"] is None
        assert out["ov1"] == {"artist": "The Band", "song_title": "The Song"}

    def test_real_data_files_load(self):
        # The committed lists should parse and contain the curated decisions.
        skip = bf.load_skip_ids()
        inc = bf.load_include_overrides()
        assert "jAAhbfmh8Bc" in skip           # a lyrics-with-vocals video
        assert "5zSFKuCEIhs" in inc            # Los Campesinos! force-include
        assert inc["5zSFKuCEIhs"]["artist"] == "Los Campesinos!"
