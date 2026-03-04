"""
Unit tests for StripeAdminService.

Tests cover:
- store_payment data extraction from expanded Checkout Sessions
- process_refund validation and Stripe API interaction
- get_revenue_summary aggregation and caching
- update_payment_status
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime


def make_mock_firestore():
    """Create a mock Firestore client with common patterns."""
    db = Mock()
    collection = Mock()
    doc_ref = Mock()
    db.collection.return_value = collection
    collection.document.return_value = doc_ref
    return db, collection, doc_ref


@pytest.fixture
def service():
    """Create a StripeAdminService with mocked dependencies."""
    with patch("backend.services.stripe_admin_service.get_settings") as mock_settings, \
         patch("backend.services.stripe_admin_service.firestore") as mock_fs:
        mock_settings.return_value = Mock()
        mock_fs.Client.return_value = Mock()
        from backend.services.stripe_admin_service import StripeAdminService
        svc = StripeAdminService()
        return svc


class TestStorePayment:
    """Tests for store_payment data extraction."""

    def test_stores_expanded_session_correctly(self, service):
        """Test extracting all fields from a fully expanded Checkout Session."""
        session = {
            "id": "cs_test_123",
            "amount_total": 5000,
            "currency": "usd",
            "livemode": True,
            "customer_email": "user@example.com",
            "customer": "cus_abc",
            "customer_details": {"name": "John Doe"},
            "metadata": {
                "order_type": "credit_purchase",
                "package_id": "10_credits",
                "credits": "10",
                "user_email": "user@example.com",
            },
            "total_details": {"amount_discount": 0},
            "payment_intent": {
                "id": "pi_test_456",
                "latest_charge": {
                    "id": "ch_test_789",
                    "payment_method_details": {
                        "type": "card",
                        "card": {"brand": "visa", "last4": "4242"},
                    },
                    "balance_transaction": {
                        "fee": 175,
                        "net": 4825,
                    },
                },
            },
        }

        result = service.store_payment(session)

        assert result is True
        # Verify Firestore was called
        service.db.collection.assert_called_with("stripe_payments")
        doc_ref = service.db.collection.return_value.document.return_value
        doc_ref.set.assert_called_once()

        # Verify stored data
        stored = doc_ref.set.call_args[0][0]
        assert stored["session_id"] == "cs_test_123"
        assert stored["payment_intent_id"] == "pi_test_456"
        assert stored["charge_id"] == "ch_test_789"
        assert stored["amount_total"] == 5000
        assert stored["stripe_fee"] == 175
        assert stored["net_amount"] == 4825
        assert stored["customer_email"] == "user@example.com"
        assert stored["customer_name"] == "John Doe"
        assert stored["payment_method_type"] == "card"
        assert stored["card_brand"] == "visa"
        assert stored["card_last4"] == "4242"
        assert stored["order_type"] == "credit_purchase"
        assert stored["credits_granted"] == 10
        assert stored["status"] == "succeeded"
        assert stored["is_test"] is False
        assert stored["refund_amount"] == 0

    def test_handles_unexpanded_payment_intent(self, service):
        """Test storing when payment_intent is just a string ID (not expanded)."""
        session = {
            "id": "cs_test_abc",
            "amount_total": 3000,
            "currency": "usd",
            "livemode": False,
            "metadata": {"order_type": "credit_purchase", "credits": "5"},
            "payment_intent": "pi_string_only",
        }

        result = service.store_payment(session)
        assert result is True

        stored = service.db.collection.return_value.document.return_value.set.call_args[0][0]
        assert stored["payment_intent_id"] == "pi_string_only"
        assert stored["charge_id"] == ""
        assert stored["stripe_fee"] == 0
        assert stored["net_amount"] == 0
        assert stored["is_test"] is True

    def test_handles_made_for_you_order(self, service):
        """Test storing a made-for-you order with artist/title metadata."""
        session = {
            "id": "cs_mfy_123",
            "amount_total": 5000,
            "currency": "usd",
            "livemode": True,
            "metadata": {
                "order_type": "made_for_you",
                "artist": "Test Artist",
                "title": "Test Song",
                "customer_email": "buyer@example.com",
                "job_id": "job_abc",
            },
            "payment_intent": {"id": "pi_mfy", "latest_charge": "ch_string"},
        }

        result = service.store_payment(session)
        assert result is True

        stored = service.db.collection.return_value.document.return_value.set.call_args[0][0]
        assert stored["order_type"] == "made_for_you"
        assert stored["artist"] == "Test Artist"
        assert stored["title"] == "Test Song"
        assert stored["job_id"] == "job_abc"
        assert "Made For You" in stored["product_description"]

    def test_handles_missing_metadata_gracefully(self, service):
        """Test storing payment with minimal/missing data doesn't crash."""
        session = {
            "id": "cs_minimal",
            "amount_total": 1000,
        }

        result = service.store_payment(session)
        assert result is True

        stored = service.db.collection.return_value.document.return_value.set.call_args[0][0]
        assert stored["session_id"] == "cs_minimal"
        assert stored["amount_total"] == 1000
        assert stored["currency"] == "usd"
        assert stored["customer_email"] == ""

    def test_returns_false_on_firestore_error(self, service):
        """Test that Firestore errors are caught and return False."""
        service.db.collection.side_effect = Exception("Firestore down")

        result = service.store_payment({"id": "cs_fail"})
        assert result is False

    def test_invalidates_revenue_cache(self, service):
        """Test that storing a payment invalidates the revenue cache."""
        service._revenue_cache = {"some": "data"}

        session = {
            "id": "cs_cache_test",
            "amount_total": 1000,
            "metadata": {},
            "payment_intent": "pi_cache",
        }
        service.store_payment(session)
        assert service._revenue_cache is None


