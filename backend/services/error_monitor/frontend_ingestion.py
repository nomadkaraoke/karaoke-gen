"""Frontend error ingestion helpers.

Converts an inbound browser crash report into a ``PatternData`` suitable for
the shared ``ErrorPatternsAdapter``. The adapter + existing error-monitor
Cloud Run Job handle all alerting / Discord plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from backend.services.error_monitor.firestore_adapter import PatternData
from backend.services.error_monitor.normalizer import (
    compute_pattern_hash,
    normalize_message,
)

MAX_SAMPLE_MESSAGE_CHARS = 4000
MAX_URL_CHARS = 512


@dataclass
class FrontendErrorReport:
    """In-memory representation of an inbound crash report."""

    message: str
    stack: str | None
    url: str
    user_agent: str
    release: str
    user_email: str | None
    viewport: dict | None
    locale: str
    extra: dict | None


def sanitize_url(url: str) -> str:
    """Strip query and fragment from a URL; cap length; tolerate junk."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if not parts.scheme:
            return url[:MAX_URL_CHARS]
        cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return cleaned[:MAX_URL_CHARS]
    except ValueError:
        return url[:MAX_URL_CHARS]


def _stack_for_hashing(report: FrontendErrorReport) -> str:
    """Pick the most stable signal we have for pattern dedup.

    Prefer the stack (stable across invocations) over the message (sometimes
    has interpolated values). Falls back to message if no stack.
    """
    if report.stack:
        return report.stack
    return report.message


def build_pattern_data(
    report: FrontendErrorReport, now: datetime | None = None
) -> PatternData:
    """Convert an inbound report to a PatternData ready for upsert."""
    if now is None:
        now = datetime.now(tz=timezone.utc)

    raw = _stack_for_hashing(report)
    normalized = normalize_message(raw)
    pattern_id = compute_pattern_hash("frontend", normalized)

    # sample_message is human-readable context. Keep the error message plus a
    # trimmed stack + sanitized URL so the Discord alert is self-contained.
    sample_parts: list[str] = []
    if report.message:
        sample_parts.append(report.message.strip())
    if report.stack and report.stack.strip() != (report.message or "").strip():
        sample_parts.append(report.stack.strip())
    clean_url = sanitize_url(report.url)
    if clean_url:
        sample_parts.append(f"URL: {clean_url}")
    if report.user_agent:
        sample_parts.append(f"UA: {report.user_agent[:200]}")
    if report.release:
        sample_parts.append(f"Build: {report.release}")
    sample_message = "\n".join(sample_parts)[:MAX_SAMPLE_MESSAGE_CHARS]

    return PatternData(
        pattern_id=pattern_id,
        service="frontend",
        resource_type="browser",
        normalized_message=normalized,
        sample_message=sample_message,
        count=1,
        timestamp=now,
    )


class RateLimiter:
    """In-memory sliding-window limiter. One instance per process is enough —
    this runs inside Cloud Run which scales to multiple instances, so the
    effective limit is (per_ip_per_minute * num_instances). That's fine for our
    threat model (non-malicious browsers reporting their own crashes).
    """

    def __init__(self, max_per_minute: int = 60) -> None:
        self._max = max_per_minute
        self._hits: dict[str, list[float]] = {}

    def allow(self, ip: str, now_ts: float) -> bool:
        cutoff = now_ts - 60.0
        hits = [t for t in self._hits.get(ip, []) if t >= cutoff]
        if len(hits) >= self._max:
            self._hits[ip] = hits
            return False
        hits.append(now_ts)
        self._hits[ip] = hits
        # simple cleanup: if the map gets huge, drop stale keys
        if len(self._hits) > 10_000:
            self._hits = {
                k: [t for t in v if t >= cutoff]
                for k, v in self._hits.items()
                if any(t >= cutoff for t in v)
            }
        return True
