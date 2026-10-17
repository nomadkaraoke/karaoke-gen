"""Unit tests for the medley builder's pure helpers (scripts/medley/make_medley.py).

The script isn't part of the installed package, so we load it by path.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "medley" / "make_medley.py"
_spec = importlib.util.spec_from_file_location("make_medley", _SCRIPT)
medley = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = medley
_spec.loader.exec_module(medley)


EXAMPLE = {
    "title": "T", "artist": "A",
    "sections": [
        {"job_id": "a", "start": 0.0, "end": 54.0},
        {"job_id": "b", "start": 0.0, "end": 52.0, "join": "crossfade", "join_duration": 2.0},
        {"job_id": "c", "start": 0.0, "end": 57.0},
        {"job_id": "d", "start": 0.0, "end": 82.0, "fade_out_start": 80.7},
        {"job_id": "a", "start": 132.2, "end": None, "join": "gap", "join_duration": 0.7, "fade_in": 0.5},
    ],
}


def _segment(seg_id: str, start: float, end: float) -> LyricsSegment:
    words = [Word(id=f"{seg_id}w1", text="hello", start_time=start, end_time=(start + end) / 2),
             Word(id=f"{seg_id}w2", text="there", start_time=(start + end) / 2, end_time=end)]
    return LyricsSegment(id=seg_id, text="hello there", words=words, start_time=start, end_time=end)


class TestParseSpec:
    def test_defaults_to_two_second_crossfade(self):
        sections = medley.parse_spec(EXAMPLE)
        assert sections[2].join == "crossfade"
        assert sections[2].join_duration == 2.0

    def test_gap_and_pot_fields(self):
        sections = medley.parse_spec(EXAMPLE)
        assert sections[3].fade_out_start == 80.7
        assert sections[4].join == "gap" and sections[4].join_duration == 0.7
        assert sections[4].fade_in == 0.5 and sections[4].end is None

    @pytest.mark.parametrize("bad", [
        {"sections": []},
        {"sections": [{"job_id": "a", "start": 10, "end": 5}]},
        {"sections": [{"job_id": "a", "start": 0, "end": 10, "join": "splice"}]},
        {"sections": [{"job_id": "a", "start": 0, "end": 10, "fade_out_start": 12}]},
    ])
    def test_rejects_invalid_specs(self, bad):
        with pytest.raises(ValueError):
            medley.parse_spec(bad)


class TestComputeOffsets:
    def test_matches_the_country_medley_timeline(self):
        sections = medley.parse_spec(EXAMPLE)
        offsets = medley.compute_offsets(sections, [54.0, 52.0, 57.0, 82.0, 199.133 - 132.2])
        assert offsets == pytest.approx([0.0, 52.0, 102.0, 157.0, 239.7])

    def test_gap_inserts_silence_and_crossfade_overlaps(self):
        sections = medley.parse_spec({"sections": [
            {"job_id": "a", "start": 0, "end": 10},
            {"job_id": "b", "start": 0, "end": 10, "join": "gap", "join_duration": 1.0},
            {"job_id": "c", "start": 0, "end": 10, "join": "crossfade", "join_duration": 3.0},
        ]})
        assert medley.compute_offsets(sections, [10, 10, 10]) == pytest.approx([0, 11, 18])


class TestShiftSegments:
    def test_keeps_lines_starting_inside_the_cut_and_shifts_them(self):
        segs = [_segment("s1", 1.0, 3.0), _segment("s2", 50.0, 53.0), _segment("s3", 54.2, 57.0)]
        out = medley.shift_segments(segs, start=0.0, end=54.0, offset=100.0, prefix="m0")
        assert [s.id for s in out] == ["m0_s1", "m0_s2"]
        assert out[1].start_time == pytest.approx(150.0)
        assert [w.start_time for w in out[1].words] == pytest.approx([150.0, 151.5])
        assert out[1].words[0].id == "m0_s2w1"

    def test_open_ended_section_keeps_everything_after_start(self):
        segs = [_segment("s1", 120.0, 125.0), _segment("s2", 138.0, 140.0), _segment("s3", 180.0, 184.0)]
        out = medley.shift_segments(segs, start=132.2, end=None, offset=107.5, prefix="m4")
        assert [s.id for s in out] == ["m4_s2", "m4_s3"]
        assert out[0].start_time == pytest.approx(245.5)

    def test_same_song_twice_gets_unique_ids(self):
        segs = [_segment("s1", 1.0, 3.0)]
        a = medley.shift_segments(segs, 0.0, 10.0, 0.0, "m0")
        b = medley.shift_segments(segs, 0.0, 10.0, 50.0, "m4")
        assert a[0].id != b[0].id
        assert segs[0].start_time == 1.0  # originals untouched


class TestBuildFiltergraph:
    def test_crossfades_gap_and_pot_fades(self):
        sections = medley.parse_spec(EXAMPLE)
        graph = medley.build_filtergraph(sections, [54.0, 52.0, 57.0, 82.0, 66.9], [0.0, 1.0, -1.0, 0.5, 2.0])
        assert graph.count("acrossfade=d=2.000") == 3
        assert "afade=t=out:st=80.700:d=1.300" in graph  # pot out on section 4
        assert "apad=pad_dur=0.700" in graph            # silence before the gap join
        assert "afade=t=in:st=0:d=0.500" in graph       # pot in on the finale
        assert "[x3][s4]concat=n=2:v=0:a=1[x4]" in graph
        assert graph.endswith("[x4]anull[out]")
        assert "loudnorm" not in graph


class TestMedleyChords:
    def test_chords_follow_their_section_and_hand_over_mid_crossfade(self):
        sections = medley.parse_spec(EXAMPLE)
        durations = [54.0, 52.0, 57.0, 82.0, 66.9]
        offsets = medley.compute_offsets(sections, durations)
        by_job = {
            "a": [{"start": 0.0, "end": 60.0, "label": "G"}, {"start": 130.0, "end": 200.0, "label": "Ab"}],
            "b": [{"start": 0.0, "end": 52.0, "label": "C"}],
            "c": [{"start": 0.0, "end": 57.0, "label": "D"}],
            "d": [{"start": 0.0, "end": 82.0, "label": "F"}],
        }
        out = medley.medley_chords(sections, durations, offsets, by_job)
        labels = [(round(c["start"], 2), round(c["end"], 2), c["label"]) for c in out]
        # opener G cut where the 2s crossfade is half done (54 - 1), C starts there
        assert labels[0] == (0.0, 53.0, "G")
        assert labels[1] == (53.0, 103.0, "C")
        assert labels[2] == (103.0, 158.0, "D")
        # F runs to the end of its section (the finale joins with a gap, not a crossfade)
        assert labels[3] == (158.0, 239.0, "F")
        # the finale's chords start at the finale (source 132.2 -> medley 239.7)
        assert labels[4][0] == pytest.approx(239.7) and labels[4][2] == "Ab"

    def test_shift_chords_keeps_capo(self):
        out = medley.shift_chords([{"start": 0.0, "end": 5.0, "label": "C", "capo": 2}], 0.0, 10.0, 100.0)
        assert out == [{"start": 100.0, "end": 105.0, "label": "C", "capo": 2}]

    def test_shift_chords_drops_slivers(self):
        out = medley.shift_chords([{"start": 0, "end": 10.02, "label": "G"}, {"start": 10.02, "end": 20, "label": "C"}],
                                  10.0, 20.0, 100.0)
        assert [c["label"] for c in out] == ["C"]


class TestSingleSong:
    def test_one_section_spec_builds_a_plain_graph_and_full_chords(self):
        sections = medley.parse_spec({"sections": [{"job_id": "a", "start": 0, "end": None}]})
        assert medley.compute_offsets(sections, [200.0]) == [0.0]
        graph = medley.build_filtergraph(sections, [200.0], [1.5])
        assert "acrossfade" not in graph and "concat" not in graph
        assert graph.endswith("[s0]anull[out]")
        chords = medley.medley_chords(sections, [200.0], [0.0], {"a": [{"start": 0, "end": 200, "label": "G"}]})
        assert chords == [{"start": 0.0, "end": 200.0, "label": "G"}]


class TestMaster:
    def test_static_gain_to_target_then_limiter(self):
        f = medley.master_filter(-18.5)
        assert f.startswith("volume=4.50dB,")
        assert "alimiter=limit=0.8913" in f and "level=false" in f and "latency=true" in f
        assert "loudnorm" not in f  # dynamic loudnorm boosted near-silent intros into artifacts
        assert f.endswith("aresample=44100")

