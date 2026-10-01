"""Render real karaoke ASS with ffmpeg/libass and verify how the highlight actually looks.

These tests exist because the generated tags can look right while the video is wrong:
a Hebrew job (5710831e, "שני משוגעים") shipped with its words laid out left-to-right
and highlighted left-to-right, and its title card full of tofu boxes. Every assertion
here is made on rendered pixels (see ass_render_harness.py for the colour scheme).

For each language we check, on frames sampled through the line:
- libass found a glyph for every character (no missing-glyph log lines)
- the line is horizontally centred
- nothing is highlighted before the first word
- the highlight starts at the reading-start edge (left for LTR, right for RTL)
- at every sample, all sung pixels precede all unsung pixels in reading order — this
  pins both word order and the fill direction *within* the word being sung
- the highlight frontier advances monotonically in reading direction
- the highlight keeps time with the words (doesn't run ahead)
- everything is highlighted after the last word
- the lead-in rectangle arrives just outside the reading-start edge

Font-fallback-sensitive checks (glyph coverage, exact lead-in gap) need libass's
fontconfig provider — what prod (Linux, static ffmpeg) and CI use. macOS ffmpeg uses
CoreText, which falls back to different fonts and can't find CJK glyphs, so those
checks are skipped there. In CI (CI=true) nothing is allowed to skip.
"""
import os
import re
from dataclasses import dataclass
from typing import Dict, List

import pytest
from PIL import features

from karaoke_gen.lyrics_transcriber.output.ass.text_direction import RTL_KARAOKE_FILL_TAGS

from .ass_render_harness import (
    WIDTH,
    RenderResult,
    ffmpeg_has_libass,
    generate_ass,
    lead_in_box,
    lyric_lines,
    make_segment,
    render_frames,
    word_mid,
)

IN_CI = os.environ.get("CI", "").lower() == "true"

if not ffmpeg_has_libass():
    if IN_CI:
        raise RuntimeError("ffmpeg with the libass 'ass' filter is required for render tests in CI")
    pytest.skip("ffmpeg with libass not available", allow_module_level=True)


def skip_unless_ci(reason: str):
    if IN_CI:
        pytest.fail(f"Render-test precondition failed in CI: {reason}")
    pytest.skip(reason)


# Pixel tolerance for the sung/unsung boundary (antialiasing, glyph overhang).
BOUNDARY_TOL = 3
# How close the first sung pixel must be to the line's reading-start edge.
EDGE_TOL = 8
# Max allowed off-centre of a rendered line.
CENTRE_TOL = 12
# The lead-in box must stop outside the text, no further away than this.
LEAD_IN_MAX_GAP = 45


@dataclass(frozen=True)
class Sample:
    name: str
    words: tuple
    rtl: bool


SAMPLES = [
    Sample("english", ("one", "two", "three", "four", "five"), rtl=False),
    Sample("hebrew", ("כמו", "איזה", "שני", "משוגעים", "בחוף"), rtl=True),
    Sample("arabic", ("كيف", "حالك", "يا", "صديقي", "العزيز"), rtl=True),
    Sample("chinese", ("青花瓷", "天青色", "等烟雨", "而我在", "等你"), rtl=False),
    Sample("japanese", ("カラオケ", "で", "歌おう", "今夜", "ずっと"), rtl=False),
    Sample("korean", ("사랑해", "너를", "정말", "많이", "보고싶어"), rtl=False),
]
NEEDS_FONTCONFIG_FALLBACK = {"chinese", "japanese", "korean"}

T_BEFORE = 3.98  # lead-in has finished moving, first word not yet started
T_AFTER = 9.5    # last word finished, line still on screen
# Progress samples: middle of every word, plus quarter points through the longest word
PROGRESS_TIMES = sorted({word_mid(i) for i in range(5)} | {word_mid(3, 0.25), word_mid(3, 0.75)})
T_LAST_WORD_NEARLY_DONE = word_mid(4, 0.8)
ALL_TIMES = [T_BEFORE, *PROGRESS_TIMES, T_LAST_WORD_NEARLY_DONE, T_AFTER]


