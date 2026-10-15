"""Fake Stripe Checkout for the local stack.

``StripeService`` calls :func:`create_fake_checkout` instead of
``stripe.checkout.Session.create`` when Stripe isn't configured and
FAKE_WORKERS is on. The returned URL is a tiny "checkout" page served by
``/api/test/fake-checkout/{id}``; paying it runs the same completion handler as
the real webhook (via ``/api/internal/test-webhook``'s logic), then redirects to
the session's success URL, just as Stripe would.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional

CHECKOUTS_COLLECTION = "local_stack_checkouts"
# Must start with "e2e-test-" so the shared test-webhook handler accepts it.
SESSION_PREFIX = "e2e-test-local-"


def public_api_url() -> str:
    """Base URL browsers / devices use to reach this API (LOCAL_STACK_API_URL)."""
    return os.getenv("LOCAL_STACK_API_URL", f"http://127.0.0.1:{os.getenv('PORT', '8000')}").rstrip("/")


def _db():
    from backend.services.firestore_service import FirestoreService

    return FirestoreService().db


def _amount_cents(session_params: Dict[str, Any]) -> int:
    return sum(
        (item.get("price_data") or {}).get("unit_amount", 0) * item.get("quantity", 1)
        for item in session_params.get("line_items") or []
    )


def create_fake_checkout(session_params: Dict[str, Any]) -> SimpleNamespace:
    session_id = SESSION_PREFIX + secrets.token_hex(8)
    line_items = session_params.get("line_items") or [{}]
    _db().collection(CHECKOUTS_COLLECTION).document(session_id).set({
        "session_id": session_id,
        "customer_email": session_params.get("customer_email"),
        "metadata": session_params.get("metadata") or {},
        "success_url": session_params.get("success_url"),
        "cancel_url": session_params.get("cancel_url"),
        "product_name": ((line_items[0].get("price_data") or {}).get("product_data") or {}).get("name"),
        "amount_cents": _amount_cents(session_params),
        "status": "open",
        "created_at": datetime.now(timezone.utc),
    })
    return SimpleNamespace(id=session_id, url=f"{public_api_url()}/api/test/fake-checkout/{session_id}")


def get_checkout(session_id: str) -> Optional[Dict[str, Any]]:
    snap = _db().collection(CHECKOUTS_COLLECTION).document(session_id).get()
    return snap.to_dict() if snap.exists else None


def mark_checkout(session_id: str, status: str) -> None:
    _db().collection(CHECKOUTS_COLLECTION).document(session_id).update({"status": status})


def resolve_url(url: Optional[str], session_id: str) -> str:
    return (url or "/").replace("{CHECKOUT_SESSION_ID}", session_id)
