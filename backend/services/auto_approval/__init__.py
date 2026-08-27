"""Auto-approvability scoring + review-diff analysis.

Shared, pure building blocks for the "fully-automated review" program:
- ``scorer`` — decide whether a job's lyrics/backing decisions are safe to
  auto-approve (used in shadow mode first, then as the real gate).
- ``lyrics_diff`` — reconstruct exactly what a human changed during review by
  diffing ``corrections.json`` against ``corrections_updated.json``.

See ``docs/archive/2026-08-25-full-auto-review-design.md``.
"""

from backend.services.auto_approval.models import (
    AutoApprovabilityVerdict,
    BackingResult,
    BackingVerdict,
    LyricsResult,
    LyricsVerdict,
)
from backend.services.auto_approval.scorer import (
    SCORER_VERSION,
    score_backing,
    score_job,
    score_lyrics,
)

__all__ = [
    "AutoApprovabilityVerdict",
    "BackingResult",
    "BackingVerdict",
    "LyricsResult",
    "LyricsVerdict",
    "SCORER_VERSION",
    "score_backing",
    "score_job",
    "score_lyrics",
]