def _font_provider(log: str) -> str:
    m = re.search(r"Using font provider (\w+)", log)
    return m.group(1) if m else "unknown"


@pytest.fixture(scope="module")
def renders(tmp_path_factory) -> Dict[str, RenderResult]:
    results = {}
    for sample in SAMPLES:
        ass = generate_ass(tmp_path_factory.mktemp(sample.name), [make_segment(sample.words)])
        results[sample.name] = render_frames(ass, ALL_TIMES)
    duet_words = SAMPLES[1].words
    ass = generate_ass(
        tmp_path_factory.mktemp("hebrew_duet"),
        # word 2 sung by singer 2 → {\1c..} override then {\r} reset mid-line
        [make_segment(duet_words, segment_singer=1, singers=[1, 1, 2, 1, 1])],
        is_duet=True,
    )
    results["hebrew_duet"] = render_frames(ass, ALL_TIMES)
    return results


def _require_fonts(sample_name: str, result: RenderResult):
    provider = _font_provider(result.log)
    if sample_name in NEEDS_FONTCONFIG_FALLBACK and provider != "fontconfig":
        skip_unless_ci(f"{sample_name} glyph fallback needs libass fontconfig provider (got {provider})")


def _line(result: RenderResult, t: float):
    lines = lyric_lines(result.frames[t])
    assert len(lines) == 1, f"expected exactly one rendered lyric line at t={t}, got {len(lines)}"
    return lines[0]


def _frontier(line, rtl: bool) -> int:
    """The x of the leading edge of the highlight (furthest point sung so far)."""
    return int(line.sung_cols.min()) if rtl else int(line.sung_cols.max())


CASES = [(s.name, s.rtl) for s in SAMPLES] + [("hebrew_duet", True)]


