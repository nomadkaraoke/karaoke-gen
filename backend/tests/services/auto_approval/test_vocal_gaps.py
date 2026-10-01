"""Tests for vocal-gap detection (sung stretches with no transcribed words).

Motivating case: job 5710831e — the transcription dropped three sung lines (11.7s,
lead vocal ~96% active) and the video showed "INSTRUMENTAL" over the singing, while
genuine instrumentals measured 1-2% active. These tests build that situation on
synthesized audio plus synthetic correction data with the same structure (including
the off-by-one anchor reference ids seen at "[Section]" header boundaries).
"""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

from backend.services.auto_approval.vocal_gaps import (
    MIN_GAP_S,
    VocalGapsResult,
    _bridge,
    _longest_run,
    compute_vocal_gaps,
    reference_lines_between,
    transcription_gaps,
)


def _synth_stem(tmp_path, sung_regions, duration_s=40.0):
    """WAV 'vocal stem': silence with a 440Hz tone over ``sung_regions``."""
    from pydub import AudioSegment
    from pydub.generators import Sine

    audio = AudioSegment.silent(duration=int(duration_s * 1000), frame_rate=22050)
    for start_s, end_s in sung_regions:
        tone = Sine(440, sample_rate=22050).to_audio_segment(
            duration=int((end_s - start_s) * 1000)
        ).apply_gain(-6)
        audio = audio.overlay(tone, position=int(start_s * 1000))
    path = os.path.join(str(tmp_path), "lead_vocals.wav")
    audio.export(path, format="wav")
    return path


def _word(wid, text, start, end):
    return {"id": wid, "text": text, "start_time": start, "end_time": end}


def _segments(*lines):
    return [{"id": f"s{i}", "text": " ".join(w["text"] for w in ws), "words": ws,
             "start_time": ws[0]["start_time"], "end_time": ws[-1]["end_time"]}
            for i, ws in enumerate(lines)]


# --------------------------------------------------------------- gap finding

class TestTranscriptionGaps:
    def test_finds_leading_inner_and_trailing_gaps(self):
        segs = _segments([_word("a", "a", 5.0, 6.0), _word("b", "b", 6.2, 7.0)],
                         [_word("c", "c", 15.0, 16.0)])
        gaps = transcription_gaps(segs, audio_duration=30.0)
        assert [(s, e) for s, e, _, _ in gaps] == [(0.0, 5.0), (7.0, 15.0), (16.0, 30.0)]
        assert gaps[1][2]["id"] == "b" and gaps[1][3]["id"] == "c"
        assert gaps[0][2] is None and gaps[2][3] is None

    def test_short_gaps_ignored(self):
        segs = _segments([_word("a", "a", 0.5, 1.0), _word("b", "b", 1.0 + MIN_GAP_S - 0.1, 5.0)])
        assert transcription_gaps(segs, audio_duration=5.5) == []

    def test_overlapping_words_do_not_create_negative_gaps(self):
        segs = _segments([_word("a", "a", 0.0, 10.0), _word("b", "b", 2.0, 3.0), _word("c", "c", 11.0, 12.0)])
        assert [(s, e) for s, e, _, _ in transcription_gaps(segs, 12.0)] == []

    def test_words_without_timing_are_skipped(self):
        segs = _segments([_word("a", "a", 0.0, 1.0), {"id": "x", "text": "x", "start_time": None, "end_time": None}])
        assert [(s, e) for s, e, _, _ in transcription_gaps(segs, 10.0)] == [(1.0, 10.0)]


class TestRunHelpers:
    def test_bridge_fills_short_breaths_only(self):
        a = np.array([1, 0, 0, 1, 0, 0, 0, 0, 1], dtype=bool)
        assert _bridge(a, 2).tolist() == [1, 1, 1, 1, 0, 0, 0, 0, 1]

    def test_longest_run(self):
        assert _longest_run(np.array([1, 1, 0, 1, 1, 1, 0], dtype=bool)) == 3
        assert _longest_run(np.zeros(4, dtype=bool)) == 0


# --------------------------------------------------------------- end to end (audio)

