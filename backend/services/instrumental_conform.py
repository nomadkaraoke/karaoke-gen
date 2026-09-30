"""
Line a user-supplied instrumental up with its mix when their lengths differ.

Artists often export the instrumental with a different edit from the mix: extra
count-in, a longer or shorter outro. Lyrics are timed against the mix, so the
instrumental must start at the same moment and run the same length. Rather than
reject the job, we find the start offset by cross-correlation (the same method
as derived_vocals), then trim/pad the start and trim (with a fade) or pad the
end so it matches the mix exactly. Files that don't correlate at all are almost
certainly the wrong pairing and are still rejected, as are files whose offset
changes between early and late in the song (an edit in the middle).

Runs in the API request (uploads-complete), so it decodes a bounded mono copy
for alignment and does the actual edit with one ffmpeg pass on the original.
"""
import json
import logging
import subprocess
from dataclasses import dataclass, asdict

import numpy as np

from backend.services.derived_vocals import (
    SAMPLE_RATE,
    DerivedVocalsError,
    _decode_mono,
    find_offset,
)

logger = logging.getLogger(__name__)

# Count-ins / intros can add several seconds; search well past that.
MAX_LAG_SECONDS = 15.0
ALIGN_WINDOW_SECONDS = 60.0
# Pearson correlation of mix vs aligned instrumental. Same-master pairs score
# ~0.75-0.85 (the vocals are the only difference); unrelated files score ~0.01.
MIN_CORRELATION = 0.3
# Fade applied when the instrumental's extra outro is cut at the mix's end.
END_FADE_SECONDS = 2.0
# Offsets measured early and late in the song must agree this closely; a bigger
# difference means an edit in the middle (removed bar, longer bridge), which a
# single start/end fix can't correct.
MAX_OFFSET_DRIFT_SECONDS = 0.02
# Where the two alignment excerpts start, as a fraction of the mix length.
ALIGN_POINTS = (0.25, 0.75)
# How close the conformed file must land to the mix length.
LENGTH_TOLERANCE_SECONDS = 0.05
FFMPEG_TIMEOUT_SECONDS = 180


class InstrumentalConformError(RuntimeError):
    """Conforming failed. ``mismatch`` is True when the files themselves don't
    line up (wrong file / mid-song edit) — a reason to reject the upload — and
    False for processing failures (ffmpeg crash/timeout), which are retryable."""

    def __init__(self, message: str, mismatch: bool = False):
        super().__init__(message)
        self.mismatch = mismatch


@dataclass
class ConformResult:
    mix_duration: float
    original_duration: float
    # > 0: instrumental had this much extra audio at the start (trimmed).
    # < 0: instrumental started late by this much (silence added).
    start_trimmed_seconds: float
    # > 0: extra outro cut (with a fade). < 0: silence padded at the end.
    end_trimmed_seconds: float
    correlation: float

    def to_dict(self) -> dict:
        return {k: round(v, 3) + 0.0 for k, v in asdict(self).items()}


def _probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def _aligned_correlation(mix: np.ndarray, inst: np.ndarray, lag: int, window: int,
                         start_fraction: float = 0.25) -> float:
    """Pearson correlation of a mix excerpt vs the instrumental shifted by lag."""
    window = max(1, min(window, len(mix)))
    start = min(int(len(mix) * start_fraction), len(mix) - window)
    excerpt = mix[start: start + window].astype(np.float64)
    shifted = np.zeros(window, dtype=np.float64)
    lo = start - lag
    src_lo, src_hi = max(0, lo), min(len(inst), lo + window)
    if src_hi > src_lo:
        shifted[src_lo - lo: src_hi - lo] = inst[src_lo:src_hi]
    if not excerpt.std() or not shifted.std():
        return 0.0
    return float(np.corrcoef(excerpt, shifted)[0, 1])


def build_filter(lag_seconds: float, original_duration: float, mix_duration: float) -> str:
    """ffmpeg -af chain moving the instrumental by lag and fitting it to mix_duration.

    lag follows find_offset: mix[t] ≈ inst[t - lag], so a negative lag means the
    instrumental has extra audio at the start.
    """
    filters = []
    if lag_seconds < 0:
        filters.append(f"atrim=start={-lag_seconds:.6f},asetpts=PTS-STARTPTS")
    elif lag_seconds > 0:
        filters.append(f"adelay=delays={lag_seconds * 1000:.3f}:all=1")
    shifted_duration = original_duration + lag_seconds
    if shifted_duration > mix_duration + LENGTH_TOLERANCE_SECONDS:
        fade = min(END_FADE_SECONDS, mix_duration / 10)
        filters.append(f"afade=t=out:st={mix_duration - fade:.6f}:d={fade:.6f}")
    else:
        filters.append(f"apad=whole_dur={mix_duration:.6f}")
    filters.append(f"atrim=end={mix_duration:.6f}")
    return ",".join(filters)


def conform_instrumental(mix_path: str, instrumental_path: str, out_path: str) -> ConformResult:
    """Write an instrumental aligned to, and exactly as long as, the mix (FLAC).

    Raises InstrumentalConformError when the files don't line up.
    """
    try:
        mix = _decode_mono(mix_path)
        inst = _decode_mono(instrumental_path)
    except DerivedVocalsError as e:
        raise InstrumentalConformError(str(e)) from e

    # Measure the offset early and late in the song. One constant shift is all
    # this can fix, so the two must agree and both must be a genuine match.
    window = int(ALIGN_WINDOW_SECONDS * SAMPLE_RATE)
    max_lag = int(MAX_LAG_SECONDS * SAMPLE_RATE)
    lags, correlations = [], []
    for fraction in ALIGN_POINTS:
        point_lag = find_offset(mix, inst, max_lag, window, start_fraction=fraction)
        lags.append(point_lag)
        correlations.append(_aligned_correlation(mix, inst, point_lag, window, start_fraction=fraction))
    correlation = min(correlations)
    if correlation < MIN_CORRELATION:
        raise InstrumentalConformError(
            f"instrumental doesn't match the mix (correlation {correlation:.2f})", mismatch=True
        )
    drift = abs(lags[1] - lags[0]) / SAMPLE_RATE
    if drift > MAX_OFFSET_DRIFT_SECONDS:
        raise InstrumentalConformError(
            f"instrumental is edited differently mid-song (offset {lags[0] / SAMPLE_RATE:+.3f}s early "
            f"vs {lags[1] / SAMPLE_RATE:+.3f}s late)", mismatch=True
        )
    lag = lags[0]

    mix_duration = _probe_duration(mix_path)
    original_duration = _probe_duration(instrumental_path)
    lag_seconds = lag / SAMPLE_RATE
    af = build_filter(lag_seconds, original_duration, mix_duration)
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", instrumental_path,
             "-af", af, "-c:a", "flac", out_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SECONDS, check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode(errors="replace").strip()[-500:]
        raise InstrumentalConformError(f"ffmpeg failed: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise InstrumentalConformError("ffmpeg timed out conforming instrumental") from e

    result_duration = _probe_duration(out_path)
    if abs(result_duration - mix_duration) > LENGTH_TOLERANCE_SECONDS:
        raise InstrumentalConformError(
            f"conformed instrumental is {result_duration:.2f}s, expected {mix_duration:.2f}s"
        )

    result = ConformResult(
        mix_duration=mix_duration,
        original_duration=original_duration,
        start_trimmed_seconds=-lag_seconds,
        end_trimmed_seconds=(original_duration + lag_seconds) - mix_duration,
        correlation=correlation,
    )
    logger.info(f"Conformed instrumental: {result.to_dict()} (filter: {af})")
    return result
