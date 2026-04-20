"""Monitor orchestrator for the production error monitoring service.

This is the main entry point.  Run as::

    python -m backend.services.error_monitor.monitor

The ``ErrorMonitor.run()`` method executes the full pipeline:

* Monitor cycle (default) — query Cloud Logging, detect new patterns, send
  Discord alerts, check auto-resolution.
* Daily digest mode (``DIGEST_MODE=true``) — bundle 24h stats into one message.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from google.cloud import logging as cloud_logging
from google.cloud import secretmanager

from backend.services.error_monitor.config import (
    GCP_PROJECT,
    LOOKBACK_MINUTES,
    MAX_LOG_ENTRIES,
    MONITORED_CLOUD_FUNCTIONS,
    MONITORED_CLOUD_RUN_JOBS,
    MONITORED_CLOUD_RUN_SERVICES,
    MONITORED_GCE_INSTANCES,
    SPIKE_MIN_COUNT,
    SPIKE_MULTIPLIER,
    get_digest_mode,
    get_discord_webhook_secret_name,
    get_llm_enabled,
)
from backend.services.error_monitor.discord import (
    ErrorMonitorDiscord,
    format_auto_resolved_alert,
    format_daily_digest,
    format_incident_alert,
    format_new_pattern_alert,
    format_spike_alert,
)
from backend.services.error_monitor.firestore_adapter import (
    ErrorPatternsAdapter,
    PatternData,
    UpsertResult,
)
from backend.services.error_monitor.known_issues import should_ignore
from backend.services.error_monitor.llm_analysis import analyze_patterns, find_duplicate_patterns
from backend.services.error_monitor.normalizer import compute_pattern_hash, normalize_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maps log resource type → (label_key, service_name_list)
RESOURCE_TYPE_FILTERS: dict[str, tuple[str, list[str]]] = {
    "cloud_run_revision": ("resource.labels.service_name", MONITORED_CLOUD_RUN_SERVICES),
    "cloud_run_job": ("resource.labels.job_name", MONITORED_CLOUD_RUN_JOBS),
    "cloud_function": ("resource.labels.function_name", MONITORED_CLOUD_FUNCTIONS),
    "gce_instance": ('labels."compute.googleapis.com/resource_name"', MONITORED_GCE_INSTANCES),
}

#: Maps log resource type → canonical resource_type string stored in Firestore.
RESOURCE_TYPE_MAP: dict[str, str] = {
    "cloud_run_revision": "cloud_run_service",
    "cloud_run_job": "cloud_run_job",
    "cloud_function": "cloud_function",
    "gce_instance": "gce_instance",
}

# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def _build_log_filter(
    resource_type: str,
    resource_names: list[str],
    lookback_minutes: int,
) -> str:
    """Build a Cloud Logging filter string.

    Args:
        resource_type:    GCP resource type (e.g. ``"cloud_run_revision"``).
        resource_names:   List of service/job/function/instance names to include.
        lookback_minutes: How many minutes back to query.

    Returns:
        A filter string suitable for ``logging_client.list_entries(filter_=...)``.
    """
    label_key = RESOURCE_TYPE_FILTERS[resource_type][0]
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=lookback_minutes)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    name_clause = " OR ".join(f'{label_key}="{name}"' for name in resource_names)
    if len(resource_names) > 1:
        name_clause = f"({name_clause})"

    return (
        f'resource.type="{resource_type}" '
        f"severity>=ERROR "
        f'timestamp>="{cutoff_iso}" '
        f"{name_clause}"
    )


def _extract_message(entry: Any) -> str | None:
    """Extract the log message text from a Cloud Logging entry.

    Handles several payload shapes:

    * ``str`` payload — returned directly.
    * ``dict`` payload — tries ``"message"``, ``"textPayload"``, ``"error"``,
      ``"msg"`` keys in that order.
    * ``None`` payload — returns ``None``.

    Args:
        entry: A Cloud Logging ``LogEntry`` object.

    Returns:
        The message string, or ``None`` if not extractable.
    """
    payload = entry.payload
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload if payload else None
    if isinstance(payload, dict):
        for key in ("message", "textPayload", "error", "msg"):
            value = payload.get(key)
            if value:
                return str(value)
        return None
    # Fallback: convert to string representation
    text = str(payload)
    return text if text else None


def _extract_service_name(entry: Any, resource_type: str) -> str:
    """Extract the service/job/function/instance name from a log entry.

    Args:
        entry:         A Cloud Logging ``LogEntry`` object.
        resource_type: GCP log resource type string.

    Returns:
        The service name string (falls back to ``"unknown"`` if not found).
    """
    labels = getattr(entry.resource, "labels", {}) or {}
    entry_labels = getattr(entry, "labels", {}) or {}

    if resource_type == "cloud_run_revision":
        return labels.get("service_name") or "unknown"
    if resource_type == "cloud_run_job":
        return labels.get("job_name") or "unknown"
    if resource_type == "cloud_function":
        return labels.get("function_name") or "unknown"
    if resource_type == "gce_instance":
        # Prefer the human-readable resource name label
        name = entry_labels.get("compute.googleapis.com/resource_name")
        if name:
            return name
        return labels.get("instance_id") or "unknown"
    return "unknown"


def _classify_resource_type(log_resource_type: str) -> str:
    """Map a Cloud Logging resource type to the canonical type stored in Firestore.

    Args:
        log_resource_type: Raw GCP resource type (e.g. ``"cloud_run_revision"``).

    Returns:
        Canonical resource type string (falls back to the input if not in map).
    """
    return RESOURCE_TYPE_MAP.get(log_resource_type, log_resource_type)


def _group_by_pattern(entries: list[dict]) -> dict[str, dict]:
    """Group normalised log entries by their pattern hash.

    Args:
        entries: List of dicts with ``service``, ``normalized``, and ``message`` keys.

    Returns:
        Dict mapping ``pattern_id`` → group dict containing ``service``,
        ``resource_type``, ``normalized``, ``sample_message``, ``count``.
    """
    groups: dict[str, dict] = {}
    for entry in entries:
        service = entry["service"]
        normalized = entry["normalized"]
        pattern_id = compute_pattern_hash(service, normalized)

        if pattern_id not in groups:
            groups[pattern_id] = {
                "pattern_id": pattern_id,
                "service": service,
                "resource_type": entry.get("resource_type", "unknown"),
                "normalized": normalized,
                "sample_message": entry["message"],
                "count": 0,
            }
        groups[pattern_id]["count"] += 1
    return groups


def _rolling_average(rolling_counts: list[dict]) -> float:
    """Compute the mean count from a list of rolling_count dicts.

    Args:
        rolling_counts: List of ``{"count": int, ...}`` dicts.

    Returns:
        The arithmetic mean as a float, or ``0.0`` for an empty list.
    """
    if not rolling_counts:
        return 0.0
    total = sum(entry.get("count", 0) for entry in rolling_counts)
    return total / len(rolling_counts)


def _is_spike(current_count: int, rolling_counts: list[dict]) -> bool:
    """Determine whether ``current_count`` represents an error rate spike.

    A spike is detected when:
    - ``current_count`` >= ``SPIKE_MIN_COUNT`` (avoids noise from rare errors), AND
    - ``current_count`` > ``rolling_average * SPIKE_MULTIPLIER``

    Args:
        current_count:  Error count in the current monitoring window.
        rolling_counts: Historical rolling counts from Firestore.

    Returns:
        ``True`` if a spike is detected, ``False`` otherwise.
    """
    if not rolling_counts:
        return False
    if current_count < SPIKE_MIN_COUNT:
        return False
    avg = _rolling_average(rolling_counts)
    if avg == 0.0:
        return False
    return current_count > avg * SPIKE_MULTIPLIER


# ---------------------------------------------------------------------------
# ErrorMonitor class
# ---------------------------------------------------------------------------


class ErrorMonitor:
    """Orchestrates the full production error monitoring pipeline.

    On each run (typically every 15 minutes via Cloud Scheduler), it:

    1. Queries Cloud Logging for recent errors across all monitored services.
    2. Groups errors into patterns using normalisation + hashing.
    3. Upserts patterns to Firestore, tracking new vs. recurrent.
    4. Optionally uses an LLM to deduplicate patterns and create incidents.
    5. Sends Discord alerts for new patterns, incidents, and spikes.
    6. Auto-resolves patterns that have been silent long enough.
    """

    def __init__(self) -> None:
        self.logging_client = cloud_logging.Client(project=GCP_PROJECT)
        self.firestore_adapter = ErrorPatternsAdapter()
        self.discord = ErrorMonitorDiscord(webhook_url=self._get_discord_webhook())

    def _get_discord_webhook(self) -> str:
        """Retrieve the Discord webhook URL.

        Checks ``DISCORD_WEBHOOK_URL`` environment variable first; falls back
        to Secret Manager.

        Returns:
            Discord webhook URL string.
        """
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        if url:
            return url

        secret_name = get_discord_webhook_secret_name()
        client = secretmanager.SecretManagerServiceClient()
        secret_path = f"projects/{GCP_PROJECT}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(name=secret_path)
        return response.payload.data.decode("utf-8").strip()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Execute the monitor pipeline (digest or cycle mode)."""
        if get_digest_mode():
            self._run_daily_digest()
        else:
            self._run_monitor_cycle()

    # ------------------------------------------------------------------
    # Monitor cycle
    # ------------------------------------------------------------------

    def _run_monitor_cycle(self) -> None:
        """Execute one monitor cycle: collect → group → upsert → alert → auto-resolve."""
        now = datetime.now(tz=timezone.utc)
        now_iso = now.isoformat()

        # ── Step 1: Collect log entries ────────────────────────────────────
        raw_entries: list[dict] = []
        for resource_type, (_, resource_names) in RESOURCE_TYPE_FILTERS.items():
            log_filter = _build_log_filter(resource_type, resource_names, LOOKBACK_MINUTES)
            logger.info("Querying %s with filter: %s", resource_type, log_filter)
            try:
                entries = self.logging_client.list_entries(
                    filter_=log_filter,
                    max_results=MAX_LOG_ENTRIES,
                )
                for entry in entries:
                    message = _extract_message(entry)
                    if not message:
                        continue
                    service = _extract_service_name(entry, resource_type)
                    ignore_reason = should_ignore(service, message)
                    if ignore_reason:
                        logger.debug(
                            "Ignoring entry from %s (%s): %s",
                            service,
                            ignore_reason.pattern_name,
                            ignore_reason.reason,
                        )
                        continue
                    normalized = normalize_message(message)
                    if not normalized:
                        continue
                    raw_entries.append(
                        {
                            "service": service,
                            "resource_type": _classify_resource_type(resource_type),
                            "normalized": normalized,
                            "message": message,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to query %s logs: %s", resource_type, exc)

        logger.info("Collected %d log entries after filtering", len(raw_entries))

        # ── Step 2: Group by pattern ───────────────────────────────────────
        groups = _group_by_pattern(raw_entries)
        logger.info("Grouped into %d distinct patterns", len(groups))

        # ── Step 3: Upsert to Firestore ────────────────────────────────────
        new_pattern_ids: list[str] = []
        spike_patterns: list[dict] = []

        for pattern_id, group in groups.items():
            data = PatternData(
                pattern_id=pattern_id,
                service=group["service"],
                resource_type=group["resource_type"],
                normalized_message=group["normalized"],
                sample_message=group["sample_message"],
                count=group["count"],
                timestamp=now,
            )
            result: UpsertResult = self.firestore_adapter.upsert_pattern(data)
            if result.is_new:
                new_pattern_ids.append(pattern_id)
                logger.info("New pattern detected: %s (%s)", pattern_id, group["service"])

            # Check for spike on existing patterns
            existing = self.firestore_adapter.get_pattern(pattern_id)
            if existing and not result.is_new:
                rolling = existing.get("rolling_counts") or []
                if _is_spike(group["count"], rolling):
                    spike_patterns.append(
                        {
                            "service": group["service"],
                            "normalized_message": group["normalized"],
                            "current_count": group["count"],
                            "rolling_average": _rolling_average(rolling),
                        }
                    )
                    logger.info("Spike detected for pattern %s", pattern_id)

        # ── Step 3b: Pick up out-of-band patterns (e.g. frontend crashes) ──
        # Patterns written via POST /api/client-errors land directly in
        # Firestore without going through the log-scraping pipeline, so they
        # won't be in new_pattern_ids / groups from Steps 1-3. Ensure any
        # pattern with status="new" and alerted_at=None still gets a Discord
        # alert on this cycle.
        try:
            unalerted = self.firestore_adapter.get_unalerted_new_patterns()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to query unalerted patterns: %s", exc)
            unalerted = []

        for pattern in unalerted:
            pid = pattern.get("pattern_id")
            if not pid or pid in groups:
                continue
            groups[pid] = {
                "service": pattern.get("service", "unknown"),
                "resource_type": pattern.get("resource_type", "unknown"),
                "normalized": pattern.get("normalized_message", ""),
                "sample_message": pattern.get("sample_message", ""),
                "count": pattern.get("total_count", 1),
            }
            if pid not in new_pattern_ids:
                new_pattern_ids.append(pid)
                logger.info(
                    "Picked up out-of-band pattern %s (%s)",
                    pid,
                    pattern.get("service", "unknown"),
                )

        # If nothing to alert on this cycle, go straight to auto-resolve.
        if not new_pattern_ids and not spike_patterns:
            logger.info("No new or unalerted patterns this cycle; checking auto-resolve.")
            self._check_auto_resolve()
            return

        # ── Step 4: LLM dedup ─────────────────────────────────────────────
        if new_pattern_ids and get_llm_enabled():
            new_patterns_data = [
                {
                    "service": groups[pid]["service"],
                    "normalized_message": groups[pid]["normalized"],
                    "count": groups[pid]["count"],
                }
                for pid in new_pattern_ids
            ]
            existing_patterns = self.firestore_adapter.get_active_patterns()
            # Exclude newly added patterns from the canonical list
            canonical = [p for p in existing_patterns if p.get("pattern_id") not in new_pattern_ids]

            if canonical:
                duplicate_groups = find_duplicate_patterns(new_patterns_data, canonical)
                for dup_group in duplicate_groups:
                    # Merge each duplicate new pattern into the canonical
                    canonical_pattern = canonical[dup_group.canonical_index]
                    canonical_id = canonical_pattern.get("pattern_id", "")
                    for dup_idx in dup_group.duplicate_indices:
                        if dup_idx < len(new_pattern_ids):
                            dup_id = new_pattern_ids[dup_idx]
                            logger.info(
                                "Merging duplicate pattern %s into %s: %s",
                                dup_id,
                                canonical_id,
                                dup_group.reason,
                            )
                            self.firestore_adapter.merge_pattern(dup_id, canonical_id, dup_group.reason)
                            # Remove from new_pattern_ids since it was merged
                            new_pattern_ids = [pid for pid in new_pattern_ids if pid != dup_id]

        # ── Step 5: LLM incident analysis ─────────────────────────────────
        incident_pattern_ids: set[str] = set()

        if len(new_pattern_ids) >= 2 and get_llm_enabled():
            patterns_for_analysis = [
                {
                    "service": groups[pid]["service"],
                    "normalized_message": groups[pid]["normalized"],
                    "count": groups[pid]["count"],
                }
                for pid in new_pattern_ids
                if pid in groups
            ]
            analysis = analyze_patterns(patterns_for_analysis)

            if analysis and analysis.incidents:
                for incident in analysis.incidents:
                    incident_pids = [
                        new_pattern_ids[idx]
                        for idx in incident.pattern_indices
                        if idx < len(new_pattern_ids)
                    ]
                    if not incident_pids:
                        continue

                    incident_id = self.firestore_adapter.create_incident(
                        title=incident.title,
                        root_cause=incident.root_cause or "",
                        severity=incident.severity,
                        suggested_fix=incident.suggested_fix or "",
                        primary_service=incident.primary_service,
                        pattern_ids=incident_pids,
                        used_llm=analysis.used_llm,
                    )
                    logger.info(
                        "Created incident %s (%s) with %d patterns",
                        incident_id,
                        incident.severity,
                        len(incident_pids),
                    )

                    # Build pattern list for the Discord alert
                    pattern_dicts = [
                        {
                            "service": groups[pid]["service"],
                            "normalized_message": groups[pid]["normalized"],
                            "count": groups[pid]["count"],
                        }
                        for pid in incident_pids
                        if pid in groups
                    ]
                    msg = format_incident_alert(
                        title=incident.title,
                        severity=incident.severity,
                        root_cause=incident.root_cause or "Unknown",
                        suggested_fix=incident.suggested_fix or "Investigate logs",
                        primary_service=incident.primary_service,
                        patterns=pattern_dicts,
                    )
                    if self.discord.send_message(msg):
                        for pid in incident_pids:
                            try:
                                self.firestore_adapter.update_pattern_alerted(pid)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("Could not update alerted_at for %s: %s", pid, exc)

                    incident_pattern_ids.update(incident_pids)

        # ── Step 6: Individual alerts for new patterns not in incidents ────
        for pattern_id in new_pattern_ids:
            if pattern_id in incident_pattern_ids:
                continue
            if pattern_id not in groups:
                continue
            group = groups[pattern_id]

            # Fetch fresh copy to get first_seen timestamp
            existing = self.firestore_adapter.get_pattern(pattern_id)
            first_seen = existing.get("first_seen", now_iso) if existing else now_iso

            msg = format_new_pattern_alert(
                service=group["service"],
                resource_type=group["resource_type"],
                normalized_message=group["normalized"],
                sample_message=group["sample_message"],
                count=group["count"],
                first_seen=first_seen,
            )
            if self.discord.send_message(msg):
                try:
                    self.firestore_adapter.update_pattern_alerted(pattern_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not update alerted_at for %s: %s", pattern_id, exc)

        # ── Step 7: Spike alerts ───────────────────────────────────────────
        for spike in spike_patterns:
            msg = format_spike_alert(
                service=spike["service"],
                normalized_message=spike["normalized_message"],
                current_count=spike["current_count"],
                rolling_average=spike["rolling_average"],
            )
            self.discord.send_message(msg)

        # ── Step 8: Auto-resolve check ─────────────────────────────────────
        self._check_auto_resolve()

    # ------------------------------------------------------------------
    # Auto-resolve helper
    # ------------------------------------------------------------------

    def _check_auto_resolve(self) -> None:
        """Check all active patterns for auto-resolution and send a consolidated alert."""
        patterns = self.firestore_adapter.get_patterns_for_auto_resolve()
        resolved: list[dict] = []

        for pattern in patterns:
            pattern_id = pattern.get("pattern_id", "")
            hours_silent = self.firestore_adapter.check_auto_resolve(pattern)
            if hours_silent is not None:
                logger.info(
                    "Auto-resolving pattern %s (silent %.1f hours)", pattern_id, hours_silent
                )
                self.firestore_adapter.auto_resolve_pattern(pattern_id, round(hours_silent, 1))
                resolved.append(
                    {
                        "service": pattern.get("service", "unknown"),
                        "message": pattern.get("normalized_message", ""),
                        "hours_silent": round(hours_silent, 1),
                    }
                )

        if resolved:
            msg = format_auto_resolved_alert(resolved)
            self.discord.send_message(msg)

    # ------------------------------------------------------------------
    # Daily digest
    # ------------------------------------------------------------------

    def _run_daily_digest(self) -> None:
        """Gather 24h stats and send a daily digest to Discord."""
        # Optional dedup sweep (best-effort; LLM errors are non-fatal)
        if get_llm_enabled():
            try:
                active = self.firestore_adapter.get_active_patterns()
                if len(active) >= 2:
                    mid = len(active) // 2
                    duplicates = find_duplicate_patterns(active[:mid], active[mid:])
                    for dup in duplicates:
                        for idx in dup.duplicate_indices:
                            target_half = active[mid:]
                            source_half = active[:mid]
                            if idx < len(target_half) and dup.canonical_index < len(source_half):
                                self.firestore_adapter.merge_pattern(
                                    target_half[idx]["pattern_id"],
                                    source_half[dup.canonical_index]["pattern_id"],
                                    dup.reason,
                                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Digest dedup sweep failed (non-fatal): %s", exc)

        # Gather stats from active patterns
        active_patterns = self.firestore_adapter.get_active_patterns()
        per_service_counts: dict[str, int] = {}
        new_count = 0
        total_errors = 0

        for pattern in active_patterns:
            service = pattern.get("service", "unknown")
            count = pattern.get("total_count", 0)
            per_service_counts[service] = per_service_counts.get(service, 0) + count
            total_errors += count
            if pattern.get("status") == "new":
                new_count += 1

        # Count recently auto-resolved patterns
        resolved_patterns = self.firestore_adapter.get_patterns_for_auto_resolve()
        resolved_count = sum(
            1
            for p in resolved_patterns
            if self.firestore_adapter.check_auto_resolve(p) is not None
        )

        msg = format_daily_digest(
            total_errors=total_errors,
            new_patterns=new_count,
            resolved_patterns=resolved_count,
            active_patterns=len(active_patterns),
            per_service_counts=per_service_counts,
        )
        self.discord.send_message(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Configure logging and run the error monitor."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    monitor = ErrorMonitor()
    monitor.run()


if __name__ == "__main__":
    main()
