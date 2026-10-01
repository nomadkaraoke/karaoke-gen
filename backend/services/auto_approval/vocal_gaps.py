"""Vocal gaps: stretches where the lead vocal is singing but the transcription has no words.

Transcription can silently drop whole sung lines (job 5710831e: 11.7s of a Hebrew
pre-chorus, three lines, no words at all). Nothing downstream notices:

- the scorer's anchor coverage is the fraction of *transcribed* words that match the
  reference, so omitted lines don't lower it;
- ``SectionDetector`` labels any >=10s gap between segments "INSTRUMENTAL" without
  looking at the audio, so the video shows "♪ INSTRUMENTAL ♪" over singing;
- a reviewer who doesn't read the language can't see that lines are missing.

For every transcription gap of at least ``MIN_GAP_S`` this measures how much of it the
lead-vocal stem is active (short breaths bridged), and which reference lyrics sit
between the reference positions of the words either side of the gap. Lines in the
reference there are the strongest evidence that lyrics were dropped rather than the gap
being ad-libs or a vocal sample.

Shadow-only for now: results are stored on the job (``state_data.vocal_gaps``) for
calibration before anything gates on them.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend.services.auto_approval.timing_check import _load_mono, _rms_activity

logger = logging.getLogger(__name__)

VOCAL_GAPS_VERSION = "0.1.0"

MIN_GAP_S = 3.0           # shorter gaps are normal phrasing
BREATH_BRIDGE_S = 0.5     # silences this short inside singing don't end a vocal run
# Shadow "suspect" threshold — calibrate on the backfill audit. Keyed on the longest
# continuous (breath-bridged) vocal run, NOT the gap's active fraction: dropped lines
# followed by an instrumental inside one transcription gap dilute the fraction (a 4s
# dropped line in a 20s gap is only 20% active). On job 5710831e genuine
# instrumentals peaked at 0.16s runs vs 11.76s for the dropped lines.
SUSPECT_MIN_RUN_S = 3.0
# A vocal run that starts at the gap's first frame is usually the previous word's held
# note (transcribed end_time under-extended — a known AudioShake failure). Don't count
# its first seconds as "unlyricked"; dropped lines run far longer (11.76s on 5710831e).
# Held notes up to ALLOWANCE + SUSPECT_MIN_RUN_S (4.5s) past the word's end are tolerated.
HELD_NOTE_ALLOWANCE_S = 1.5  # calibrate with the audit; 2.0 hid a single 4s dropped line
MAX_REFERENCE_LINES = 12  # more than this between anchors = misaligned anchors, not a gap


@dataclass
class VocalGap:
    start: float
    end: float
    duration: float
    active_fraction: float     # share of the gap where the lead vocal is active
    longest_run_s: float       # longest vocal run inside the gap, breaths bridged
    reference_lines: Dict[str, List[str]] = field(default_factory=dict)  # source -> lines
    suspect: bool = False


@dataclass
class VocalGapsResult:
    version: str = VOCAL_GAPS_VERSION
    gaps: List[VocalGap] = field(default_factory=list)
    suspect_count: int = 0
    max_suspect_run_s: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---- transcription gaps ---------------------------------------------------------

def _words(segments: List[dict]) -> List[dict]:
    words = [w for seg in segments for w in (seg.get("words") or [])
             if w.get("start_time") is not None and w.get("end_time") is not None]
    return sorted(words, key=lambda w: w["start_time"])


def transcription_gaps(segments: List[dict], audio_duration: float,
                       min_gap_s: float = MIN_GAP_S) -> List[Tuple[float, float, Optional[dict], Optional[dict]]]:
    """(start, end, word_before, word_after) for every gap >= ``min_gap_s``.

    Includes the stretch before the first word and after the last one.
    """
    words = _words(segments)
    gaps = []
    cursor, prev = 0.0, None
    for w in words:
        if w["start_time"] - cursor >= min_gap_s:
            gaps.append((cursor, w["start_time"], prev, w))
        cursor = max(cursor, w["end_time"])
        prev = w
    if audio_duration - cursor >= min_gap_s:
        gaps.append((cursor, audio_duration, prev, None))
    return gaps


# ---- vocal activity -------------------------------------------------------------

def _bridge(active: np.ndarray, max_gap_frames: int) -> np.ndarray:
    """Fill inactive runs of <= ``max_gap_frames`` that sit between active frames."""
    out = active.copy()
    idx = np.flatnonzero(active)
    if len(idx) < 2:
        return out
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= max_gap_frames + 1:
            out[a:b] = True
    return out


def _longest_run(mask: np.ndarray) -> int:
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


# ---- reference lines in a gap ---------------------------------------------------

def _reference_index(correction_data: Dict[str, Any]) -> Dict[str, Dict[str, Tuple[int, int, int]]]:
    """source -> reference word id -> (line index, word index within line, line length)."""
    index: Dict[str, Dict[str, Tuple[int, int, int]]] = {}
    for source, ref in (correction_data.get("reference_lyrics") or {}).items():
        ids: Dict[str, Tuple[int, int, int]] = {}
        for li, seg in enumerate((ref or {}).get("segments") or []):
            seg_words = seg.get("words") or []
            for wi, w in enumerate(seg_words):
                if w.get("id"):
                    ids[w["id"]] = (li, wi, len(seg_words))
        index[source] = ids
    return index


def _transcribed_to_reference(correction_data: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """transcribed word id -> source -> reference word ids (anchors + gap sequences)."""
    mapping: Dict[str, Dict[str, List[str]]] = {}
    for seq in (correction_data.get("anchor_sequences") or []) + (correction_data.get("gap_sequences") or []):
        t_ids = seq.get("transcribed_word_ids") or []
        r_ids = seq.get("reference_word_ids") or {}
        for source, refs in r_ids.items():
            if len(refs) == len(t_ids):  # anchors: 1:1 by position
                for t, r in zip(t_ids, refs):
                    mapping.setdefault(t, {})[source] = [r]
            else:  # gap sequences: not aligned word-for-word
                for t in t_ids:
                    mapping.setdefault(t, {})[source] = list(refs)
    return mapping


def reference_lines_between(correction_data: Dict[str, Any], word_before: Optional[dict],
                            word_after: Optional[dict], _indexes=None) -> Dict[str, List[str]]:
    """Reference lines strictly between the reference positions of the words around a gap.

    Only whole reference lines are returned, and only when both neighbours map to the
    same source in increasing order (repeated choruses can anchor to a different
    occurrence; those cases return nothing rather than a misleading span).
    """
    if not word_before or not word_after:
        return {}
    ref_index, t2r = _indexes or (_reference_index(correction_data), _transcribed_to_reference(correction_data))
    before = t2r.get(word_before.get("id"), {})
    after = t2r.get(word_after.get("id"), {})
    out: Dict[str, List[str]] = {}
    for source in set(before) & set(after):
        ids = ref_index.get(source, {})
        b = [ids[r] for r in before[source] if r in ids]
        a = [ids[r] for r in after[source] if r in ids]
        if not b or not a:
            continue
        b_line, b_word, _ = max(b, key=lambda x: (x[0], x[1]))
        a_line, a_word, a_len = min(a, key=lambda x: (x[0], x[1]))
        # Anchor reference ids can be off by one word at line boundaries (section
        # headers like "[Chorus]" tokenised differently): a gap's preceding word
        # "landing" on the first word of a line really ended the previous line, and
        # a following word landing on the last word of a line starts the next one.
        b_len = len(correction_data["reference_lyrics"][source]["segments"][b_line].get("words") or [])
        if b_word == 0 and b_len > 1:
            b_line -= 1
        if a_word == a_len - 1 and a_len > 1:
            a_line += 1
        # Whole lines only: after the line holding word_before, before the line of word_after
        first, last = b_line + 1, a_line - 1
        if first > last or last - first + 1 > MAX_REFERENCE_LINES:
            continue
        segs = correction_data["reference_lyrics"][source]["segments"]
        lines = [(segs[i].get("text") or "").strip() for i in range(first, last + 1)]
        lines = [l for l in lines if l and not (l.startswith("[") and l.endswith("]"))]  # drop [Chorus] headers
        if lines:
            out[source] = lines
    return out


# ---- entry point ----------------------------------------------------------------

def compute_vocal_gaps(segments: List[dict], lead_vocals_path: str,
                       correction_data: Optional[Dict[str, Any]] = None) -> VocalGapsResult:
    """Never raises: failures return ``VocalGapsResult(error=...)``."""
    try:
        samples, sr = _load_mono(lead_vocals_path)
        active, frame_s = _rms_activity(samples, sr)
        if len(active) == 0 or not active.any():
            # A failed separation must not be recorded as a clean "no gaps" pass
            return VocalGapsResult(error="empty or silent lead-vocal audio")
        bridged = _bridge(active, int(round(BREATH_BRIDGE_S / frame_s)))
        duration = len(active) * frame_s
        allowance = int(round(HELD_NOTE_ALLOWANCE_S / frame_s))

        correction_data = correction_data or {}
        try:
            indexes = (_reference_index(correction_data), _transcribed_to_reference(correction_data))
        except Exception as e:  # noqa: BLE001 — reference evidence is secondary
            logger.warning("vocal gaps: reference index failed: %s", e)
            indexes = None

        result = VocalGapsResult()
        for start, end, before, after in transcription_gaps(segments, duration):
            i0, i1 = int(start / frame_s), min(len(active), int(np.ceil(end / frame_s)))
            if i1 <= i0:
                continue
            frac = float(active[i0:i1].mean())
            window = bridged[i0:i1].copy()
            # Discount a held note carried over from the word before the gap
            if before is not None and window[0]:
                window[:allowance] = False
            run_s = _longest_run(window) * frame_s
            try:
                refs = reference_lines_between(correction_data, before, after, indexes) if indexes else {}
            except Exception as e:  # noqa: BLE001 — never lose the audio result to bad reference data
                logger.warning("vocal gaps: reference lookup failed: %s", e)
                refs = {}
            gap = VocalGap(
                start=round(start, 2), end=round(end, 2), duration=round(end - start, 2),
                active_fraction=round(frac, 3), longest_run_s=round(run_s, 2),
                reference_lines=refs,
            )
            gap.suspect = run_s >= SUSPECT_MIN_RUN_S
            result.gaps.append(gap)

        suspects = [g for g in result.gaps if g.suspect]
        result.suspect_count = len(suspects)
        result.max_suspect_run_s = max((g.longest_run_s for g in suspects), default=0.0)
        return result
    except Exception as e:  # noqa: BLE001 — shadow analysis is best-effort
        logger.warning("vocal gap analysis failed: %s", e, exc_info=True)
        return VocalGapsResult(error=str(e))
