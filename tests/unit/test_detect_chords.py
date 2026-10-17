"""Unit tests for the chord-detection ensemble's pure helpers (scripts/medley/detect_chords.py)."""

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "medley" / "detect_chords.py"
_spec = importlib.util.spec_from_file_location("detect_chords", _SCRIPT)
dc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dc
_spec.loader.exec_module(dc)


def test_simplify_maps_to_guitar_chords():
    assert dc.simplify("G#:7") == (8, "7")
    assert dc.simplify("C:maj/3") == (0, "")
    assert dc.simplify("A:min7") == (9, "m7")
    assert dc.simplify("D:hdim7") == (2, "m7b5")
    assert dc.simplify("Bb:min6") == (10, "m")
    assert dc.simplify("N") is None and dc.simplify("X") is None


def test_spelling_follows_the_key():
    ab, g, c = 8, 7, 0
    assert dc.spell(8, "", ab) == "Ab" and dc.spell(1, "", ab) == "Db" and dc.spell(3, "7", ab) == "Eb7"
    assert dc.spell(6, "m", 2) == "F#m"  # F#m in D
    assert dc.spell(10, "", c) == "Bb"  # borrowed bVII in C stays flat
    assert dc.spell(10, "", g) == "Bb" and dc.spell(3, "", g) == "Eb"  # bIII and bVI in G
    assert dc.spell(1, "", 9) == "C#"  # C# in A


def test_estimate_key_picks_the_diatonic_home():
    i_iv_v_in_ab = [(0, 10, (8, "")), (10, 15, (1, "")), (15, 20, (3, "7"))]
    assert dc.estimate_key(i_iv_v_in_ab, 0, 20) == 8
    in_d = [(0, 4, (2, "")), (4, 6, (6, "m7")), (6, 8, (7, "")), (8, 10, (9, ""))]
    assert dc.estimate_key(in_d, 0, 10) == 2


def test_vote_weights_and_ties():
    labs = [
        (1.0, [(0.0, 1.0, "G"), (1.0, 2.0, "C")]),
        (1.0, [(0.0, 1.0, "G"), (1.0, 2.0, "A:min")]),
        (1.5, [(0.0, 2.0, "G")]),
    ]
    frames = dc.vote(labs, 2.0)
    assert frames[0] == (7, "")
    assert frames[-1] == (7, "")  # G 1.5 beats C 1.0 and Am 1.0


def test_merge_short_folds_blips_into_neighbours():
    segs = [[0, 4, "G"], [4, 4.2, "C"], [4.2, 8, "G"], [8, 12, "D"]]
    assert dc.merge_short(segs) == [[0, 8, "G"], [8, 12, "D"]]


def test_snap_to_beats_moves_boundaries_within_tolerance_only():
    segs = [[0, 4.1, "G"], [4.1, 8.5, "C"], [8.5, 12, "D"]]
    out = dc.snap_to_beats([s[:] for s in segs], [2.0, 4.0, 6.0, 8.0, 10.0])
    assert out[0][1] == out[1][0] == 4.0
    assert out[1][1] == 8.5  # 0.5s from the nearest beat: left alone


def test_label_segments_spells_and_drops_no_chord():
    segs = [[0, 10, (8, "")], [10, 12, None], [12, 20, (1, "")], [20, 26, (3, "7")]]
    assert [c["label"] for c in dc.label_segments(segs)] == ["Ab", "Db", "Eb7"]
