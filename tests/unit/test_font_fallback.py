"""Unit tests for karaoke_gen.utils.font_fallback (fontconfig mocked; real bundled fonts)."""
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from karaoke_gen.utils import font_fallback as ff

RESOURCES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "karaoke_gen", "resources"))
AVENIR = os.path.join(RESOURCES, "AvenirNext-Bold.ttf")
MONTSERRAT = os.path.join(RESOURCES, "Montserrat-Bold.ttf")


@pytest.fixture(autouse=True)
def _clear_caches():
    ff.find_font_covering.cache_clear()
    yield
    ff.find_font_covering.cache_clear()


class TestMissingCodepoints:
    def test_latin_fully_covered(self):
        assert ff.missing_codepoints(AVENIR, "Omer Adam - Café!") == frozenset()

    def test_hebrew_missing_from_theme_font(self):
        assert ff.missing_codepoints(AVENIR, "שני") == {ord("ש"), ord("נ"), ord("י")}

    def test_whitespace_and_bidi_marks_need_no_glyph(self):
        assert ff.missing_codepoints(AVENIR, "a‏ b‍ c\tfe0f️") == frozenset()

    def test_none_font_means_pil_default_latin_only(self):
        assert ff.missing_codepoints(None, "abc") == frozenset()
        assert ff.missing_codepoints(None, "שני")

    def test_unreadable_font_assumed_to_cover(self, tmp_path):
        bogus = tmp_path / "bogus.ttf"
        bogus.write_bytes(b"not a font")
        assert ff.missing_codepoints(str(bogus), "שני") == frozenset()
        assert ff.missing_codepoints("/does/not/exist.ttf", "שני") == frozenset()

    @pytest.mark.parametrize("text", ["", None])
    def test_empty_text(self, text):
        assert ff.missing_codepoints(AVENIR, text) == frozenset()


def _fc(match_stdout="", list_stdout=""):
    def run(cmd, **kwargs):
        out = match_stdout if cmd[0] == "fc-match" else list_stdout
        return SimpleNamespace(returncode=0, stdout=out)
    return run


class TestFindFontCovering:
    def test_verified_fc_match_result_is_used(self):
        with patch.object(ff.subprocess, "run", side_effect=_fc(match_stdout=MONTSERRAT)) as run:
            assert ff.find_font_covering(frozenset({ord("a")})) == MONTSERRAT
        match_cmd = run.call_args_list[0].args[0]
        assert match_cmd[0] == "fc-match" and "charset=61" in match_cmd[-1] and ":weight=bold" in match_cmd[-1]

    def test_regular_weight_when_not_bold(self):
        with patch.object(ff.subprocess, "run", side_effect=_fc(match_stdout=MONTSERRAT)) as run:
            ff.find_font_covering(frozenset({ord("a")}), bold=False)
        assert ":weight=bold" not in run.call_args_list[0].args[0][-1]

    def test_fc_match_result_rejected_if_it_lacks_the_glyphs(self):
        # fc-match always returns *something*; Avenir has no Hebrew so it must be skipped
        with patch.object(ff.subprocess, "run", side_effect=_fc(match_stdout=AVENIR, list_stdout="")):
            assert ff.find_font_covering(frozenset({ord("ש")})) is None

    def test_falls_through_to_fc_list_candidates(self):
        listing = f"{AVENIR}: \n{MONTSERRAT}: \n"
        with patch.object(ff.subprocess, "run", side_effect=_fc(match_stdout=AVENIR, list_stdout=listing)), \
             patch.object(ff, "_font_codepoints", side_effect=lambda p: frozenset({1488}) if p == MONTSERRAT else frozenset()):
            assert ff.find_font_covering(frozenset({1488})) == MONTSERRAT

    def test_no_fontconfig_returns_none(self):
        with patch.object(ff.subprocess, "run", side_effect=FileNotFoundError("fc-match")):
            assert ff.find_font_covering(frozenset({ord("ש")})) is None

    def test_timeout_returns_none(self):
        with patch.object(ff.subprocess, "run", side_effect=subprocess.TimeoutExpired("fc-match", 5)):
            assert ff.find_font_covering(frozenset({ord("ש")})) is None

    def test_empty_request(self):
        assert ff.find_font_covering(frozenset()) is None


