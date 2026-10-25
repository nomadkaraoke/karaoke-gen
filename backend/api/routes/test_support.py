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


@router.get("/jobs/{job_id}/review-result")
def review_result(job_id: str) -> Dict[str, Any]:
    """What the review submitted: the saved corrected lines plus the instrumental / duet choice.

    Lets Maestro assert on a review without going through the review auth.
    ``lines`` is empty until a review was completed (corrections_updated.json exists).
    """
    from backend.services.job_manager import JobManager
    from backend.services.storage_service import StorageService

    job = JobManager().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    state = job.state_data or {}
    path = ((job.file_urls or {}).get("lyrics") or {}).get("corrections_updated") \
        or f"jobs/{job_id}/lyrics/corrections_updated.json"
    storage = StorageService()
    lines = []
    if storage.file_exists(path):
        for seg in storage.download_json(path).get("corrected_segments") or []:
            lines.append({
                "text": seg.get("text"),
                "singer": seg.get("singer"),
                "words": [
                    {"text": w.get("text"), "start_time": w.get("start_time"), "end_time": w.get("end_time")}
                    for w in seg.get("words") or []
                ],
            })
    return {
        "status": getattr(job.status, "value", job.status),
        "instrumental_selection": state.get("instrumental_selection"),
        "is_duet": bool(state.get("is_duet", False)),
        "lines": lines,
    }


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


# ------------------------------------------------------------------ fake in-app purchases


class FakeIapPurchaseRequest(BaseModel):
    """A store purchase, as RevenueCat would report it. Give ``email`` or ``app_user_id``."""
    product_id: str
    email: Optional[str] = None
    app_user_id: Optional[str] = None
    transaction_id: Optional[str] = None
    store: str = "APP_STORE"
    price: Optional[float] = None
    takehome_percentage: Optional[float] = None
    country_code: str = "US"


class FakeIapRefundRequest(BaseModel):
    """Refunds debit the account the purchase granted, so no user is needed."""
    transaction_id: str
    product_id: str
    store: str = "APP_STORE"


def _fake_app_user_id(email: Optional[str], app_user_id: Optional[str]) -> str:
    from backend.services.user_service import get_user_service

    if app_user_id:
        return app_user_id
    if not email:
        raise HTTPException(status_code=422, detail="email or app_user_id is required")
    user_service = get_user_service()
    user_service.get_or_create_user(email)
    return user_service.ensure_app_user_id(email)


def _fake_iap_result(event: Dict[str, Any]) -> Dict[str, Any]:
    from backend.services.iap_service import get_iap_service

    result = get_iap_service().handle_event(event)
    return {
        "action": result.action,
        "credits": result.credits,
        "store": event["store"],
        "transaction_id": event["transaction_id"],
    }


# US store prices of the credit packs (spec: $9.99 / $23.99 / $34.99 / $59.99).
_FAKE_IAP_PRICES = {
    "nomad_credits_1": 9.99,
    "nomad_credits_3": 23.99,
    "nomad_credits_5": 34.99,
    "nomad_credits_10": 59.99,
}


@router.post("/fake-iap/purchase")
def fake_iap_purchase(body: FakeIapPurchaseRequest) -> Dict[str, Any]:
    """Grant a store credit pack through the same IapService as the RevenueCat webhook."""
    import uuid

    price = body.price if body.price is not None else _FAKE_IAP_PRICES.get(body.product_id, 0.0)
    event: Dict[str, Any] = {
        "type": "NON_RENEWING_PURCHASE",
        "app_user_id": _fake_app_user_id(body.email, body.app_user_id),
        "product_id": body.product_id,
        "transaction_id": body.transaction_id or f"fake-{uuid.uuid4().hex}",
        "store": body.store,
        "price": price,
        "price_in_purchased_currency": price,
        "currency": "USD",
        "country_code": body.country_code,
        "environment": "SANDBOX",
    }
    if body.takehome_percentage is not None:
        event["takehome_percentage"] = body.takehome_percentage
    return _fake_iap_result(event)


@router.post("/fake-iap/refund")
def fake_iap_refund(body: FakeIapRefundRequest) -> Dict[str, Any]:
    """Refund a fake store purchase (RevenueCat CANCELLATION / CUSTOMER_SUPPORT)."""
    price = -_FAKE_IAP_PRICES.get(body.product_id, 0.0)
    event: Dict[str, Any] = {
        "type": "CANCELLATION",
        "cancel_reason": "CUSTOMER_SUPPORT",
        "app_user_id": "",
        "product_id": body.product_id,
        "transaction_id": body.transaction_id,
        "store": body.store,
        "price": price,
        "price_in_purchased_currency": price,
        "currency": "USD",
        "environment": "SANDBOX",
    }
    return _fake_iap_result(event)
