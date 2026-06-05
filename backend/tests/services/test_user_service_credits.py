"""
Tests for UserService.deduct_credits(amount) and the deduct_credit wrapper.

TDD: these tests are written BEFORE the implementation.

Construction pattern mirrors test_credit_evaluation.py:
- patch backend.services.user_service.firestore and get_settings
- instantiate UserService() directly
- seed Firestore via mock_db

USERS_COLLECTION = "gen_users"
CreditTransaction fields: id, amount, reason, job_id, created_at
Tuple return shape: (success: bool, remaining_credits: int, message: str)
"""
import uuid
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime


# ---------------------------------------------------------------------------
# Helper: build a minimal Firestore document snapshot dict for a user
# ---------------------------------------------------------------------------

def _make_user_doc(credits: int, transactions: list = None):
    """Return the dict that doc.to_dict() would return for a user document."""
    return {
        "email": "test@example.com",
        "credits": credits,
        "credit_transactions": transactions or [],
        "total_jobs_created": 0,
    }


def _make_mock_db(user_dict):
    """
    Build a mock Firestore db whose collection().document().get() returns
    a snapshot whose .to_dict() yields user_dict.
    """
    mock_db = MagicMock()

    # Snapshot
    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = user_dict

    # doc_ref.get(transaction=...) returns snapshot
    mock_doc_ref = MagicMock()
    mock_doc_ref.get.return_value = mock_snapshot

    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_doc_ref

    mock_db.collection.return_value = mock_collection

    # transaction() must return an object that can be used as a Firestore transaction.
    # The @firestore.transactional decorator calls the inner function with a
    # google.cloud.firestore.Transaction object; here we mock so that
    # deduct_in_transaction(fs_transaction) is invoked synchronously.
    mock_transaction = MagicMock()
    mock_db.transaction.return_value = mock_transaction

    return mock_db, mock_doc_ref, mock_snapshot, mock_transaction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def user_service_factory():
    """
    Factory: given a user dict, return an instantiated UserService whose db
    is wired to return that user from gen_users/<email>.

    Uses the same mock pattern as test_credit_evaluation.py:
        @patch('backend.services.user_service.get_settings')
        @patch('backend.services.user_service.firestore')
    """
    def _factory(user_dict, *, firestore_transactional_passthrough=True):
        with patch('backend.services.user_service.get_settings') as mock_settings, \
             patch('backend.services.user_service.firestore') as mock_firestore_module:

            mock_settings.return_value = MagicMock(google_cloud_project='test')
            mock_db, mock_doc_ref, mock_snapshot, mock_transaction = _make_mock_db(user_dict)
            mock_firestore_module.Client.return_value = mock_db

            # Make @firestore.transactional a pass-through decorator so that
            # deduct_in_transaction is called directly when we call it with the
            # mock transaction object.
            if firestore_transactional_passthrough:
                mock_firestore_module.transactional = lambda fn: fn

            from backend.services.user_service import UserService
            service = UserService()
            # Replace db with our wired mock (UserService.__init__ sets self.db)
            service.db = mock_db

            return service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction

    return _factory


# ---------------------------------------------------------------------------
# Tests: deduct_credits(email, job_id, amount, reason)
# ---------------------------------------------------------------------------

class TestDeductCredits:

    def test_deduct_3_from_5_returns_success_remaining_2(self, user_service_factory):
        """deduct_credits(amount=3) on a user with 5 credits → success, remaining=2."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credits(
            "test@example.com", "job-abc", amount=3
        )

        assert success is True
        assert remaining == 2
        # transaction.update must have been called with new balance 2
        mock_transaction.update.assert_called_once()
        update_args = mock_transaction.update.call_args[0]
        assert update_args[1]['credits'] == 2

    def test_deduct_4_from_2_returns_failure_unchanged(self, user_service_factory):
        """deduct_credits(amount=4) on user with 2 credits → failure, balance unchanged at 2."""
        user_dict = _make_user_doc(credits=2)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credits(
            "test@example.com", "job-xyz", amount=4
        )

        assert success is False
        assert remaining == 2  # unchanged
        assert "insufficient" in message.lower()
        # No mutation must have happened
        mock_transaction.update.assert_not_called()

    def test_deduct_1_from_1_via_deduct_credits_returns_success_remaining_0(self, user_service_factory):
        """deduct_credits(amount=1) on user with 1 credit → success, remaining=0."""
        user_dict = _make_user_doc(credits=1)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credits(
            "test@example.com", "job-001", amount=1
        )

        assert success is True
        assert remaining == 0
        mock_transaction.update.assert_called_once()
        update_args = mock_transaction.update.call_args[0]
        assert update_args[1]['credits'] == 0

    def test_invalid_amount_zero_returns_failure(self, user_service_factory):
        """deduct_credits(amount=0) → failure, message about positive amount."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credits(
            "test@example.com", "job-000", amount=0
        )

        assert success is False
        assert remaining == 0
        assert "positive" in message.lower()
        mock_transaction.update.assert_not_called()

    def test_invalid_amount_negative_returns_failure(self, user_service_factory):
        """deduct_credits(amount=-1) → failure, message about positive amount."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credits(
            "test@example.com", "job-000", amount=-1
        )

        assert success is False
        assert remaining == 0
        assert "positive" in message.lower()
        mock_transaction.update.assert_not_called()

    def test_credit_transaction_record_has_correct_amount(self, user_service_factory):
        """CreditTransaction stored has amount=-3 for deduct_credits(amount=3)."""
        user_dict = _make_user_doc(credits=10)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        service.deduct_credits("test@example.com", "job-txn", amount=3)

        mock_transaction.update.assert_called_once()
        update_kwargs = mock_transaction.update.call_args[0][1]
        transactions = update_kwargs['credit_transactions']
        assert len(transactions) == 1
        assert transactions[0]['amount'] == -3


# ---------------------------------------------------------------------------
# Tests: deduct_credit wrapper (legacy single-credit deduction)
# ---------------------------------------------------------------------------

class TestDeductCreditWrapper:

    def test_legacy_deduct_credit_deducts_exactly_1(self, user_service_factory):
        """deduct_credit(email, job_id) deducts exactly 1 and returns (True, n-1, message)."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credit(
            "test@example.com", "job-legacy"
        )

        assert success is True
        assert remaining == 4
        # Verify the transaction updated credits by exactly 1
        update_args = mock_transaction.update.call_args[0]
        assert update_args[1]['credits'] == 4

    def test_legacy_deduct_credit_returns_same_tuple_shape(self, user_service_factory):
        """deduct_credit returns (bool, int, str) tuple — same shape as before."""
        user_dict = _make_user_doc(credits=3)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        result = service.deduct_credit("test@example.com", "job-shape")

        assert isinstance(result, tuple)
        assert len(result) == 3
        success, remaining, message = result
        assert isinstance(success, bool)
        assert isinstance(remaining, int)
        assert isinstance(message, str)

    def test_legacy_deduct_credit_uses_default_reason(self, user_service_factory):
        """deduct_credit default reason is 'job_creation', passed through to CreditTransaction."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        service.deduct_credit("test@example.com", "job-reason")

        update_args = mock_transaction.update.call_args[0]
        transactions = update_args[1]['credit_transactions']
        assert transactions[0]['reason'] == 'job_creation'

    def test_legacy_deduct_credit_insufficient_returns_failure(self, user_service_factory):
        """deduct_credit when credits=0 returns (False, 0, msg) — same as before."""
        user_dict = _make_user_doc(credits=0)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credit(
            "test@example.com", "job-broke"
        )

        assert success is False
        assert remaining == 0
        mock_transaction.update.assert_not_called()
