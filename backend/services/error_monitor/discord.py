"""Discord alert client for the production error monitoring service.

Provides formatting functions for each alert type, and an ErrorMonitorDiscord
webhook client that rate-limits outbound messages to MAX_DISCORD_MESSAGES_PER_RUN
per monitor run.

Usage:
    from backend.services.error_monitor.discord import (
        ErrorMonitorDiscord,
        format_new_pattern_alert,
        format_incident_alert,
        format_auto_resolved_alert,
        format_spike_alert,
        format_daily_digest,
    )

    discord = ErrorMonitorDiscord(
        webhook_url="https://discord.com/api/webhooks/...",        # #alerts
        noise_webhook_url="https://discord.com/api/webhooks/...",  # #noise
    )
    discord.send_message(format_new_pattern_alert(...))        # a real problem
    discord.send_noise_message(format_auto_resolved_alert(...))  # informational
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from backend.services.error_monitor.config import (
    DISCORD_MAX_MESSAGE_LENGTH,
    MAX_DISCORD_MESSAGES_PER_RUN,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_EMOJI: dict[str, str] = {
    "P0": "🚨",
    "P1": "🔴",
    "P2": "⚠️",
    "P3": "🟡",
}

_EASTERN = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _now_eastern() -> str:
    """Return the current Eastern time as 'HH:MM AM/PM ET' (DST-aware)."""
    now = datetime.now(tz=_EASTERN)
    return now.strftime("%I:%M %p ET").lstrip("0")


def _truncate(msg: str) -> str:
    """Truncate *msg* to DISCORD_MAX_MESSAGE_LENGTH characters."""
    if len(msg) <= DISCORD_MAX_MESSAGE_LENGTH:
        return msg
    suffix = "…"
    return msg[: DISCORD_MAX_MESSAGE_LENGTH - len(suffix)] + suffix


# ---------------------------------------------------------------------------
# Formatting functions
# ---------------------------------------------------------------------------


def format_new_pattern_alert(
    service: str,
    resource_type: str,
    normalized_message: str,
    sample_message: str,
    count: int,
    first_seen: str,
) -> str:
    """Format a Discord alert for a newly-detected error pattern.

    Args:
        service: Name of the service emitting the error.
        resource_type: GCP resource type (e.g. 'cloud_run_service', 'gce_instance').
        normalized_message: Normalised error pattern string.
        sample_message: A representative raw log message.
        count: Number of occurrences seen so far.
        first_seen: ISO 8601 timestamp when the pattern was first seen.

    Returns:
        Formatted Discord message string, truncated to 2000 chars.
    """
    lines = [
        f"🔴 **New Error Pattern Detected**",
        f"",
        f"**Service:** {service}",
        f"**Resource type:** {resource_type}",
        f"**Pattern:** {normalized_message}",
        f"**Sample:** {sample_message}",
        f"**Count:** {count}",
        f"**First seen:** {first_seen}",
        f"**Alerted at:** {_now_eastern()}",
        f"",
        f"To investigate: `/prod-investigate {service} \"{normalized_message}\"`",
    ]
    return _truncate("\n".join(lines))


def format_incident_alert(
    title: str,
    severity: str,
    root_cause: str,
    suggested_fix: str,
    primary_service: str,
    patterns: list[dict],
) -> str:
    """Format a Discord alert for an analysed incident (LLM-generated).

    Args:
        title: Short incident title.
        severity: Severity level — one of P0, P1, P2, P3.
        root_cause: Human-readable root-cause analysis.
        suggested_fix: Recommended remediation steps.
        primary_service: The main service involved in the incident.
        patterns: List of dicts, each with keys: service, normalized_message, count.
                  Only the first 5 are shown; normalized_message is truncated to 80 chars.

    Returns:
        Formatted Discord message string, truncated to 2000 chars.
    """
    emoji = SEVERITY_EMOJI.get(severity, "⚠️")
    lines = [
        f"{emoji} **[{severity}] {title}**",
        f"",
        f"**Primary service:** {primary_service}",
        f"**Root cause:** {root_cause}",
        f"**Suggested fix:** {suggested_fix}",
    ]

    shown_patterns = patterns[:5]
    if shown_patterns:
        lines.append("")
        lines.append(f"**Patterns ({len(shown_patterns)} of {len(patterns)}):**")
        for p in shown_patterns:
            svc = p.get("service", "unknown")
            msg = p.get("normalized_message", "")[:80]
            cnt = p.get("count", 0)
            lines.append(f"  • `{svc}` — {msg} ({cnt}x)")

    lines.append("")
    lines.append(f"**Alerted at:** {_now_eastern()}")

    return _truncate("\n".join(lines))


def format_auto_resolved_alert(resolved_patterns: list[dict]) -> str:
    """Format a consolidated auto-resolved alert.

    Args:
        resolved_patterns: List of dicts, each with keys: service, message, hours_silent.
                           Only the first 8 are shown; message is truncated to 60 chars.

    Returns:
        Formatted Discord message string, truncated to 2000 chars.
    """
    lines = [
        f"✅ **Patterns Auto-Resolved**",
        f"",
    ]

    shown = resolved_patterns[:8]
    if shown:
        for p in shown:
            svc = p.get("service", "unknown")
            msg = p.get("message", "")[:60]
            hours = p.get("hours_silent", 0)
            lines.append(f"  • `{svc}` — {msg} (silent {hours}h)")
    else:
        lines.append("  *(no patterns)*")

    if len(resolved_patterns) > 8:
        lines.append(f"  … and {len(resolved_patterns) - 8} more")

    lines.append("")
    lines.append(f"**Resolved at:** {_now_eastern()}")

    return _truncate("\n".join(lines))


def format_spike_alert(
    service: str,
    normalized_message: str,
    current_count: int,
    rolling_average: float,
) -> str:
    """Format a Discord alert for an error rate spike.

    Args:
        service: Name of the service.
        normalized_message: Normalised error pattern string.
        current_count: Error count in the current window.
        rolling_average: Baseline rolling average count.

    Returns:
        Formatted Discord message string, truncated to 2000 chars.
    """
    if rolling_average > 0:
        multiplier = current_count / rolling_average
        multiplier_str = f"{multiplier:.1f}x"
    else:
        multiplier_str = "∞x"

    lines = [
        f"⚠️ **Error Rate Spike Detected**",
        f"",
        f"**Service:** {service}",
        f"**Pattern:** {normalized_message}",
        f"**Current count:** {current_count}",
        f"**Normal (rolling avg):** {rolling_average}",
        f"**Multiplier:** {multiplier_str} above normal",
        f"",
        f"**Alerted at:** {_now_eastern()}",
    ]
    return _truncate("\n".join(lines))


def format_daily_digest(
    total_errors: int,
    new_patterns: int,
    resolved_patterns: int,
    active_patterns: int,
    per_service_counts: dict,
) -> str:
    """Format the daily 24h error digest.

    Args:
        total_errors: Total error count in the last 24 hours.
        new_patterns: Number of new error patterns detected.
        resolved_patterns: Number of patterns auto-resolved.
        active_patterns: Total number of currently active patterns.
        per_service_counts: Mapping of service name → error count.
                            Displayed sorted by count descending.

    Returns:
        Formatted Discord message string, truncated to 2000 chars.
    """
    lines = [
        f"📊 **Daily Error Digest (24h)**",
        f"",
        f"**Total errors:** {total_errors}",
        f"**New patterns:** {new_patterns}",
        f"**Resolved patterns:** {resolved_patterns}",
        f"**Active patterns:** {active_patterns}",
    ]

    if per_service_counts:
        lines.append("")
        lines.append("**Per-service breakdown:**")
        sorted_services = sorted(per_service_counts.items(), key=lambda kv: kv[1], reverse=True)
        for svc, cnt in sorted_services:
            lines.append(f"  • `{svc}`: {cnt}")

    lines.append("")
    lines.append(f"**Generated at:** {_now_eastern()}")

    return _truncate("\n".join(lines))


# ---------------------------------------------------------------------------
# Discord webhook client
# ---------------------------------------------------------------------------


class ErrorMonitorDiscord:
    """Webhook client for sending error monitor messages to Discord.

    Two channels:

    * **#alerts** (``webhook_url``, :meth:`send_message`) — signal only: real
      problems someone should look at (new patterns, spikes, incidents).
    * **#noise** (``noise_webhook_url``, :meth:`send_noise_message`) —
      informational posts (auto-resolved summaries, daily digest). If no noise
      webhook is configured, or a noise post fails, the message falls back to
      the alert webhook so it is never silently dropped.

    Each channel has its own per-run rate limit of MAX_DISCORD_MESSAGES_PER_RUN,
    so noise can never eat the alert budget during an incident window.

    Attributes:
        webhook_url: The #alerts Discord incoming webhook URL.
        noise_webhook_url: The #noise webhook URL (None → use webhook_url).
        messages_sent: #alerts messages attempted this run (success or failure).
        noise_messages_sent: #noise messages attempted this run.
    """

    def __init__(self, webhook_url: str, noise_webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url
        self.noise_webhook_url = noise_webhook_url
        self.messages_sent: int = 0
        self.noise_messages_sent: int = 0

    @staticmethod
    def _post(url: str, content: str) -> bool:
        try:
            response = requests.post(url, json={"content": content}, timeout=30)
            response.raise_for_status()
            logger.debug("Discord message sent successfully.")
            return True
        except Exception as exc:
            logger.error("Failed to send Discord message: %s", exc)
            return False

    def send_message(self, content: str) -> bool:
        """Send a message to the #alerts webhook (real problems only).

        Rate-limits: if messages_sent >= MAX_DISCORD_MESSAGES_PER_RUN, the
        message is silently skipped and False is returned without incrementing
        the counter.

        Args:
            content: The message text to send (should be pre-formatted and
                     pre-truncated to ≤2000 chars).

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        if self.messages_sent >= MAX_DISCORD_MESSAGES_PER_RUN:
            logger.warning(
                "Discord rate limit reached (%d messages sent this run); skipping message.",
                self.messages_sent,
            )
            return False

        try:
            return self._post(self.webhook_url, content)
        finally:
            self.messages_sent += 1

    def send_noise_message(self, content: str) -> bool:
        """Send an informational message to the #noise webhook.

        Falls back to the #alerts webhook if no noise webhook is configured or
        the noise post fails. Rate-limited separately from :meth:`send_message`.

        Returns:
            True if the message was delivered to either channel.
        """
        if self.noise_messages_sent >= MAX_DISCORD_MESSAGES_PER_RUN:
            logger.warning(
                "Discord noise rate limit reached (%d messages sent this run); skipping message.",
                self.noise_messages_sent,
            )
            return False

        try:
            noise_url = self.noise_webhook_url or self.webhook_url
            if self._post(noise_url, content):
                return True
            if noise_url != self.webhook_url:
                logger.warning("Noise webhook post failed; falling back to the alert webhook.")
                return self._post(self.webhook_url, content)
            return False
        finally:
            self.noise_messages_sent += 1
