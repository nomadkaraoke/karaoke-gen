"""POST /api/client-events — persistent telemetry for user-visible degradation.

Records every time the frontend shows a user a degraded-service surface (the
orange "trouble reaching our servers" banner, the stronger "temporarily
unavailable" error, a failed lyrics load, a slow/failed waveform), with enough
metadata to review frequency and context later. Two sinks per event:

  1. A structured INFO log line (``client_event type=…``) — queryable in Cloud
     Logging / Log Analytics alongside backend symptoms from the same window.
  2. A Firestore doc in ``client_events`` — survives log retention, cheap to
     aggregate for "how often are users seeing this?" reviews.

Unauthenticated (review-token users must be able to report) and rate-limited
per IP, mirroring /api/client-errors. Events are dropped, never queued, when
the limiter trips — this is telemetry, not a delivery guarantee.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from backend.services.error_monitor.frontend_ingestion import (
    RateLimiter,
    is_bot_user_agent,
    sanitize_url,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/client-events", tags=["client-events"])

_limiter = RateLimiter(max_per_minute=30)
_db_singleton = None

# The full closed set of reportable events. Reject anything else so the
# collection stays reviewable (no free-form types accumulating).
EventType = Literal[
    "banner_reconnecting",
    "banner_unavailable",
    "lyrics_load_failed",
    "waveform_slow",
    "waveform_failed",
]


def _get_db():
    global _db_singleton
    if _db_singleton is None:
        from google.cloud import firestore  # type: ignore[import]

        _db_singleton = firestore.Client(project="nomadkaraoke")
    return _db_singleton


class ClientEventPayload(BaseModel):
    type: EventType
    url: str = Field("", max_length=2048)
    job_id: Optional[str] = Field(None, max_length=64)
    user_email: Optional[str] = Field(None, max_length=320)
    locale: str = Field("en", max_length=10)
    release: str = Field("", max_length=64)
    # Small free-form context (stall age, http status, elapsed ms, probe state…).
    # Size-capped server-side; values are stored as-is.
    detail: Optional[dict] = None

    @field_validator("url", "locale", "release")
    @classmethod
    def strip(cls, v: str) -> str:
        return (v or "").strip()


def _capped_detail(detail: Optional[dict]) -> dict[str, Any]:
    """Keep detail reviewable: at most 12 keys, scalar-ish values, short strings."""
    if not isinstance(detail, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in list(detail.items())[:12]:
        k = str(key)[:40]
        if isinstance(value, (int, float, bool)) or value is None:
            out[k] = value
        else:
            out[k] = str(value)[:200]
    return out


@router.post("", status_code=202)
def report_client_event(payload: ClientEventPayload, request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    if not _limiter.allow(client_ip, time.monotonic()):
        raise HTTPException(status_code=429, detail="too many events")

    user_agent = request.headers.get("user-agent", "")[:1024]
    if is_bot_user_agent(user_agent):
        return {"status": "ignored"}

    event = {
        "type": payload.type,
        "url": sanitize_url(payload.url),
        "job_id": payload.job_id,
        "user_email": payload.user_email,
        "locale": payload.locale,
        "release": payload.release,
        "user_agent": user_agent,
        "detail": _capped_detail(payload.detail),
        "created_at": datetime.now(timezone.utc),
    }

    # Sink 1: structured log line — correlate with backend symptoms in Logging.
    logger.info(
        "client_event type=%s job_id=%s user=%s url=%s release=%s detail=%s",
        event["type"],
        event["job_id"],
        event["user_email"],
        event["url"],
        event["release"],
        event["detail"],
    )

    # Sink 2: Firestore — best-effort; telemetry must never error back to users.
    try:
        _get_db().collection("client_events").add(event)
    except Exception:
        logger.exception("Failed to persist client event (non-fatal)")

    return {"status": "recorded"}
