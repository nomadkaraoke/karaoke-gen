"""3-stem comparison signals for the backing-vocals decider.

Compares the backing_vocals stem against the lead_vocals stem and the full
vocals stem (stage-1 "vocals_clean") to distinguish genuine backing harmonies
from the "pink but don't keep" failure modes identified in the 2026-08
replay-review corpus (docs/automation-corpus/backing-vocals-decision-logic.md,
private):

- **Backing stem IS the lead** (catastrophic if kept): a quiet/reverby lead is
  misclassified as "backing", so the backing stem tracks the entire vocal line.
  Tells: backing audible-coverage ≈ full-vocals coverage, high envelope
  correlation with the vocals stem, and a lead stem that is quiet/sparse
  relative to the backing stem.
- **Lead-vocal bleed**: a few backing blobs coincide with lead activity.
  Tell: backing-audible windows mostly overlap lead-audible windows.
- **Flat-noise pink** (lo-fi/fuzzy mixes): the noise floor sits above the
  silence threshold, producing sustained low-dynamics "pink" that is noise,
  not voice. Tell: low within-run amplitude variance.

Pure Python + pydub (same dependency set as ``analyzer.py``); works on local
file paths so it runs identically in the cloud workers and the local CLI.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from pydub import AudioSegment

logger = logging.getLogger(__name__)

SILENT_DB = -100.0


@dataclass
class StemComparison:
    """Signals from comparing backing / lead / full-vocals stems.

    All fractions are 0..1. ``None`` medians mean the stem had no audible
    windows. JSON-serializable via ``to_dict`` for ``state_data`` storage.
    """

    window_ms: int = 100
    silence_threshold_db: float = -40.0
    duration_seconds: float = 0.0
    # Audible coverage (fraction of windows above the silence threshold).
    backing_audible_fraction: float = 0.0
    lead_audible_fraction: float = 0.0
    vocals_audible_fraction: float = 0.0
    # backing coverage relative to the full vocal line (capped at 1.0).
    coverage_ratio: float = 0.0
    # Pearson correlation of linear RMS envelopes.
    corr_backing_vocals: float = 0.0
    corr_backing_lead: float = 0.0
    # Median window level (dBFS) over each stem's own audible windows.
    backing_median_db: Optional[float] = None
    lead_median_db: Optional[float] = None
    vocals_median_db: Optional[float] = None
    # Fraction of backing-audible windows where the lead stem is also audible.
    lead_overlap_fraction: float = 0.0
    # Amplitude dynamics of the backing stem's audible content.
    backing_db_std: float = 0.0
    # Fraction of backing-audible time inside "flat" runs (sustained, low
    # within-run variance — the noise signature).
    flat_fraction: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _rms_db_envelope(path: str, window_ms: int) -> List[float]:
    """RMS level (dBFS) per window for a mono-mixed audio file."""
    audio = AudioSegment.from_file(path)
    if audio.channels > 1:
        audio = audio.set_channels(1)
    envelope: List[float] = []
    duration_ms = len(audio)
    for start_ms in range(0, duration_ms, window_ms):
        window = audio[start_ms : start_ms + window_ms]
        if window.rms > 0:
            envelope.append(
                20 * math.log10(window.rms / window.max_possible_amplitude)
            )
        else:
            envelope.append(SILENT_DB)
    return envelope


def _db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0) if db > SILENT_DB else 0.0


def _pearson(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a, b = a[:n], b[:n]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0.0 or var_b <= 0.0:
        return 0.0
    return cov / math.sqrt(var_a * var_b)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _flat_fraction(
    envelope_db: List[float],
    threshold_db: float,
    *,
    window_ms: int,
    min_run_seconds: float = 3.0,
    flat_std_db: float = 2.5,
) -> float:
    """Fraction of audible windows sitting in long, low-variance ("flat") runs.

    Noise pushed above the silence threshold by a fuzzy mix is sustained and
    even; real voice has onsets/peaks. A run is a maximal stretch of
    consecutive audible windows; long runs whose within-run dB std is small
    count as flat.
    """
    min_run_windows = max(2, int(min_run_seconds * 1000 / window_ms))
    audible_total = 0
    flat_total = 0
    run: List[float] = []

    def close_run() -> None:
        nonlocal flat_total
        if len(run) >= min_run_windows and _std(run) < flat_std_db:
            flat_total += len(run)

    for db in envelope_db:
        if db > threshold_db:
            audible_total += 1
            run.append(db)
        else:
            close_run()
            run = []
    close_run()
    return flat_total / audible_total if audible_total else 0.0


def compare_stems(
    backing_path: str,
    lead_path: str,
    vocals_path: str,
    *,
    silence_threshold_db: float = -40.0,
    window_ms: int = 100,
) -> StemComparison:
    """Compute the 3-stem comparison signals from local audio files.

    Never raises: any failure returns a StemComparison with ``error`` set so
    the caller stores an explicit "comparison unavailable" marker (the scorer
    treats that as no-comparison, never as evidence).
    """
    try:
        for p in (backing_path, lead_path, vocals_path):
            if not Path(p).exists():
                raise FileNotFoundError(p)

        backing = _rms_db_envelope(backing_path, window_ms)
        lead = _rms_db_envelope(lead_path, window_ms)
        vocals = _rms_db_envelope(vocals_path, window_ms)
        n = min(len(backing), len(lead), len(vocals))
        backing, lead, vocals = backing[:n], lead[:n], vocals[:n]
        if n == 0:
            raise ValueError("empty audio")

        thr = silence_threshold_db
        backing_audible = [db for db in backing if db > thr]
        lead_audible = [db for db in lead if db > thr]
        vocals_audible = [db for db in vocals if db > thr]

        backing_fraction = len(backing_audible) / n
        vocals_fraction = len(vocals_audible) / n
        overlap_windows = sum(
            1 for b, l in zip(backing, lead) if b > thr and l > thr
        )

        return StemComparison(
            window_ms=window_ms,
            silence_threshold_db=thr,
            duration_seconds=round(n * window_ms / 1000.0, 2),
            backing_audible_fraction=round(backing_fraction, 4),
            lead_audible_fraction=round(len(lead_audible) / n, 4),
            vocals_audible_fraction=round(vocals_fraction, 4),
            coverage_ratio=round(
                min(1.0, backing_fraction / vocals_fraction)
                if vocals_fraction > 0
                else 0.0,
                4,
            ),
            corr_backing_vocals=round(
                _pearson(
                    [_db_to_linear(db) for db in backing],
                    [_db_to_linear(db) for db in vocals],
                ),
                4,
            ),
            corr_backing_lead=round(
                _pearson(
                    [_db_to_linear(db) for db in backing],
                    [_db_to_linear(db) for db in lead],
                ),
                4,
            ),
            backing_median_db=_round_opt(_median(backing_audible)),
            lead_median_db=_round_opt(_median(lead_audible)),
            vocals_median_db=_round_opt(_median(vocals_audible)),
            lead_overlap_fraction=round(
                overlap_windows / len(backing_audible), 4
            )
            if backing_audible
            else 0.0,
            backing_db_std=round(_std(backing_audible), 3),
            flat_fraction=round(
                _flat_fraction(backing, thr, window_ms=window_ms), 4
            ),
        )
    except Exception as e:  # noqa: BLE001 — comparison is best-effort by design
        logger.warning("stem comparison failed: %s", e, exc_info=True)
        return StemComparison(
            window_ms=window_ms,
            silence_threshold_db=silence_threshold_db,
            error=str(e),
        )


def _round_opt(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)
