"""Tests for the timing-plausibility gate signals (timing_check.py).

The acoustic behavior is validated on real jobs by the private corpus
harness (docs/automation-corpus/validate_timing_gate.py); these tests pin the
algorithm's building blocks and end-to-end behavior on synthesized audio where
the ground truth is constructed: words aligned with sung regions must not
fire, words claimed in silence / long unclaimed sung regions must.
"""
from __future__ import annotations

import os

import pytest

from backend.services.auto_approval.timing_check import (
    G1_PCT_START_INACTIVE,
    G2_N_SUSPECT_BAD,
    G3_MAX_UNCLAIMED_RUN_S,
    TimingSignals,
    _eqdur_run_flags,
    _repetition_run_flags,
    compute_timing_signals,
    gate_fired,
    shadow_fired,
)


# ---------------------------------------------------------------- structural

def _w(seg: int, text: str, start: float, end: float) -> dict:
    return {"seg": seg, "text": text, "start": start, "end": end}


class TestEqualDurationRuns:
    def test_three_identical_durations_flagged(self):
        words = [_w(0, "a", 0.0, 1.0), _w(0, "b", 1.0, 2.0), _w(0, "c", 2.0, 3.0)]
        assert _eqdur_run_flags(words) == [True, True, True]

    def test_two_identical_durations_not_flagged(self):
        words = [_w(0, "a", 0.0, 1.0), _w(0, "b", 1.0, 2.0), _w(0, "c", 2.0, 2.5)]
        assert _eqdur_run_flags(words) == [False, False, False]

    def test_segment_boundary_breaks_run(self):
        words = [_w(0, "a", 0.0, 1.0), _w(0, "b", 1.0, 2.0), _w(1, "c", 2.0, 3.0)]
        assert _eqdur_run_flags(words) == [False, False, False]

    def test_varied_durations_not_flagged(self):
        words = [_w(0, "a", 0.0, 0.3), _w(0, "b", 0.3, 0.9), _w(0, "c", 0.9, 1.1)]
        assert _eqdur_run_flags(words) == [False, False, False]

    def test_zero_duration_words_not_flagged(self):
        words = [_w(0, "a", 1.0, 1.0), _w(0, "b", 2.0, 2.0), _w(0, "c", 3.0, 3.0)]
        assert _eqdur_run_flags(words) == [False, False, False]


class TestRepetitionRuns:
    def test_single_token_needs_three_reps(self):
        assert _repetition_run_flags(["come", "come"]) == [False, False]
        assert _repetition_run_flags(["come", "come", "come"]) == [True] * 3

    def test_phrase_repetition_two_reps(self):
        toks = ["come", "on", "come", "on"]
        assert _repetition_run_flags(toks) == [True] * 4

    def test_pendulum_come_on_x4(self):
        toks = ["come", "on", "come", "on", "come", "on", "come", "on"]
        assert _repetition_run_flags(toks) == [True] * 8

    def test_no_repetition(self):
        toks = ["under", "the", "waves", "tonight"]
        assert _repetition_run_flags(toks) == [False] * 4

    def test_empty_tokens_never_flagged(self):
        assert _repetition_run_flags(["", "", ""]) == [False, False, False]


# ---------------------------------------------------------------- gate rules

class TestGateRules:
    def test_no_fire_on_clean_signals(self):
        assert gate_fired(TimingSignals(n_words=100)) == []

    def test_g1_start_silence(self):
        sig = TimingSignals(pct_start_inactive=G1_PCT_START_INACTIVE)
        assert gate_fired(sig) == ["start-silence"]

    def test_g2_suspect_mistimed(self):
        sig = TimingSignals(n_suspect_bad=G2_N_SUSPECT_BAD)
        assert gate_fired(sig) == ["suspect-mistimed"]

    def test_g3_unclaimed_vocal_is_shadow_only(self):
        # G3 has a known semantic FP mode (ad-libs / vocal samples absent from
        # the lyrics, e.g. e34f1782's zero-touch 17.6s run) — it is recorded
        # for calibration but must never gate.
        sig = TimingSignals(max_unclaimed_run_s=G3_MAX_UNCLAIMED_RUN_S)
        assert gate_fired(sig) == []
        assert shadow_fired(sig) == ["unclaimed-vocal"]

    def test_below_thresholds_no_fire(self):
        sig = TimingSignals(
            pct_start_inactive=G1_PCT_START_INACTIVE - 0.1,
            n_suspect_bad=G2_N_SUSPECT_BAD - 1,
            max_unclaimed_run_s=G3_MAX_UNCLAIMED_RUN_S - 0.1,
        )
        assert gate_fired(sig) == []