class TestComputeVocalGaps:
    def test_dropped_sung_lines_are_suspect_and_instrumental_is_not(self, tmp_path):
        # Words 2-8s; singing continues 8-20s with no words (dropped lines, with
        # short breaths); a silent instrumental 20-30s; words again 30-34s.
        sung = [(2.0, 8.0), (8.3, 12.0), (12.4, 16.0), (16.3, 20.0), (30.0, 34.0)]
        stem = _synth_stem(tmp_path, sung)
        segs = _segments([_word("a", "a", 2.0, 5.0), _word("b", "b", 5.0, 8.0)],
                         [_word("c", "c", 30.0, 32.0), _word("d", "d", 32.0, 34.0)])
        result = compute_vocal_gaps(segs, stem)
        assert result.error is None
        by_start = {g.start: g for g in result.gaps}
        dropped = by_start[8.0]
        assert dropped.suspect
        # Diluted by the instrumental sharing the gap — the run, not the fraction, decides
        assert 0.4 < dropped.active_fraction < 0.6
        # 12s of bridged singing, minus the held-note allowance at the gap start
        assert dropped.longest_run_s >= 10.0, "breaths inside singing must be bridged"
        assert result.suspect_count == 1
        assert result.max_suspect_run_s == dropped.longest_run_s

    def test_genuine_instrumental_is_not_suspect(self, tmp_path):
        stem = _synth_stem(tmp_path, [(2.0, 8.0), (25.0, 30.0)])
        segs = _segments([_word("a", "a", 2.0, 8.0)], [_word("b", "b", 25.0, 30.0)])
        result = compute_vocal_gaps(segs, stem)
        assert [(g.start, g.suspect) for g in result.gaps] == [(8.0, False), (30.0, False)]  # middle, outro
        assert result.suspect_count == 0

    def test_short_dropped_line_in_long_gap_is_still_suspect(self, tmp_path):
        """A 4s dropped line followed by a long instrumental in the same gap."""
        stem = _synth_stem(tmp_path, [(2.0, 8.0), (8.5, 12.5), (28.0, 30.0)])
        segs = _segments([_word("a", "a", 2.0, 8.0)], [_word("b", "b", 28.0, 30.0)])
        gap = next(g for g in compute_vocal_gaps(segs, stem).gaps if g.start == 8.0)
        assert gap.active_fraction < 0.25
        assert gap.suspect

    def test_held_note_past_last_word_is_not_suspect(self, tmp_path):
        """The word's transcribed end is 3.5s before the singer stops (held note)."""
        stem = _synth_stem(tmp_path, [(2.0, 11.5), (25.0, 30.0)])
        segs = _segments([_word("a", "a", 2.0, 8.0)], [_word("b", "b", 25.0, 30.0)])
        gap = next(g for g in compute_vocal_gaps(segs, stem).gaps if g.start == 8.0)
        assert not gap.suspect
        assert gap.longest_run_s == pytest.approx(2.0, abs=0.1)

    def test_singing_after_a_pause_gets_no_allowance(self, tmp_path):
        """A dropped line that starts after a real pause is counted in full."""
        stem = _synth_stem(tmp_path, [(2.0, 8.0), (10.0, 14.0), (25.0, 30.0)])
        segs = _segments([_word("a", "a", 2.0, 8.0)], [_word("b", "b", 25.0, 30.0)])
        gap = next(g for g in compute_vocal_gaps(segs, stem).gaps if g.start == 8.0)
        assert gap.suspect
        assert gap.longest_run_s == pytest.approx(4.0, abs=0.1)

    def test_silent_stem_is_an_error_not_a_clean_pass(self, tmp_path):
        stem = _synth_stem(tmp_path, [], duration_s=20.0)
        result = compute_vocal_gaps(_segments([_word("a", "a", 2.0, 4.0)]), stem)
        assert result.error and "silent" in result.error
        assert result.gaps == []

    def test_malformed_reference_does_not_lose_audio_result(self, tmp_path):
        stem = _synth_stem(tmp_path, [(2.0, 20.0)], duration_s=25.0)
        bad = {"reference_lyrics": {"genius": {"segments": [{"words": [{"text": "no id"}]}]}},
               "anchor_sequences": [{"transcribed_word_ids": ["a"], "reference_word_ids": {"genius": ["zz"]}}]}
        result = compute_vocal_gaps(_segments([_word("a", "a", 2.0, 4.0)]), stem, bad)
        assert result.error is None
        assert result.suspect_count == 1

    def test_brief_ad_lib_in_gap_is_not_suspect(self, tmp_path):
        stem = _synth_stem(tmp_path, [(2.0, 8.0), (14.0, 15.5), (25.0, 30.0)])
        segs = _segments([_word("a", "a", 2.0, 8.0)], [_word("b", "b", 25.0, 30.0)])
        gap = next(g for g in compute_vocal_gaps(segs, stem).gaps if g.start == 8.0)
        assert not gap.suspect
        assert 1.0 < gap.longest_run_s < 2.0

    def test_missing_stem_returns_error_not_raise(self, tmp_path):
        result = compute_vocal_gaps(_segments([_word("a", "a", 0.0, 1.0)]), str(tmp_path / "nope.flac"))
        assert isinstance(result, VocalGapsResult)
        assert result.error

    def test_result_is_json_serializable(self, tmp_path):
        stem = _synth_stem(tmp_path, [(2.0, 20.0)], duration_s=25.0)
        result = compute_vocal_gaps(_segments([_word("a", "a", 2.0, 4.0)]), stem)
        json.dumps(result.to_dict())


