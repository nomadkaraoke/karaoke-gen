"""Customer-delivery guard for Dropbox distribution.

Why this exists
---------------
Job ``dd892383`` (NOMAD-1800, 2026-10-22) finished while the business Dropbox was
at its 6 TiB quota. Both Dropbox upload passes failed with
``insufficient_space``, the failure was only recorded as a
``distribution_warnings`` entry + a Pushbullet push, and the job still went
COMPLETE and emailed the customer a delivery email whose Dropbox link was the
literal placeholder ``[Dropbox URL not available]``. The customer replied that
there was no link. The only #alerts signal was a raw traceback.

This module makes that impossible to repeat silently:

* :func:`completion_email_hold_reason` decides whether a completion email must
  be HELD (the job was meant to deliver to Dropbox but has no share link).
* :func:`hold_completion_email` records ``state_data.completion_email_held`` and
  posts ONE actionable #alerts message per job (what broke, impact, fix).
* :func:`check_dropbox_space_usage` is the proactive quota check: it alerts once
  when the Dropbox crosses :data:`DROPBOX_QUOTA_ALERT_THRESHOLD` used, and stays
  quiet while it remains high.

Everything here is best-effort and never raises into the caller.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Fraction of the Dropbox allocation at which we page #alerts.
DROPBOX_QUOTA_ALERT_THRESHOLD = 0.95

# One held-delivery alert per job: throttle key is per job, window is a week so
# a re-completion of the same job within that time doesn't page twice.
_HELD_ALERT_THROTTLE_MINUTES = 7 * 24 * 60

# Firestore doc holding the quota-alert state (shares the ops-alert dedup collection).
_QUOTA_STATE_COLLECTION = "ops_failure_alerts"
_QUOTA_STATE_DOC = "dropbox-space-usage"

_TROUBLESHOOTING_REF = "karaoke-gen docs/TROUBLESHOOTING.md § Held completion email (Dropbox delivery failed)"

# Reason codes stored in state_data.completion_email_held.reason_code
REASON_DROPBOX_FULL = "dropbox_full"
REASON_DROPBOX_UPLOAD_FAILED = "dropbox_upload_failed"
REASON_DROPBOX_LINK_MISSING = "dropbox_link_missing"


def classify_dropbox_failure(warnings: Optional[list]) -> tuple[str, str]:
    """Classify a job's Dropbox distribution failure from its warnings.

    Returns ``(reason_code, human_reason)``.
    """
    dropbox_warnings = [
        str(w) for w in (warnings or []) if "dropbox" in str(w).lower()
    ]
    for w in dropbox_warnings:
        if "insufficient_space" in w:
            return REASON_DROPBOX_FULL, "Dropbox is FULL (insufficient_space)"
    if dropbox_warnings:
        detail = dropbox_warnings[0]
        if len(detail) > 300:
            detail = detail[:300] + " …"
        return REASON_DROPBOX_UPLOAD_FAILED, detail
    return REASON_DROPBOX_LINK_MISSING, "Dropbox upload produced no share link"


def job_expects_dropbox_delivery(job: Any) -> bool:
    """True if this job's distribution settings upload it to Dropbox.

    Mirrors the worker condition (``dropbox_path and brand_prefix``) on the
    effective settings, so private (NonPublished) jobs count too and tenant /
    no-Dropbox jobs don't.
    """
    try:
        from backend.services.job_defaults_service import get_effective_distribution_for_job

        dist = get_effective_distribution_for_job(job)
        return bool(dist.dropbox_path and dist.brand_prefix)
    except Exception as exc:  # noqa: BLE001 - never block an email on a settings bug
        logger.warning("delivery_guard: could not resolve distribution for job %s: %s",
                       getattr(job, "job_id", "?"), exc)
        return False


def completion_email_hold_reason(job: Any) -> Optional[Dict[str, str]]:
    """Return a hold reason if the completion email must NOT be sent, else None.

    The email is held when the job was meant to deliver to Dropbox but
    ``state_data.dropbox_link`` is missing — sending it would hand the customer a
    delivery email with no files link.
    """
    state_data = getattr(job, "state_data", None) or {}
    if state_data.get("dropbox_link"):
        return None
    if not job_expects_dropbox_delivery(job):
        return None
    reason_code, reason = classify_dropbox_failure(state_data.get("distribution_warnings"))
    return {"reason_code": reason_code, "reason": reason}


def build_held_delivery_alert(
    job: Any, hold: Dict[str, str], recipient: Optional[str]
) -> str:
    """Build the actionable #alerts message for a held delivery email."""
    state_data = getattr(job, "state_data", None) or {}
    brand_code = state_data.get("brand_code")
    track = " - ".join(p for p in (getattr(job, "artist", None), getattr(job, "title", None)) if p)
    lines = [
        "🔴 **Customer delivery HELD — Dropbox upload failed**",
        f"**Job:** `{job.job_id}`" + (f"  ({brand_code})" if brand_code else ""),
        f"**Track:** {track or '(unknown track)'}",
        f"**Reason:** {hold['reason']}",
        f"**Impact:** completion email to {_mask_email(recipient)} was NOT sent (no Dropbox link). "
        "The job is COMPLETE; files are in GCS.",
    ]
    if hold.get("reason_code") == REASON_DROPBOX_FULL:
        lines.append(
            "**Fix:** 1) free space in the business Dropbox; 2) upload the job's outputs to its "
            "Dropbox folder + create the share link, set `state_data.dropbox_link`; "
            "3) `POST /api/admin/jobs/{id}/send-completion-email`."
        )
    else:
        lines.append(
            "**Fix:** 1) check Dropbox credentials/API status; 2) upload the job's outputs to its "
            "Dropbox folder + create the share link, set `state_data.dropbox_link`; "
            "3) `POST /api/admin/jobs/{id}/send-completion-email`."
        )
    lines.append(f"**Runbook:** {_TROUBLESHOOTING_REF}")
    return "\n".join(lines)


