"""Tests for checkout ``return_to: "app"`` and the https → app-scheme bounce.

Live Stripe only takes https return URLs, so a mobile checkout returns to gen's
own ``GET /api/users/credits/app-return``, which 302s to
``{MOBILE_APP_SCHEME}://checkout?status=…``. The client only ever sends
``return_to: "app"`` (never a URL), so there's no open redirect.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import users as users_routes
from backend.services.stripe_service import get_stripe_service

EMAIL = "buyer@example.com"


@pytest.fixture
def stripe_service():
    svc = MagicMock()
    svc.can_checkout.return_value = True
    svc.create_checkout_session.return_value = (True, "https://checkout.stripe.com/c/x", "ok")
    return svc


@pytest.fixture
def client(monkeypatch, stripe_service):
    # No referral / promo discounts in these tests (both are best-effort lookups).
    from backend.services import conversion_emails, referral_service

    monkeypatch.setattr(referral_service, "get_referral_service",
                        lambda: SimpleNamespace(get_discount_for_checkout=lambda email: None))
    monkeypatch.setattr(conversion_emails, "promo_coupon_for_checkout", lambda *a, **k: None)
    monkeypatch.setenv("BACKEND_URL", "https://api.example.test/")
    app = FastAPI()
    app.include_router(users_routes.router, prefix="/api")
    app.dependency_overrides[get_stripe_service] = lambda: stripe_service
    return TestClient(app)


def _checkout(client, **extra):
    return client.post("/api/users/credits/checkout", json={"package_id": "1_credit", "email": EMAIL, **extra})


def test_checkout_return_to_app_sets_bounce_urls(client, stripe_service):
    resp = _checkout(client, return_to="app")
    assert resp.status_code == 200
    assert resp.json()["checkout_url"] == "https://checkout.stripe.com/c/x"
    kwargs = stripe_service.create_checkout_session.call_args.kwargs
    assert kwargs["success_url"] == (
        "https://api.example.test/api/users/credits/app-return?status=success&session_id={CHECKOUT_SESSION_ID}"
    )
    assert kwargs["cancel_url"] == "https://api.example.test/api/users/credits/app-return?status=cancelled"


def test_checkout_default_urls_unchanged(client, stripe_service):
    assert _checkout(client).status_code == 200
    kwargs = stripe_service.create_checkout_session.call_args.kwargs
    assert kwargs.get("success_url") is None and kwargs.get("cancel_url") is None


@pytest.mark.parametrize("bad", ["https://evil.example/x", "web", ""])
def test_checkout_return_to_only_accepts_app(client, bad):
    assert _checkout(client, return_to=bad).status_code == 422


def test_bounce_base_is_local_stack_url_in_fake_mode(monkeypatch):
    monkeypatch.setattr(users_routes, "fake_workers_enabled", lambda: True)
    monkeypatch.setenv("LOCAL_STACK_API_URL", "http://10.0.2.2:8010/")
    assert users_routes.public_api_base_url() == "http://10.0.2.2:8010"


def test_bounce_base_defaults_to_production_api(monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)
    monkeypatch.setattr(users_routes, "fake_workers_enabled", lambda: False)
    assert users_routes.public_api_base_url() == "https://api.nomadkaraoke.com"


def test_app_return_redirects_success(client):
    resp = client.get(
        "/api/users/credits/app-return",
        params={"status": "success", "session_id": "cs_test_123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "nomadkaraoke://checkout?status=success"
    # Browsers that block scheme redirects get a link back to the app.
    assert 'href="nomadkaraoke://checkout?status=success"' in resp.text
    assert "cs_test_123" not in resp.text


@pytest.mark.parametrize("status", ["cancelled", "bogus", "", None])
def test_app_return_redirects_cancelled_for_unknown_status(client, status):
    params = {"status": status} if status is not None else {}
    resp = client.get("/api/users/credits/app-return", params=params, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "nomadkaraoke://checkout?status=cancelled"


def test_app_return_uses_configured_scheme(client, monkeypatch):
    monkeypatch.setattr(users_routes, "get_settings", lambda: SimpleNamespace(mobile_app_scheme="nkdev"))
    resp = client.get("/api/users/credits/app-return?status=success", follow_redirects=False)
    assert resp.headers["location"] == "nkdev://checkout?status=success"


def test_fake_checkout_cancel_reaches_app_return(client, stripe_service, monkeypatch):
    """Local stack: the fake checkout's Cancel lands on the same app-return bounce."""
    from backend.api.routes import test_support
    from backend.services.fake_workers import checkout as fake_checkout
    from backend.tests.test_fake_workers import FakeDB

    db = FakeDB()
    monkeypatch.setattr(fake_checkout, "_db", lambda: db)
    monkeypatch.setattr(users_routes, "fake_workers_enabled", lambda: True)
    monkeypatch.setenv("LOCAL_STACK_API_URL", "http://127.0.0.1:8010")

    def create(package_id, user_email, success_url=None, cancel_url=None, coupon_id=None):
        session = fake_checkout.create_fake_checkout({
            "customer_email": user_email, "metadata": {"credits": "1"},
            "success_url": success_url, "cancel_url": cancel_url,
        })
        return True, session.url, "ok"

    stripe_service.create_checkout_session.side_effect = create
    checkout_url = _checkout(client, return_to="app").json()["checkout_url"]
    session_id = checkout_url.rsplit("/", 1)[1]

    app = FastAPI()
    app.include_router(test_support.router, prefix="/api")
    app.include_router(users_routes.router, prefix="/api")
    test_client = TestClient(app)
    with patch("backend.api.routes.internal.test_webhook") as webhook:
        resp = test_client.post(f"/api/test/fake-checkout/{session_id}/cancel", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "http://127.0.0.1:8010/api/users/credits/app-return?status=cancelled"
    webhook.assert_not_called()  # cancelling never completes the purchase (no credits)
    assert fake_checkout.get_checkout(session_id)["status"] == "cancelled"

    bounce = test_client.get("/api/users/credits/app-return?status=cancelled", follow_redirects=False)
    assert bounce.headers["location"] == "nomadkaraoke://checkout?status=cancelled"
