"""CJK (Japanese/Chinese/Korean) line-splitting tests for SegmentResizer.

Bug (job 4dcca2d6): a Japanese karaoke video rendered with overlapping lyric lines.
``SegmentResizer`` gated and split lines on raw character count
(``len(text) <= max_line_length``), a proxy for visual width calibrated for Latin
text. East-Asian glyphs render ~2x as wide, so a line well under the 40-char budget
was still far wider than the 4K frame; libass smart-wrapped it onto a second physical
row that overlapped the slot below.

The fix measures a *display width* (East-Asian Wide/Fullwidth glyphs count as
``WIDE_RATIO`` units) so CJK lines split by visual width, while pure-Latin text —
where display width equals ``len`` — is unaffected. These tests cover the CJK path
and lock in the "Latin output is byte-for-byte unchanged" guarantee.
"""
import logging

from karaoke_gen.lyrics_transcriber.output.segment_resizer import (
    SegmentResizer,
    WIDE_RATIO,
    display_width,
)
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word


def _segment_from_words(words_text) -> LyricsSegment:
    """Build a segment whose text is the concatenation of its word tokens.

    Mirrors the render (transcription) path where CJK Word tokens are ~1 char and
    the segment text has no inter-word spaces.
    """
    words = []
    t = 0.0
    for i, w in enumerate(words_text):
        words.append(Word(id=f"w{i}", text=w, start_time=t, end_time=t + 0.3))
        t += 0.35
    text = "".join(words_text)
    return LyricsSegment(id="s", text=text, words=words, start_time=0.0, end_time=t)


def _resizer(max_line_length=40):
    return SegmentResizer(max_line_length=max_line_length, logger=logging.getLogger("test"))


# --------------------------------------------------------------------------- #
# display_width unit tests
# --------------------------------------------------------------------------- #

def test_display_width_ascii_equals_len():
    for s in ["", "hello world", "I have been searching, for a reason!", "abc-123 (x)"]:
        assert display_width(s) == len(s)


def test_display_width_counts_wide_glyphs():
    # Hiragana, Katakana, Kanji, Hangul are all East-Asian Wide.
    assert display_width("あ") == WIDE_RATIO
    assert display_width("ア") == WIDE_RATIO
    assert display_width("漢") == WIDE_RATIO
    assert display_width("한") == WIDE_RATIO
    assert display_width("漢字") == 2 * WIDE_RATIO


def test_display_width_mixed_cjk_and_latin():
    # "科学ok" -> 2 wide + 2 narrow
    assert display_width("科学ok") == 2 * WIDE_RATIO + 2


def test_display_width_halfwidth_katakana_counts_as_one():
    # Halfwidth katakana ('H' in east_asian_width) is not full-width.
    assert display_width("ｱ") == 1


# --------------------------------------------------------------------------- #
# Splitting behavior
# --------------------------------------------------------------------------- #

def test_japanese_line_split_under_display_width_budget():
    r = _resizer(40)
    jp = "その即会な輩に立ち向かう神明を不可信にて人々の閉ざされた心の闇に科学の力では誰も存在もできない"
    result = r.resize_segments([_segment_from_words(list(jp))])

    assert len(result) > 1, "expected the wide CJK line to be split into multiple lines"
    over = [s.text for s in result if display_width(s.text) > r.max_line_length and len(s.words) > 1]
    assert not over, f"Lines exceeding display-width budget: {over}"


def test_japanese_split_preserves_all_words_in_order():
    r = _resizer(40)
    jp = "その即会な輩に立ち向かう神明を不可信にて人々の閉ざされた心の闇に科学の力では誰も存在もできない"
    original = list(jp)
    result = r.resize_segments([_segment_from_words(original)])

    out_words = [w.text for s in result for w in s.words]
    assert out_words == original, "CJK word tokens lost or reordered across the split"


def test_chinese_line_split_under_budget():
    r = _resizer(40)
    zh = "我能吞下玻璃而不伤身体因为玻璃是透明的所以看起来很漂亮但其实很危险请不要模仿这个动作谢谢"
    result = r.resize_segments([_segment_from_words(list(zh))])

    assert len(result) > 1
    over = [s.text for s in result if display_width(s.text) > r.max_line_length and len(s.words) > 1]
    assert not over, f"Lines exceeding display-width budget: {over}"


