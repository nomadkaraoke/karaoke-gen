"""
Tests for admin payment API endpoints.

Tests cover:
- Refund endpoint with credit deduction logic
- Payment summary endpoint
- Payment list endpoint
- Payment detail endpoint (404 handling)
"""

import pytest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.api.routes.admin import router
from backend.api.dependencies import require_admin


# Create a test app with the admin router
app = FastAPI()
app.include_router(router, prefix="/api")


def get_mock_admin():
    """Override for require_admin dependency."""
    from backend.api.dependencies import AuthResult, UserType
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=999,
        message="Admin authenticated",
        user_email="admin@example.com",
        is_admin=True,
    )


app.dependency_overrides[require_admin] = get_mock_admin


@pytest.fixture
def client():
    return TestClient(app)


class TestRefundEndpoint:
    """Tests for POST /api/admin/payments/{session_id}/refund."""

    def test_full_refund_success(self, client):
        """Test successful full refund."""
        mock_service = Mock()
        mock_service.get_payment_detail.return_value = {
            "session_id": "cs_test",
            "order_type": "credit_purchase",
            "credits_granted": 10,
            "amount_total": 5000,
            "customer_email": "user@example.com",
        }
        mock_service.process_refund.return_value = (True, "Refund of $50.00 processed")

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service), \
             patch("backend.api.routes.admin.get_user_service") as mock_user_svc:
            mock_us = Mock()
            mock_user_svc.return_value = mock_us

            response = client.post(
                "/api/admin/payments/cs_test/refund",
                json={"reason": "requested_by_customer"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["session_id"] == "cs_test"

            # Verify credit deduction was called
            mock_us.add_credits.assert_called_once()
            call_kwargs = mock_us.add_credits.call_args[1]
            assert call_kwargs["email"] == "user@example.com"
            assert call_kwargs["amount"] == -10  # All credits deducted for full refund

    def test_partial_refund_deducts_proportional_credits(self, client):
        """Test that partial refund deducts proportional credits."""
        mock_service = Mock()
        mock_service.get_payment_detail.return_value = {
            "session_id": "cs_test",
            "order_type": "credit_purchase",
            "credits_granted": 10,
            "amount_total": 5000,
            "customer_email": "user@example.com",
        }
        mock_service.process_refund.return_value = (True, "Refund of $25.00 processed")

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service), \
             patch("backend.api.routes.admin.get_user_service") as mock_user_svc:
            mock_us = Mock()
            mock_user_svc.return_value = mock_us

            response = client.post(
                "/api/admin/payments/cs_test/refund",
                json={"amount": 2500, "reason": "requested_by_customer"},
            )

            assert response.status_code == 200
            # Proportional: 10 credits * (2500/5000) = 5 credits
            call_kwargs = mock_us.add_credits.call_args[1]
            assert call_kwargs["amount"] == -5

    def test_made_for_you_refund_skips_credit_deduction(self, client):
        """Test that made-for-you refund doesn't try to deduct credits."""
        mock_service = Mock()
        mock_service.get_payment_detail.return_value = {
            "session_id": "cs_mfy",
            "order_type": "made_for_you",
            "credits_granted": 0,
            "amount_total": 5000,
            "customer_email": "user@example.com",
        }
        mock_service.process_refund.return_value = (True, "Refund processed")

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service), \
             patch("backend.api.routes.admin.get_user_service") as mock_user_svc:
            mock_us = Mock()
            mock_user_svc.return_value = mock_us

            response = client.post(
                "/api/admin/payments/cs_mfy/refund",
                json={"reason": "duplicate"},
            )

            assert response.status_code == 200
            mock_us.add_credits.assert_not_called()

    def test_refund_payment_not_found_returns_404(self, client):
        """Test 404 when payment doesn't exist."""
        mock_service = Mock()
        mock_service.get_payment_detail.return_value = None

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service):
            response = client.post(
                "/api/admin/payments/cs_nonexistent/refund",
                json={"reason": "requested_by_customer"},
            )

            assert response.status_code == 404

    def test_refund_stripe_failure_returns_400(self, client):
        """Test that Stripe refund failure returns 400."""
        mock_service = Mock()
        mock_service.get_payment_detail.return_value = {
            "session_id": "cs_test",
            "order_type": "credit_purchase",
            "credits_granted": 0,
            "amount_total": 5000,
        }
        mock_service.process_refund.return_value = (False, "Card declined")

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service):
            response = client.post(
                "/api/admin/payments/cs_test/refund",
                json={"reason": "requested_by_customer"},
            )

            assert response.status_code == 400
            assert "Card declined" in response.json()["detail"]

    def test_credit_deduction_failure_doesnt_fail_refund(self, client):
        """Test that credit deduction failure doesn't roll back the refund."""
        mock_service = Mock()
        mock_service.get_payment_detail.return_value = {
            "session_id": "cs_test",
            "order_type": "credit_purchase",
            "credits_granted": 10,
            "amount_total": 5000,
            "customer_email": "user@example.com",
        }
        mock_service.process_refund.return_value = (True, "Refund processed")

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service), \
             patch("backend.api.routes.admin.get_user_service") as mock_user_svc:
            mock_us = Mock()
            mock_us.add_credits.side_effect = Exception("Firestore down")
            mock_user_svc.return_value = mock_us

            response = client.post(
                "/api/admin/payments/cs_test/refund",
                json={"reason": "requested_by_customer"},
            )

            # Refund should still succeed
            assert response.status_code == 200
            assert response.json()["success"] is True


