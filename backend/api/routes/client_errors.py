"""POST /api/client-errors — ingest frontend crash reports.

Unauthenticated (logged-out users' crashes still matter). Deduped and alerted
via the existing error-monitor pipeline — see
``backend/services/error_monitor``.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.services.error_monitor.firestore_adapter import ErrorPatternsAdapter
from backend.services.error_monitor.frontend_ingestion import (
    FrontendErrorReport,
    RateLimiter,
    build_pattern_data,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/client-errors", tags=["client-errors"])

_limiter = RateLimiter(max_per_minute=60)
_adapter_singleton: ErrorPatternsAdapter | None = None


def _get_adapter() -> ErrorPatternsAdapter:
    global _adapter_singleton
    if _adapter_singleton is None:
        _adapter_singleton = ErrorPatternsAdapter()
    return _adapter_singleton


class ClientErrorPayload(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    stack: Optional[str] = Field(None, max_length=1_000_000)
    url: str = Field("", max_length=2048)
    user_agent: str = Field("", max_length=1024)
    release: str = Field("", max_length=64)
    user_email: Optional[str] = Field(None, max_length=320)
    viewport: Optional[dict] = None
    locale: str = Field("en", max_length=10)
    source: str = Field("unknown", max_length=64)
    # optional free-form extra for debugging. Capped server-side.
    extra: Optional[dict] = None

    @field_validator("message", "url", "user_agent", "locale", "source")
    @classmethod
    def strip(cls, v: str) -> str:
        return (v or "").strip()


class ClientErrorResponse(BaseModel):
    pattern_id: str
    is_new: bool


@router.post("", response_model=ClientErrorResponse, status_code=202)
def report_client_error(payload: ClientErrorPayload, request: Request) -> ClientErrorResponse:
    client_ip = request.client.host if request.client else "unknown"
    if not _limiter.allow(client_ip, time.monotonic()):
        raise HTTPException(status_code=429, detail="too many reports")

    report = FrontendErrorReport(
        message=payload.message,
        stack=payload.stack,
        url=payload.url,
        user_agent=payload.user_agent,
        release=payload.release,
        user_email=payload.user_email,
        viewport=payload.viewport,
        locale=payload.locale,
        extra=payload.extra,
    )
    pattern_data = build_pattern_data(report)

    try:
        result = _get_adapter().upsert_pattern(pattern_data)
    except Exception:  # pragma: no cover - Firestore transient errors
        logger.exception("Failed to upsert frontend error pattern")
        raise HTTPException(status_code=503, detail="storage unavailable")

    logger.info(
        "frontend_crash_reported pattern_id=%s is_new=%s source=%s",
        result.pattern_id,
        result.is_new,
        payload.source,
    )
    return ClientErrorResponse(pattern_id=result.pattern_id, is_new=result.is_new)