def test_korean_line_split_under_budget():
    r = _resizer(40)
    ko = "나는유리를먹을수있어요그래도아프지않아요왜냐하면유리는투명해서예뻐보이지만사실은위험하니따라하지마세요"
    result = r.resize_segments([_segment_from_words(list(ko))])

    assert len(result) > 1
    over = [s.text for s in result if display_width(s.text) > r.max_line_length and len(s.words) > 1]
    assert not over, f"Lines exceeding display-width budget: {over}"


def test_mixed_cjk_and_latin_line_split_under_budget():
    r = _resizer(40)
    words = ["科", "学", "の", "power", "and", "力", "で", "は", "誰", "も", "存", "在", "できない", "really"]
    result = r.resize_segments([_segment_from_words(words)])

    over = [s.text for s in result if display_width(s.text) > r.max_line_length and len(s.words) > 1]
    assert not over, f"Lines exceeding display-width budget: {over}"
    out_words = [w.text for s in result for w in s.words]
    assert out_words == words, "words lost or reordered on a mixed CJK/Latin line"


def test_short_japanese_line_not_split():
    r = _resizer(40)
    # 10 glyphs -> display width 20, comfortably under the 40-unit budget.
    jp = "君の名前を呼んでいる"
    result = r.resize_segments([_segment_from_words(list(jp))])

    assert len(result) == 1
    assert result[0].text == jp


def test_multichar_cjk_tokens_never_fragmented():
    """A CJK Word token can span several characters (e.g. 立ち向かう, 不可信にて —
    both real single tokens in job 4dcca2d6). Splitting must never cut inside such a
    token: every input token must survive whole and in order, with no multi-word line
    over budget. Uses space-less text (the worst case for the old text-position
    splitter, which re-matched tokens by substring and dropped fragments)."""
    r = _resizer(36)
    tokens = ["立ち向かう", "神明", "不可信にて", "悪霊退散", "悪霊退散",
              "本能", "ごろ", "び", "こまった", "科学", "の", "力", "では"]
    seg = LyricsSegment(
        id="s",
        text="".join(tokens),  # space-less
        words=[Word(id=f"w{i}", text=t, start_time=i * 0.4, end_time=i * 0.4 + 0.3) for i, t in enumerate(tokens)],
        start_time=0.0,
        end_time=len(tokens) * 0.4,
    )
    result = r.resize_segments([seg])

    # Tokens preserved whole and in order (no fragmentation, no loss, no reorder).
    assert [w.text for s in result for w in s.words] == tokens
    # Each multi-word line respects the display-width budget.
    over = [s.text for s in result if display_width(s.text) > r.max_line_length and len(s.words) > 1]
    assert not over, f"Lines exceeding display-width budget: {over}"


# --------------------------------------------------------------------------- #
# English / Latin regression guard: output must be unchanged by the width metric
# --------------------------------------------------------------------------- #

def test_english_output_is_byte_for_byte_unchanged():
    """The fix must not alter Latin/European output at all. Lines without wide glyphs
    never enter the CJK packing path and the natural-break text splitter is untouched,
    so the produced line texts must match the exact pre-fix output for this fixture."""
    r = _resizer(36)
    text = "I have been searching for a reason to believe in something more than this"
    words = text.split()
    result = r.resize_segments(
        [
            LyricsSegment(
                id="s",
                text=text,
                words=[Word(id=f"w{i}", text=w, start_time=i * 0.3, end_time=i * 0.3 + 0.2) for i, w in enumerate(words)],
                start_time=0.0,
                end_time=len(words) * 0.3,
            )
        ]
    )

    # Exact line texts as produced on main before the fix (char-count splitter).
    assert [s.text for s in result] == [
        "I have been searching for",
        "a reason to believe",
        "in something more than this",
    ]
    for s in result:
        assert display_width(s.text) == len(s.text)  # no wide glyphs => identical metric
    assert [w.text for s in result for w in s.words] == words