class TestProcessRefund:
    """Tests for process_refund logic."""

    def _setup_payment_doc(self, service, data):
        """Helper to set up a mock Firestore doc for refund tests."""
        doc_ref = Mock()
        doc = Mock()
        doc.exists = True
        doc.to_dict.return_value = data
        doc_ref.get.return_value = doc
        service.db.collection.return_value.document.return_value = doc_ref
        return doc_ref

    def test_full_refund_success(self, service):
        """Test full refund for a payment."""
        doc_ref = self._setup_payment_doc(service, {
            "payment_intent_id": "pi_test",
            "amount_total": 5000,
            "refund_amount": 0,
        })

        with patch("backend.services.stripe_admin_service.stripe") as mock_stripe:
            mock_stripe.Refund.create.return_value = {"id": "re_test_123"}

            success, message = service.process_refund("cs_test")

            assert success is True
            assert "$50.00" in message
            mock_stripe.Refund.create.assert_called_once_with(
                payment_intent="pi_test",
                amount=5000,
                reason="requested_by_customer",
            )
            doc_ref.update.assert_called_once()
            update_data = doc_ref.update.call_args[0][0]
            assert update_data["status"] == "refunded"
            assert update_data["refund_amount"] == 5000

    def test_partial_refund_success(self, service):
        """Test partial refund."""
        self._setup_payment_doc(service, {
            "payment_intent_id": "pi_test",
            "amount_total": 5000,
            "refund_amount": 0,
        })

        with patch("backend.services.stripe_admin_service.stripe") as mock_stripe:
            mock_stripe.Refund.create.return_value = {"id": "re_partial"}

            success, message = service.process_refund("cs_test", amount=2500)

            assert success is True
            assert "$25.00" in message
            doc_ref = service.db.collection.return_value.document.return_value
            update_data = doc_ref.update.call_args[0][0]
            assert update_data["status"] == "partially_refunded"
            assert update_data["refund_amount"] == 2500

    def test_refund_after_partial_refund(self, service):
        """Test refund on already partially refunded payment."""
        self._setup_payment_doc(service, {
            "payment_intent_id": "pi_test",
            "amount_total": 5000,
            "refund_amount": 2000,
        })

        with patch("backend.services.stripe_admin_service.stripe") as mock_stripe:
            mock_stripe.Refund.create.return_value = {"id": "re_rest"}

            success, message = service.process_refund("cs_test", amount=3000)

            assert success is True
            doc_ref = service.db.collection.return_value.document.return_value
            update_data = doc_ref.update.call_args[0][0]
            assert update_data["status"] == "refunded"
            assert update_data["refund_amount"] == 5000

    def test_refund_exceeding_original_fails(self, service):
        """Test that refunding more than original amount fails."""
        self._setup_payment_doc(service, {
            "payment_intent_id": "pi_test",
            "amount_total": 5000,
            "refund_amount": 3000,
        })

        success, message = service.process_refund("cs_test", amount=3000)

        assert success is False
        assert "exceed" in message.lower()

    def test_refund_payment_not_found(self, service):
        """Test refund when payment doesn't exist."""
        doc = Mock()
        doc.exists = False
        service.db.collection.return_value.document.return_value.get.return_value = doc

        success, message = service.process_refund("cs_nonexistent")

        assert success is False
        assert "not found" in message.lower()

    def test_refund_no_payment_intent_id(self, service):
        """Test refund when payment has no payment_intent_id."""
        self._setup_payment_doc(service, {
            "payment_intent_id": "",
            "amount_total": 5000,
            "refund_amount": 0,
        })

        success, message = service.process_refund("cs_test")

        assert success is False
        assert "payment intent" in message.lower()

    def test_refund_zero_amount_fails(self, service):
        """Test that zero refund amount fails."""
        self._setup_payment_doc(service, {
            "payment_intent_id": "pi_test",
            "amount_total": 5000,
            "refund_amount": 5000,  # Already fully refunded
        })

        success, message = service.process_refund("cs_test")

        assert success is False
        assert "invalid" in message.lower()

    def test_stripe_error_handled(self, service):
        """Test that Stripe API errors are caught and reported."""
        self._setup_payment_doc(service, {
            "payment_intent_id": "pi_test",
            "amount_total": 5000,
            "refund_amount": 0,
        })

        with patch("backend.services.stripe_admin_service.stripe") as mock_stripe:
            mock_stripe.error.StripeError = Exception
            mock_stripe.Refund.create.side_effect = Exception("Card declined")

            success, message = service.process_refund("cs_test")

            assert success is False

    def test_refund_invalidates_cache(self, service):
        """Test that refund invalidates revenue cache."""
        service._revenue_cache = {"cached": True}
        self._setup_payment_doc(service, {
            "payment_intent_id": "pi_test",
            "amount_total": 5000,
            "refund_amount": 0,
        })

        with patch("backend.services.stripe_admin_service.stripe") as mock_stripe:
            mock_stripe.Refund.create.return_value = {"id": "re_cache"}
            service.process_refund("cs_test")

        assert service._revenue_cache is None