class TestResolveFontForText:
    def test_covered_text_keeps_font_without_fontconfig(self):
        with patch.object(ff, "find_font_covering") as find:
            assert ff.resolve_font_for_text(AVENIR, "Omer Adam") == AVENIR
        find.assert_not_called()

    def test_fallback_must_cover_whole_string(self):
        """PIL draws the whole string in one font, so Latin in a mixed title counts too."""
        with patch.object(ff, "find_font_covering", return_value="/fonts/NotoSansHebrew-Bold.ttf") as find:
            assert ff.resolve_font_for_text(AVENIR, "Omer - שני") == "/fonts/NotoSansHebrew-Bold.ttf"
        (needed,), _ = find.call_args
        assert {ord("O"), ord("-"), ord("ש")} <= needed and ord(" ") not in needed

    def test_no_covering_font_keeps_original(self):
        with patch.object(ff, "find_font_covering", return_value=None):
            assert ff.resolve_font_for_text(AVENIR, "שני") == AVENIR


class TestAssFontScale:
    def test_avenir_matches_libass_sizing(self):
        # Verified against libass render width: 0.732 predicts 565px vs 564px rendered
        assert ff.ass_font_scale(AVENIR) == pytest.approx(0.732, abs=0.001)

    def test_unreadable_font_uses_legacy_constant(self, tmp_path):
        bogus = tmp_path / "x.ttf"
        bogus.write_bytes(b"nope")
        assert ff.ass_font_scale(str(bogus)) == 0.70


class TestFallbackLookupRobustness:
    def test_fc_list_skipped_when_fc_match_covers(self):
        with patch.object(ff.subprocess, "run", side_effect=_fc(match_stdout=MONTSERRAT)) as run:
            ff.find_font_covering(frozenset({ord("a")}))
        assert [c.args[0][0] for c in run.call_args_list] == ["fc-match"]

    def test_failed_lookup_is_not_cached(self):
        """A transient fontconfig timeout (cold cache) must not disable fallback for the
        rest of the process."""
        cps = frozenset({ord("a")})
        with patch.object(ff.subprocess, "run", side_effect=subprocess.TimeoutExpired("fc-match", 5)):
            assert ff.find_font_covering(cps) is None
        with patch.object(ff.subprocess, "run", side_effect=_fc(match_stdout=MONTSERRAT)):
            assert ff.find_font_covering(cps) == MONTSERRAT

    def test_successful_lookup_is_cached(self):
        cps = frozenset({ord("a")})
        with patch.object(ff.subprocess, "run", side_effect=_fc(match_stdout=MONTSERRAT)) as run:
            ff.find_font_covering(cps)
            ff.find_font_covering(cps)
        assert run.call_count == 1

    @pytest.mark.parametrize("bold,expected", [(True, "NotoSansHebrew-Bold.ttf"), (False, "NotoSansHebrew-Regular.ttf")])
    def test_fc_list_candidates_prefer_requested_weight(self, bold, expected):
        listing = "/f/NotoSansHebrew-Bold.ttf: \n/f/NotoSansHebrew-Regular.ttf: \n"
        with patch.object(ff.subprocess, "run", side_effect=_fc(match_stdout="", list_stdout=listing)), \
             patch.object(ff, "_covers", side_effect=lambda path, cps: path.startswith("/f/")):
            assert ff.find_font_covering(frozenset({1488}), bold=bold) == "/f/" + expected

    def test_default_font_covers_typographic_punctuation(self):
        assert ff.missing_codepoints(None, "Don’t Stop – “Me” Now…") == frozenset()
