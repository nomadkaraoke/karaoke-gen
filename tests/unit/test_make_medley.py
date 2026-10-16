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
        assert graph.endswith("[x4]loudnorm=I=-14.0:TP=-1.0:LRA=11,aresample=44100[out]")
