"""Near-real-time operational alerting (incident-hardening D1 + G1 channel).

Why this exists
---------------
The production error monitor (``backend/services/error_monitor``) is
**spike-based**: a pattern only alerts when its current count exceeds a rolling
average by a multiplier *and* clears a minimum-count floor. A brand-new error
has ``rolling_avg == 0`` → it is mathematically never a spike, so a novel,
low-volume failure is invisible to it. That is exactly how incident NOMAD-1632's
follow-on outage (2 jobs failing with a never-before-seen error string) went
unnoticed until a human spotted failed jobs in the UI.

This module is the **universal net**: the moment *any* job transitions to
``FAILED`` we emit an immediate Discord alert, independent of the spike detector,
de-duplicated by a normalized error signature so a burst collapses into one
alert. Novel error signatures always alert regardless of count.

It also exposes :func:`send_ops_alert`, a generic best-effort sender reused by
the publish-completeness shadow invariant (G1).

Everything here is **best-effort**: any failure to alert is swallowed and
logged. It must never raise into — nor add a failure mode to — the status write
that triggered it.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Secret Manager name of the ops/alert Discord webhook (same channel the startup
# credential-validation alert uses — see backend/main.py).
_ALERT_WEBHOOK_SECRET = "discord-alert-webhook"

# Firestore collection used to de-dupe/throttle failure alerts by signature.
_DEDUP_COLLECTION = "ops_failure_alerts"

# Collapse repeat alerts for the same error signature within this window into a
# single Discord message (the burst is counted, not re-sent). Override with
# FAILURE_ALERT_THROTTLE_MINUTES.
_DEFAULT_THROTTLE_MINUTES = 30

# Environments whose failures we do NOT page on (intentional test/dev failures
# would otherwise be noise). Production + unlabeled jobs always alert. Override
# with FAILURE_ALERT_SKIP_ENVIRONMENTS (comma-separated).
_DEFAULT_SKIP_ENVIRONMENTS = {"test", "development"}


def _is_enabled() -> bool:
    """Only alert from a deployed environment.

    Local runs and the test suite have no ``K_SERVICE`` / non-production
    ``ENVIRONMENT`` (see :func:`backend.config.is_production`), so this short
    circuits before any Secret Manager or Discord call — keeping unit tests
    silent and fast.
    """
    if os.environ.get("FAILURE_ALERTS_ENABLED", "").lower() in ("1", "true", "yes"):
        return True
    try:
        from backend.config import is_production

        return is_production()
    except Exception:  # noqa: BLE001 - never let config import break a status write
        return False


def _throttle_minutes() -> int:
    try:
        return int(os.environ.get("FAILURE_ALERT_THROTTLE_MINUTES", _DEFAULT_THROTTLE_MINUTES))
    except (TypeError, ValueError):
        return _DEFAULT_THROTTLE_MINUTES


def _skip_environments() -> set[str]:
    raw = os.environ.get("FAILURE_ALERT_SKIP_ENVIRONMENTS")
    if raw is None:
        return set(_DEFAULT_SKIP_ENVIRONMENTS)
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _get_webhook_url() -> Optional[str]:
    """Resolve the ops-alert Discord webhook (cached via settings.get_secret)."""
    try:
        from backend.config import settings

        if hasattr(settings, "get_secret"):
            return settings.get_secret(_ALERT_WEBHOOK_SECRET)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ops_alerts: could not resolve webhook: %s", exc)
    return None


def send_ops_alert(message: str) -> bool:
    """Post a plaintext ops alert to the Discord alert channel. Best-effort.

    Returns True if the message was sent, False otherwise (disabled, no webhook,
    or a delivery error). Never raises.
    """
    try:
        if not _is_enabled():
            logger.debug("ops_alerts: disabled (not a deployed environment); skipping")
            return False
        webhook = _get_webhook_url()
        if not webhook:
            logger.debug("ops_alerts: no webhook configured; skipping")
            return False
        from backend.services.discord_service import get_discord_notification_service

        service = get_discord_notification_service(webhook_url=webhook)
        return service.post_message(message, webhook_url=webhook)
    except Exception as exc:  # noqa: BLE001 - alerting must never raise
        logger.warning("ops_alerts: failed to send alert: %s", exc)
        return False


def _signature(error_message: str) -> str:
    """Stable fingerprint for an error string (reuses the error-monitor normalizer)."""
    try:
        from backend.services.error_monitor.normalizer import (
            compute_pattern_hash,
            normalize_message,
        )

        return compute_pattern_hash("karaoke-job", normalize_message(error_message or ""))
    except Exception:  # noqa: BLE001
        # Fallback: hash the raw string so dedup still works if the normalizer
        # import fails for any reason.
        import hashlib

        return hashlib.sha256((error_message or "").encode("utf-8")).hexdigest()


def _should_alert(db: Any, signature: str, now: datetime) -> tuple[bool, bool, int, Any]:
    """Decide whether to send, WITHOUT yet acknowledging delivery.

    Returns ``(should_send, is_novel, suppressed_since_last, doc_ref)``:
      * ``should_send`` — emit a Discord alert this time.
      * ``is_novel`` — we have never seen this signature before.
      * ``suppressed_since_last`` — how many failures were collapsed (throttled)
        since the last *successful* alert for this signature.
      * ``doc_ref`` — the dedup doc, passed to :func:`_mark_alerted` **only after a
        successful send** so a failed Discord delivery does NOT advance the
        throttle window (which would suppress the next real failure). ``None`` if
        Firestore was unreachable.

    This records ``first_seen`` / ``total_count`` (and, when throttled, the
    suppressed counter) but deliberately does NOT touch ``last_alerted_at`` — that
    is the delivery acknowledgement, set separately once the alert actually sends.

    Best-effort: on any Firestore error we default to *send* (fail open — an extra
    alert is far better than a missed outage). NOTE: the ``get()`` + ``update()``
    are not transactional, so two truly-concurrent failures of the same signature
    could each send once. That's an acceptable trade (a duplicate alert is
    harmless; a missed one is not), so we don't pay for a transaction here.
    """
    try:
        doc_ref = db.collection(_DEDUP_COLLECTION).document(signature)
        snap = doc_ref.get()
        now_iso = now.isoformat()
        if not snap.exists:
            # Create the record but leave last_alerted_at unset — it's stamped by
            # _mark_alerted only if the send succeeds.
            doc_ref.set(
                {
                    "signature": signature,
                    "first_seen": now_iso,
                    "last_alerted_at": None,
                    "total_count": 1,
                    "alert_count": 0,
                    "suppressed_since_last": 0,
                }
            )
            return True, True, 0, doc_ref

        data = snap.to_dict() or {}
        last_alerted_raw = data.get("last_alerted_at")
        within_window = False
        if last_alerted_raw:
            try:
                last_alerted = datetime.fromisoformat(last_alerted_raw)
                within_window = (now - last_alerted) < timedelta(minutes=_throttle_minutes())
            except (TypeError, ValueError):
                within_window = False

        if within_window:
            # Collapse: count it but don't re-page.
            doc_ref.update(
                {
                    "total_count": (data.get("total_count", 0) or 0) + 1,
                    "suppressed_since_last": (data.get("suppressed_since_last", 0) or 0) + 1,
                }
            )
            return False, False, 0, doc_ref

        doc_ref.update({"total_count": (data.get("total_count", 0) or 0) + 1})
        suppressed = data.get("suppressed_since_last", 0) or 0
        return True, False, suppressed, doc_ref
    except Exception as exc:  # noqa: BLE001
        logger.debug("ops_alerts: dedup lookup failed (%s); defaulting to send", exc)
        return True, False, 0, None


def _mark_alerted(doc_ref: Any, now: datetime) -> None:
    """Acknowledge a successful send: advance the throttle window + reset counters.

    Called ONLY after :func:`send_ops_alert` returns True. Best-effort.
    """
    if doc_ref is None:
        return
    try:
        snap = doc_ref.get()
        data = snap.to_dict() or {} if snap.exists else {}
        doc_ref.update(
            {
                "last_alerted_at": now.isoformat(),
                "alert_count": (data.get("alert_count", 0) or 0) + 1,
                "suppressed_since_last": 0,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("ops_alerts: could not record alert delivery: %s", exc)


def notify_job_failed(
    db: Any,
    collection: str,
    job_id: str,
    message: Optional[str] = None,
    additional_fields: Optional[dict] = None,
) -> bool:
    """Emit a near-real-time alert for a job that just transitioned to FAILED.

    Invoked from the single status-write chokepoint
    (:meth:`FirestoreService.update_job_status`). ``db`` is the caller's existing
    Firestore client (reused to avoid a second client / circular import);
    ``collection`` is the jobs collection so we can enrich the alert with the
    job's artist/title/environment. Best-effort; never raises.

    Returns True if an alert was sent.
    """
    try:
        if not _is_enabled():
            return False

        # Enrich from the job doc (best-effort). We only just wrote it, so this
        # is a single extra read on the rare FAILED path.
        artist = title = environment = brand_code = None
        error_message = (additional_fields or {}).get("error_message") or message or ""
        try:
            snap = db.collection(collection).document(job_id).get()
            if snap.exists:
                job = snap.to_dict() or {}
                artist = job.get("artist")
                title = job.get("title")
                meta = job.get("request_metadata") or {}
                environment = meta.get("environment")
                state = job.get("state_data") or {}
                brand_code = state.get("brand_code")
                if not error_message:
                    error_message = job.get("error_message") or ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("ops_alerts: job enrichment read failed: %s", exc)

        if environment and environment.lower() in _skip_environments():
            logger.debug("ops_alerts: skipping failure alert for %s env job %s", environment, job_id)
            return False

        now = datetime.now(timezone.utc)
        signature = _signature(error_message)
        should_send, is_novel, suppressed, doc_ref = _should_alert(db, signature, now)
        if not should_send:
            return False

        track = " - ".join(p for p in (artist, title) if p) or "(unknown track)"
        lines = [
            "🔴 **Job failed** — near-real-time alert",
            f"**Job:** `{job_id}`" + (f"  ({brand_code})" if brand_code else ""),
            f"**Track:** {track}",
        ]
        if environment:
            lines.append(f"**Env:** {environment}")
        if is_novel:
            lines.append("🆕 **First time we've ever seen this error signature.**")
        elif suppressed:
            lines.append(f"_(Collapsed {suppressed} earlier occurrence(s) of this signature.)_")
        err = (error_message or "").strip()
        if err:
            if len(err) > 1500:
                err = err[:1500] + " …[truncated]"
            lines.append(f"**Error:** ```{err}```")

        sent = send_ops_alert("\n".join(lines))
        # Only advance the throttle window once delivery actually succeeded — a
        # failed send must NOT suppress the next occurrence of this signature.
        if sent:
            _mark_alerted(doc_ref, now)
        return sent
    except Exception as exc:  # noqa: BLE001 - alerting must never raise
        logger.warning("ops_alerts: notify_job_failed failed: %s", exc)
        return False
