"""User-facing knobs for AI auto-correction suggestions."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AutoCorrectSettings:
    """Reviewer-tunable behaviour for a suggestion run.

    suggest_adlib_removal: allow suggestions that delete transcribed
        backing-vocal / adlib phrases absent from the references.
    allow_insertions: allow suggestions that insert words the
        transcription missed entirely.
    min_confidence: drop suggestions below this confidence server-side
        (the UI can filter further client-side).
    compare_models: query every configured compare model in parallel,
        deduplicate identical suggestions and report per-suggestion
        model consensus (slower, costs one call per model).
    """

    suggest_adlib_removal: bool = True
    allow_insertions: bool = True
    min_confidence: float = 0.0
    compare_models: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def settings_from_dict(data: dict) -> AutoCorrectSettings:
    known = {f for f in AutoCorrectSettings.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    merged = {**AutoCorrectSettings().to_dict(), **data}
    if not isinstance(merged["suggest_adlib_removal"], bool):
        raise ValueError("suggest_adlib_removal must be a boolean")
    if not isinstance(merged["allow_insertions"], bool):
        raise ValueError("allow_insertions must be a boolean")
    if not isinstance(merged["compare_models"], bool):
        raise ValueError("compare_models must be a boolean")
    mc = merged["min_confidence"]
    if isinstance(mc, bool) or not isinstance(mc, (int, float)) or not 0.0 <= float(mc) <= 1.0:
        raise ValueError("min_confidence must be a number between 0 and 1")
    merged["min_confidence"] = float(mc)
    return AutoCorrectSettings(**merged)
