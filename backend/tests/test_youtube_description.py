"""Tests for the shared YouTube description/tags renderer."""
import pytest

from backend.config import get_settings
from backend.services.youtube_description import (
    build_youtube_tags,
    hashtagify,
    render_youtube_description,
)


class TestHashtagify:
    def test_strips_punctuation_and_spaces(self):
        assert hashtagify("Twenty One Pilots") == "TwentyOnePilots"
        assert hashtagify("P!nk") == "Pnk"
        assert hashtagify("AC/DC") == "ACDC"

    def test_empty_or_symbol_only_returns_empty(self):
        assert hashtagify("") == ""
        assert hashtagify(None) == ""
        assert hashtagify("!!!") == ""

    def test_keeps_unicode_letters(self):
        assert hashtagify("Beyoncé") == "Beyoncé"


class TestRenderDefaultTemplate:
    """Renders against the real canonical template from config."""

    def _render(self, **kwargs):
        return render_youtube_description(**kwargs)

    def test_fills_all_placeholders(self):
        out = self._render(artist="Coldplay", title="Yellow", brand_code="NOMAD-1603")
        assert "Yellow" in out
        assert "Coldplay" in out
        assert "#Coldplay" in out
        assert "Brand Code: NOMAD-1603" in out
        assert "https://nomadkaraoke.com/r/youtube" in out

    def test_no_leftover_placeholders(self):
        out = self._render(artist="Coldplay", title="Yellow", brand_code="NOMAD-1603")
        for token in ("{artist}", "{title}", "{artist_hashtag}", "{brand_code}"):
            assert token not in out

    def test_removes_ai_framing(self):
        out = self._render(artist="Coldplay", title="Yellow", brand_code="NOMAD-1")
        assert "AI" not in out
        assert "AI-powered" not in out

    def test_omits_brand_code_line_when_absent(self):
        out = self._render(artist="Coldplay", title="Yellow", brand_code=None)
        assert "Brand Code" not in out
        assert "{brand_code}" not in out
        # No dangling blank block at the end.
        assert not out.endswith("\n")

    def test_drops_artist_hashtag_when_unrenderable(self):
        out = self._render(artist="!!!", title="Song", brand_code="NOMAD-2")
        # No stray empty hashtag left behind.
        assert "# " not in out
        assert "#{artist_hashtag}" not in out
        assert "#karaoke #instrumental" in out

    def test_config_default_is_the_canonical_template(self):
        # Guard: the config default must carry the placeholders the renderer expects.
        tpl = get_settings().default_youtube_description
        assert "{title}" in tpl and "{artist}" in tpl
        assert "{brand_code}" in tpl
        assert "nomadkaraoke.com/r/youtube" in tpl


class TestRenderLegacyTemplate:
    def test_template_without_placeholders_appends_brand_code(self):
        legacy = "Karaoke video created with Nomad Karaoke.\n\n#karaoke"
        out = render_youtube_description(
            artist="Coldplay", title="Yellow", brand_code="NOMAD-1603", template=legacy
        )
        assert out.endswith("Brand Code: NOMAD-1603")
        assert out.startswith("Karaoke video created with Nomad Karaoke.")

    def test_template_without_placeholders_no_brand_code(self):
        legacy = "Karaoke video created with Nomad Karaoke.\n\n#karaoke"
        out = render_youtube_description(
            artist="Coldplay", title="Yellow", brand_code=None, template=legacy
        )
        assert "Brand Code" not in out


class TestBuildTags:
    def test_includes_core_and_song_specific_tags(self):
        tags = build_youtube_tags("Coldplay", "Yellow")
        assert "karaoke" in tags
        assert "Coldplay" in tags
        assert "Yellow" in tags
        assert "Coldplay karaoke" in tags

    def test_deduplicates_case_insensitively(self):
        # An artist literally named "Karaoke" must not duplicate the core tag.
        tags = build_youtube_tags("karaoke", "Test")
        assert sum(1 for t in tags if t.lower() == "karaoke") == 1

    def test_skips_empty_artist_title(self):
        tags = build_youtube_tags("", "")
        assert "" not in tags
        assert "karaoke" in tags

    def test_respects_total_length_budget(self):
        tags = build_youtube_tags("A" * 300, "B" * 300)
        total = sum(len(t) + 1 for t in tags)
        assert total <= 460
