"""POST /api/karaokehunt/request — intake for retired-KaraokeHunt-app requests.

A Cloudflare Worker on create.karaokehunt.com (the URL baked into the
unchangeable app binary) answers the app instantly and forwards each payload
here with a shared secret. This route validates + logs the payload, then runs
the full conversion synchronously (see ``workers/karaokehunt_conversion``) —
the Worker doesn't wait for us, so latency is a non-issue and uvicorn finishes
the handler even if the forwarding subrequest is reaped.

Unauthenticated by necessity (the app can't auth), gated three ways: the
shared secret (503 until configured — deploys dark), a per-IP rate limit, and
the conversion worker's own daily job cap + per-song dedup.
"""
from __future__ import annotations

import hmac
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.dependencies import require_admin
from backend.config import get_settings
from backend.services.error_monitor.frontend_ingestion import RateLimiter
from backend.workers import karaokehunt_conversion

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/karaokehunt", tags=["karaokehunt"])

# ~1 legit request every 3 days historically; 10/min/IP is already generous.
_limiter = RateLimiter(max_per_minute=10)


def _client_ip(request: Request) -> str:
    # The Worker forwards the app's real IP; direct callers fall back to peer IP.
    return (request.headers.get("x-kh-client-ip")
            or (request.client.host if request.client else "unknown"))


@router.post("/request", status_code=200)
async def intake_karaokehunt_request(request: Request) -> dict:
    settings = get_settings()
    if not settings.karaokehunt_forwarder_secret:
        raise HTTPException(status_code=503, detail="not configured")

    provided = request.headers.get("x-kh-forwarder-secret", "")
    if not hmac.compare_digest(provided, settings.karaokehunt_forwarder_secret):
        raise HTTPException(status_code=403, detail="forbidden")

    if not _limiter.allow(_client_ip(request), time.monotonic()):
        raise HTTPException(status_code=429, detail="too many requests")

    # The live app binary's exact schema is unverifiable (built from a
    # FlutterFlow export newer than any repo branch), so parse leniently:
    # any JSON object is accepted and logged; unusable ones become
    # outcome="invalid" audit rows rather than errors.
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON")

    doc = karaokehunt_conversion.create_intake(
        payload,
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:512],
    )
    if doc["outcome"] == "invalid":
        return {"status": "success", "id": doc["id"]}

    try:
        result = await karaokehunt_conversion.process_intake(doc["id"])
        logger.info("karaokehunt: processed %s -> %s", doc["id"], result.get("status"))
    except Exception:  # noqa: BLE001 — the intake doc + reprocess endpoint carry retries
        logger.exception("karaokehunt: conversion failed for %s", doc["id"])
        try:
            karaokehunt_conversion._get_db().collection(
                karaokehunt_conversion.COLLECTION
            ).document(doc["id"]).update({"outcome": "error", "error": "unhandled exception"})
        except Exception:  # noqa: BLE001
            logger.exception("karaokehunt: failed to mark %s as error", doc["id"])

    # The app ignores the body; always succeed so nothing user-visible breaks.
    return {"status": "success", "id": doc["id"]}


@router.post("/internal/reprocess/{doc_id}")
async def reprocess_karaokehunt_request(
    doc_id: str, auth_result=Depends(require_admin)
) -> dict:
    """Admin retry for a failed/errored intake (idempotent markers prevent
    double credits/jobs). Also accepts force=true via query for terminal docs."""
    return await karaokehunt_conversion.process_intake(doc_id, force=True)
