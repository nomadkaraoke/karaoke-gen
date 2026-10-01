"""ASS output for right-to-left lyrics and karaoke timing accuracy (no rendering).

Pixel-level behaviour is covered by test_ass_render_highlight.py; these pin the tags we
emit so failures point straight at the generator.
"""
import re
from datetime import timedelta
from unittest.mock import patch

import pytest

from karaoke_gen.lyrics_transcriber.output.ass.config import LineState, LineTimingInfo, ScreenConfig
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_line import LyricsLine
from karaoke_gen.lyrics_transcriber.output.ass.style import Style, build_karaoke_styles
from karaoke_gen.lyrics_transcriber.output.ass.text_direction import (
    ASS_ENCODING_AUTO_DIRECTION,
    RTL_KARAOKE_FILL_TAGS,
    is_rtl_text,
)
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE


class TestIsRtlText:
    @pytest.mark.parametrize("text", ["שני משוגעים", "كيف حالك", "سلام", "123 שלום", "«שלום»", " \u200fשלום"])
    def test_rtl(self, text):
        assert is_rtl_text(text) is True

    @pytest.mark.parametrize("text", ["hello", "Omer Adam - שני משוגעים", "青花瓷", "방탄소년단", "カラオケ", "", "123 ...", None])
    def test_not_rtl(self, text):
        assert is_rtl_text(text) is False


def _screen_config(**kw):
    return ScreenConfig(line_height=60, video_width=1920, video_height=1080, **kw)


def _segment(words, start=10.0, step=0.5, dur=0.4, singer=None, word_singers=None):
    ws = [
        Word(id=f"w{i}", text=t, start_time=start + i * step, end_time=start + i * step + dur,
             singer=word_singers[i] if word_singers else None)
        for i, t in enumerate(words)
    ]
    return LyricsSegment(id="s", text=" ".join(words), words=ws, start_time=ws[0].start_time,
                         end_time=ws[-1].end_time, singer=singer)


def _style():
    return build_karaoke_styles(DEFAULT_KARAOKE_STYLE, singers=[1], solo=True)[0]


def _state(fade_in=9.0):
    return LineState(text="", timing=LineTimingInfo(fade_in_time=fade_in, end_time=14.0,
                                                   fade_out_time=14.3, clear_time=14.3), y_position=300)


def _main_event_text(segment, **kw):
    line = LyricsLine(segment=segment, screen_config=_screen_config())
    events = line.create_ass_events(_state(), _style(), _screen_config(lead_in_enabled=False), **kw)
    assert len(events) == 1
    return events[0].Text


class TestStyleEncoding:
    def test_theme_encoding_zero_is_overridden_to_auto_direction(self):
        style = dict(DEFAULT_KARAOKE_STYLE, encoding=0)
        (s,) = build_karaoke_styles(style, singers=[1], solo=True)
        assert s.Encoding == ASS_ENCODING_AUTO_DIRECTION == -1

    def test_duet_styles_all_auto_direction(self):
        styles = build_karaoke_styles(DEFAULT_KARAOKE_STYLE, singers=[1, 2, 0], solo=False)
        assert {s.Encoding for s in styles} == {-1}


class TestRtlFillTags:
    def test_rtl_line_gets_fill_tags_before_karaoke(self):
        text = _main_event_text(_segment(["כמו", "איזה", "שני"]))
        assert RTL_KARAOKE_FILL_TAGS in text
        assert text.index(RTL_KARAOKE_FILL_TAGS) < text.index(r"{\k")

    @pytest.mark.parametrize("words", [["one", "two"], ["青花瓷", "天青色"], ["Omer", "שני"]])
    def test_ltr_line_has_no_fill_tags(self, words):
        assert RTL_KARAOKE_FILL_TAGS not in _main_event_text(_segment(words))

    def test_tags_reemitted_after_every_style_reset(self):
        """{\\r} resets all overrides, including the rotation — without re-emitting it the
        words after a duet singer switch would fill left-to-right again."""
        styles = build_karaoke_styles(
            dict(DEFAULT_KARAOKE_STYLE, singers={"1": {}, "2": {"primary_color": "247, 112, 180, 255"}, "both": {}}),
            singers=[1, 2], solo=False,
        )
        by_singer = {1: styles[0], 2: styles[1]}
        seg = _segment(["א", "ב", "ג", "ד"], singer=1, word_singers=[1, 2, 1, 1])
        text = _main_event_text(seg, styles_by_singer=by_singer)
        resets = [m.start() for m in re.finditer(re.escape(r"{\r}"), text)]
        assert resets, "expected a style reset after the singer-2 word"
        for pos in resets:
            assert text[pos + len(r"{\r}"):].startswith(RTL_KARAOKE_FILL_TAGS)