def hold_completion_email(
    job_manager: Any, job: Any, hold: Dict[str, str], recipient: Optional[str]
) -> None:
    """Record the held email on the job and post one actionable #alerts message."""
    job_id = job.job_id
    logger.warning(
        f"[job:{job_id}] Completion email HELD: {hold['reason']} "
        f"(job expects Dropbox delivery but has no dropbox_link)"
    )
    try:
        job_manager.update_job(job_id, {
            "state_data.completion_email_held": {
                "reason_code": hold["reason_code"],
                "reason": hold["reason"],
                "recipient": recipient,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[job:{job_id}] Failed to record completion_email_held: {exc}")

    try:
        from backend.services.ops_alerts import send_throttled_ops_alert

        send_throttled_ops_alert(
            f"delivery-held-{job_id}",
            build_held_delivery_alert(job, hold, recipient),
            throttle_minutes=_HELD_ALERT_THROTTLE_MINUTES,
        )
    except Exception as exc:  # noqa: BLE001 - alerting must never raise
        logger.warning(f"[job:{job_id}] Failed to send held-delivery alert: {exc}")


# =============================================================================
# Proactive Dropbox quota check
# =============================================================================

def space_usage_bytes(usage: Any) -> tuple[int, int]:
    """Extract ``(used, allocated)`` bytes from a Dropbox ``SpaceUsage``.

    For a team (business) account the quota is the TEAM allocation, so use the
    team-wide ``used`` against it. ``allocated == 0`` means unknown/unlimited.
    """
    allocation = usage.allocation
    if allocation.is_team():
        team = allocation.get_team()
        return int(team.used), int(team.allocated)
    if allocation.is_individual():
        return int(usage.used), int(allocation.get_individual().allocated)
    return int(usage.used), 0


def evaluate_quota(
    used: int, allocated: int, was_alerting: bool,
    threshold: float = DROPBOX_QUOTA_ALERT_THRESHOLD,
) -> str:
    """Decide what to do for one quota reading.

    Returns ``"alert"`` (crossed the threshold — page once), ``"recovered"``
    (dropped back below it after an alert), or ``"none"``.
    """
    if allocated <= 0:
        return "none"
    high = (used / allocated) >= threshold
    if high and not was_alerting:
        return "alert"
    if not high and was_alerting:
        return "recovered"
    return "none"


def _fmt_tib(n: int) -> str:
    return f"{n / (1024 ** 4):.3f} TiB"


def build_quota_alert(used: int, allocated: int) -> str:
    pct = used / allocated * 100
    return "\n".join([
        f"🔴 **Dropbox almost FULL — {pct:.1f}% used** ({_fmt_tib(used)} of {_fmt_tib(allocated)})",
        "**Impact:** once full, every job's Dropbox upload fails with `insufficient_space` and "
        "customer completion emails are HELD (no files link).",
        "**Fix:** free space in the business Dropbox (or raise the quota). "
        "This alert fires once; it re-arms after usage drops below "
        f"{DROPBOX_QUOTA_ALERT_THRESHOLD * 100:.0f}%.",
    ])


def check_dropbox_space_usage(db: Any = None, dropbox_service: Any = None) -> Dict[str, Any]:
    """Read Dropbox space usage and alert #alerts once when it crosses the threshold.

    Rides on an existing hourly cron. Best-effort; never raises. Returns a small
    result dict for logging.
    """
    try:
        if dropbox_service is None:
            from backend.services.dropbox_service import get_dropbox_service
            dropbox_service = get_dropbox_service()
        if not dropbox_service.is_configured:
            return {"status": "skipped", "reason": "dropbox not configured"}

        used, allocated = space_usage_bytes(dropbox_service.client.users_get_space_usage())
        if db is None:
            from backend.services.firestore_service import get_firestore_client
            db = get_firestore_client()

        doc_ref = db.collection(_QUOTA_STATE_COLLECTION).document(_QUOTA_STATE_DOC)
        snap = doc_ref.get()
        state = (snap.to_dict() or {}) if snap.exists else {}
        was_alerting = bool(state.get("alerting"))

        action = evaluate_quota(used, allocated, was_alerting)
        pct = round(used / allocated * 100, 2) if allocated else None
        alerting = was_alerting
        if action == "alert":
            from backend.services.ops_alerts import send_ops_alert
            # Only latch "alerting" once delivery succeeded, so a failed send retries next run.
            alerting = bool(send_ops_alert(build_quota_alert(used, allocated)))
            logger.warning(f"DROPBOX_QUOTA high: {pct}% used (alert_sent={alerting})")
        elif action == "recovered":
            alerting = False
            logger.info(f"DROPBOX_QUOTA recovered: {pct}% used")
        else:
            logger.info(f"DROPBOX_QUOTA ok: {pct}% used")

        doc_ref.set({
            "alerting": alerting,
            "used_bytes": used,
            "allocated_bytes": allocated,
            "used_pct": pct,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }, merge=True)
        return {"status": "ok", "used_pct": pct, "action": action, "alerting": alerting}
    except Exception as exc:  # noqa: BLE001 - a health check must never raise
        logger.warning(f"DROPBOX_QUOTA check failed: {exc}")
        return {"status": "error", "error": str(exc)}


def _mask_email(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return "(no recipient)"
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"