# --------------------------------------------------------------- reference lines

def _ref(lines):
    """Reference lyrics source: list of line strings -> segments with word ids."""
    segs = []
    for li, line in enumerate(lines):
        segs.append({"text": line, "words": [{"id": f"r{li}_{wi}", "text": t}
                                              for wi, t in enumerate(line.split())]})
    return {"segments": segs}


REFERENCE = [
    "[Verse]",
    "come run far away",
    "[Pre-Chorus]",
    "like two crazy people",
    "we kept moments close",       # dropped
    "you were pretty as a flower",  # dropped
    "[Chorus]",
    "like two crazy people",
]


def _correction_data(before_ref_ids, after_ref_ids):
    return {
        "reference_lyrics": {"genius": _ref(REFERENCE)},
        "anchor_sequences": [
            {"transcribed_word_ids": ["t_before"], "reference_word_ids": {"genius": before_ref_ids}},
            {"transcribed_word_ids": ["t_after"], "reference_word_ids": {"genius": after_ref_ids}},
        ],
        "gap_sequences": [],
    }


class TestReferenceLinesBetween:
    before, after = {"id": "t_before"}, {"id": "t_after"}

    def test_lines_between_neighbouring_anchors(self):
        data = _correction_data(["r3_3"], ["r7_0"])  # "people" ... "like"
        assert reference_lines_between(data, self.before, self.after) == {
            "genius": ["we kept moments close", "you were pretty as a flower"]
        }

    def test_off_by_one_anchor_at_line_start_is_snapped(self):
        # Real job: the word before the gap mapped to the FIRST word of the next line
        # ("people" -> "we") because "[Section]" headers shift anchor ids by one.
        data = _correction_data(["r4_0"], ["r7_0"])
        assert reference_lines_between(data, self.before, self.after)["genius"][0] == "we kept moments close"

    def test_off_by_one_anchor_at_line_end_is_snapped(self):
        data = _correction_data(["r3_3"], ["r6_0"])  # "like" landed on the "[Chorus]" header's last word
        assert reference_lines_between(data, self.before, self.after) == {
            "genius": ["we kept moments close", "you were pretty as a flower"]
        }

    def test_single_word_line_before_gap_is_not_reported_missing(self):
        lines = ["intro words here", "Yeah", "dropped line one", "after the gap"]
        data = {"reference_lyrics": {"genius": _ref(lines)},
                "anchor_sequences": [
                    {"transcribed_word_ids": ["t_before"], "reference_word_ids": {"genius": ["r1_0"]}},
                    {"transcribed_word_ids": ["t_after"], "reference_word_ids": {"genius": ["r3_0"]}}],
                "gap_sequences": []}
        assert reference_lines_between(data, self.before, self.after) == {"genius": ["dropped line one"]}

    def test_section_headers_are_dropped(self):
        data = _correction_data(["r1_3"], ["r7_0"])
        lines = reference_lines_between(data, self.before, self.after)["genius"]
        assert not any(l.startswith("[") for l in lines)

    def test_reversed_order_from_repeated_chorus_returns_nothing(self):
        data = _correction_data(["r7_3"], ["r3_0"])
        assert reference_lines_between(data, self.before, self.after) == {}

    def test_gap_at_song_edge_has_no_reference_lines(self):
        data = _correction_data(["r3_3"], ["r7_0"])
        assert reference_lines_between(data, None, self.after) == {}
        assert reference_lines_between(data, self.before, None) == {}

    def test_unmapped_words_return_nothing(self):
        data = _correction_data(["r3_3"], ["r7_0"])
        assert reference_lines_between(data, {"id": "other"}, self.after) == {}

    def test_compute_includes_reference_lines(self, tmp_path):
        stem = _synth_stem(tmp_path, [(2.0, 20.0), (30.0, 34.0)])
        segs = _segments([_word("t_before", "people", 2.0, 8.0)], [_word("t_after", "like", 30.0, 34.0)])
        gap = next(g for g in compute_vocal_gaps(segs, stem, _correction_data(["r3_3"], ["r7_0"])).gaps
                   if g.start == 8.0)
        assert gap.suspect
        assert gap.reference_lines == {"genius": ["we kept moments close", "you were pretty as a flower"]}
