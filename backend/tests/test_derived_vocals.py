"""Approximate vocals (mix − aligned user instrumental) for the review waveform."""
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.services.derived_vocals import (
    SAMPLE_RATE,
    DerivedVocalsError,
    derive_vocals_file,
    find_offset,
    subtract_instrumental,
)
from backend.utils.stems import vocals_stem_path


def _signals(lag, gain=0.8, seconds=6):
    rng = np.random.default_rng(1)
    n = SAMPLE_RATE * seconds
    inst = rng.normal(0, 0.2, n).astype(np.float32)
    t = np.arange(n) / SAMPLE_RATE
    # "vocals": 440 Hz bursts every other second
    vocals = (0.3 * np.sin(2 * np.pi * 440 * t) * ((t.astype(int) % 2) == 0)).astype(np.float32)
    shifted = np.zeros(n, dtype=np.float32)
    if lag >= 0:
        shifted[lag:] = inst[: n - lag]
    else:
        shifted[: n + lag] = inst[-lag:]
    return vocals + gain * shifted, inst, vocals


@pytest.mark.parametrize("lag", [0, 300, -1200, 20000, -44000])
def test_find_offset_recovers_known_lag(lag):
    mix, inst, _ = _signals(lag)
    assert find_offset(mix, inst, int(2 * SAMPLE_RATE)) == lag


@pytest.mark.parametrize("lag", [-100, 100])
def test_find_offset_on_very_short_input_is_not_ambiguous(lag):
    """Regression: with inputs shorter than the lag window, positive/negative lag
    slices used to overlap and a negative lag came back as a large positive one."""
    mix, inst, _ = _signals(lag, seconds=1)
    assert find_offset(mix, inst, int(2 * SAMPLE_RATE)) == lag


def test_find_offset_uses_bounded_window_on_long_tracks():
    """Alignment correlates a fixed excerpt, so a long track costs the same
    memory/time as a short one (a whole-track FFT could OOM the lyrics worker)."""
    mix, inst, _ = _signals(lag=777, seconds=240)
    with patch("backend.services.derived_vocals.np.fft.rfft", wraps=np.fft.rfft) as rfft:
        assert find_offset(mix, inst, int(2 * SAMPLE_RATE), window=int(10 * SAMPLE_RATE)) == 777
    assert max(call.args[1] for call in rfft.call_args_list) <= 1 << 20


def test_subtraction_recovers_vocals_and_gain():
    mix, inst, vocals = _signals(lag=300, gain=0.8)
    residual, result = subtract_instrumental(mix, inst)
    assert result.offset_samples == 300
    assert result.gain == pytest.approx(0.8, abs=0.02)
    assert result.residual_ratio == pytest.approx(float(np.dot(vocals, vocals) / np.dot(mix, mix)), abs=0.02)
    assert np.corrcoef(residual, vocals)[0, 1] > 0.98


def test_unrelated_instrumental_leaves_the_mix_mostly_intact():
    mix, _, _ = _signals(lag=0)
    other = np.random.default_rng(7).normal(0, 0.2, len(mix)).astype(np.float32)
    residual, result = subtract_instrumental(mix, other)
    assert result.residual_ratio > 0.9
    assert np.corrcoef(residual, mix)[0, 1] > 0.95


def _write_wav(path, samples, sr=44100):
    # Write at 44.1 kHz stereo like a real upload; the module resamples to mono 22.05 kHz.
    up = np.repeat(samples, 2).astype(np.float32)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                    "-ac", "2", "-c:a", "pcm_s24le", path], input=up.tobytes(), check=True)


def test_derive_vocals_file_end_to_end(tmp_path):
    mix, inst, vocals = _signals(lag=150)
    mix_path, inst_path, out_path = (str(tmp_path / n) for n in ("mix.wav", "inst.wav", "out.flac"))
    _write_wav(mix_path, mix)
    _write_wav(inst_path, inst)

    result = derive_vocals_file(mix_path, inst_path, out_path)

    assert result.useful
    assert os.path.getsize(out_path) > 0
    assert abs(result.offset_samples - 150) <= 1
    assert result.gain == pytest.approx(0.8, abs=0.02)
    # ≈ the vocals' share of the mix energy (~0.47 here) — i.e. the instrumental cancelled
    ideal = float(np.dot(vocals, vocals) / np.dot(mix, mix))
    assert result.residual_ratio == pytest.approx(ideal, abs=0.1)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,channels,sample_rate",
                            "-of", "csv=p=0", out_path], capture_output=True, text=True, check=True).stdout.strip()
    assert probe == f"flac,{SAMPLE_RATE},1"


