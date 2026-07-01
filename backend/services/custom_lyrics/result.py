"""Result types for the custom-lyrics service."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.services.custom_lyrics.settings import GenerationSettings
from backend.services.custom_lyrics.validator import LineValidation


class StopReason(str, Enum):
    SUCCESS = "success"
    PLATEAU = "plateau"
    MAX_ITERS_REACHED = "max_iters_reached"
    LINE_COUNT_MISMATCH = "line_count_mismatch"
    VERBATIM_SKIP = "verbatim_skip"
    # A repair iteration failed (e.g. the model returned an empty/blocked
    # response); we keep the best result found so far instead of failing.
    REPAIR_FAILED = "repair_failed"


@dataclass
class CustomLyricsResult:
    lines: list[str]
    line_metadata: list[LineValidation]
    iterations_used: int
    stop_reason: StopReason
    settings_applied: GenerationSettings
    model: str
    duration_ms: int
    new_segment_timing: Optional[list[tuple[float, float]]] = None
    line_count_mismatch: bool = False
    warnings: list[str] = field(default_factory=list)
