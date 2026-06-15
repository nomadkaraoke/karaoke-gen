"""The verdict returned by the match judge."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# kind values:
#   "cosmetic"  – same song, formatting-only fix → apply invisibly
#   "content"   – actual characters change (typo/different title) → surface + maybe re-search
#   "ambiguous" – more than one plausible song → ask the user first
#   "none"      – nothing to change (already correct, or no confident suggestion)
KIND_COSMETIC = "cosmetic"
KIND_CONTENT = "content"
KIND_AMBIGUOUS = "ambiguous"
KIND_NONE = "none"


Kind = Literal["cosmetic", "content", "ambiguous", "none"]
Engine = Literal["deterministic", "catalog", "ai"]


@dataclass(frozen=True)
class MatchVerdict:
    kind: Kind
    confident: bool
    canonical_artist: str
    canonical_title: str
    alternatives: list[dict] = field(default_factory=list)  # [{"artist","title"}]
    engine: Engine = "deterministic"
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "confident": self.confident,
            "canonical_artist": self.canonical_artist,
            "canonical_title": self.canonical_title,
            "alternatives": self.alternatives,
            "engine": self.engine,
            "reason": self.reason,
        }
