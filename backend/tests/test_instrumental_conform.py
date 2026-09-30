"""Lining a user instrumental up with its mix when their lengths differ."""
import shutil
import subprocess

import numpy as np
import pytest

from backend.services.instrumental_conform import (
    InstrumentalConformError,
    _probe_duration,
    build_filter,
    conform_instrumental,
)
from backend.services.derived_vocals import SAMPLE_RATE, _decode_mono, find_offset

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed"
)

SR = 44100


def _write_wav(path, samples):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
         "-c:a", "pcm_s24le", str(path)],
        input=samples.astype(np.float32).tobytes(), check=True,
    )


def _song(seconds, seed=1):
    """Band-limited noise 'instrumental' + tone-burst 'vocals'."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    inst = np.convolve(rng.normal(0, 0.2, n), np.ones(8) / 8, mode="same")
    t = np.arange(n) / SR
    vocals = 0.2 * np.sin(2 * np.pi * 440 * t) * ((t.astype(int) % 2) == 0)
    return inst, vocals


@requires_ffmpeg
@pytest.mark.parametrize(
    "lead_in, tail, expect_start, expect_end",
    [
        (3.0, 0.0, 3.0, 0.0),    # extra count-in on the instrumental (Keep Smiling)
        (0.0, 6.5, 0.0, 6.5),    # longer outro on the instrumental (Simulation)
        (0.0, -4.5, 0.0, -4.5),  # instrumental stops early (Faithful and True)
        (-1.5, 0.0, -1.5, 0.0),  # instrumental starts late
    ],
)
def test_conform_aligns_and_fits_mix_length(tmp_path, lead_in, tail, expect_start, expect_end):
    inst, vocals = _song(40)
    mix = inst + vocals
    body = inst
    if lead_in > 0:
        body = np.concatenate([np.random.default_rng(9).normal(0, 0.1, int(lead_in * SR)), body])
    elif lead_in < 0:
        body = body[int(-lead_in * SR):]
    if tail > 0:
        body = np.concatenate([body, np.random.default_rng(7).normal(0, 0.2, int(tail * SR))])
    elif tail < 0:
        body = body[: int(tail * SR)]
    _write_wav(tmp_path / "mix.wav", mix)
    _write_wav(tmp_path / "inst.wav", body)

    out = tmp_path / "out.flac"
    result = conform_instrumental(str(tmp_path / "mix.wav"), str(tmp_path / "inst.wav"), str(out))

    assert result.start_trimmed_seconds == pytest.approx(expect_start, abs=0.01)
    assert result.end_trimmed_seconds == pytest.approx(expect_end, abs=0.01)
    assert _probe_duration(str(out)) == pytest.approx(40.0, abs=0.05)
    # The conformed file now lines up with the mix at zero offset.
    lag = find_offset(_decode_mono(str(tmp_path / "mix.wav")), _decode_mono(str(out)), 2 * SAMPLE_RATE)
    assert abs(lag) <= 1


@requires_ffmpeg
def test_conform_rejects_unrelated_instrumental(tmp_path):
    inst, vocals = _song(30, seed=1)
    other, _ = _song(33, seed=2)
    _write_wav(tmp_path / "mix.wav", inst + vocals)
    _write_wav(tmp_path / "inst.wav", other)
    with pytest.raises(InstrumentalConformError, match="doesn't match") as err:
        conform_instrumental(str(tmp_path / "mix.wav"), str(tmp_path / "inst.wav"), str(tmp_path / "o.flac"))
    assert err.value.mismatch


def test_build_filter_trims_lead_in_and_fades_long_outro():
    af = build_filter(lag_seconds=-3.0, original_duration=210.0, mix_duration=200.0)
    assert af.startswith("atrim=start=3.000000,asetpts=PTS-STARTPTS")
    assert "afade=t=out:st=198.000000:d=2.000000" in af
    assert af.endswith("atrim=end=200.000000")


def test_build_filter_delays_late_start_and_pads_short_outro():
    af = build_filter(lag_seconds=1.5, original_duration=190.0, mix_duration=200.0)
    assert af.startswith("adelay=delays=1500.000:all=1")
    assert "apad=whole_dur=200.000000" in af
    assert "afade" not in af


@requires_ffmpeg
def test_conform_rejects_mid_song_edit(tmp_path):
    """A bar removed in the middle: the start lines up but the second half is
    early. A start/end fix would hide that, so it must be rejected."""
    inst, vocals = _song(180)
    mix = inst + vocals
    cut = int(90 * SR)  # between the early (45s) and late (135s) alignment windows
    edited = np.concatenate([inst[:cut], inst[cut + int(1.5 * SR):]])
    _write_wav(tmp_path / "mix.wav", mix)
    _write_wav(tmp_path / "inst.wav", edited)
    with pytest.raises(InstrumentalConformError, match="mid-song") as err:
        conform_instrumental(str(tmp_path / "mix.wav"), str(tmp_path / "inst.wav"), str(tmp_path / "o.flac"))
    assert err.value.mismatch
