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

from backend.services.auto_approval.apply import pick_accept_all_winners
from backend.services.auto_approval.models import (
    AutoApprovabilityVerdict,
    BackingResult,
    BackingSignals,
    BackingVerdict,
    LyricsResult,
    LyricsSignals,
    LyricsVerdict,
)

SCORER_VERSION = "0.4.0"

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

# --- 3-stem backing decider thresholds (Phase 2B) ---
# Calibrated on the 20-job replay-review corpus signals (private
# validate_backing_decider.py — rerun after ANY change here). Corpus rules:
# pink present -> KEEP (the human under-keeps; every with_backing pick was
# correct), EXCEPT the "pink but don't keep" modes below.
#
# Backing-stem-IS-the-lead (1d45b286 — catastrophic if kept): the backing stem
# tracks the whole vocal line while the real lead stem is comparatively
# sparse. Corpus values: covR 0.94 / corr 0.95 / backing 60% audible vs lead
# 18%. The coverage comparison (backing > lead) is the decisive discriminator:
# the nearest genuine keep (33453fa0) hits covR 0.77 / corr 0.86 but its
# backing coverage (28%) sits well BELOW its lead coverage (34%).
BACKING_IS_LEAD_MIN_COVERAGE_RATIO = 0.80
BACKING_IS_LEAD_MIN_CORR = 0.80
# Noise-floor pink (95d8e844, grungegaze): a fuzzy mix pushes the backing
# stem's noise floor just above the -40 dB threshold, producing widespread
# near-threshold "pink" that does not correlate with the vocal line (corpus:
# median -38.6 dB over 30% of the track, corr -0.23). Genuine quiet harmonies
# (d508adb6: -38.1 dB but only 1% audible; 44622ffa: -35.9 dB, 11%) are sparse.
# Marginal either way per the corpus -> REVIEW, never a confident verdict.
NOISE_FLOOR_MAX_MEDIAN_DB = -35.0
NOISE_FLOOR_MIN_AUDIBLE_FRACTION = 0.15
NOISE_FLOOR_MAX_CORR = 0.05
# Lead bleed (ae0cd7e8): tiny audible content riding entirely on lead
# activity. The corpus bleed case is already caught by the near-silent rule;
# this catches slightly-larger blobs. Deliberately NARROW (overlap >= 0.95):
# d508adb6's genuine harmonies overlap the lead 0.89 and must stay keepable.
BLEED_MAX_AUDIBLE_PCT = 3.0
BLEED_MIN_LEAD_OVERLAP = 0.95

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
    deleted_word_ids: Optional[set] = None,
) -> Tuple[int, int, int, bool, float, int, int, bool]:
    """Scan corrected_segments for the two never-auto gating classes.

    ``deleted_word_ids`` are words an auto-applied delete suggestion will remove
    before any human (or the auto-shipped render) sees the lyrics — e.g. the P8
    phantom-parenthetical fixer. Those words are excluded from the scan so a
    phantom that WILL be deleted no longer trips the phantom gate.

    Returns (vocalization_word_count, vocalization_max_run, long_vocalization_word_count,
    has_vocalization_section, max_word_duration_s, absurd_duration_word_count,
    suspicious_parenthetical_count, has_phantom_signature).
    """
    deleted = deleted_word_ids or set()
    voc_count = 0
    max_run = 0
    run = 0  # runs continue across segment boundaries (consecutive vocalization lines)
    long_voc = 0
    very_long_voc = 0
    max_duration = 0.0
    absurd = 0
    suspicious_paren = 0

    for seg in correction_data.get("corrected_segments") or []:
        # Only the words that survive the auto-applied deletes.
        words = [w for w in (seg.get("words") or []) if w.get("id") not in deleted]
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
        # (e.g. three "(I'm sorry)" lines, one 7.4s — corpus job 33453fa0). Recompute
        # from surviving words so a partially-deleted line is judged on what remains.
        if not words:
            continue
        seg_text = " ".join((w.get("text") or "") for w in words).strip()
        if (
            seg_text.startswith("(")
            and seg_text.endswith(")")
            and len(words) <= PHANTOM_PARENTHETICAL_MAX_WORDS
        ):
            seg_start, seg_end = words[0].get("start_time"), words[-1].get("end_time")
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


