"""Firestore adapter for the production error monitoring service.

Handles all CRUD for three Firestore collections:
  - error_patterns:  normalized error patterns (pattern_id as doc ID)
  - error_incidents: groups of related patterns (incident_id as doc ID)
  - discord_alerts:  audit trail for Discord messages (alert_id as doc ID)
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.error_monitor.config import (
    AUTO_RESOLVE_FALLBACK_HOURS,
    AUTO_RESOLVE_MAX_HOURS,
    AUTO_RESOLVE_MIN_HOURS,
    AUTO_RESOLVE_MULTIPLIER,
    ROLLING_WINDOW_DAYS,
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PatternData:
    """Input data for creating or updating an error pattern."""

    pattern_id: str
    service: str
    resource_type: str
    normalized_message: str
    sample_message: str
    count: int
    timestamp: datetime


@dataclass
class UpsertResult:
    """Result of an upsert_pattern operation."""

    pattern_id: str
    is_new: bool
    previous_status: str | None = None


# ---------------------------------------------------------------------------
# Collection names
# ---------------------------------------------------------------------------

_COL_PATTERNS = "error_patterns"
_COL_INCIDENTS = "error_incidents"
_COL_ALERTS = "discord_alerts"

# Statuses that can be queried as "active"
_ACTIVE_STATUSES = ["new", "acknowledged", "known"]

# Statuses from which a pattern is reactivated when the error recurs
_REACTIVATION_STATUSES = {"auto_resolved", "fixed"}

# Statuses eligible for auto-resolve checking
_AUTO_RESOLVE_ELIGIBLE = {"new", "acknowledged"}


def _utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    """Serialise a datetime to ISO-8601 string."""
    return dt.isoformat()


def _generate_id(prefix: str, fmt: str) -> str:
    """Generate a time-stamped ID with 8 hex random suffix.

    Args:
        prefix: e.g. "inc" or "alert"
        fmt:    strftime format string applied to utcnow(), e.g. "%Y%m%d_%H%M"
    """
    ts = _utcnow().strftime(fmt)
    suffix = secrets.token_hex(4)  # 8 hex chars
    return f"{prefix}_{ts}_{suffix}"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ErrorPatternsAdapter:
    """Firestore CRUD adapter for error monitoring collections.

    Args:
        db: Optional Firestore client.  If omitted, a default client for the
            ``nomadkaraoke`` GCP project is constructed on first use.
    """

    def __init__(self, db=None) -> None:
        if db is None:
            from google.cloud import firestore  # type: ignore[import]

            db = firestore.Client(project="nomadkaraoke")
        self._db = db

    # ------------------------------------------------------------------
    # Patterns — read
    # ------------------------------------------------------------------

    def get_pattern(self, pattern_id: str) -> dict | None:
        """Return the pattern document as a dict, or None if it doesn't exist.

        Args:
            pattern_id: Firestore document ID in the ``error_patterns`` collection.
        """
        snap = self._db.collection(_COL_PATTERNS).document(pattern_id).get()
        if not snap.exists:
            return None
        return snap.to_dict()

    def get_active_patterns(self) -> list[dict]:
        """Return all patterns with status in [new, acknowledged, known]."""
        query = self._db.collection(_COL_PATTERNS).where(
            "status", "in", _ACTIVE_STATUSES
        )
        return [snap.to_dict() for snap in query.stream()]

    def get_patterns_for_auto_resolve(self) -> list[dict]:
        """Return active patterns eligible for auto-resolution (new or acknowledged)."""
        query = self._db.collection(_COL_PATTERNS).where(
            "status", "in", list(_AUTO_RESOLVE_ELIGIBLE)
        )
        return [snap.to_dict() for snap in query.stream()]

    def get_unalerted_new_patterns(self) -> list[dict]:
        """Return patterns with status="new" and alerted_at=None.

        These are typically patterns written by a path other than the current
        monitor cycle — most notably frontend crashes ingested via
        ``POST /api/client-errors``. The monitor uses this to ensure out-of-band
        patterns still get a Discord alert on the next scheduled run.
        """
        query = self._db.collection(_COL_PATTERNS).where(
            "status", "==", "new"
        ).where("alerted_at", "==", None)
        return [snap.to_dict() for snap in query.stream()]

    # ------------------------------------------------------------------
    # Patterns — upsert
    # ------------------------------------------------------------------

    def upsert_pattern(self, data: PatternData) -> UpsertResult:
        """Create a new pattern or update an existing one.

        New pattern:
        - status="new", severity="P3"
        - Initialize ``rolling_counts`` with one entry for this run.

        Existing pattern:
        - Increment ``total_count`` and append to ``rolling_counts``.
        - If status was ``auto_resolved`` or ``fixed`` → reactivate (set status="new").
        - Prune ``rolling_counts`` entries older than ROLLING_WINDOW_DAYS.

        Returns:
            UpsertResult with ``is_new=True`` for brand-new patterns *and* for
            reactivated patterns (auto_resolved → new, fixed → new).
        """
        doc_ref = self._db.collection(_COL_PATTERNS).document(data.pattern_id)
        snap = doc_ref.get()
        ts_iso = _iso(data.timestamp)

        new_rolling_entry = {"ts": ts_iso, "count": data.count}

        if not snap.exists:
            # ── brand new pattern ──────────────────────────────────────────
            doc_ref.set(
                {
                    "pattern_id": data.pattern_id,
                    "service": data.service,
                    "resource_type": data.resource_type,
                    "normalized_message": data.normalized_message,
                    "sample_message": data.sample_message,
                    "status": "new",
                    "severity": "P3",
                    "total_count": data.count,
                    "rolling_counts": [new_rolling_entry],
                    "first_seen": ts_iso,
                    "last_seen": ts_iso,
                    "alerted_at": None,
                    "created_at": ts_iso,
                    "updated_at": ts_iso,
                }
            )
            return UpsertResult(pattern_id=data.pattern_id, is_new=True)

        # ── existing pattern ───────────────────────────────────────────────
        existing = snap.to_dict()
        prev_status = existing.get("status", "new")

        rolling_counts: list[dict] = list(existing.get("rolling_counts") or [])
        rolling_counts.append(new_rolling_entry)
        rolling_counts = self._prune_rolling_counts(rolling_counts)

        update: dict[str, Any] = {
            "total_count": existing.get("total_count", 0) + data.count,
            "rolling_counts": rolling_counts,
            "last_seen": ts_iso,
            "sample_message": data.sample_message,
            "updated_at": ts_iso,
        }

        # Reactivation: pattern was considered resolved/fixed but error recurred
        is_new = False
        if prev_status in _REACTIVATION_STATUSES:
            update["status"] = "new"
            update["alerted_at"] = None
            is_new = True

        doc_ref.update(update)
        return UpsertResult(
            pattern_id=data.pattern_id,
            is_new=is_new,
            previous_status=prev_status,
        )

    # ------------------------------------------------------------------
    # Patterns — status changes
    # ------------------------------------------------------------------

    def auto_resolve_pattern(self, pattern_id: str, hours_silent: float) -> None:
        """Mark a pattern as auto-resolved.

        Args:
            pattern_id:   Firestore document ID.
            hours_silent: How many hours the pattern has been silent.
        """
        self._db.collection(_COL_PATTERNS).document(pattern_id).update(
            {
                "status": "auto_resolved",
                "auto_resolve_hours_silent": hours_silent,
                "auto_resolved_at": _iso(_utcnow()),
                "updated_at": _iso(_utcnow()),
            }
        )

    def resolve_pattern(
        self,
        pattern_id: str,
        pr_url: str | None = None,
        note: str = "",
    ) -> None:
        """Mark a pattern as fixed (human-resolved).

        Args:
            pattern_id: Firestore document ID.
            pr_url:     Optional URL of the PR that fixed this pattern.
            note:       Optional human-readable note about the fix.
        """
        now_iso = _iso(_utcnow())
        self._db.collection(_COL_PATTERNS).document(pattern_id).update(
            {
                "status": "fixed",
                "fixed_by": {
                    "pr_url": pr_url,
                    "note": note,
                    "fixed_at": now_iso,
                },
                "updated_at": now_iso,
            }
        )

    def update_pattern_alerted(self, pattern_id: str) -> None:
        """Set the ``alerted_at`` timestamp on a pattern.

        Called after a Discord alert has been successfully sent for the pattern.
        """
        self._db.collection(_COL_PATTERNS).document(pattern_id).update(
            {
                "alerted_at": _iso(_utcnow()),
                "updated_at": _iso(_utcnow()),
            }
        )

    def merge_pattern(self, source_id: str, target_id: str, reason: str) -> None:
        """Merge *source* into *target* by muting the source pattern.

        Args:
            source_id: Pattern to mute.
            target_id: Pattern that the source is being merged into.
            reason:    Human-readable rationale for the merge.
        """
        self._db.collection(_COL_PATTERNS).document(source_id).update(
            {
                "status": "muted",
                "merged_into": target_id,
                "merge_reason": reason,
                "updated_at": _iso(_utcnow()),
            }
        )

    # ------------------------------------------------------------------
    # Auto-resolve logic
    # ------------------------------------------------------------------

    def check_auto_resolve(self, pattern: dict) -> float | None:
        """Determine whether *pattern* should be auto-resolved.

        Computes the frequency-aware silence threshold and compares it against
        how long the pattern has been silent.

        Args:
            pattern: A pattern document dict (as returned by get_pattern).

        Returns:
            The number of hours the pattern has been silent if the threshold is
            exceeded, or ``None`` if the pattern should not yet be resolved.
        """
        status = pattern.get("status", "")
        if status not in _AUTO_RESOLVE_ELIGIBLE:
            return None

        last_seen_str = pattern.get("last_seen")
        if not last_seen_str:
            return None

        last_seen = datetime.fromisoformat(last_seen_str)
        now = datetime.now(tz=timezone.utc)

        # Ensure both datetimes are tz-aware for subtraction
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)

        hours_silent = (now - last_seen).total_seconds() / 3600.0

        rolling_counts = pattern.get("rolling_counts") or []
        threshold = self._compute_resolve_threshold(rolling_counts)

        if hours_silent >= threshold:
            return hours_silent
        return None

    def _compute_resolve_threshold(self, rolling_counts: list[dict]) -> float:
        """Compute the frequency-aware auto-resolve threshold in hours.

        Algorithm:
        1. If fewer than 3 data points → return AUTO_RESOLVE_FALLBACK_HOURS (48).
        2. Compute mean interval between consecutive timestamps.
        3. threshold = mean_interval_hours * AUTO_RESOLVE_MULTIPLIER.
        4. Clamp result to [AUTO_RESOLVE_MIN_HOURS, AUTO_RESOLVE_MAX_HOURS].

        Args:
            rolling_counts: List of ``{"ts": ISO-string, "count": int}`` dicts.

        Returns:
            Threshold in hours (float).
        """
        if len(rolling_counts) < 3:
            return float(AUTO_RESOLVE_FALLBACK_HOURS)

        # Parse timestamps and sort ascending
        timestamps: list[datetime] = []
        for entry in rolling_counts:
            ts = datetime.fromisoformat(entry["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            timestamps.append(ts)
        timestamps.sort()

        # Compute pairwise intervals
        intervals_hours: list[float] = []
        for i in range(1, len(timestamps)):
            delta = (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600.0
            intervals_hours.append(delta)

        if not intervals_hours:
            return float(AUTO_RESOLVE_FALLBACK_HOURS)

        mean_interval = sum(intervals_hours) / len(intervals_hours)
        threshold = mean_interval * AUTO_RESOLVE_MULTIPLIER

        # Clamp to configured bounds
        threshold = max(float(AUTO_RESOLVE_MIN_HOURS), threshold)
        threshold = min(float(AUTO_RESOLVE_MAX_HOURS), threshold)
        return threshold

    def _prune_rolling_counts(self, rolling_counts: list[dict]) -> list[dict]:
        """Remove rolling_count entries older than ROLLING_WINDOW_DAYS.

        Args:
            rolling_counts: List of ``{"ts": ISO-string, "count": int}`` dicts.

        Returns:
            Filtered list with only recent entries.
        """
        if not rolling_counts:
            return []

        cutoff = _utcnow() - timedelta(days=ROLLING_WINDOW_DAYS)
        result = []
        for entry in rolling_counts:
            ts = datetime.fromisoformat(entry["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                result.append(entry)
        return result

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    def create_incident(
        self,
        title: str,
        root_cause: str,
        severity: str,
        suggested_fix: str,
        primary_service: str,
        pattern_ids: list[str],
        used_llm: bool,
    ) -> str:
        """Create a new incident document and link associated patterns.

        Args:
            title:           Short human-readable title.
            root_cause:      LLM-generated or manual root-cause description.
            severity:        Severity string (e.g. "P1", "P2", "P3").
            suggested_fix:   Suggested remediation steps.
            primary_service: The primary affected service.
            pattern_ids:     List of pattern_ids that belong to this incident.
            used_llm:        Whether LLM analysis was used to generate this incident.

        Returns:
            The generated incident_id string.
        """
        incident_id = _generate_id("inc", "%Y%m%d_%H%M")
        now_iso = _iso(_utcnow())

        self._db.collection(_COL_INCIDENTS).document(incident_id).set(
            {
                "incident_id": incident_id,
                "title": title,
                "root_cause": root_cause,
                "severity": severity,
                "suggested_fix": suggested_fix,
                "primary_service": primary_service,
                "pattern_ids": pattern_ids,
                "used_llm": used_llm,
                "created_at": now_iso,
                "updated_at": now_iso,
                "status": "open",
            }
        )

        # Link each pattern back to this incident
        for pattern_id in pattern_ids:
            self._db.collection(_COL_PATTERNS).document(pattern_id).update(
                {
                    "incident_id": incident_id,
                    "updated_at": now_iso,
                }
            )

        return incident_id

    # ------------------------------------------------------------------
    # Discord audit trail
    # ------------------------------------------------------------------

    def log_discord_alert(
        self,
        alert_type: str,
        content: str,
        success: bool,
        metadata: dict | None = None,
    ) -> None:
        """Write an audit record to the ``discord_alerts`` collection.

        Args:
            alert_type: Category of alert (e.g. "new_pattern", "digest").
            content:    The message body that was (or would have been) sent.
            success:    Whether the Discord webhook call succeeded.
            metadata:   Optional additional data (pattern_ids, incident_id, etc.).
        """
        alert_id = _generate_id("alert", "%Y%m%d_%H%M%S")
        now_iso = _iso(_utcnow())

        self._db.collection(_COL_ALERTS).document(alert_id).set(
            {
                "alert_id": alert_id,
                "alert_type": alert_type,
                "content": content,
                "success": success,
                "metadata": metadata or {},
                "created_at": now_iso,
            }
        )
