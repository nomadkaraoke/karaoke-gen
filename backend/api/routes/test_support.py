"""Test-only API for the local E2E stack (``/api/test/*``).

Mounted ONLY when FAKE_WORKERS is on (``backend/main.py``), which startup
refuses in production, and unauthenticated by design: the stack runs on
emulators on a developer machine or CI runner. Web Playwright and mobile Maestro
tests use it to reset state, seed scenarios, tune the fake workers, read
captured email and complete fake Stripe checkouts.
"""
from __future__ import annotations

import html
import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from backend.config import get_settings
from backend.services import fake_workers
from backend.services.fake_workers import checkout as fake_checkout
from backend.services.fake_workers.config import clear_fake_config, get_fake_config, set_fake_config
from backend.services.fake_workers.outbox import list_emails
from backend.services.fake_workers.scenarios import SCENARIOS, ScenarioError, run_scenario

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/test", tags=["test-support"])

# Seeded by scripts/seed-local-data.py; kept across resets.
PRESERVED_BLOB_PREFIXES = ("themes/",)


class ScenarioRequest(BaseModel):
    name: str
    email: Optional[str] = None
    credits: Optional[int] = None
    artist: Optional[str] = None
    title: Optional[str] = None


@router.get("/status")
def status() -> Dict[str, Any]:
    return {
        "fake_workers": True,
        "active_workers": fake_workers.active_worker_count(),
        "config": get_fake_config().to_dict(),
        "scenarios": SCENARIOS,
    }


@router.post("/reset")
def reset() -> Dict[str, Any]:
    """Wipe every Firestore document and every bucket object except the seed (themes)."""
    emulator = os.getenv("FIRESTORE_EMULATOR_HOST")
    if not emulator:
        raise HTTPException(status_code=409, detail="reset only runs against the Firestore emulator")
    project = get_settings().google_cloud_project
    resp = httpx.delete(
        f"http://{emulator}/emulator/v1/projects/{project}/databases/(default)/documents", timeout=30,
    )
    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"emulator reset failed: {resp.status_code} {resp.text}")

    from backend.services.storage_service import StorageService

    bucket = StorageService().bucket
    deleted = 0
    for blob in list(bucket.list_blobs()):
        if not blob.name.startswith(PRESERVED_BLOB_PREFIXES):
            blob.delete()
            deleted += 1
    clear_fake_config()
    return {"status": "reset", "blobs_deleted": deleted}


@router.get("/fake-config")
def read_config(email: Optional[str] = None) -> Dict[str, Any]:
    return get_fake_config(email=email).to_dict()


@router.put("/fake-config")
def update_config(updates: Dict[str, Any], email: Optional[str] = None) -> Dict[str, Any]:
    """Merge ``updates`` into the global knobs, or into ``?email=``'s per-user overrides."""
    try:
        return set_fake_config(updates, email=email).to_dict()
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/scenario")
def scenario(body: ScenarioRequest) -> Dict[str, Any]:
    try:
        return run_scenario(body.name, email=body.email, credits=body.credits, artist=body.artist, title=body.title)
    except ScenarioError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/emails")
def emails(to: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    return {"emails": list_emails(to, limit=limit)}


# ------------------------------------------------------------------ fake Stripe

_CHECKOUT_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fake Checkout</title>
<style>body{{font-family:system-ui,sans-serif;max-width:420px;margin:48px auto;padding:0 16px}}
button{{font-size:18px;padding:12px 20px;margin:8px 8px 0 0;border-radius:8px;border:0}}
#pay{{background:#635bff;color:#fff}}#cancel{{background:#eee}}</style></head>
<body><h1>Fake Checkout</h1>
<p data-testid="fake-checkout-product">{product}</p>
<p data-testid="fake-checkout-amount">${amount}</p>
<p>{email}</p>
<form method="post" action="{base}/api/test/fake-checkout/{sid}/pay" style="display:inline">
<button id="pay" data-testid="fake-checkout-pay" type="submit">Pay</button></form>
<form method="post" action="{base}/api/test/fake-checkout/{sid}/cancel" style="display:inline">
<button id="cancel" data-testid="fake-checkout-cancel" type="submit">Cancel</button></form>
</body></html>"""


def _checkout_or_404(session_id: str) -> Dict[str, Any]:
    record = fake_checkout.get_checkout(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="unknown fake checkout session")
    return record


@router.get("/fake-checkout/{session_id}", response_class=HTMLResponse)
def checkout_page(session_id: str) -> HTMLResponse:
    record = _checkout_or_404(session_id)
    return HTMLResponse(_CHECKOUT_PAGE.format(
        product=html.escape(record.get("product_name") or ""),
        amount=f"{(record.get('amount_cents') or 0) / 100:.2f}",
        email=html.escape(record.get("customer_email") or ""),
        base=fake_checkout.public_api_url(),
        sid=html.escape(session_id),
    ))


@router.post("/fake-checkout/{session_id}/pay")
async def checkout_pay(session_id: str):
    """Complete the session through the same handler as the real Stripe webhook."""
    from backend.api.routes.internal import TestWebhookRequest, test_webhook

    record = _checkout_or_404(session_id)
    result = await test_webhook(
        TestWebhookRequest(
            event_type="checkout.session.completed",
            session_id=session_id,
            customer_email=record.get("customer_email") or "",
            metadata=record.get("metadata") or {},
        ),
        auth_data=None,
    )
    if result.status not in ("processed", "already_processed"):
        raise HTTPException(status_code=500, detail=f"fake checkout completion failed: {result.message}")
    fake_checkout.mark_checkout(session_id, "complete")
    return RedirectResponse(fake_checkout.resolve_url(record.get("success_url"), session_id), status_code=303)


@router.post("/fake-checkout/{session_id}/cancel")
def checkout_cancel(session_id: str):
    record = _checkout_or_404(session_id)
    fake_checkout.mark_checkout(session_id, "cancelled")
    return RedirectResponse(fake_checkout.resolve_url(record.get("cancel_url"), session_id), status_code=303)