@pytest.mark.parametrize("name,rtl", CASES)
class TestRenderedHighlight:
    def test_all_glyphs_found(self, renders, name, rtl):
        result = renders[name]
        provider = _font_provider(result.log)
        if provider != "fontconfig":
            skip_unless_ci(f"glyph fallback check needs libass fontconfig provider (got {provider})")
        assert result.missing_glyphs == [], f"libass has no font for glyphs {sorted(set(result.missing_glyphs))}"

    def test_line_is_centred(self, renders, name, rtl):
        _require_fonts(name, renders[name])
        line = _line(renders[name], T_BEFORE)
        assert abs(line.center_x - WIDTH / 2) <= CENTRE_TOL, f"line spans {line.left}-{line.right}"

    def test_nothing_sung_before_first_word(self, renders, name, rtl):
        _require_fonts(name, renders[name])
        line = _line(renders[name], T_BEFORE)
        assert len(line.unsung_cols) > 0
        assert len(line.sung_cols) == 0

    def test_highlight_starts_at_reading_edge(self, renders, name, rtl):
        _require_fonts(name, renders[name])
        line = _line(renders[name], word_mid(0))
        assert len(line.sung_cols) > 0, "first word should be partly highlighted"
        if rtl:
            assert line.right - line.sung_cols.max() <= EDGE_TOL, "RTL highlight must start at the right edge"
            assert line.sung_cols.min() > line.center_x, "first RTL word must be on the right half"
        else:
            assert line.sung_cols.min() - line.left <= EDGE_TOL, "LTR highlight must start at the left edge"
            assert line.sung_cols.max() < line.center_x, "first LTR word must be on the left half"

    @pytest.mark.parametrize("t", PROGRESS_TIMES)
    def test_sung_precedes_unsung_in_reading_order(self, renders, name, rtl, t):
        """Pins word order *and* in-word fill direction: no unsung pixel may sit on the
        reading-start side of a sung one."""
        _require_fonts(name, renders[name])
        line = _line(renders[name], t)
        assert len(line.sung_cols) and len(line.unsung_cols), f"t={t}: expected a partly sung line"
        if rtl:
            assert line.sung_cols.min() >= line.unsung_cols.max() - BOUNDARY_TOL, (
                f"t={t}: sung x∈[{line.sung_cols.min()},{line.sung_cols.max()}] should be right of "
                f"unsung x∈[{line.unsung_cols.min()},{line.unsung_cols.max()}]"
            )
        else:
            assert line.sung_cols.max() <= line.unsung_cols.min() + BOUNDARY_TOL, (
                f"t={t}: sung x∈[{line.sung_cols.min()},{line.sung_cols.max()}] should be left of "
                f"unsung x∈[{line.unsung_cols.min()},{line.unsung_cols.max()}]"
            )

    def test_highlight_advances_in_reading_direction(self, renders, name, rtl):
        _require_fonts(name, renders[name])
        lines = [_line(renders[name], t) for t in PROGRESS_TIMES]
        frontiers = [_frontier(l, rtl) for l in lines]
        fractions = [l.sung_fraction for l in lines]
        steps = list(zip(frontiers, frontiers[1:]))
        if rtl:
            assert all(b < a for a, b in steps), f"RTL frontier should move left: {frontiers}"
        else:
            assert all(b > a for a, b in steps), f"LTR frontier should move right: {frontiers}"
        assert all(b > a for a, b in zip(fractions, fractions[1:])), f"sung fraction should grow: {fractions}"

    def test_highlight_keeps_time_with_the_vocal(self, renders, name, rtl):
        """80% through the last word it must still be filling. Inter-word gaps
        (0.1s here) used to be dropped, so the highlight ran ahead and finished early."""
        _require_fonts(name, renders[name])
        line = _line(renders[name], T_LAST_WORD_NEARLY_DONE)
        assert 0.8 < line.sung_fraction < 0.99, f"sung fraction {line.sung_fraction:.2f}"

    def test_everything_sung_after_last_word(self, renders, name, rtl):
        _require_fonts(name, renders[name])
        line = _line(renders[name], T_AFTER)
        assert line.sung_fraction >= 0.99

    def test_lead_in_arrives_outside_reading_start_edge(self, renders, name, rtl):
        _require_fonts(name, renders[name])
        result = renders[name]
        line = _line(result, T_BEFORE)
        box = lead_in_box(result.frames[T_BEFORE])
        assert box is not None, "lead-in rectangle should be visible just before the line starts"
        left, _, right, _ = box
        if rtl:
            assert left > line.right, f"RTL lead-in {left}-{right} must be right of the text (ends {line.right})"
            gap = left - line.right
        else:
            assert right < line.left, f"LTR lead-in {left}-{right} must be left of the text (starts {line.left})"
            gap = line.left - right
        # The gap depends on our PIL measurement matching libass's fallback font
        provider = _font_provider(result.log)
        if provider != "fontconfig":
            skip_unless_ci(f"lead-in gap check needs libass fontconfig provider (got {provider})")
        if name.startswith("arabic") and not features.check("raqm"):
            skip_unless_ci("measuring Arabic width needs Pillow with raqm (shaping)")
        assert gap <= LEAD_IN_MAX_GAP, f"lead-in stops {gap}px from the text"


@pytest.mark.parametrize("name", ["hebrew", "arabic"])
def test_rtl_fill_tags_do_not_move_the_line(tmp_path, name):
    """{\\frz180\\frx180\\fry180} must be a visual no-op apart from the fill direction:
    the same line rendered without it must occupy exactly the same pixels."""
    words = next(s.words for s in SAMPLES if s.name == name)
    ass_path = generate_ass(tmp_path, [make_segment(words)])
    with open(ass_path, encoding="utf-8") as f:
        ass = f.read()
    assert RTL_KARAOKE_FILL_TAGS in ass
    plain_path = tmp_path / "plain.ass"
    plain_path.write_text(ass.replace(RTL_KARAOKE_FILL_TAGS, ""), encoding="utf-8")

    with_tags = _line(render_frames(ass_path, [T_BEFORE]), T_BEFORE)
    without = _line(render_frames(str(plain_path), [T_BEFORE]), T_BEFORE)
    for attr in ("left", "right", "top", "bottom"):
        assert abs(getattr(with_tags, attr) - getattr(without, attr)) <= 1, (
            f"{attr}: {getattr(with_tags, attr)} with tags vs {getattr(without, attr)} without"
        )
