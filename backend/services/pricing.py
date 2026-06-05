"""Duration-based credit pricing. Single source of truth for credit cost.

credits = max(1, ceil(duration_seconds / 600))   # 10-minute tiers
blocked = duration_seconds > 3600                 # 60-minute hard ceiling
"""
from typing import Optional
import math

SECONDS_PER_CREDIT_TIER = 600          # 10 minutes per credit tier
DURATION_CREDIT_BLOCK_SECONDS = 3600   # inputs longer than 60 min are not supported


def duration_to_credits(seconds: Optional[float]) -> int:
    """Credits required to process `seconds` of audio. Minimum 1."""
    if seconds is None or seconds < 0:
        return 1
    return max(1, math.ceil(seconds / SECONDS_PER_CREDIT_TIER))


def is_blocked(seconds: Optional[float]) -> bool:
    """True if the input exceeds the supported duration ceiling."""
    return seconds is not None and seconds > DURATION_CREDIT_BLOCK_SECONDS