def test_vocals_stem_path_uses_derived_only_as_last_resort():
    derived = SimpleNamespace(file_urls={"stems": {"vocals_derived": "d.flac"}})
    both = SimpleNamespace(file_urls={"stems": {"vocals_derived": "d.flac", "vocals_clean": "v.flac"}})
    assert vocals_stem_path(derived) == "d.flac"
    assert vocals_stem_path(both) == "v.flac"


class TestStoreDerivedVocals:
    def _call(self, tmp_path, derive):
        from backend.workers.lyrics_worker import _store_derived_vocals

        storage, job_manager, job_log = MagicMock(), MagicMock(), MagicMock()
        with patch("backend.services.derived_vocals.derive_vocals_file", derive):
            _store_derived_vocals("j1", "uploads/j1/audio/existing_instrumental.wav", "/tmp/mix.wav",
                                  storage, job_manager, job_log)
        return storage, job_manager, job_log

    def test_uploads_and_registers_waveform_stem(self, tmp_path):
        derive = MagicMock(return_value=SimpleNamespace(offset_samples=0, gain=1.0, residual_ratio=0.1, useful=True))
        storage, job_manager, _ = self._call(tmp_path, derive)

        assert storage.download_file.call_args[0][0] == "uploads/j1/audio/existing_instrumental.wav"
        local_out, remote = storage.upload_file.call_args[0]
        assert local_out.endswith("vocals_derived.flac") and remote == "jobs/j1/stems/vocals_derived.flac"
        # own temp dir, cleaned up afterwards
        assert not os.path.exists(os.path.dirname(local_out))
        job_manager.update_file_url.assert_called_once_with("j1", "stems", "vocals_derived", "jobs/j1/stems/vocals_derived.flac")

    def test_useless_subtraction_is_not_registered(self, tmp_path):
        derive = MagicMock(return_value=SimpleNamespace(offset_samples=0, gain=0.0, residual_ratio=0.99, useful=False))
        storage, job_manager, job_log = self._call(tmp_path, derive)

        storage.upload_file.assert_not_called()
        job_manager.update_file_url.assert_not_called()
        assert "no vocal waveform" in job_log.info.call_args[0][0]

    def test_failure_is_non_fatal(self, tmp_path):
        derive = MagicMock(side_effect=RuntimeError("ffmpeg exploded"))
        _, job_manager, job_log = self._call(tmp_path, derive)

        job_manager.update_file_url.assert_not_called()
        assert "non-fatal" in job_log.warning.call_args[0][0]


def test_unrelated_instrumental_is_not_written(tmp_path):
    """A subtraction that removed nothing would just show the full-mix envelope."""
    mix, _, _ = _signals(lag=0)
    other = np.random.default_rng(9).normal(0, 0.2, len(mix)).astype(np.float32)
    mix_path, inst_path, out_path = (str(tmp_path / n) for n in ("mix.wav", "inst.wav", "out.flac"))
    _write_wav(mix_path, mix)
    _write_wav(inst_path, other)

    result = derive_vocals_file(mix_path, inst_path, out_path)

    assert not result.useful
    assert not os.path.exists(out_path)


def test_empty_or_undecodable_input_raises_with_ffmpeg_detail(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(DerivedVocalsError, match="ffmpeg failed"):
        derive_vocals_file(str(bad), str(bad), str(tmp_path / "o.flac"))

    silent = str(tmp_path / "tiny.wav")
    _write_wav(silent, np.zeros(1000, dtype=np.float32))  # ~0.05s
    with pytest.raises(DerivedVocalsError, match="decoded only"):
        derive_vocals_file(silent, silent, str(tmp_path / "o.flac"))