# ---------------------------------------------------------------- end-to-end

def _synth_stem(tmp_path, sung_regions, duration_s=30.0):
    """Write a WAV 'vocal stem': silence with 440Hz tone over sung_regions."""
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


def _segments(words):
    return [{
        "id": "s0",
        "words": [
            {"id": f"w{i}", "text": t, "start_time": s, "end_time": e}
            for i, (t, s, e) in enumerate(words)
        ],
    }]


class TestComputeTimingSignals:
    def test_aligned_words_do_not_fire(self, tmp_path):
        # Words exactly covering the sung regions -> plausible timing.
        words = [(f"word{i}", 1.0 + i * 2.0, 2.4 + i * 2.0) for i in range(10)]
        stem = _synth_stem(tmp_path, [(s, e) for _, s, e in words])
        sig = compute_timing_signals(_segments(words), stem)
        assert sig.error is None
        assert sig.fired == []
        assert sig.n_words == 10
        assert sig.pct_start_inactive < G1_PCT_START_INACTIVE

    def test_words_claimed_in_silence_fire_start_silence(self, tmp_path):
        # Singing happens 20-28s but every word is claimed at 1-11s (silence):
        # the hand-retimed-section signature (corpus 1d45b286).
        words = [(f"word{i}", 1.0 + i, 1.8 + i) for i in range(10)]
        stem = _synth_stem(tmp_path, [(20.0, 28.0)])
        sig = compute_timing_signals(_segments(words), stem)
        assert sig.error is None
        assert "start-silence" in sig.fired
        assert sig.pct_start_inactive > 50

    def test_long_unclaimed_sung_region_recorded_as_shadow(self, tmp_path):
        # Words cover 1-3s; 10 further seconds of singing claimed by nothing
        # (held-note under-extension / missing words; corpus f986dfe5 = 8.2s).
        # Recorded as shadow only — must NOT gate (ad-lib FP mode).
        words = [("hello", 1.0, 2.0), ("there", 2.0, 3.0)]
        stem = _synth_stem(tmp_path, [(1.0, 3.0), (10.0, 20.0)])
        sig = compute_timing_signals(_segments(words), stem)
        assert sig.error is None
        assert "unclaimed-vocal" not in sig.fired
        assert "unclaimed-vocal" in sig.shadow_fired
        assert sig.max_unclaimed_run_s >= G3_MAX_UNCLAIMED_RUN_S

    def test_missing_file_returns_error_not_raise(self):
        sig = compute_timing_signals(
            _segments([("a", 0.0, 1.0)]), "/nonexistent/stem.flac"
        )
        assert sig.error is not None
        assert sig.fired == []

    def test_no_words_returns_error(self, tmp_path):
        stem = _synth_stem(tmp_path, [(1.0, 2.0)], duration_s=5.0)
        sig = compute_timing_signals([], stem)
        assert sig.error is not None

    def test_to_dict_is_json_serializable(self, tmp_path):
        import json

        words = [("hey", 1.0, 1.5)]
        stem = _synth_stem(tmp_path, [(1.0, 1.5)], duration_s=5.0)
        sig = compute_timing_signals(_segments(words), stem)
        json.dumps(sig.to_dict())  # must not raise

    def test_silent_stem_is_analysis_unavailable(self, tmp_path):
        # A silent (failed-separation) stem must NOT mark every word start
        # inactive and fire — it goes down the fail-open error path.
        words = [(f"word{i}", 1.0 + i, 1.8 + i) for i in range(10)]
        stem = _synth_stem(tmp_path, [])  # no sung regions at all
        sig = compute_timing_signals(_segments(words), stem)
        assert sig.error is not None
        assert sig.fired == []
