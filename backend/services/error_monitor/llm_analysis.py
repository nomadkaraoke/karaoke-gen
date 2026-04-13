"""LLM-powered incident analysis and duplicate detection for the error monitor.

Uses Gemini Flash via the google-generativeai library to:
  1. Group related error patterns into incidents with root-cause analysis.
  2. Identify near-duplicate patterns that the regex normalizer missed.

Both functions fall back gracefully when the LLM is unavailable or returns
unparseable output.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from google import genai
from google.genai import types as genai_types

from backend.services.error_monitor.config import (
    GCP_PROJECT,
    LLM_ANALYSIS_MODEL,
    LLM_VERTEX_LOCATION,
    MIN_PATTERNS_FOR_ANALYSIS,
    SERVICE_DEPENDENCY_MAP,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Incident:
    """A grouped incident produced by LLM analysis or the fallback grouper."""

    title: str
    root_cause: str | None
    severity: str  # P0 / P1 / P2 / P3
    suggested_fix: str | None
    primary_service: str
    pattern_indices: list[int]


@dataclass
class IncidentAnalysis:
    """The result of an incident grouping pass."""

    incidents: list[Incident]
    used_llm: bool = True


@dataclass
class DuplicateGroup:
    """A canonical pattern plus the new patterns that duplicate it."""

    canonical_index: int
    duplicate_indices: list[int]
    reason: str


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 2.0  # seconds


def _call_llm(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
) -> str:
    """Call Gemini and return the response text.

    Retries up to _RETRY_ATTEMPTS times with exponential back-off on failure.

    Args:
        system_prompt: System instruction for the model.
        user_prompt: User-facing prompt content.
        temperature: Sampling temperature (lower = more deterministic).

    Returns:
        The model's response text.

    Raises:
        Exception: Re-raises the last exception after all retries are exhausted.
    """
    last_exc: Exception | None = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            client = genai.Client(
                vertexai=True,
                project=GCP_PROJECT,
                location=LLM_VERTEX_LOCATION,
            )
            response = client.models.generate_content(
                model=LLM_ANALYSIS_MODEL,
                contents=user_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                ),
            )
            return response.text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            delay = _RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1,
                _RETRY_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_MD_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _strip_markdown_code_block(text: str) -> str:
    """Remove surrounding ```json ... ``` or ``` ... ``` fences if present."""
    match = _MD_CODE_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_llm_response(text: str) -> IncidentAnalysis | None:
    """Parse the LLM response text into an IncidentAnalysis.

    Args:
        text: Raw LLM output, possibly wrapped in a markdown code block.

    Returns:
        IncidentAnalysis on success, None on any parse failure.
    """
    if not text:
        return None

    cleaned = _strip_markdown_code_block(text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON response (first 200 chars): %.200s", text)
        return None

    if "incidents" not in data:
        logger.warning("LLM response missing 'incidents' key: %s", list(data.keys()))
        return None

    incidents: list[Incident] = []
    for raw in data["incidents"]:
        try:
            incidents.append(
                Incident(
                    title=raw.get("title", "Unknown incident"),
                    root_cause=raw.get("root_cause"),
                    severity=raw.get("severity", "P2"),
                    suggested_fix=raw.get("suggested_fix"),
                    primary_service=raw.get("primary_service", "unknown"),
                    pattern_indices=raw.get("pattern_indices", []),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed incident entry: %s — %s", raw, exc)

    return IncidentAnalysis(incidents=incidents, used_llm=True)


# ---------------------------------------------------------------------------
# Fallback grouper
# ---------------------------------------------------------------------------


def _fallback_group_by_service(patterns: list[dict]) -> IncidentAnalysis:
    """Group patterns by service name when the LLM is unavailable.

    Each unique service becomes one P2 incident containing all of that
    service's pattern indices.

    Args:
        patterns: List of error pattern dicts (must include 'service' key).

    Returns:
        IncidentAnalysis with used_llm=False.
    """
    service_to_indices: dict[str, list[int]] = defaultdict(list)
    for idx, pattern in enumerate(patterns):
        service = pattern.get("service", "unknown")
        service_to_indices[service].append(idx)

    incidents = [
        Incident(
            title=f"Errors in {service}",
            root_cause=None,
            severity="P2",
            suggested_fix=None,
            primary_service=service,
            pattern_indices=indices,
        )
        for service, indices in service_to_indices.items()
    ]

    return IncidentAnalysis(incidents=incidents, used_llm=False)


# ---------------------------------------------------------------------------
# Main analysis functions
# ---------------------------------------------------------------------------

_ANALYZE_SYSTEM_PROMPT = f"""\
You are an expert site-reliability engineer for Nomad Karaoke, a karaoke video
generation platform running on Google Cloud Platform.

Your task is to analyse a list of active error patterns detected across
production services, group them into meaningful incidents, and identify root
causes.

## Service dependency map

{SERVICE_DEPENDENCY_MAP}

## Severity definitions

- P0: Service is completely down / total outage
- P1: Major pipeline broken — orders cannot complete
- P2: Degraded performance — some orders affected
- P3: Minor / cosmetic issue with no direct user impact

## Output format

Respond with a single JSON object (no markdown fencing) with this schema:

{{
  "incidents": [
    {{
      "title": "<short human-readable title>",
      "root_cause": "<root cause or null>",
      "severity": "P0|P1|P2|P3",
      "suggested_fix": "<actionable suggestion or null>",
      "primary_service": "<service name>",
      "pattern_indices": [<list of 0-based indices from the input list>]
    }}
  ]
}}

Group related patterns together into a single incident where they share a
common root cause (e.g., a downstream dependency failure). Unrelated patterns
should be in separate incidents. Every input pattern must appear in exactly
one incident.
"""

_DEDUP_SYSTEM_PROMPT = """\
You are an expert at identifying near-duplicate error patterns in production
monitoring systems.

Your task is to compare a list of NEW error patterns against a list of
EXISTING (canonical) patterns and identify which new patterns are essentially
duplicates of existing ones — i.e., they describe the same underlying error
but with slightly different wording due to variation in log messages.

## Output format

Respond with a single JSON object (no markdown fencing) with this schema:

{
  "duplicates": [
    {
      "canonical_index": <0-based index into the EXISTING patterns list>,
      "duplicate_indices": [<0-based indices into the NEW patterns list>],
      "reason": "<brief explanation>"
    }
  ]
}

If no duplicates are found, return {"duplicates": []}.
"""


def analyze_patterns(patterns: list[dict]) -> IncidentAnalysis | None:
    """Group error patterns into incidents using Gemini LLM analysis.

    Returns None if there are fewer than MIN_PATTERNS_FOR_ANALYSIS patterns.
    Falls back to service-based grouping when the LLM fails or returns an
    unparseable response.

    Args:
        patterns: List of error pattern dicts to analyse.

    Returns:
        IncidentAnalysis, or None if insufficient patterns.
    """
    if len(patterns) < MIN_PATTERNS_FOR_ANALYSIS:
        return None

    user_prompt = _build_analyze_user_prompt(patterns)

    try:
        raw_response = _call_llm(_ANALYZE_SYSTEM_PROMPT, user_prompt, temperature=0.2)
        result = _parse_llm_response(raw_response)
        if result is not None:
            return result

        logger.warning("LLM returned unparseable response; falling back to service grouping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM analysis failed: %s — falling back to service grouping", exc)

    return _fallback_group_by_service(patterns)


def find_duplicate_patterns(
    new_patterns: list[dict],
    existing_patterns: list[dict],
) -> list[DuplicateGroup]:
    """Identify near-duplicate patterns that the regex normalizer missed.

    Returns an empty list if either input list is empty, or if the LLM call
    fails for any reason.

    Args:
        new_patterns: Newly detected error patterns to check.
        existing_patterns: Currently tracked (canonical) error patterns.

    Returns:
        List of DuplicateGroup objects (may be empty).
    """
    if not new_patterns or not existing_patterns:
        return []

    user_prompt = _build_dedup_user_prompt(new_patterns, existing_patterns)

    try:
        raw_response = _call_llm(_DEDUP_SYSTEM_PROMPT, user_prompt, temperature=0.1)
        return _parse_duplicate_response(raw_response)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Duplicate detection LLM call failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_analyze_user_prompt(patterns: list[dict]) -> str:
    """Build the user prompt listing patterns for incident grouping."""
    lines = ["Error patterns to analyse:\n"]
    for idx, pattern in enumerate(patterns):
        service = pattern.get("service", "unknown")
        message = pattern.get("normalized_message", "")
        count = pattern.get("count", 0)
        lines.append(f"[{idx}] service={service!r}  count={count}  message={message!r}")
    return "\n".join(lines)


def _build_dedup_user_prompt(
    new_patterns: list[dict],
    existing_patterns: list[dict],
) -> str:
    """Build the user prompt listing existing and new patterns for dedup."""
    lines = ["EXISTING (canonical) patterns:\n"]
    for idx, pattern in enumerate(existing_patterns):
        service = pattern.get("service", "unknown")
        message = pattern.get("normalized_message", "")
        lines.append(f"[{idx}] service={service!r}  message={message!r}")

    lines.append("\nNEW patterns to check:\n")
    for idx, pattern in enumerate(new_patterns):
        service = pattern.get("service", "unknown")
        message = pattern.get("normalized_message", "")
        lines.append(f"[{idx}] service={service!r}  message={message!r}")

    return "\n".join(lines)


def _parse_duplicate_response(text: str) -> list[DuplicateGroup]:
    """Parse the LLM response for duplicate detection.

    Args:
        text: Raw LLM output.

    Returns:
        List of DuplicateGroup objects, or [] on any failure.
    """
    if not text:
        return []

    cleaned = _strip_markdown_code_block(text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Dedup LLM returned non-JSON: %.200s", text)
        return []

    if "duplicates" not in data:
        logger.warning("Dedup LLM response missing 'duplicates' key")
        return []

    groups: list[DuplicateGroup] = []
    for raw in data["duplicates"]:
        try:
            groups.append(
                DuplicateGroup(
                    canonical_index=int(raw["canonical_index"]),
                    duplicate_indices=list(raw.get("duplicate_indices", [])),
                    reason=raw.get("reason", ""),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed duplicate entry: %s — %s", raw, exc)

    return groups
