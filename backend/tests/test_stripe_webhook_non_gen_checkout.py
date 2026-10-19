"""Stripe webhook: checkouts that didn't come from gen (e.g. a Stripe Payment Link).

A Payment Link session carries no gen metadata (no user_email / credits /
order_type) and customer_email=None. It must not log ERROR (the error monitor
pages #alerts on every new ERROR pattern) and must still be stored in
stripe_payments so revenue reporting includes it.
"""
import logging
from unittest.mock import MagicMock, patch

import pytest

from backend.services.stripe_service import StripeService


PAYMENT_LINK_SESSION = {
    "id": "cs_live_plink_test",
    "object": "checkout.session",
    "amount_total": 3600,
    "currency": "usd",
    "livemode": True,
    "metadata": {},
    "customer_email": None,
    "customer_details": {"email": "friend@example.com", "name": "A Friend"},
    "payment_link": "plink_123",
}


class TestIsGenCheckout:
    def test_payment_link_session_is_not_gen(self):
        assert StripeService.is_gen_checkout(PAYMENT_LINK_SESSION) is False

    def test_credit_purchase_is_gen(self):
        session = {"metadata": {"user_email": "a@b.com", "credits": "5", "package_id": "5_credits"}}
        assert StripeService.is_gen_checkout(session) is True

    def test_made_for_you_is_gen(self):
        assert StripeService.is_gen_checkout({"metadata": {"order_type": "made_for_you"}}) is True

    def test_null_metadata_is_not_gen(self):
        assert StripeService.is_gen_checkout({"metadata": None}) is False


@pytest.mark.asyncio
async def test_payment_link_checkout_logs_info_and_stores_payment(caplog):
    from backend.api.routes import users as users_routes

    event = {"id": "evt_1", "type": "checkout.session.completed",
             "data": {"object": dict(PAYMENT_LINK_SESSION)}}

    stripe_service = MagicMock()
    stripe_service.verify_webhook_signature.return_value = (True, event, "ok")
    user_service = MagicMock()
    user_service.is_stripe_session_processed.return_value = False
    user_service.get_user_doc_ref.return_value = None
    email_service = MagicMock()
    admin_service = MagicMock()

    request = MagicMock()

    async def body():
        return b"{}"
    request.body = body

    expanded = dict(PAYMENT_LINK_SESSION)
    with patch("backend.services.stripe_admin_service.get_stripe_admin_service",
               return_value=admin_service), \
         patch("stripe.checkout.Session.retrieve", return_value=expanded), \
         caplog.at_level(logging.DEBUG):
        result = await users_routes.stripe_webhook(
            request=request,
            stripe_signature="sig",
            stripe_service=stripe_service,
            user_service=user_service,
            email_service=email_service,
        )

    assert result["status"] == "received"
    # Never treated as a credit purchase
    stripe_service.handle_checkout_completed.assert_not_called()
    user_service.add_credits.assert_not_called()
    # Still recorded for revenue reporting
    admin_service.store_payment.assert_called_once()
    # Logged at INFO, nothing at ERROR
    assert any("non-gen checkout" in r.message and r.levelno == logging.INFO for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    # The success audit row carries the fallback email
    final_call = admin_service.log_webhook_event.call_args_list[-1]
    assert final_call.kwargs["customer_email"] == "friend@example.com"
    assert final_call.kwargs["status"] == "success"