class TestKaraokeTiming:
    @staticmethod
    def _tags(text):
        return [(tag, int(cs)) for tag, cs in re.findall(r"\{\\(kf|k)(\d+)\}", text)]

    def test_small_gaps_are_kept_so_highlight_does_not_run_early(self):
        # 10 words, 0.05s gaps: the old generator dropped every gap (<= 0.1s) and the
        # last word started 0.45s early.
        seg = _segment([f"w{i}" for i in range(10)], start=10.0, step=0.45, dur=0.4)
        line = LyricsLine(segment=seg, screen_config=_screen_config())
        text = line._create_ass_text(timedelta(seconds=9.0))
        elapsed = 0
        starts = []
        for tag, cs in self._tags(text):
            if tag == "kf":
                starts.append(elapsed)
            elapsed += cs
        expected = [round((w.start_time - 9.0) * 100) for w in seg.words]
        assert starts == expected
        assert elapsed == round((seg.words[-1].end_time - 9.0) * 100)

    def test_rounding_does_not_accumulate(self):
        # 0.333s words: independently rounded durations drift by 0.3cs/word
        seg = _segment([f"w{i}" for i in range(30)], start=10.0, step=1 / 3, dur=1 / 3)
        text = LyricsLine(segment=seg, screen_config=_screen_config())._create_ass_text(timedelta(seconds=10.0))
        total = sum(cs for _, cs in self._tags(text))
        assert total == round((seg.words[-1].end_time - 10.0) * 100)

    def test_overlapping_words_do_not_push_later_words_late(self):
        seg = _segment(["a", "b", "c"], start=10.0, step=0.3, dur=0.5)  # each overlaps the next by 0.2s
        text = LyricsLine(segment=seg, screen_config=_screen_config())._create_ass_text(timedelta(seconds=10.0))
        tags = self._tags(text)
        assert all(cs >= 0 for _, cs in tags)
        assert sum(cs for _, cs in tags) == round((seg.words[-1].end_time - 10.0) * 100)

    def test_line_starting_before_event_clamps_initial_delay(self):
        seg = _segment(["a", "b"], start=10.0)
        text = LyricsLine(segment=seg, screen_config=_screen_config())._create_ass_text(timedelta(seconds=10.5))
        assert text.startswith(r"{\k0}")


class TestLeadIn:
    """Lead-in geometry. With \\an8, libass centres a drawing's width on the \\move
    target, so the LTR box ends rect_width/2 before the text and RTL mirrors that."""

    def _lead_in(self, words, text_width=600):
        seg = _segment(words)
        cfg = _screen_config(lead_in_enabled=True)
        line = LyricsLine(segment=seg, screen_config=cfg)
        with patch.object(LyricsLine, "_measure_text", return_value=(text_width, 50)):
            events = line.create_ass_events(_state(), _style(), cfg, previous_end_time=None)
        lead = events[0].Text
        move = re.search(r"\\move\((-?\d+),(-?\d+),(-?\d+),(-?\d+)", lead)
        shape = re.search(r"\{\\p1\}(.*?)\{\\p0\}", lead).group(1)
        rect_w = int(cfg.video_width * cfg.lead_in_width_percent / 100)
        return tuple(int(v) for v in move.groups()), shape, rect_w, cfg

    def test_ltr_lead_in_moves_from_left_to_text_left_edge(self):
        (x0, _, x1, _), shape, rect_w, cfg = self._lead_in(["one", "two"])
        assert x0 == 0
        assert x1 == cfg.video_width // 2 - 300
        assert shape.startswith(f"m {-rect_w} ")

    def test_rtl_lead_in_moves_from_right_to_text_right_edge(self):
        (x0, _, x1, _), shape, rect_w, cfg = self._lead_in(["כמו", "איזה"])
        text_right = cfg.video_width // 2 - 300 + 600
        assert x0 == cfg.video_width + rect_w
        assert x1 == text_right + rect_w
        assert shape.startswith("m 0 ")

    def test_rtl_horizontal_offset_is_mirrored(self):
        seg = _segment(["כמו", "איזה"])
        cfg = _screen_config(lead_in_enabled=True, lead_in_horiz_offset_percent=-2.0)
        line = LyricsLine(segment=seg, screen_config=cfg)
        with patch.object(LyricsLine, "_measure_text", return_value=(600, 50)):
            lead = line.create_ass_events(_state(), _style(), cfg, previous_end_time=None)[0].Text
        x1 = int(re.search(r"\\move\(-?\d+,-?\d+,(-?\d+)", lead).group(1))
        rect_w = int(cfg.video_width * cfg.lead_in_width_percent / 100)
        # negative offset pushes the box further from the text: left for LTR, right for RTL
        assert x1 == cfg.video_width // 2 + 300 + rect_w + int(cfg.video_width * 0.02)


class TestMeasureText:
    def test_style_font_used_for_covered_text(self):
        style = _style()
        style.Fontpath = _avenir()
        line = LyricsLine(segment=_segment(["hi"]), screen_config=_screen_config())
        with patch("karaoke_gen.lyrics_transcriber.output.ass.lyrics_line.find_font_covering") as find:
            w, h = line._measure_text("hello world", style)
        find.assert_not_called()
        assert w > 0 and h > 0

    def test_uncovered_chars_measured_with_fallback_font(self):
        style = _style()
        style.Fontpath = _avenir()
        line = LyricsLine(segment=_segment(["hi"]), screen_config=_screen_config())
        with patch("karaoke_gen.lyrics_transcriber.output.ass.lyrics_line.find_font_covering", return_value=None) as find:
            line._measure_text("שני משוגעים", style)
        (missing,), kwargs = find.call_args
        assert ord("ש") in missing and ord(" ") not in missing
        assert kwargs == {"bold": bool(style.Bold)}

    def test_missing_font_file_falls_back_to_default(self):
        style = _style()
        style.Fontpath = "/nonexistent/font.ttf"
        line = LyricsLine(segment=_segment(["hi"]), screen_config=_screen_config())
        w, h = line._measure_text("hello", style)
        assert w > 0


def _avenir():
    import os
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                                        "karaoke_gen", "resources", "AvenirNext-Bold.ttf"))