def _deleted_word_ids(ai_suggestions: Optional[List[Dict[str, Any]]]) -> set:
    """Word ids that an auto-applied WINNING delete suggestion will remove.

    Only winners count: a delete that loses its conflict group to another
    suggestion won't actually remove the words (the winner rewrites them
    instead), so treating it as deleted would be optimistic. Mirrors the apply
    step's accept-all winner selection so the scorer reasons about the exact
    post-apply state.
    """
    if not ai_suggestions:
        return set()
    winners = set(pick_accept_all_winners(ai_suggestions))
    ids: set = set()
    for s in ai_suggestions:
        if s.get("op") == "delete" and (s.get("id") in winners):
            ids.update(s.get("word_ids") or [])
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
    ) = _extract_gating_signals(correction_data, _deleted_word_ids(ai_suggestions))

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
    timing_signals: Optional[Any] = None,
) -> LyricsResult:
    """Decide whether the lyrics look safe to auto-approve.

    ``timing_signals`` (a ``timing_check.TimingSignals``) is optional because
    computing it needs the lead-vocal stem (audio IO the executor performs only
    when the text signals would otherwise be AUTO). When provided and fired,
    it is a never-auto gate: word timing that contradicts the vocal audio is a
    quality problem no text signal can see (corpus: timing is the dominant
    residual human-edit class, median retime 0.70s).
    """
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

    if timing_signals is not None and getattr(timing_signals, "fired", None):
        sig = timing_signals
        reasons.append(
            f"timing-plausibility gate fired ({', '.join(sig.fired)}): "
            f"{sig.pct_start_inactive:.1f}% of word starts in vocal silence, "
            f"{sig.n_suspect_bad} structurally-suspect words contradicted by the "
            f"audio, longest unclaimed vocal run {sig.max_unclaimed_run_s:.1f}s — "
            "word timing likely needs human retiming"
        )
        return LyricsResult(LyricsVerdict.REVIEW, "timing-gate", s, reasons)

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

    signals = BackingSignals(
        analysis_present=True,
        has_audible_content=bool(backing_analysis.get("has_audible_content", True)),
        audible_percentage=float(backing_analysis.get("audible_percentage", 0.0) or 0.0),
        segment_count=len(segments),
        loud_segment_count=loud,
        peak_amplitude_db=peak,
        recommended_selection=backing_analysis.get("recommended_selection"),
    )

    # 3-stem comparison (Phase 2B). A comparison that errored is treated as
    # absent — never as evidence in either direction.
    comparison = backing_analysis.get("stem_comparison") or {}
    if comparison and not comparison.get("error"):
        def _num(key: str, default: float = 0.0) -> float:
            value = comparison.get(key)
            return float(value) if isinstance(value, (int, float)) else default

        signals.comparison_present = True
        signals.coverage_ratio = _num("coverage_ratio")
        signals.corr_backing_vocals = _num("corr_backing_vocals")
        signals.corr_backing_lead = _num("corr_backing_lead")
        signals.lead_overlap_fraction = _num("lead_overlap_fraction")
        signals.lead_audible_fraction = _num("lead_audible_fraction")
        signals.backing_audible_fraction = _num("backing_audible_fraction")
        signals.backing_db_std = _num("backing_db_std")
        signals.flat_fraction = _num("flat_fraction")
        backing_median = comparison.get("backing_median_db")
        lead_median = comparison.get("lead_median_db")
        if isinstance(backing_median, (int, float)):
            signals.backing_median_db = float(backing_median)
        if isinstance(lead_median, (int, float)):
            signals.lead_median_db = float(lead_median)
    return signals


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

    # Audible backing exists. Without the 3-stem comparison the clean-vs-with
    # call stays a human judgement (pre-Phase-2B jobs, or comparison failed).
    if not s.comparison_present:
        reasons.append(
            f"audible backing present ({s.audible_percentage:.1f}%, {s.segment_count} segments, "
            f"{s.loud_segment_count} loud) but no 3-stem comparison available — "
            "retain-or-not needs a human listen"
        )
        return BackingResult(BackingVerdict.REVIEW, False, s, reasons)

    # -- "pink but don't keep" mode 1: the backing stem IS the lead --
    # (separation misclassified a quiet/reverby lead; keeping it would put the
    # lead vocal back into the karaoke track — the worst possible output.)
    if (
        s.coverage_ratio >= BACKING_IS_LEAD_MIN_COVERAGE_RATIO
        and s.corr_backing_vocals >= BACKING_IS_LEAD_MIN_CORR
        and s.backing_audible_fraction > s.lead_audible_fraction
    ):
        reasons.append(
            f"backing stem appears to BE the lead vocal: covers "
            f"{s.coverage_ratio:.0%} of the vocal line with envelope correlation "
            f"{s.corr_backing_vocals:.2f}, and is more present than the lead stem "
            f"({s.backing_audible_fraction:.0%} vs {s.lead_audible_fraction:.0%} "
            "audible) — keeping it would re-insert the lead; needs a human listen"
        )
        return BackingResult(BackingVerdict.REVIEW, False, s, reasons)

    # -- mode 2: noise-floor pink (lo-fi/fuzzy mix) --
    if (
        s.backing_median_db is not None
        and s.backing_median_db <= NOISE_FLOOR_MAX_MEDIAN_DB
        and s.backing_audible_fraction >= NOISE_FLOOR_MIN_AUDIBLE_FRACTION
        and s.corr_backing_vocals <= NOISE_FLOOR_MAX_CORR
    ):
        reasons.append(
            f"widespread near-threshold backing (median "
            f"{s.backing_median_db:.1f} dB over {s.backing_audible_fraction:.0%} "
            f"of the track) uncorrelated with the vocal line "
            f"({s.corr_backing_vocals:.2f}) — noise-floor signature; marginal "
            "either way, needs a human listen"
        )
        return BackingResult(BackingVerdict.REVIEW, False, s, reasons)

    # -- mode 3: lead bleed (small blobs riding entirely on lead activity) --
    if (
        s.audible_percentage <= BLEED_MAX_AUDIBLE_PCT
        and s.lead_overlap_fraction >= BLEED_MIN_LEAD_OVERLAP
    ):
        reasons.append(
            f"sparse backing ({s.audible_percentage:.1f}%) almost entirely "
            f"overlapping lead activity ({s.lead_overlap_fraction:.0%}) — "
            "likely lead bleed, not harmony; needs a human listen"
        )
        return BackingResult(BackingVerdict.REVIEW, False, s, reasons)

    # Real pink harmony remains -> KEEP. Corpus: the human under-keeps (every
    # with_backing pick was right, 2 of 8 clean picks were wrong); with the
    # three don't-keep modes carved out above, retaining is the non-subjective
    # default ("retain backing vocals where possible").
    reasons.append(
        f"clear backing-vocal content present ({s.audible_percentage:.1f}%, "
        f"{s.segment_count} segments, {s.loud_segment_count} loud; flat fraction "
        f"{s.flat_fraction:.0%}, vocal-line coverage {s.coverage_ratio:.0%}) with "
        "no lead-bleed / misclassified-lead / noise signature — retain"
    )
    return BackingResult(BackingVerdict.WITH_BACKING, True, s, reasons)


def score_job(
    correction_data: Dict[str, Any],
    backing_analysis: Optional[Dict[str, Any]],
    ai_suggestions: Optional[List[Dict[str, Any]]] = None,
    timing_signals: Optional[Any] = None,
) -> AutoApprovabilityVerdict:
    """Produce the combined shadow verdict for a job.

    ``overall_auto`` is the narrow safe intersection: confident lyrics AND a
    non-subjective (no-audible-backing) backing decision.
    """
    lyrics = score_lyrics(correction_data, ai_suggestions, timing_signals)
    backing = score_backing(backing_analysis)
    overall_auto = lyrics.verdict == LyricsVerdict.AUTO and backing.non_subjective
    return AutoApprovabilityVerdict(
        lyrics=lyrics,
        backing=backing,
        overall_auto=overall_auto,
        scorer_version=SCORER_VERSION,
    )
