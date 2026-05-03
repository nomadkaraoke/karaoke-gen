"""Operator-facing generation settings and strictness→params mapping."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any


class StrictnessLevel(str, Enum):
    VERBATIM = "verbatim"
    LOOSE = "loose"
    BALANCED = "balanced"
    TIGHT = "tight"
    STRICT = "strict"


@dataclass(frozen=True)
class StrictnessParams:
    tolerance: int
    max_iterations: int
    prompt_phrase: str


_STRICTNESS_TABLE: dict[StrictnessLevel, StrictnessParams] = {
    StrictnessLevel.VERBATIM: StrictnessParams(
        tolerance=10**6,
        max_iterations=0,
        prompt_phrase="Use the client's text as-is. Rhythm matching is not a goal.",
    ),
    StrictnessLevel.LOOSE: StrictnessParams(
        tolerance=4,
        max_iterations=1,
        prompt_phrase="Aim to roughly match the original syllable count where convenient.",
    ),
    StrictnessLevel.BALANCED: StrictnessParams(
        tolerance=2,
        max_iterations=2,
        prompt_phrase="Match each line's syllable count within 2 where possible.",
    ),
    StrictnessLevel.TIGHT: StrictnessParams(
        tolerance=1,
        max_iterations=3,
        prompt_phrase="Closely match each line's syllable count. Aim for ±1 syllable.",
    ),
    StrictnessLevel.STRICT: StrictnessParams(
        tolerance=0,
        max_iterations=4,
        prompt_phrase="Match each line's syllable count exactly. Rhythm precision is the priority.",
    ),
}


def params_for(level: StrictnessLevel) -> StrictnessParams:
    return _STRICTNESS_TABLE[level]


@dataclass
class GenerationSettings:
    allow_reword: bool = True
    allow_omit: bool = True
    fixed_line_count: bool = True
    strictness: StrictnessLevel = StrictnessLevel.BALANCED

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["strictness"] = self.strictness.value
        return d


def settings_from_dict(data: dict[str, Any]) -> GenerationSettings:
    """Construct GenerationSettings from a partial dict (used by the API layer)."""
    kwargs: dict[str, Any] = {}
    if "allow_reword" in data:
        kwargs["allow_reword"] = bool(data["allow_reword"])
    if "allow_omit" in data:
        kwargs["allow_omit"] = bool(data["allow_omit"])
    if "fixed_line_count" in data:
        kwargs["fixed_line_count"] = bool(data["fixed_line_count"])
    if "strictness" in data:
        kwargs["strictness"] = StrictnessLevel(data["strictness"])  # raises ValueError if invalid
    return GenerationSettings(**kwargs)