class TestGetRevenueSummary:
    """Tests for get_revenue_summary aggregation."""

    def test_aggregates_correctly(self, service):
        """Test revenue summary aggregation."""
        # Mock streaming docs
        docs = []
        for i, (amount, fee, net, refund, is_test, order_type) in enumerate([
            (5000, 175, 4825, 0, False, "credit_purchase"),
            (3000, 117, 2883, 0, False, "credit_purchase"),
            (5000, 175, 4825, 0, False, "made_for_you"),
        ]):
            doc = Mock()
            doc.to_dict.return_value = {
                "amount_total": amount,
                "stripe_fee": fee,
                "net_amount": net,
                "refund_amount": refund,
                "is_test": is_test,
                "order_type": order_type,
                "created_at": datetime.utcnow(),
            }
            docs.append(doc)

        query = Mock()
        query.stream.return_value = iter(docs)
        collection = Mock()
        collection.where.return_value = query
        service.db.collection.return_value = collection

        result = service.get_revenue_summary(days=30, exclude_test=True)

        assert result["total_gross"] == 13000
        assert result["total_fees"] == 467
        assert result["total_net"] == 12533
        assert result["transaction_count"] == 3
        assert result["average_order_value"] == round(13000 / 3)
        assert result["revenue_by_type"]["credit_purchase"] == 8000
        assert result["revenue_by_type"]["made_for_you"] == 5000

    def test_excludes_test_transactions(self, service):
        """Test that test transactions are excluded when flag is set."""
        docs = []
        for is_test in [False, True, False]:
            doc = Mock()
            doc.to_dict.return_value = {
                "amount_total": 1000,
                "stripe_fee": 0,
                "net_amount": 1000,
                "refund_amount": 0,
                "is_test": is_test,
                "order_type": "credit_purchase",
            }
            docs.append(doc)

        query = Mock()
        query.stream.return_value = iter(docs)
        collection = Mock()
        collection.where.return_value = query
        service.db.collection.return_value = collection

        result = service.get_revenue_summary(days=30, exclude_test=True)
        assert result["transaction_count"] == 2
        assert result["total_gross"] == 2000

    def test_empty_collection_returns_zeros(self, service):
        """Test revenue summary with no payments."""
        query = Mock()
        query.stream.return_value = iter([])
        collection = Mock()
        collection.where.return_value = query
        service.db.collection.return_value = collection

        result = service.get_revenue_summary(days=30)

        assert result["total_gross"] == 0
        assert result["transaction_count"] == 0
        assert result["average_order_value"] == 0

    def test_uses_cache_within_ttl(self, service):
        """Test that cached results are returned within TTL."""
        import time
        cached = {
            "_key": "30_True",
            "total_gross": 999,
            "transaction_count": 1,
        }
        service._revenue_cache = cached
        service._revenue_cache_time = time.time()

        result = service.get_revenue_summary(days=30, exclude_test=True)

        assert result == cached
        service.db.collection.assert_not_called()


class TestUpdatePaymentStatus:
    """Tests for update_payment_status."""

    def test_updates_existing_payment(self, service):
        """Test updating status of existing payment."""
        doc = Mock()
        doc.exists = True
        doc_ref = Mock()
        doc_ref.get.return_value = doc
        service.db.collection.return_value.document.return_value = doc_ref

        result = service.update_payment_status("cs_test", {"status": "refunded"})

        assert result is True
        doc_ref.update.assert_called_with({"status": "refunded"})

    def test_returns_false_for_missing_payment(self, service):
        """Test updating non-existent payment returns False."""
        doc = Mock()
        doc.exists = False
        service.db.collection.return_value.document.return_value.get.return_value = doc

        result = service.update_payment_status("cs_missing", {"status": "refunded"})

        assert result is False
