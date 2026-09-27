"""Frontend error ingestion helpers.

Converts an inbound browser crash report into a ``PatternData`` suitable for
the shared ``ErrorPatternsAdapter``. The adapter + existing error-monitor
Cloud Run Job handle all alerting / Discord plumbing.
"""
from __future__ import annotations

import re
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
# Stack goes LAST in the sample and is trimmed: Discord shows only the head of
# the sample, and a long stack used to push URL/UA/build/diagnostics off the end.
MAX_SAMPLE_STACK_LINES = 15
MAX_SAMPLE_BREADCRUMBS = 10

# Crawlers/bots execute our JS (e.g. bingbot/Googlebot run headless Chrome) and
# trip React DOM-reconciliation errors that no real user ever sees. Their reports
# are pure alert-channel noise, so we drop them at ingestion. Matches the common
# crawler tokens plus the generic bot/crawler/spider/headless markers that
# well-behaved automated agents put in their UA string.
_BOT_UA_RE = re.compile(
    # Generic markers. The bot forms are anchored so we never match the "bot"
    # inside ordinary words like "robot": a standalone "bot" token (\bbot\b does
    # not match inside "robot" — no word boundary there) or the crawler
    # "<name>bot/<version>" convention, with "robot/" explicitly excluded.
    r"\bbot\b|(?<!ro)bot/|spider|crawler|crawl\b|slurp|bingpreview|headless|"
    # Explicit crawler names (some don't use the "/version" convention).
    r"googlebot|bingbot|applebot|yandex|baiduspider|duckduckbot|"
    r"ahrefs|semrush|petalbot|facebookexternalhit|"
    r"chrome-lighthouse|google page speed|python-requests|curl/|wget/",
    re.IGNORECASE,
)


def is_bot_user_agent(user_agent: str | None) -> bool:
    """Return True if the UA looks like a crawler/bot/automated agent.

    Conservative: only matches explicit bot markers, so a real browser UA (which
    never contains these tokens) is never misclassified.
    """
    if not user_agent:
        return False
    return bool(_BOT_UA_RE.search(user_agent))


# Browsers mask errors from cross-origin scripts (third-party tags, in-app
# browser/extension injections) as a bare "Script error." with no error object,
# file or line — nothing actionable. The frontend drops these at the source; this
# also covers older cached bundles that still report them (as "Error: Script
# error." whose stack is only our own window.onerror handler's frame, so the stack
# can't be used to tell them apart). The discriminator is the ErrorEvent's own
# filename/lineno, which browsers blank for opaque errors — a genuine
# `throw new Error("Script error.")` from our code keeps its file/line and is kept.
_OPAQUE_SCRIPT_ERROR_RE = re.compile(r"^(?:Error:\s*)?Script error\.?$", re.IGNORECASE)


def is_opaque_script_error(message: str | None, source: str | None = None, extra: dict | None = None) -> bool:
    """Return True for the browser's opaque cross-origin "Script error." report."""
    if not message or not _OPAQUE_SCRIPT_ERROR_RE.match(message.strip()):
        return False
    if source != "window.onerror" or not isinstance(extra, dict):
        return False
    return not extra.get("filename") and not extra.get("lineno")


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


def _error_location(extra: dict | None) -> str | None:
    """`file:line:col` from a window.onerror report's extra, if present."""
    if not isinstance(extra, dict) or not extra.get("filename"):
        return None
    return f"{extra.get('filename')}:{extra.get('lineno', '?')}:{extra.get('colno', '?')}"


def _stack_for_hashing(report: FrontendErrorReport) -> str:
    """Pick the most stable signal we have for pattern dedup.

    Prefer the stack (stable across invocations) over the message (sometimes
    has interpolated values). Errors with no real stack (the browser gave no
    Error object, e.g. Firefox "out of memory") hash on message + location.
    """
    if report.stack:
        return report.stack
    location = _error_location(report.extra)
    if location:
        return f"{report.message} @ {location}"
    return report.message


def _format_diagnostics(extra: dict | None) -> str | None:
    """One compact line from the client's diagnostics snapshot."""
    diag = extra.get("diagnostics") if isinstance(extra, dict) else None
    if not isinstance(diag, dict) or not diag:
        return None
    parts = [f"{k}={v}" for k, v in diag.items() if isinstance(v, (int, float, str, bool)) and v != ""]
    return "Diag: " + " ".join(parts) if parts else None


def _format_breadcrumbs(extra: dict | None) -> str | None:
    """The last few client breadcrumbs (what the page was doing before the error)."""
    crumbs = extra.get("breadcrumbs") if isinstance(extra, dict) else None
    if not isinstance(crumbs, list) or not crumbs:
        return None
    lines = []
    for c in crumbs[-MAX_SAMPLE_BREADCRUMBS:]:
        if isinstance(c, dict):
            lines.append(f"  {c.get('t', '?')}s [{c.get('category', '?')}] {str(c.get('message', ''))[:120]}")
    return "Trail:\n" + "\n".join(lines) if lines else None


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
    if not report.stack:
        location = _error_location(report.extra)
        if location:
            sample_parts.append(f"At: {location} (no stack from browser)")
    clean_url = sanitize_url(report.url)
    if clean_url:
        sample_parts.append(f"URL: {clean_url}")
    if report.user_agent:
        sample_parts.append(f"UA: {report.user_agent[:200]}")
    if report.release:
        sample_parts.append(f"Build: {report.release}")
    for line in (_format_diagnostics(report.extra), _format_breadcrumbs(report.extra)):
        if line:
            sample_parts.append(line)
    if report.stack and report.stack.strip() != (report.message or "").strip():
        stack_lines = report.stack.strip().splitlines()
        trimmed = "\n".join(stack_lines[:MAX_SAMPLE_STACK_LINES])
        if len(stack_lines) > MAX_SAMPLE_STACK_LINES:
            trimmed += f"\n  … {len(stack_lines) - MAX_SAMPLE_STACK_LINES} more frames"
        sample_parts.append("Stack:\n" + trimmed)
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
