"""Unit tests for importing a customer's chord chart (scripts/medley/chord_chart.py)."""

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "medley" / "chord_chart.py"
_spec = importlib.util.spec_from_file_location("chord_chart", _SCRIPT)
cc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cc
_spec.loader.exec_module(cc)


def sung_line(text: str, start: float, step: float = 0.5):
    """(text, start, end) per word, back to back."""
    return [(w, start + i * step, start + (i + 1) * step - 0.05) for i, w in enumerate(text.split())]


# ---- reading ----


def test_docx_text_keeps_spacing_and_ignores_tab_stops(tmp_path):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = (f'<w:document {ns}><w:body>'
            '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="916"/></w:tabs></w:pPr>'
            '<w:r><w:t xml:space="preserve">   </w:t></w:r><w:r><w:t>G</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>He said</w:t><w:br/><w:t>hi</w:t><w:tab/><w:t>x</w:t></w:r></w:p>'
            '</w:body></w:document>')
    path = tmp_path / "chart.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", body)
    assert cc.docx_text(str(path)) == "   G\nHe said\nhi\tx"


def test_chart_lines_undo_soft_wrapping():
    long = "Everything I got on" + " " * 41 + "F    G"  # F at column 60 = 3rd row of 30
    assert cc.chart_lines(long + "\nWhen that sun", wrap=30) == [
        "Everything I got on", "", "F    G", "When that sun"]
    assert cc.chart_lines("C    Some words") == ["C", "Some words"]  # Word soft line break


def test_chord_and_capo_recognition():
    assert cc.is_chord_line("G/Em   C   D7  F#m  Bbmaj7 Asus4 N.C.")
    assert not cc.is_chord_line("Amarillo by mornin'")
    assert cc.capo_directive("Capo 2") == 2
    assert cc.capo_directive("No Capo        ") == 0
    assert cc.capo_directive("Key change Capo 1") == 1
    assert cc.capo_directive("He put a capo on and played it all night long") is None
    assert cc.is_comment("Chorus:") and cc.is_comment("[Chorus]") and cc.is_comment("(Talking)")
    assert cc.is_comment("Instrumental") and cc.is_comment("Verse 2")
    assert not cc.is_comment("hearts:")  # a lyric that happens to end in a colon


# ---- parsing ----


CHART = """\
"My Song" (Full intro)
Capo 2
C           Em
Amarillo by mornin'

         G/Em   C   D
Remember when.

Instrumental
C Em F
Key change Capo 3
              G
He stopped loving her
"""


def test_parse_chart():
    chart = cc.parse_chart(CHART)
    assert chart.words == ["amarillo", "by", "mornin'", "remember", "when", "he", "stopped", "loving", "her"]
    got = [(c.label, c.capo, c.word, c.after) for c in chart.chords]
    assert got == [
        ("C", 2, 0, None), ("Em", 2, 2, None),          # over "Amarillo" and "mornin'"
        ("G/Em", 2, 4, None),                           # over "when." (blank line in between is fine)
        ("C", 2, None, 4), ("D", 2, None, 4),           # past the end of the line: played after it
        ("C", 2, None, 4), ("Em", 2, None, 4), ("F", 2, None, 4),  # chords-only line: the break
        ("G", 3, 7, None),                              # col 14 = "loving", after the capo change
    ]
    assert chart.lines == [(0, 2), (3, 4), (5, 8)]
    assert len({c.group for c in chart.chords if c.word is None}) == 2


# ---- timing ----


def test_to_shape_and_roots():
    assert cc.to_shape("D", 2) == "C" and cc.to_shape("F#m7", 2) == "Em" and cc.to_shape("Ab", 1) == "G"
    assert cc.to_shape("Eb7", 1) == "D7" and cc.to_shape("Gmaj7", 0) == "G"
    assert cc.root_pitches("G/Em") == {7, 4}


def test_align_words_interpolates_misses():
    mapping = cc.align_words(["dated", "nineteensixtytwo", "he"], ["dated", "1962", "he"])
    assert mapping == {0: 0, 1: 1, 2: 2}
    with pytest.raises(ValueError):
        cc.align_words(["la"], ["nothing", "alike"])


def test_chart_to_chords_end_to_end():
    chart = cc.parse_chart("""\
No Capo
                          G
He said I'll love you til die
            C
Hoping she'd come back
Key change Capo 1
               G
Kept some letters
""")
    sung = sung_line("He said I'll love you til die", 10.0) + sung_line("Hoping she'd come back", 15.0) \
        + sung_line("Kept some letters", 25.0)
    detected = [
        {"start": 2.0, "end": 6.0, "label": "C"},    # intro (uncharted): kept, no capo
        {"start": 6.0, "end": 13.0, "label": "G"},   # intro: kept
        {"start": 13.0, "end": 17.0, "label": "G"},  # mid-line: the chart rules
        {"start": 17.0, "end": 21.0, "label": "C"},  # break: same chord as the one ringing -> dropped
        {"start": 21.0, "end": 24.0, "label": "Eb"},  # break: pivot into the new key -> D, capo 1
        {"start": 24.0, "end": 26.0, "label": "Ab"},  # just before the line, anticipates its G -> dropped
        {"start": 26.0, "end": 30.0, "label": "Ab"},
    ]
    chords = cc.chart_to_chords(chart, sung, detected, duration=32.0)
    got = [(c["start"], c["label"], c["capo"]) for c in chords]
    assert got == [(2.0, "C", 0), (6.0, "G", 0), (13.0, "G", 0), (16.0, "C", 0), (21.0, "D", 1), (26.0, "G", 1)]
    assert chords[-1]["end"] == 30.0  # last detected chord's end
    assert all(a["end"] == b["start"] for a, b in zip(chords, chords[1:]))


def test_free_chords_follow_detected_changes_or_spread_evenly():
    chart = cc.parse_chart("""\
         G   C   D
Remember when.
G
Again
""")
    sung = sung_line("Remember when.", 10.0) + sung_line("Again", 20.0)
    detected = [{"start": 12.0, "end": 14.0, "label": "C"}]  # C heard at 12; D not detected
    chords = cc.chart_to_chords(chart, sung, detected, duration=25.0)
    (g, g_at), (c, c_at), (d, d_at), (g2, g2_at) = [(c["label"], c["start"]) for c in chords]
    assert (g, c, d, g2) == ("G", "C", "D", "G")
    assert g_at == 10.5  # on "when."
    assert c_at == 12.0  # matched to the detected change
    assert 12.0 < d_at <= 14.0  # spread after it
    assert g2_at == 20.0


def test_break_line_after_trailing_chords_keeps_chart_order():
    chart = cc.parse_chart(CHART)
    sung = sung_line("Amarillo by mornin'", 10.0) + sung_line("Remember when.", 14.0) \
        + sung_line("He stopped loving her", 40.0)
    chords = cc.chart_to_chords(chart, sung, [], duration=45.0)
    labels = [c["label"] for c in chords]
    assert labels == ["C", "Em", "G/Em", "C", "D", "C", "Em", "F", "G"]
    assert all(a["start"] < b["start"] for a, b in zip(chords, chords[1:]))
