"""Known infrastructure noise patterns for the production error monitoring service.

Filters out transient, expected log messages so the error monitor is not flooded
with Cloud Run cold-start probes, deploy events, spot VM preemptions, and similar
operational noise.

Usage:
    from backend.services.error_monitor.known_issues import should_ignore

    reason = should_ignore(service, message)
    if reason:
        # skip this log entry — it's known infrastructure noise
        ...
"""

import re
from dataclasses import dataclass


@dataclass
class IgnoreReason:
    """Describes why a log entry should be suppressed.

    Attributes:
        pattern_name: Machine-readable name of the matching ignore pattern.
        reason: Human-readable explanation of why this is infrastructure noise.
    """

    pattern_name: str
    reason: str


# ---------------------------------------------------------------------------
# Compiled ignore patterns (applied in order)
# All patterns use re.IGNORECASE for case-insensitive matching.
# ---------------------------------------------------------------------------

_IGNORE_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    (
        "startup_probe",
        re.compile(r"startup probe failed", re.IGNORECASE),
        "Cloud Run cold start — startup probe failure is transient and expected",
    ),
    (
        "ready_condition",
        re.compile(r"ready condition status changed", re.IGNORECASE),
        "Cloud Run deploy event — readiness condition change is expected on deploy",
    ),
    (
        "spot_preemption",
        re.compile(r"instance was preempted", re.IGNORECASE),
        "Spot/preemptible VM preemption — expected behaviour for spot instances",
    ),
    (
        "idle_shutdown",
        re.compile(r"stopping idle encoding worker", re.IGNORECASE),
        "Encoding worker idle shutdown — expected behaviour when no jobs are queued",
    ),
    (
        "health_check_404",
        re.compile(
            r"(?:GET|HEAD)\b[^\n]*?/health[^\n]*?\b(?:404|not found)\b",
            re.IGNORECASE,
        ),
        "Load balancer health check returning 404 — transient during cold start",
    ),
    (
        "scheduler_retry",
        re.compile(r"cloud scheduler.*retry", re.IGNORECASE),
        "Cloud Scheduler transient retry — expected on intermittent job failures",
    ),
    (
        "runner_startup",
        re.compile(r"github\.runner\..*(?:starting|stopping|idle)", re.IGNORECASE),
        "GitHub Actions runner lifecycle event — expected during runner start/stop",
    ),
    (
        "container_shutdown",
        re.compile(r"container called exit\(0\)", re.IGNORECASE),
        "Container clean exit — exit code 0 indicates a graceful, expected shutdown",
    ),
]


def should_ignore(service: str, message: str) -> IgnoreReason | None:
    """Check whether a log entry matches a known infrastructure noise pattern.

    The check is purely message-based; ``service`` is accepted for API symmetry
    and future service-specific overrides but does not gate any pattern today.

    Args:
        service: The originating service name (e.g. ``"karaoke-backend"``).
        message: The raw log message string to evaluate.

    Returns:
        An :class:`IgnoreReason` if the message matches a known noise pattern,
        or ``None`` if the message should be processed normally.
    """
    if not message:
        return None

    for pattern_name, pattern, reason in _IGNORE_PATTERNS:
        if pattern.search(message):
            return IgnoreReason(pattern_name=pattern_name, reason=reason)

    return None
