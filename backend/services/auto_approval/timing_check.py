"""Timing-plausibility gate signals: word times vs the lead-vocal stem.

WHY: reconstruction-based measurement of real reviews (2026-08-30/31, private
corpus) showed TIMING is the dominant residual human-edit class — present in
14/25 measurable corpus jobs, median human retime 0.70s (72% > 0.3s = clearly
audible), and 100% invisible to every text-based signal the scorer has. One
corpus job (f986dfe5) reaches the AUTO ai-resolved tier while carrying +3-4s
held-note timing errors — the exact leak this gate closes.

WHAT: three signals computed from the POST-AI word stream (the state auto-ship
would publish) + the job's already-separated ``lead_vocals`` stem:

- G1 ``start-silence``: fraction of words whose claimed start sits in vocal
  silence. Catches hand-retimed *section shifts* (corpus 1d45b286: 62 retimes,
  63% of words started in silence).
- G2 ``suspect-mistimed``: count of *structurally suspect* words (inside an
  equal-duration run — the signature of machine-DISTRIBUTED timing — or a
  repeated-phrase run) that the audio contradicts (start in silence, mostly
  silent span, or no spectral-flux onset near the claimed start). Catches
  repetitive-phrase mistiming ("waves and waves and waves", "come on x4").
- G3 ``unclaimed-vocal`` (SHADOW-ONLY): longest contiguous run of vocal energy
  claimed by NO word. Catches held-note under-extension and missing words
  (f986dfe5: 8.2s) — but the 2026-08-31 out-of-sample run found a semantic
  false-positive mode: vocal content deliberately absent from the lyrics
  (ad-libs / DnB vocal samples; e34f1782 shipped zero-touch with a 17.6s
  unclaimed run, and run-adjacency does not separate the two). So G3 is
  recorded in the signals + logged for calibration but never gates.

Thresholds calibrated on the 25-job reconstruction corpus and validated
out-of-sample on ~50 recent prod jobs (docs/automation-corpus/
timing-gate-2026-08-31.md + validate_timing_gate.py, private): G1+G2 catch
every corpus job with >=8 human timing edits and change the outcome of ZERO
currently-auto jobs in either sample. Keep the private validator green after
ANY change here — the exact algorithm is mirrored there as
timing_check_proto.py.

Dependencies: numpy + pydub only (ffmpeg is in every worker image). The onset
detector is a dependency-free spectral-flux stand-in for librosa's, validated
against it on the corpus. Runtime ~2-4s for a typical track (22kHz mono).
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---- analysis parameters -------------------------------------------------
FRAME_S = 0.02  # 20ms RMS frames — fine enough for the 90ms start window
FLOOR_PCTL = 20  # noise floor: max(p20 * 3, peak * 0.02) of linear frame RMS
EQDUR_EPS_S = 0.005  # equal-duration run tolerance
EQDUR_MIN_RUN = 3
START_WIN = (-0.03, 0.06)  # window around a claimed start checked for energy
START_ACTIVE_MIN = 0.3  # fraction of that window that must be vocal-active
WORD_ACTIVE_MIN = 0.5  # word-span active fraction below which it's "dead"
ONSET_SR = 22050
ONSET_NFFT = 1024
ONSET_HOP = 256  # ~11.6ms
ONSET_MAX_DIST_S = 0.15  # start farther than this from any onset = suspect

# ---- gate thresholds (v0 — 25-job corpus calibration 2026-08-31) ---------
G1_PCT_START_INACTIVE = 8.0
G2_N_SUSPECT_BAD = 15
G3_MAX_UNCLAIMED_RUN_S = 4.0  # shadow-only (see module docstring)


@dataclass
class TimingSignals:
    """JSON-serializable timing-plausibility signals for one job."""

    n_words: int = 0
    pct_start_inactive: float = 0.0
    n_suspect: int = 0
    n_suspect_bad: int = 0
    max_unclaimed_run_s: float = 0.0
    unclaimed_fraction: float = 0.0
    n_eqdur_words: int = 0
    n_repetition_words: int = 0
    fired: List[str] = field(default_factory=list)
    # Rules that would fire but are calibration-only (never gate): currently
    # G3 unclaimed-vocal. Recorded so prod shadow data accumulates.
    shadow_fired: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- audio

def _load_mono(path: str) -> Tuple[np.ndarray, int]:
    from pydub import AudioSegment

    audio = AudioSegment.from_file(path)
    if audio.channels > 1:
        audio = audio.set_channels(1)
    if audio.frame_rate != ONSET_SR:
        audio = audio.set_frame_rate(ONSET_SR)
    samples = np.asarray(audio.get_array_of_samples(), dtype=np.float64)
    peak = float(1 << (8 * audio.sample_width - 1))
    return samples / peak, audio.frame_rate


def _rms_activity(samples: np.ndarray, sr: int) -> Tuple[np.ndarray, float]:
    """(vocal-active bool array at FRAME_S resolution, frame seconds)."""
    frame_len = max(1, int(sr * FRAME_S))
    n_frames = len(samples) // frame_len
    if n_frames == 0:
        return np.zeros(0, dtype=bool), FRAME_S
    trimmed = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt((trimmed**2).mean(axis=1))
    # Vocal stems are near-silent between phrases; the floor sits well above
    # residual separation bleed but scales with the track's own level.
    floor = max(np.percentile(rms, FLOOR_PCTL) * 3.0, rms.max() * 0.02)
    return rms > floor, FRAME_S


def _detect_onsets(samples: np.ndarray, sr: int) -> np.ndarray:
    """Vocal onset times (s) via numpy spectral flux + adaptive peak-picking.

    Log-magnitude STFT positive flux; peaks must be local maxima exceeding a
    moving average by a fixed delta and sit >=35ms apart. A dependency-free
    stand-in for librosa.onset.onset_detect, validated against it on the
    private corpus (both catch the same gate outcomes).
    """
    n = (len(samples) - ONSET_NFFT) // ONSET_HOP
    if n < 3:
        return np.zeros(0)
    # Batched STFT flux: a full-track frame matrix would be O(minutes * 60MB)
    # and can OOM a worker on long inputs. Each batch re-computes one overlap
    # frame so the flux diff is continuous across batch boundaries.
    window = np.hanning(ONSET_NFFT)
    batch = 4096
    flux_parts = []
    prev_last_logmag = None
    for b0 in range(0, n, batch):
        b1 = min(n, b0 + batch)
        idx = (
            np.arange(b0, b1)[:, None] * ONSET_HOP
            + np.arange(ONSET_NFFT)[None, :]
        )
        logmag = np.log1p(1000.0 * np.abs(np.fft.rfft(samples[idx] * window, axis=1)))
        if prev_last_logmag is not None:
            logmag = np.vstack([prev_last_logmag, logmag])
        flux_parts.append(np.maximum(0.0, np.diff(logmag, axis=0)).sum(axis=1))
        prev_last_logmag = logmag[-1]
    flux = np.concatenate(flux_parts)
    if not flux.size or flux.max() <= 0:
        return np.zeros(0)
    flux = flux / flux.max()
    win = 21  # moving average over ~±0.12s
    avg = np.convolve(flux, np.ones(win) / win, mode="same")
    delta = 0.07
    peaks = []
    last = -10
    for i in range(1, len(flux) - 1):
        if (
            flux[i] >= flux[i - 1]
            and flux[i] >= flux[i + 1]
            and flux[i] > avg[i] + delta
            and i - last >= 3
        ):
            peaks.append(i)
            last = i
    return (np.asarray(peaks, dtype=float) * ONSET_HOP + ONSET_NFFT // 2) / sr


# ---------------------------------------------------------------- words

_NORM_RE = re.compile(r"[^a-z']+")


def _norm(text: str) -> str:
    return _NORM_RE.sub("", (text or "").lower())


def _flatten_words(segments: List[dict]) -> List[dict]:
    out = []
    for si, seg in enumerate(segments):
        for w in seg.get("words") or []:
            out.append(
                {
                    "seg": si,
                    "text": (w.get("text") or "").strip(),
                    "start": float(w.get("start_time") or 0.0),
                    "end": float(w.get("end_time") or 0.0),
                }
            )
    return out


def _repetition_run_flags(tokens: List[str]) -> List[bool]:
    """Words inside a consecutive repetition of a 1-4 token phrase
    (>=3 reps for single tokens, >=2 for multi-token phrases)."""
    n = len(tokens)
    flags = [False] * n
    i = 0
    while i < n:
        best_span = 0
        for plen in range(1, 5):
            if i + 2 * plen > n or not all(tokens[i + j] for j in range(plen)):
                continue
            reps = 1
            while (
                i + (reps + 1) * plen <= n
                and tokens[i + reps * plen : i + (reps + 1) * plen]
                == tokens[i : i + plen]
            ):
                reps += 1
            if reps >= (3 if plen == 1 else 2):
                best_span = max(best_span, reps * plen)
        if best_span:
            for k in range(i, i + best_span):
                flags[k] = True
            i += best_span
        else:
            i += 1
    return flags


def _eqdur_run_flags(words: List[dict]) -> List[bool]:
    """Words inside >=3 consecutive same-segment words with identical
    durations (±EQDUR_EPS_S) — the machine-distributed-timing signature
    (humans retime exactly these blocks; corpus 1d45b286 = 49/62)."""
    n = len(words)
    flags = [False] * n
    durs = [max(0.0, w["end"] - w["start"]) for w in words]
    i = 0
    while i < n:
        j = i
        while (
            j + 1 < n
            and words[j + 1]["seg"] == words[i]["seg"]
            and abs(durs[j + 1] - durs[i]) < EQDUR_EPS_S
        ):
            j += 1
        if j - i + 1 >= EQDUR_MIN_RUN and durs[i] > 0.01:
            for k in range(i, j + 1):
                flags[k] = True
        i = j + 1
    return flags


# ---------------------------------------------------------------- signals

def _frac_active(active: np.ndarray, frame_s: float, t0: float, t1: float) -> float:
    i0 = max(0, int(math.floor(t0 / frame_s)))
    i1 = min(len(active), int(math.ceil(t1 / frame_s)))
    if i1 <= i0:
        return 0.0
    return float(active[i0:i1].mean())


def compute_timing_signals(
    segments: List[dict], lead_vocals_path: str
) -> TimingSignals:
    """Compute gate signals for the given (post-AI) segments + lead stem.

    Never raises: failures return ``TimingSignals(error=...)`` so callers
    record an explicit "analysis unavailable" marker (the fail-open/-closed
    choice belongs to the caller, not here).
    """
    try:
        words = _flatten_words(segments)
        if not words:
            raise ValueError("no words")
        samples, sr = _load_mono(lead_vocals_path)
        active, frame_s = _rms_activity(samples, sr)
        if not len(active) or not active.any():
            # A silent/failed separation output would mark every word start
            # inactive and falsely fire — treat as analysis-unavailable.
            raise ValueError("empty or silent audio")
        onsets = _detect_onsets(samples, sr)

        rep = _repetition_run_flags([_norm(w["text"]) for w in words])
        eqd = _eqdur_run_flags(words)

        n = len(words)
        start_inactive = 0
        n_suspect = n_suspect_bad = 0
        claimed = np.zeros_like(active)
        for i, w in enumerate(words):
            s, e = w["start"], w["end"]
            start_ok = (
                _frac_active(active, frame_s, s + START_WIN[0], s + START_WIN[1])
                >= START_ACTIVE_MIN
            )
            if not start_ok:
                start_inactive += 1
            if rep[i] or eqd[i]:
                n_suspect += 1
                dead = _frac_active(active, frame_s, s, e) < WORD_ACTIVE_MIN
                far = (
                    float(np.min(np.abs(onsets - s))) > ONSET_MAX_DIST_S
                    if len(onsets)
                    else False
                )
                if (not start_ok) or dead or far:
                    n_suspect_bad += 1
            i0 = max(0, int(math.floor(s / frame_s)))
            i1 = min(len(claimed), int(math.ceil(e / frame_s)))
            claimed[i0:i1] = True

        orphan = active & ~claimed
        n_active = int(active.sum())
        best = run = 0
        for a in orphan:
            run = run + 1 if a else 0
            best = max(best, run)

        sig = TimingSignals(
            n_words=n,
            pct_start_inactive=round(100.0 * start_inactive / n, 1),
            n_suspect=n_suspect,
            n_suspect_bad=n_suspect_bad,
            max_unclaimed_run_s=round(best * frame_s, 2),
            unclaimed_fraction=round(
                float(orphan.sum()) / n_active if n_active else 0.0, 4
            ),
            n_eqdur_words=sum(eqd),
            n_repetition_words=sum(rep),
        )
        sig.fired = gate_fired(sig)
        sig.shadow_fired = shadow_fired(sig)
        return sig
    except Exception as e:  # noqa: BLE001 — analysis is best-effort by design
        logger.warning("timing check failed: %s", e, exc_info=True)
        return TimingSignals(error=str(e))


def gate_fired(sig: TimingSignals) -> List[str]:
    """Which GATING rules the signals trip (empty = timing looks plausible)."""
    fired = []
    if sig.pct_start_inactive >= G1_PCT_START_INACTIVE:
        fired.append("start-silence")
    if sig.n_suspect_bad >= G2_N_SUSPECT_BAD:
        fired.append("suspect-mistimed")
    return fired


def shadow_fired(sig: TimingSignals) -> List[str]:
    """Calibration-only rules (recorded + logged, never gate)."""
    shadow = []
    if sig.max_unclaimed_run_s >= G3_MAX_UNCLAIMED_RUN_S:
        shadow.append("unclaimed-vocal")
    return shadow
