"""Token pricing for auto-correct model calls.

Used to turn the per-call token usage we capture from each provider into an
estimated USD cost, for both the production usage logs and the offline
measurement script (``scripts/measure_autocorrect_cost.py``).

Prices are USD per 1,000,000 tokens.

- **Anthropic** prices are authoritative (published Claude API pricing).
  ``output`` is the billed output rate, which already includes adaptive
  *thinking* tokens — they are billed as output and are not separable from
  the final answer in the Anthropic usage object.
- **Gemini** prices are a best-known ESTIMATE for the Vertex preview tier and
  are clearly marked below. Verify against current Vertex pricing before
  relying on the dollar figure. Gemini thinking tokens ARE separable
  (``thoughts_token_count``) and are billed at the output rate, so we fold
  them into the output total.

The raw token counts are always logged alongside the dollar figure, so the
cost here is a derived convenience — if a rate is stale, recompute from the
logged tokens rather than trusting the historical USD value.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class TokenUsage(TypedDict):
    """Normalized, provider-agnostic usage for one model call.

    ``output_tokens`` is the total billed output (final answer + thinking).
    ``thinking_tokens`` is informational only (None when the provider does
    not report it separately) — it is already counted inside ``output_tokens``.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    thinking_tokens: Optional[int]


class _Rate(TypedDict):
    input: float
    output: float
    cache_read: float


# USD per 1,000,000 tokens.
MODEL_PRICING: dict[str, _Rate] = {
    # --- Anthropic (authoritative) ---
    "claude-fable-5": {"input": 10.0, "output": 50.0, "cache_read": 1.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.1},
    # --- Gemini (ESTIMATE — Vertex 3.x Pro preview, <=200K-token context
    #     tier; confirm against current Google pricing before trusting USD) ---
    "gemini-3.1-pro-preview": {"input": 2.0, "output": 12.0, "cache_read": 0.2},
}


def get_rate(model: str) -> Optional[_Rate]:
    """Return the rate card for ``model``, or None if we have no pricing.

    Falls back to a prefix match on the model family (e.g. a dated
    ``claude-fable-5-2026...`` snapshot still resolves to ``claude-fable-5``)
    so a minor model-id change doesn't silently drop cost tracking.
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    for known, rate in MODEL_PRICING.items():
        if model.startswith(known):
            return rate
    return None


def estimate_cost_usd(model: str, usage: Optional[TokenUsage]) -> Optional[float]:
    """Estimate the USD cost of one model call from its token usage.

    Returns None when usage is unavailable or the model has no known pricing,
    so callers can distinguish "free/zero" from "unknown".
    """
    if not usage:
        return None
    rate = get_rate(model)
    if rate is None:
        return None
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    cache_read = usage.get("cache_read_tokens") or 0
    return (
        input_tokens * rate["input"]
        + output_tokens * rate["output"]
        + cache_read * rate["cache_read"]
    ) / 1_000_000.0
