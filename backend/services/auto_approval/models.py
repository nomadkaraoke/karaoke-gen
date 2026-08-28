"""Data models for the auto-approvability scorer.

These are plain dataclasses (JSON-serializable via ``asdict``) so the verdict can
be logged into ``processing_metadata`` and written into the offline recording
corpus without pulling in extra dependencies.

See ``docs/archive/2026-08-25-full-auto-review-design.md`` for the design.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class LyricsVerdict(str, Enum):
    """Whether the lyrics look safe to auto-approve without human review."""

    AUTO = "auto"  # confident the synced lyrics are already correct
    REVIEW = "review"  # needs a human


class BackingVerdict(str, Enum):
    """Backing-vocals decision confidence."""

    CLEAN = "clean"  # non-subjective: no backing vocals to retain -> clean
    WITH_BACKING = "with_backing"  # (reserved) confidently retain
    REVIEW = "review"  # subjective -> needs a human listen


@dataclass
class LyricsSignals:
    """The quantitative signals extracted from ``corrections.json``."""

    total_words: int = 0
    anchor_word_count: int = 0
    gap_word_count: int = 0
    anchor_word_fraction: float = 0.0
    uncorrected_gap_fraction: float = 0.0
    corrections_made: int = 0
    anchor_sequences_count: int = 0
    gap_sequences_count: int = 0
    # Reference-lyrics agreement
    accepted_reference_sources: List[str] = field(default_factory=list)
    best_reference_relevance: float = 0.0
    strong_reference_sources: int = 0
    has_synced_reference: bool = False
    # Guards
    correction_actually_ran: bool = False
    # AI auto-correct suggestions (auto-applied on review load — see corpus SYNTHESIS):
    # gap words COVERED by a suggestion get fixed before a human would ever look,
    # so post-AI confidence keys off the UNCOVERED remainder, not the raw gap count.
    ai_suggestions_available: bool = False
    ai_suggestion_count: int = 0
    ai_full_consensus_count: int = 0
    gap_words_covered_by_ai: int = 0
    uncovered_gap_word_count: int = 0
    uncovered_gap_fraction: float = 0.0
    # Gating class: non-lexical vocalization sections (Pattern 3 — never fully auto)
    vocalization_word_count: int = 0
    vocalization_max_run: int = 0
    long_vocalization_word_count: int = 0
    has_vocalization_section: bool = False
    # Gating class: phantom/hallucinated lines with absurd durations (Pattern 8)
    max_word_duration_s: float = 0.0
    absurd_duration_word_count: int = 0
    suspicious_parenthetical_count: int = 0
    has_phantom_signature: bool = False


@dataclass
class BackingSignals:
    """The quantitative signals extracted from ``backing_vocals_analysis``."""

    analysis_present: bool = False
    has_audible_content: bool = True
    audible_percentage: float = 0.0
    segment_count: int = 0
    loud_segment_count: int = 0
    peak_amplitude_db: Optional[float] = None
    recommended_selection: Optional[str] = None
    # 3-stem comparison (backing vs lead vs full vocals) — present on jobs
    # analyzed since the Phase-2B backing decider; absent before that.
    comparison_present: bool = False
    coverage_ratio: float = 0.0
    corr_backing_vocals: float = 0.0
    corr_backing_lead: float = 0.0
    lead_overlap_fraction: float = 0.0
    lead_audible_fraction: float = 0.0
    backing_audible_fraction: float = 0.0
    backing_median_db: Optional[float] = None
    lead_median_db: Optional[float] = None
    backing_db_std: float = 0.0
    flat_fraction: float = 0.0


@dataclass
class LyricsResult:
    verdict: LyricsVerdict
    tier: str  # short label, e.g. "synced-perfect", "high-anchor", "needs-review"
    signals: LyricsSignals
    reasons: List[str] = field(default_factory=list)


@dataclass
class BackingResult:
    verdict: BackingVerdict
    non_subjective: bool  # True only when the decision has no taste component
    signals: BackingSignals
    reasons: List[str] = field(default_factory=list)


@dataclass
class AutoApprovabilityVerdict:
    """Combined shadow verdict for a job.

    ``overall_auto`` is intentionally the *conjunction* of a confident lyrics
    verdict and a non-subjective backing decision — the narrow, safe intersection
    Andrew asked to start with. Anything less stays in human review.
    """

    lyrics: LyricsResult
    backing: BackingResult
    overall_auto: bool
    scorer_version: str

    def to_dict(self) -> Dict[str, Any]:
        # Convert Enum members to their plain string values so the dict is safe
        # for both json.dumps and Firestore writes.
        return asdict(
            self,
            dict_factory=lambda pairs: {
                k: (v.value if isinstance(v, Enum) else v) for k, v in pairs
            },
        )
