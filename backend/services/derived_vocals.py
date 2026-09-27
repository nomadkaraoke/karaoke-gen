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

Runs inside the lyrics worker (4Gi, shared with transcription), so memory is
bounded: alignment correlates a fixed-length excerpt, not the whole track, and
very long inputs are skipped.
"""
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Waveform-only use: mono at 22.05 kHz keeps a 5-minute song ~26 MB as float32.
SAMPLE_RATE = 22050
# Encoder delay / leading silence differences are small; search ±2s.
MAX_LAG_SECONDS = 2.0
# Alignment excerpt length — plenty to lock onto, and keeps the FFT tiny.
ALIGN_WINDOW_SECONDS = 60.0
# Bound memory on the shared worker: skip absurdly long inputs (mashups, DJ sets).
MAX_DURATION_SECONDS = 20 * 60
# Minimum usable decode.
MIN_DURATION_SECONDS = 1.0
# residual energy / mix energy above this = the instrumental didn't cancel
# (different master, big offset, edited mix). Showing that as "vocals" would
# just be the full-mix envelope and mislead timing edits, so don't register it.
MAX_USEFUL_RESIDUAL_RATIO = 0.85
FFMPEG_TIMEOUT_SECONDS = 120


class DerivedVocalsError(RuntimeError):
    pass


@dataclass
class DerivationResult:
    offset_samples: int
    gain: float
    # residual energy / mix energy. With a same-master instrumental this is
    # roughly the vocals' share of the mix; near 1.0 means the subtraction
    # removed little (the waveform is then ≈ the mix).
    residual_ratio: float

    @property
    def useful(self) -> bool:
        return self.residual_ratio <= MAX_USEFUL_RESIDUAL_RATIO


def _run_ffmpeg(args: list, input_bytes: Optional[bytes] = None) -> bytes:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", *args],
            input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SECONDS, check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode(errors="replace").strip()[-500:]
        raise DerivedVocalsError(f"ffmpeg failed (exit {e.returncode}): {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise DerivedVocalsError(f"ffmpeg timed out after {FFMPEG_TIMEOUT_SECONDS}s") from e
    return proc.stdout


def _decode_mono(path: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any audio file to mono float32 at sample_rate via ffmpeg."""
    raw = _run_ffmpeg(["-i", path, "-t", str(MAX_DURATION_SECONDS + 1), "-ac", "1",
                       "-ar", str(sample_rate), "-f", "f32le", "-"])
    samples = np.frombuffer(raw, dtype=np.float32).copy()
    seconds = len(samples) / sample_rate
    if seconds < MIN_DURATION_SECONDS:
        raise DerivedVocalsError(f"decoded only {seconds:.2f}s of audio from {path}")
    if seconds > MAX_DURATION_SECONDS:
        raise DerivedVocalsError(f"audio longer than {MAX_DURATION_SECONDS // 60} min; skipping")
    return samples


def _encode_flac(samples: np.ndarray, out_path: str, sample_rate: int = SAMPLE_RATE) -> None:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1.0:
        samples = samples / peak
    _run_ffmpeg(["-y", "-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "-i", "-",
                 "-c:a", "flac", out_path], input_bytes=samples.astype(np.float32).tobytes())


def find_offset(mix: np.ndarray, inst: np.ndarray, max_lag: int,
                window: int = int(ALIGN_WINDOW_SECONDS * SAMPLE_RATE)) -> int:
    """Lag (samples) that best aligns inst to mix: mix[n] ≈ inst[n - lag].

    Correlates a `window`-sample excerpt of the mix (from a quarter of the way
    in, past intros) against the matching instrumental span ±max_lag, so memory
    and time are independent of track length.
    """
    window = max(1, min(window, len(mix)))
    start = min(len(mix) // 4, len(mix) - window)
    excerpt = mix[start: start + window].astype(np.float64)

    # Instrumental span covering every candidate lag, zero-padded where it runs
    # off either end: seg[j] = inst[start - max_lag + j].
    seg = np.zeros(window + 2 * max_lag, dtype=np.float64)
    lo = start - max_lag
    src_lo, src_hi = max(0, lo), min(len(inst), lo + len(seg))
    if src_hi > src_lo:
        seg[src_lo - lo: src_hi - lo] = inst[src_lo:src_hi]

    # c[k] = Σ_j excerpt[j] · seg[j + k]  for k in [0, 2·max_lag]  ⇒  lag = max_lag − k
    size = 1 << (len(seg) + window - 1).bit_length()
    corr = np.fft.irfft(np.conj(np.fft.rfft(excerpt, size)) * np.fft.rfft(seg, size), size)
    k = int(np.argmax(corr[: 2 * max_lag + 1]))
    return max_lag - k


def subtract_instrumental(mix: np.ndarray, inst: np.ndarray, sample_rate: int = SAMPLE_RATE):
    """Return (residual, DerivationResult) with inst aligned + gain-matched to mix."""
    lag = find_offset(mix, inst, int(MAX_LAG_SECONDS * sample_rate), int(ALIGN_WINDOW_SECONDS * sample_rate))
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
    """Compute approximate vocals; write a FLAC (mono, 22.05 kHz) only if useful.

    Raises DerivedVocalsError on decode/encode problems. Check ``result.useful``
    before registering the output — nothing is written when it's False.
    """
    mix = _decode_mono(mix_path)
    inst = _decode_mono(instrumental_path)
    residual, result = subtract_instrumental(mix, inst)
    logger.info(
        "Derived vocals: offset=%d samples (%.1f ms) gain=%.3f residual_ratio=%.3f useful=%s",
        result.offset_samples, 1000 * result.offset_samples / SAMPLE_RATE, result.gain,
        result.residual_ratio, result.useful,
    )
    if result.useful:
        _encode_flac(residual, out_path)
    return result
