"""Auto-approvability scorer.

Pure functions that decide, from signals that already exist on every job, whether
a job's lyrics look safe to auto-approve without human review, and whether the
backing-vocals decision is non-subjective.

This module runs **in shadow mode**: ``screens_worker`` computes + records the
verdict in ``processing_metadata.auto_approval_shadow`` just before every job goes
to human review, so verdicts can be validated against real review outcomes before
the scorer is allowed to gate. See
``docs/archive/2026-08-25-full-auto-review-design.md`` (+ session-4 update).

Design notes / traps handled here:
- In production, auto-correction is DISABLED, so ``corrections.json`` always reports
  ``corrections_made == 0`` and ``confidence == 1.0`` regardless of lyric quality
  (``controller.py`` uncorrected path). We therefore NEVER treat those two fields as
  positive evidence. Confidence comes from anchor coverage against reference lyrics,
  gap words left *uncovered* by the auto-applied AI suggestions, reference-source
  agreement, and the absence of the never-auto gating signatures.
- ``anchor_sequences`` and ``gap_sequences`` are still computed when correction is
  disabled, so anchor/gap coverage is available on every prod job.
- The review UI auto-applies the (pre-generated) AI suggestions on load, so a gap
  word covered by a suggestion is fixed before any human would look — raw gap
  counts alone do NOT separate AI-solved jobs from human-needed ones (2026-08-27
  20-job corpus calibration).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.services.auto_approval.models import (
    AutoApprovabilityVerdict,
    BackingResult,
    BackingSignals,
    BackingVerdict,
    LyricsResult,
    LyricsSignals,
    LyricsVerdict,
)

SCORER_VERSION = "0.2.0"

# --- Lyrics thresholds (deliberately conservative; the safe intersection first) ---
# The AUTO tier requires a synced reference the transcription matches with ZERO
# unresolved gaps. Empirically (job 79c4f60c "Clarity", 2026-08), a single gap word
# on an otherwise 99.6%-anchored track was a genuine mis-transcription the reviewer
# fixed ("I" -> "High"): a gap word is by definition a word with no confident reference
# match, i.e. exactly where errors hide. So we do not tolerate any gaps for AUTO.
MIN_TOTAL_WORDS = 10
AUTO_MIN_ANCHOR_FRACTION = 0.99
AUTO_MAX_GAP_FRACTION = 0.0
# The "ai-resolved" AUTO tier accounts for the auto-applied AI corrections: raw gap
# words that an AI suggestion covers get fixed on load before any human would look,
# so confidence keys off the UNCOVERED remainder (corpus 2026-08-27: raw gap counts
# do NOT separate AI-solved jobs from human-needed ones; uncovered gaps + the
# never-auto gates + an anchor floor do).
AI_RESOLVED_MIN_ANCHOR_FRACTION = 0.90
AI_RESOLVED_MAX_UNCOVERED_FRACTION = 0.02
AI_RESOLVED_MAX_UNCOVERED_WORDS = 6
# A looser "near-miss" band, recorded for calibration but still sent to review.
NEARMISS_MIN_ANCHOR_FRACTION = 0.95
NEARMISS_MAX_GAP_FRACTION = 0.05

# --- Gating class: non-lexical vocalization sections (Pattern 3; never fully auto) ---
# AudioShake mistimes da-da-dun / oh-oh / woo-woo sections badly (wrong count, rhythm,
# segmentation); the correct grouping is a musical interpretation not recoverable from
# its output (corpus jobs b5a7b8aa, a4dcaa21, 8f2305ee). Detect and route to a human.
VOCALIZATION_TOKENS = frozenset({
    "da", "dun", "dum", "la", "na", "nah", "oh", "ooh", "oohs", "ah", "ahh", "aah",
    "woo", "whoa", "woah", "yeah", "hey", "ba", "bah", "doo", "dee", "di", "mm",
    "mmm", "hmm", "sha", "bop", "pa", "ra", "ta",
})
# A run of this many consecutive vocalization words = a vocalization section.
VOCALIZATION_MIN_RUN = 5
# A single sung vocalization rarely lasts this long; a multi-second "Ooh"/"da" almost
# always means AudioShake lumped many short vocalizations into one (10.1s/4.0s examples).
LONG_VOCALIZATION_DURATION_S = 3.5
VERY_LONG_VOCALIZATION_DURATION_S = 6.0

# --- Gating class: phantom/hallucinated lines (Pattern 8) ---
# Real sung words basically never span this long; corpus phantoms were 7.4s and 4.8s.
ABSURD_WORD_DURATION_S = 5.0
# Short parenthetical lines ("(I'm sorry)") stretched over multiple seconds are the
# classic phantom signature.
PHANTOM_PARENTHETICAL_DURATION_S = 4.0
PHANTOM_PARENTHETICAL_MAX_WORDS = 4

# --- Backing thresholds ---
# Only "there is literally no audible backing content" is treated as non-subjective.
# A tiny non-zero floor absorbs separation noise but stays extremely conservative.
NON_SUBJECTIVE_MAX_AUDIBLE_PCT = 0.5  # percent
LOUD_SEGMENT_DB = -20.0

_TOKEN_STRIP_RE = re.compile(r"^[^\w]+|[^\w]+$")


def _count_ids(sequences: List[Dict[str, Any]]) -> int:
    total = 0
    for seq in sequences or []:
        ids = seq.get("transcribed_word_ids")
        if ids:
            total += len(ids)
    return total


def _is_vocalization_token(text: str) -> bool:
    """True if a word looks like a non-lexical vocalization ("Da-", "dun,", "Woo-woo")."""
    t = _TOKEN_STRIP_RE.sub("", (text or "").lower())
    if not t:
        return False
    parts = [p for p in t.split("-") if p]
    return bool(parts) and all(p in VOCALIZATION_TOKENS for p in parts)


def _word_duration(word: Dict[str, Any]) -> float:
    start = word.get("start_time")
    end = word.get("end_time")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
        return float(end) - float(start)
    return 0.0


def _extract_gating_signals(
    correction_data: Dict[str, Any],
) -> Tuple[int, int, int, bool, float, int, int, bool]:
    """Scan corrected_segments for the two never-auto gating classes.

    Returns (vocalization_word_count, vocalization_max_run, long_vocalization_word_count,
    has_vocalization_section, max_word_duration_s, absurd_duration_word_count,
    suspicious_parenthetical_count, has_phantom_signature).
    """
    voc_count = 0
    max_run = 0
    run = 0  # runs continue across segment boundaries (consecutive vocalization lines)
    long_voc = 0
    very_long_voc = 0
    max_duration = 0.0
    absurd = 0
    suspicious_paren = 0

    for seg in correction_data.get("corrected_segments") or []:
        words = seg.get("words") or []
        for word in words:
            duration = _word_duration(word)
            max_duration = max(max_duration, duration)
            if duration > ABSURD_WORD_DURATION_S:
                absurd += 1
            if _is_vocalization_token(word.get("text") or ""):
                voc_count += 1
                run += 1
                max_run = max(max_run, run)
                if duration >= VERY_LONG_VOCALIZATION_DURATION_S:
                    very_long_voc += 1
                    long_voc += 1
                elif duration >= LONG_VOCALIZATION_DURATION_S:
                    long_voc += 1
            else:
                run = 0

        # Phantom signature: a short parenthetical line stretched over several seconds
        # (e.g. three "(I'm sorry)" lines, one 7.4s — corpus job 33453fa0).
        seg_text = (seg.get("text") or "").strip()
        if (
            seg_text.startswith("(")
            and seg_text.endswith(")")
            and 0 < len(words) <= PHANTOM_PARENTHETICAL_MAX_WORDS
        ):
            seg_start, seg_end = seg.get("start_time"), seg.get("end_time")
            if (
                isinstance(seg_start, (int, float))
                and isinstance(seg_end, (int, float))
                and seg_end - seg_start >= PHANTOM_PARENTHETICAL_DURATION_S
            ):
                suspicious_paren += 1

    has_vocalization_section = (
        max_run >= VOCALIZATION_MIN_RUN or long_voc >= 2 or very_long_voc >= 1
    )
    has_phantom_signature = absurd >= 1 or suspicious_paren >= 1
    return (
        voc_count,
        max_run,
        long_voc,
        has_vocalization_section,
        round(max_duration, 3),
        absurd,
        suspicious_paren,
        has_phantom_signature,
    )


def _total_words(correction_data: Dict[str, Any]) -> int:
    meta = correction_data.get("metadata") or {}
    if isinstance(meta.get("total_words"), int) and meta["total_words"] > 0:
        return meta["total_words"]
    # Fallback: count from corrected_segments
    total = 0
    for seg in correction_data.get("corrected_segments") or []:
        total += len(seg.get("words") or [])
    return total


def _gap_word_ids(correction_data: Dict[str, Any]) -> set:
    ids: set = set()
    for seq in correction_data.get("gap_sequences") or []:
        ids.update(seq.get("transcribed_word_ids") or [])
    return ids


def extract_lyrics_signals(
    correction_data: Dict[str, Any],
    ai_suggestions: Optional[List[Dict[str, Any]]] = None,
) -> LyricsSignals:
    """Pull the quantitative lyrics signals out of a ``corrections.json`` dict.

    ``ai_suggestions`` is the (pre-generated) auto-correct suggestion list — the
    ones the review UI auto-applies on load. When provided, gap coverage is
    computed against it: a gap word touched by a suggestion will be fixed before
    any human looks, so only the *uncovered* gaps carry residual risk.
    """
    meta = correction_data.get("metadata") or {}
    total_words = _total_words(correction_data)

    anchor_words = _count_ids(correction_data.get("anchor_sequences"))
    gap_words = _count_ids(correction_data.get("gap_sequences"))

    suggestion_word_ids: set = set()
    full_consensus = 0
    for sug in ai_suggestions or []:
        suggestion_word_ids.update(sug.get("word_ids") or [])
        total_models = sug.get("total_models")
        if (
            isinstance(total_models, int)
            and total_models >= 2
            and sug.get("consensus") == total_models
        ):
            full_consensus += 1
    gap_ids = _gap_word_ids(correction_data)
    covered = len(gap_ids & suggestion_word_ids)
    uncovered = max(0, gap_words - covered)
    uncovered_fraction = min(1.0, uncovered / total_words) if total_words else 1.0

    anchor_fraction = min(1.0, anchor_words / total_words) if total_words else 0.0
    gap_fraction = min(1.0, gap_words / total_words) if total_words else 1.0

    reference_lyrics = correction_data.get("reference_lyrics") or {}
    accepted_sources = list(reference_lyrics.keys())

    has_synced = False
    for src in accepted_sources:
        src_meta = ((reference_lyrics.get(src) or {}).get("metadata")) or {}
        if src_meta.get("is_synced"):
            has_synced = True
            break

    # Relevance of accepted sources isn't stored directly; rejected_sources carries it.
    # Use it only to compute a best-observed relevance for context, never as a gate.
    best_relevance = 0.0
    rejected = meta.get("rejected_sources") or {}
    for info in rejected.values():
        rel = info.get("relevance")
        if isinstance(rel, (int, float)):
            best_relevance = max(best_relevance, float(rel))

    agentic_routing = meta.get("agentic_routing")
    correction_type = meta.get("correction_type")
    correction_actually_ran = (
        agentic_routing not in (None, "disabled") and correction_type != "none"
    )

    (
        voc_count,
        voc_max_run,
        long_voc,
        has_voc_section,
        max_duration,
        absurd,
        suspicious_paren,
        has_phantom,
    ) = _extract_gating_signals(correction_data)

    return LyricsSignals(
        total_words=total_words,
        anchor_word_count=anchor_words,
        gap_word_count=gap_words,
        anchor_word_fraction=round(anchor_fraction, 4),
        uncorrected_gap_fraction=round(gap_fraction, 4),
        corrections_made=int(correction_data.get("corrections_made") or 0),
        anchor_sequences_count=len(correction_data.get("anchor_sequences") or []),
        gap_sequences_count=len(correction_data.get("gap_sequences") or []),
        accepted_reference_sources=accepted_sources,
        best_reference_relevance=round(best_relevance, 4),
        strong_reference_sources=len(accepted_sources),
        has_synced_reference=has_synced,
        correction_actually_ran=correction_actually_ran,
        ai_suggestions_available=ai_suggestions is not None,
        ai_suggestion_count=len(ai_suggestions or []),
        ai_full_consensus_count=full_consensus,
        gap_words_covered_by_ai=covered,
        uncovered_gap_word_count=uncovered,
        uncovered_gap_fraction=round(uncovered_fraction, 4),
        vocalization_word_count=voc_count,
        vocalization_max_run=voc_max_run,
        long_vocalization_word_count=long_voc,
        has_vocalization_section=has_voc_section,
        max_word_duration_s=max_duration,
        absurd_duration_word_count=absurd,
        suspicious_parenthetical_count=suspicious_paren,
        has_phantom_signature=has_phantom,
    )


def score_lyrics(
    correction_data: Dict[str, Any],
    ai_suggestions: Optional[List[Dict[str, Any]]] = None,
) -> LyricsResult:
    """Decide whether the lyrics look safe to auto-approve."""
    s = extract_lyrics_signals(correction_data, ai_suggestions)
    reasons: List[str] = []

    if s.total_words < MIN_TOTAL_WORDS:
        reasons.append(
            f"only {s.total_words} words (< {MIN_TOTAL_WORDS}); too little signal to trust"
        )
        return LyricsResult(LyricsVerdict.REVIEW, "too-few-words", s, reasons)

    # Never-auto gating classes: these override everything, including a perfect
    # anchor/gap profile — the transcription can be "anchored" yet still wrong in
    # ways only a human can judge (Pattern 3) or plainly hallucinated (Pattern 8).
    if s.has_vocalization_section:
        reasons.append(
            f"non-lexical vocalization section detected ({s.vocalization_word_count} "
            f"vocalization words, longest run {s.vocalization_max_run}, "
            f"{s.long_vocalization_word_count} multi-second) — timing/grouping is a "
            "musical judgement; never fully auto"
        )
        return LyricsResult(LyricsVerdict.REVIEW, "vocalization-gate", s, reasons)

    if s.has_phantom_signature:
        reasons.append(
            f"phantom-line signature: {s.absurd_duration_word_count} words > "
            f"{ABSURD_WORD_DURATION_S:.0f}s (max {s.max_word_duration_s:.1f}s), "
            f"{s.suspicious_parenthetical_count} multi-second short parenthetical lines"
        )
        return LyricsResult(LyricsVerdict.REVIEW, "phantom-gate", s, reasons)

    if not s.accepted_reference_sources:
        reasons.append("no reference lyrics survived the relevance filter")
        return LyricsResult(LyricsVerdict.REVIEW, "no-reference", s, reasons)

    auto_ok = (
        s.has_synced_reference
        and s.anchor_word_fraction >= AUTO_MIN_ANCHOR_FRACTION
        and s.uncorrected_gap_fraction <= AUTO_MAX_GAP_FRACTION
    )
    if auto_ok:
        reasons.append(
            f"synced reference present; anchor coverage {s.anchor_word_fraction:.1%} "
            f">= {AUTO_MIN_ANCHOR_FRACTION:.0%}; zero unresolved gap words"
        )
        return LyricsResult(LyricsVerdict.AUTO, "synced-perfect", s, reasons)

    # AI-resolved tier: the auto-applied suggestions cover (almost) every gap word,
    # anchoring is high, and no never-auto gate fired. What remains uncovered is a
    # handful of words with no confident reference match — empirically adlib/benign.
    ai_resolved_ok = (
        s.ai_suggestions_available
        and s.has_synced_reference
        and s.anchor_word_fraction >= AI_RESOLVED_MIN_ANCHOR_FRACTION
        and s.uncovered_gap_fraction <= AI_RESOLVED_MAX_UNCOVERED_FRACTION
        and s.uncovered_gap_word_count <= AI_RESOLVED_MAX_UNCOVERED_WORDS
    )
    if ai_resolved_ok:
        reasons.append(
            f"synced reference; anchor {s.anchor_word_fraction:.1%} >= "
            f"{AI_RESOLVED_MIN_ANCHOR_FRACTION:.0%}; {s.gap_word_count} gap words of "
            f"which {s.gap_words_covered_by_ai} covered by auto-applied AI suggestions, "
            f"{s.uncovered_gap_word_count} uncovered ({s.uncovered_gap_fraction:.1%})"
        )
        return LyricsResult(LyricsVerdict.AUTO, "ai-resolved", s, reasons)

    # Not auto — classify why, for calibration.
    if (
        s.anchor_word_fraction >= NEARMISS_MIN_ANCHOR_FRACTION
        and s.uncorrected_gap_fraction <= NEARMISS_MAX_GAP_FRACTION
    ):
        tier = "near-miss"
        if not s.has_synced_reference:
            reasons.append("high anchor coverage but NO synced reference (unsynced only)")
        else:
            reasons.append(
                f"anchor coverage {s.anchor_word_fraction:.1%} / gap "
                f"{s.uncorrected_gap_fraction:.1%} close but below AUTO thresholds"
            )
    else:
        tier = "needs-review"
        reasons.append(
            f"anchor coverage {s.anchor_word_fraction:.1%}, unresolved-gap fraction "
            f"{s.uncorrected_gap_fraction:.1%} ({s.gap_sequences_count} gaps)"
        )
    return LyricsResult(LyricsVerdict.REVIEW, tier, s, reasons)


def extract_backing_signals(backing_analysis: Optional[Dict[str, Any]]) -> BackingSignals:
    """Pull backing-vocals signals out of ``state_data.backing_vocals_analysis``."""
    if not backing_analysis:
        return BackingSignals(analysis_present=False)
    # A FAILED analysis stores has_audible_content=None + analysis_error; that
    # must never read as "no audible content" (which would wrongly auto-select
    # clean). Treat it as no-analysis -> review.
    if backing_analysis.get("analysis_error") or backing_analysis.get("has_audible_content") is None:
        return BackingSignals(analysis_present=False)

    segments = backing_analysis.get("audible_segments") or []
    loud = 0
    peak: Optional[float] = None
    for seg in segments:
        avg_db = seg.get("avg_amplitude_db")
        if isinstance(avg_db, (int, float)) and avg_db > LOUD_SEGMENT_DB:
            loud += 1
        pk = seg.get("peak_amplitude_db")
        if isinstance(pk, (int, float)):
            peak = pk if peak is None else max(peak, pk)

    return BackingSignals(
        analysis_present=True,
        has_audible_content=bool(backing_analysis.get("has_audible_content", True)),
        audible_percentage=float(backing_analysis.get("audible_percentage", 0.0) or 0.0),
        segment_count=len(segments),
        loud_segment_count=loud,
        peak_amplitude_db=peak,
        recommended_selection=backing_analysis.get("recommended_selection"),
    )


def score_backing(backing_analysis: Optional[Dict[str, Any]]) -> BackingResult:
    """Decide whether the backing-vocals call is non-subjective (no audible backing)."""
    s = extract_backing_signals(backing_analysis)
    reasons: List[str] = []

    if not s.analysis_present:
        reasons.append("no backing-vocals analysis on the job")
        return BackingResult(BackingVerdict.REVIEW, False, s, reasons)

    non_subjective_clean = (
        not s.has_audible_content
        or (s.audible_percentage <= NON_SUBJECTIVE_MAX_AUDIBLE_PCT and s.loud_segment_count == 0)
    )
    if non_subjective_clean:
        reasons.append(
            "no audible backing vocals detected"
            if not s.has_audible_content
            else f"near-silent backing ({s.audible_percentage:.2f}% audible, no loud segments)"
        )
        return BackingResult(BackingVerdict.CLEAN, True, s, reasons)

    # Audible backing exists -> the clean-vs-with call is a taste/quality judgement.
    reasons.append(
        f"audible backing present ({s.audible_percentage:.1f}%, {s.segment_count} segments, "
        f"{s.loud_segment_count} loud) — retain-or-not is subjective; energy heuristic says "
        f"'{s.recommended_selection}'"
    )
    return BackingResult(BackingVerdict.REVIEW, False, s, reasons)


def score_job(
    correction_data: Dict[str, Any],
    backing_analysis: Optional[Dict[str, Any]],
    ai_suggestions: Optional[List[Dict[str, Any]]] = None,
) -> AutoApprovabilityVerdict:
    """Produce the combined shadow verdict for a job.

    ``overall_auto`` is the narrow safe intersection: confident lyrics AND a
    non-subjective (no-audible-backing) backing decision.
    """
    lyrics = score_lyrics(correction_data, ai_suggestions)
    backing = score_backing(backing_analysis)
    overall_auto = lyrics.verdict == LyricsVerdict.AUTO and backing.non_subjective
    return AutoApprovabilityVerdict(
        lyrics=lyrics,
        backing=backing,
        overall_auto=overall_auto,
        scorer_version=SCORER_VERSION,
    )
