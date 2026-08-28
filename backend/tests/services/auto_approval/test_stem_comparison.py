"""Tests for the pure 3-stem comparison signal extraction.

Synthesizes tiny WAVs with pydub so the signal math is exercised on real
audio decoding, not mocks.
"""
from __future__ import annotations

import os

import pytest
from pydub import AudioSegment
from pydub.generators import Sine, WhiteNoise

from karaoke_gen.instrumental_review.stem_comparison import (
    StemComparison,
    compare_stems,
)


def _silence(ms: int = 4000) -> AudioSegment:
    return AudioSegment.silent(duration=ms, frame_rate=16000)


def _tone_bursts(ms: int = 4000, *, burst_ms: int = 500, gain_db: float = -10.0) -> AudioSegment:
    """Alternating 500ms tone / 500ms silence."""
    out = AudioSegment.silent(duration=0, frame_rate=16000)
    tone = Sine(440, sample_rate=16000).to_audio_segment(duration=burst_ms).apply_gain(gain_db)
    quiet = AudioSegment.silent(duration=burst_ms, frame_rate=16000)
    while len(out) < ms:
        out += tone + quiet
    return out[:ms]


def _write(tmp_path, name: str, audio: AudioSegment) -> str:
    path = os.path.join(tmp_path, name)
    audio.export(path, format="wav")
    return path


def test_silent_backing_has_zero_fractions(tmp_path) -> None:
    backing = _write(tmp_path, "b.wav", _silence())
    lead = _write(tmp_path, "l.wav", _tone_bursts())
    vocals = _write(tmp_path, "v.wav", _tone_bursts())
    c = compare_stems(backing, lead, vocals)
    assert c.error is None
    assert c.backing_audible_fraction == 0.0
    assert c.coverage_ratio == 0.0
    assert c.lead_overlap_fraction == 0.0
    assert c.lead_audible_fraction > 0.3


def test_backing_identical_to_vocals_is_high_corr_full_coverage(tmp_path) -> None:
    # The backing-stem-is-the-lead shape: backing tracks the whole vocal line
    # while the lead stem is empty.
    bursts = _tone_bursts()
    backing = _write(tmp_path, "b.wav", bursts)
    lead = _write(tmp_path, "l.wav", _silence())
    vocals = _write(tmp_path, "v.wav", bursts)
    c = compare_stems(backing, lead, vocals)
    assert c.error is None
    assert c.coverage_ratio == 1.0
    assert c.corr_backing_vocals > 0.95
    assert c.backing_audible_fraction > c.lead_audible_fraction
    assert c.lead_overlap_fraction == 0.0


def test_offset_backing_has_low_overlap_and_corr(tmp_path) -> None:
    # Backing sings only in the lead's gaps (genuine call-and-response shape).
    bursts = _tone_bursts()
    offset = _silence(500) + bursts
    backing = _write(tmp_path, "b.wav", offset[: len(bursts)])
    lead = _write(tmp_path, "l.wav", bursts)
    vocals = _write(tmp_path, "v.wav", bursts)
    c = compare_stems(backing, lead, vocals)
    assert c.error is None
    assert c.corr_backing_vocals < 0.0  # anti-phase with the vocal line
    assert c.lead_overlap_fraction < 0.2


def test_continuous_noise_floor_is_flat(tmp_path) -> None:
    noise = WhiteNoise(sample_rate=16000).to_audio_segment(duration=8000).apply_gain(-25.0)
    backing = _write(tmp_path, "b.wav", noise)
    lead = _write(tmp_path, "l.wav", _tone_bursts(8000))
    vocals = _write(tmp_path, "v.wav", _tone_bursts(8000))
    c = compare_stems(backing, lead, vocals)
    assert c.error is None
    assert c.backing_audible_fraction == 1.0
    assert c.flat_fraction > 0.9  # sustained low-variance content
    assert c.backing_db_std < 2.5


def test_missing_file_reports_error_not_raise(tmp_path) -> None:
    lead = _write(tmp_path, "l.wav", _silence(500))
    c = compare_stems(os.path.join(tmp_path, "nope.wav"), lead, lead)
    assert isinstance(c, StemComparison)
    assert c.error is not None


def test_to_dict_round_trips_json_safe(tmp_path) -> None:
    import json

    p = _write(tmp_path, "a.wav", _tone_bursts(1000))
    c = compare_stems(p, p, p)
    payload = c.to_dict()
    assert json.loads(json.dumps(payload)) == payload
