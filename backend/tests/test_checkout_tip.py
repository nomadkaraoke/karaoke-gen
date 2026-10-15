"""Tests for the opt-in checkout tip (Stripe Checkout optional item)."""
import os
from unittest.mock import MagicMock, patch

import pytest
import stripe

from backend.services.stripe_service import (
    TIP_DEFAULT_UNITS,
    TIP_MAX_UNITS,
    TIP_PRICE_LOOKUP_KEY,
    StripeService,
)


@pytest.fixture
def service():
    with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_fake"}):
        return StripeService()


def _session_mock():
    m = MagicMock()
    m.id = "cs_test_1"
    m.url = "https://checkout.stripe.com/x"
    return m


class TestTipPrice:
    def test_reuses_existing_price_by_lookup_key(self, service):
        with patch("stripe.Price.list") as mock_list, patch("stripe.Price.create") as mock_create:
            mock_list.return_value = MagicMock(data=[MagicMock(id="price_tip")])
            assert service.get_tip_price_id() == "price_tip"
            assert service.get_tip_price_id() == "price_tip"  # cached
            mock_list.assert_called_once_with(lookup_keys=[TIP_PRICE_LOOKUP_KEY], active=True, limit=1)
            mock_create.assert_not_called()

    def test_creates_price_when_missing(self, service):
        with patch("stripe.Price.list") as mock_list, patch("stripe.Price.create") as mock_create:
            mock_list.return_value = MagicMock(data=[])
            mock_create.return_value = MagicMock(id="price_new")
            assert service.get_tip_price_id() == "price_new"
            kwargs = mock_create.call_args.kwargs
            assert kwargs["unit_amount"] == 100
            assert kwargs["lookup_key"] == TIP_PRICE_LOOKUP_KEY

    def test_stripe_error_means_no_tip(self, service):
        with patch("stripe.Price.list", side_effect=stripe.error.APIConnectionError("down")):
            assert service.get_tip_price_id() is None

    def test_kill_switch(self, service):
        with patch("backend.services.stripe_service.get_settings") as gs, patch("stripe.Price.list") as mock_list:
            gs.return_value.checkout_tip_enabled = False
            assert service.get_tip_price_id() is None
            mock_list.assert_not_called()


class TestCheckoutOffersTip:
    def test_credit_checkout_has_opt_in_optional_item(self, service):
        with patch.object(service, "get_tip_price_id", return_value="price_tip"), \
             patch("stripe.checkout.Session.create", return_value=_session_mock()) as mock_create:
            ok, _, _ = service.create_checkout_session("1_credit", "a@example.com")
        assert ok
        params = mock_create.call_args.kwargs
        assert params["optional_items"] == [{
            "price": "price_tip",
            "quantity": TIP_DEFAULT_UNITS,
            "adjustable_quantity": {"enabled": True, "minimum": 1, "maximum": TIP_MAX_UNITS},
        }]
        # Opt-in only: the tip is never pre-added to the cart
        assert len(params["line_items"]) == 1
        assert params["metadata"]["credits"] == "1"

    def test_made_for_you_checkout_has_tip(self, service):
        with patch.object(service, "get_tip_price_id", return_value="price_tip"), \
             patch("stripe.checkout.Session.create", return_value=_session_mock()) as mock_create:
            service.create_made_for_you_checkout_session("a@example.com", "Artist", "Song")
        assert mock_create.call_args.kwargs["optional_items"][0]["price"] == "price_tip"

    def test_no_tip_price_means_no_optional_items(self, service):
        with patch.object(service, "get_tip_price_id", return_value=None), \
             patch("stripe.checkout.Session.create", return_value=_session_mock()) as mock_create:
            service.create_checkout_session("1_credit", "a@example.com", coupon_id="referral-50pct")
        params = mock_create.call_args.kwargs
        assert "optional_items" not in params
        assert params["discounts"] == [{"coupon": "referral-50pct"}]


class TestTipCents:
    def test_sums_only_tip_lines(self):
        session = {"line_items": {"data": [
            {"amount_total": 1000, "price": {"lookup_key": None}},
            {"amount_total": 500, "price": {"lookup_key": TIP_PRICE_LOOKUP_KEY}},
        ]}}
        assert StripeService.tip_cents_from_session(session) == 500

    def test_missing_line_items_is_zero(self):
        assert StripeService.tip_cents_from_session({}) == 0
        assert StripeService.tip_cents_from_session({"line_items": None}) == 0

    def test_get_tip_cents_swallows_errors(self, service):
        with patch("stripe.checkout.Session.retrieve", side_effect=stripe.error.APIConnectionError("x")):
            assert service.get_tip_cents("cs_1") is None
        assert service.get_tip_cents("") is None

    def test_get_tip_cents_expands_line_items(self, service):
        with patch("stripe.checkout.Session.retrieve") as mock_retrieve:
            mock_retrieve.return_value = {"line_items": {"data": [
                {"amount_total": 300, "price": {"lookup_key": TIP_PRICE_LOOKUP_KEY}},
            ]}}
            assert service.get_tip_cents("cs_1") == 300
            mock_retrieve.assert_called_once_with("cs_1", expand=["line_items"])


class TestReferralBase:
    def test_subtracts_known_tip(self, service):
        with patch.object(service, "get_tip_cents", return_value=500):
            assert service.referral_base_cents({"id": "cs", "amount_total": 1500, "metadata": {"package_id": "1_credit"}}) == 1000

    def test_unknown_tip_caps_at_package_price(self, service):
        with patch.object(service, "get_tip_cents", return_value=None):
            assert service.referral_base_cents({"id": "cs", "amount_total": 1500, "metadata": {"package_id": "1_credit"}}) == 1000
            # Discounted purchase below list price is used as-is
            assert service.referral_base_cents({"id": "cs", "amount_total": 500, "metadata": {"package_id": "1_credit"}}) == 500