class TestPaymentSummaryEndpoint:
    """Tests for GET /api/admin/payments/summary."""

    def test_returns_revenue_summary(self, client):
        """Test that summary endpoint returns data."""
        mock_service = Mock()
        mock_service.get_revenue_summary.return_value = {
            "total_gross": 10000,
            "total_fees": 350,
            "total_net": 9650,
            "total_refunds": 0,
            "transaction_count": 2,
            "average_order_value": 5000,
            "revenue_by_type": {"credit_purchase": 10000},
        }

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service):
            response = client.get("/api/admin/payments/summary?days=30")

            assert response.status_code == 200
            data = response.json()
            assert data["total_gross"] == 10000
            assert data["transaction_count"] == 2


class TestPaymentDetailEndpoint:
    """Tests for GET /api/admin/payments/{session_id}."""

    def test_returns_payment_detail(self, client):
        """Test getting payment detail."""
        mock_service = Mock()
        mock_service.get_payment_detail.return_value = {
            "session_id": "cs_test",
            "amount_total": 5000,
            "status": "succeeded",
        }

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service):
            response = client.get("/api/admin/payments/cs_test")

            assert response.status_code == 200
            assert response.json()["session_id"] == "cs_test"

    def test_returns_404_for_missing_payment(self, client):
        """Test 404 when payment not found."""
        mock_service = Mock()
        mock_service.get_payment_detail.return_value = None

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service):
            response = client.get("/api/admin/payments/cs_nonexistent")

            assert response.status_code == 404


class TestPaymentListEndpoint:
    """Tests for GET /api/admin/payments."""

    def test_returns_paginated_payments(self, client):
        """Test payment list with pagination."""
        mock_service = Mock()
        mock_service.list_payments.return_value = {
            "payments": [
                {"session_id": "cs_1", "amount_total": 5000},
                {"session_id": "cs_2", "amount_total": 3000},
            ],
            "total": 2,
            "has_more": False,
        }

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service):
            response = client.get("/api/admin/payments?limit=50&offset=0")

            assert response.status_code == 200
            data = response.json()
            assert len(data["payments"]) == 2
            assert data["total"] == 2

    def test_passes_filters_to_service(self, client):
        """Test that query params are passed to service."""
        mock_service = Mock()
        mock_service.list_payments.return_value = {
            "payments": [],
            "total": 0,
            "has_more": False,
        }

        with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=mock_service):
            response = client.get(
                "/api/admin/payments?order_type=credit_purchase&status=succeeded&email=user@example.com"
            )

            assert response.status_code == 200
            mock_service.list_payments.assert_called_once()
            call_kwargs = mock_service.list_payments.call_args[1]
            assert call_kwargs["order_type"] == "credit_purchase"
            assert call_kwargs["status"] == "succeeded"
            assert call_kwargs["email"] == "user@example.com"
