"""POST /api/client-events — persistent telemetry for user-visible degradation.

Records every time the frontend shows a user a degraded-service surface (the
orange "trouble reaching our servers" banner, the stronger "temporarily
unavailable" error, a failed lyrics load, a slow/failed waveform), with enough
metadata to review frequency and context later. Two sinks per event:

  1. A structured INFO log line (``client_event type=…``) — queryable in Cloud
     Logging / Log Analytics alongside backend symptoms from the same window.
  2. A Firestore doc in ``client_events`` — survives log retention, cheap to
     aggregate for "how often are users seeing this?" reviews.

Identity (2026-10-03): the client sends its session token (if any) and the
server resolves ``user_email`` / ``is_admin`` / ``is_internal`` / ``is_test``
from it — a client-supplied email is never trusted (and is ignored). Each
event also carries the FingerprintJS ``device_fingerprint`` (same visitorId the
magic-link abuse checks use), a per-tab ``tab_id`` and an ``episode_id`` that
groups one degradation episode (first banner → ``banner_recovered``).

The watchdog in ``backend/services/loop_watchdog.py`` writes server-side
``server_loop_stall`` docs into the same collection; clients cannot submit
that type.

Unauthenticated (review-token users must be able to report) and rate-limited
per IP, mirroring /api/client-errors. The limiter is process-local, so the
effective ceiling is 120/min/IP × live instances — acceptable for this threat
model (non-malicious browsers, 60s client-side per-type throttle); Cloudflare's
zone-wide rate limit sits in front as a flood backstop. Events are dropped,
never queued, when the limiter trips — telemetry, not a delivery guarantee.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from backend.utils.test_data import is_internal_email, is_test_email
from backend.services.error_monitor.frontend_ingestion import (
    RateLimiter,
    is_bot_user_agent,
    sanitize_url,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/client-events", tags=["client-events"])

# Sized for a legitimate worst case from ONE IP: a many-tab burst (e.g. 16 review
# tabs at once) where every tab reports a banner + a waveform event — that must be
# fully recorded, not self-suppressed. Each tab throttles per-type client-side.
_limiter = RateLimiter(max_per_minute=120)
_db_singleton = None

# The full closed set of reportable events. Reject anything else so the
# collection stays reviewable (no free-form types accumulating).
EventType = Literal[
    "banner_reconnecting",
    "banner_unavailable",
    "banner_waking",
    "banner_recovered",
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
    # Legacy field from pre-2026-10-03 frontends — accepted but IGNORED (the
    # server resolves identity from the bearer token instead).
    user_email: Optional[str] = Field(None, max_length=320)
    device_fingerprint: Optional[str] = Field(None, max_length=128)
    tab_id: Optional[str] = Field(None, max_length=64)
    episode_id: Optional[str] = Field(None, max_length=64)
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


_TENANT_HOST_EXCLUDE = {"gen", "www", "api", "app", "decide", "localhost"}


def _tenant_from_url(url: str) -> Optional[str]:
    """``randy-vild.nomadkaraoke.com/...`` → ``randy-vild``; consumer hosts → None."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host.endswith(".nomadkaraoke.com"):
        return None
    sub = host[: -len(".nomadkaraoke.com")]
    if not sub or "." in sub or sub in _TENANT_HOST_EXCLUDE:
        return None
    return sub


def _resolve_identity(request: Request) -> dict[str, Any]:
    """Resolve the caller from its bearer token. Never raises; anonymous on failure."""
    identity: dict[str, Any] = {
        "user_email": None, "is_admin": False, "is_internal": False, "is_test": False,
    }
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return identity
    token = auth[7:].strip()
    if not token:
        return identity
    try:
        from backend.services.auth_service import get_auth_service

        result = get_auth_service().validate_token_full(token)
    except Exception:
        logger.debug("client_event token resolution failed", exc_info=True)
        return identity
    if not result.is_valid:
        return identity
    email = result.user_email
    identity["user_email"] = email
    identity["is_admin"] = bool(result.is_admin)
    identity["is_internal"] = bool(email and is_internal_email(email))
    identity["is_test"] = bool(email and is_test_email(email))
    return identity


@router.post("", status_code=202)
def report_client_event(payload: ClientEventPayload, request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    if not _limiter.allow(client_ip, time.monotonic()):
        raise HTTPException(status_code=429, detail="too many events")

    user_agent = request.headers.get("user-agent", "")[:1024]
    if is_bot_user_agent(user_agent):
        return {"status": "ignored"}

    # Sync handler → runs in FastAPI's threadpool, so the token lookup
    # (Firestore) never touches the event loop.
    identity = _resolve_identity(request)

    event = {
        "type": payload.type,
        "source": "client",
        "url": sanitize_url(payload.url),
        "tenant": _tenant_from_url(payload.url),
        "job_id": payload.job_id,
        **identity,
        "device_fingerprint": payload.device_fingerprint,
        "tab_id": payload.tab_id,
        "episode_id": payload.episode_id,
        "locale": payload.locale,
        "release": payload.release,
        "user_agent": user_agent,
        "detail": _capped_detail(payload.detail),
        "created_at": datetime.now(timezone.utc),
    }

    # Sink 1: structured log line — correlate with backend symptoms in Logging.
    logger.info(
        "client_event type=%s job_id=%s user=%s admin=%s fp=%s episode=%s url=%s release=%s detail=%s",
        event["type"],
        event["job_id"],
        event["user_email"],
        event["is_admin"],
        event["device_fingerprint"],
        event["episode_id"],
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
