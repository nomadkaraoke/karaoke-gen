"""
Approximate vocals for jobs where the user supplied their own instrumental.

Those jobs skip GPU separation, so there is no vocals stem and the lyrics review
"Waveforms" mode had nothing to draw. The waveform only needs a peak envelope,
so a cheap CPU estimate is enough: vocals ≈ mix − gain × instrumental, after
aligning the instrumental to the mix by cross-correlation.

The result is registered as ``stems.vocals_derived`` — used ONLY for the review
waveform (see backend/utils/stems.py). It is never shipped as a deliverable stem
or used for the original-vocals guide, since it can contain residue when the
instrumental isn't from the same master as the mix.
"""
import logging
import subprocess
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# Waveform-only use: mono at 22.05 kHz keeps a 5-minute song ~26 MB as float32.
SAMPLE_RATE = 22050
# Encoder delay / leading silence differences are small; search ±2s.
MAX_LAG_SECONDS = 2.0


@dataclass
class DerivationResult:
    offset_samples: int
    gain: float
    # residual energy / mix energy. With a same-master instrumental this is
    # roughly the vocals' share of the mix; near 1.0 means the subtraction
    # removed little (the waveform is then ≈ the mix).
    residual_ratio: float


def _decode_mono(path: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any audio file to mono float32 at sample_rate via ffmpeg."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sample_rate),
         "-f", "f32le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def _encode_flac(samples: np.ndarray, out_path: str, sample_rate: int = SAMPLE_RATE) -> None:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1.0:
        samples = samples / peak
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", str(sample_rate), "-ac", "1",
         "-i", "-", "-c:a", "flac", out_path],
        input=samples.astype(np.float32).tobytes(), stderr=subprocess.PIPE, check=True,
    )


def find_offset(mix: np.ndarray, inst: np.ndarray, max_lag: int) -> int:
    """Lag (samples) that best aligns inst to mix: mix[n] ≈ inst[n - lag]."""
    n = len(mix) + len(inst) - 1
    size = 1 << (n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(mix, size) * np.conj(np.fft.rfft(inst, size)), size)
    # corr[k] for lag k>=0 at index k; negative lags wrap to the end.
    lags = np.concatenate([np.arange(0, max_lag + 1), np.arange(-max_lag, 0)])
    values = np.concatenate([corr[: max_lag + 1], corr[size - max_lag:]])
    return int(lags[int(np.argmax(values))])


def subtract_instrumental(mix: np.ndarray, inst: np.ndarray, sample_rate: int = SAMPLE_RATE):
    """Return (residual, DerivationResult) with inst aligned + gain-matched to mix."""
    lag = find_offset(mix, inst, int(MAX_LAG_SECONDS * sample_rate))
    aligned = np.zeros_like(mix)
    if lag >= 0:
        src = inst[: max(0, len(mix) - lag)]
        aligned[lag: lag + len(src)] = src
    else:
        src = inst[-lag: -lag + len(mix)]
        aligned[: len(src)] = src
    denom = float(np.dot(aligned, aligned))
    gain = float(np.dot(mix, aligned) / denom) if denom > 0 else 0.0
    residual = mix - gain * aligned
    mix_energy = float(np.dot(mix, mix))
    ratio = float(np.dot(residual, residual) / mix_energy) if mix_energy > 0 else 1.0
    return residual, DerivationResult(offset_samples=lag, gain=gain, residual_ratio=ratio)


def derive_vocals_file(mix_path: str, instrumental_path: str, out_path: str) -> DerivationResult:
    """Write an approximate-vocals FLAC (mono, 22.05 kHz) for waveform display."""
    mix = _decode_mono(mix_path)
    inst = _decode_mono(instrumental_path)
    residual, result = subtract_instrumental(mix, inst)
    _encode_flac(residual, out_path)
    logger.info(
        "Derived vocals: offset=%d samples (%.1f ms) gain=%.3f residual_ratio=%.3f",
        result.offset_samples, 1000 * result.offset_samples / SAMPLE_RATE, result.gain, result.residual_ratio,
    )
    return result
