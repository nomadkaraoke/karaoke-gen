"""
Tests for UserService.deduct_credits(amount) and the deduct_credit wrapper.

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

    Patches are started immediately and stopped at test teardown so they remain
    active when the test body calls service methods (important for asserting that
    @firestore.transactional is called at decoration time inside deduct_credits).
    """
    active_patches = []

    def _factory(user_dict):
        p_settings = patch('backend.services.user_service.get_settings')
        p_firestore = patch('backend.services.user_service.firestore')

        mock_settings = p_settings.start()
        mock_firestore_module = p_firestore.start()
        active_patches.extend([p_settings, p_firestore])

        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db, mock_doc_ref, mock_snapshot, mock_transaction = _make_mock_db(user_dict)
        mock_firestore_module.Client.return_value = mock_db

        # Make @firestore.transactional a MagicMock whose side_effect is a
        # pass-through (returns the inner function unchanged).  This means:
        #   1. The decorator still works — deduct_in_transaction is callable.
        #   2. We can assert that mock_firestore_module.transactional was
        #      called, so removing the @firestore.transactional decorator
        #      from the source would break the atomicity tests.
        mock_firestore_module.transactional = MagicMock(side_effect=lambda fn: fn)

        from backend.services.user_service import UserService
        service = UserService()
        # Replace db with our wired mock (UserService.__init__ sets self.db)
        service.db = mock_db

        return service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module

    yield _factory

    # Teardown: stop all patches after each test
    for p in active_patches:
        p.stop()


# ---------------------------------------------------------------------------
# Tests: deduct_credits(email, job_id, amount, reason)
# ---------------------------------------------------------------------------

class TestDeductCredits:

    def test_deduct_3_from_5_returns_success_remaining_2(self, user_service_factory):
        """deduct_credits(amount=3) on a user with 5 credits → success, remaining=2."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credits(
            "test@example.com", "job-abc", amount=3
        )

        assert success is True
        assert remaining == 2
        # transaction.update must have been called with new balance 2
        mock_transaction.update.assert_called_once()
        update_args = mock_transaction.update.call_args[0]
        assert update_args[1]['credits'] == 2

        # --- C2: verify the transactional path is actually exercised ---
        # If the @firestore.transactional decorator were removed, this would fail.
        mock_firestore_module.transactional.assert_called_once()
        mock_db.transaction.assert_called_once()

    def test_deduct_4_from_2_returns_failure_unchanged(self, user_service_factory):
        """deduct_credits(amount=4) on user with 2 credits → failure, balance unchanged at 2."""
        user_dict = _make_user_doc(credits=2)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

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
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

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
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

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
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

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
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

        service.deduct_credits("test@example.com", "job-txn", amount=3)

        mock_transaction.update.assert_called_once()
        update_kwargs = mock_transaction.update.call_args[0][1]
        transactions = update_kwargs['credit_transactions']
        assert len(transactions) == 1
        assert transactions[0]['amount'] == -3

    # --- I2: total_jobs_created assertions ---

    def test_count_as_job_creation_true_increments_total_jobs(self, user_service_factory):
        """count_as_job_creation=True (default) increments total_jobs_created by 1."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

        service.deduct_credits("test@example.com", "job-new", amount=1, count_as_job_creation=True)

        update_payload = mock_transaction.update.call_args[0][1]
        assert update_payload['total_jobs_created'] == 1  # was 0 → now 1

    def test_count_as_job_creation_false_does_not_set_total_jobs(self, user_service_factory):
        """count_as_job_creation=False leaves total_jobs_created out of the update payload."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

        service.deduct_credits("test@example.com", "job-reconcile", amount=2, count_as_job_creation=False)

        update_payload = mock_transaction.update.call_args[0][1]
        assert 'total_jobs_created' not in update_payload

    # --- I1: user-not-found ---

    def test_user_not_found_returns_failure(self, user_service_factory):
        """When the user doc does not exist, returns (False, 0, 'User not found') with no update."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

        # Override snapshot to simulate a missing document
        mock_snapshot.exists = False

        success, remaining, message = service.deduct_credits(
            "ghost@example.com", "job-ghost", amount=1
        )

        assert success is False
        assert remaining == 0
        assert "user not found" in message.lower()
        mock_transaction.update.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: deduct_credit wrapper (legacy single-credit deduction)
# ---------------------------------------------------------------------------

class TestDeductCreditWrapper:

    def test_legacy_deduct_credit_deducts_exactly_1(self, user_service_factory):
        """deduct_credit(email, job_id) deducts exactly 1 and returns (True, n-1, message)."""
        user_dict = _make_user_doc(credits=5)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

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
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

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
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

        service.deduct_credit("test@example.com", "job-reason")

        update_args = mock_transaction.update.call_args[0]
        transactions = update_args[1]['credit_transactions']
        assert transactions[0]['reason'] == 'job_creation'

    def test_legacy_deduct_credit_insufficient_returns_failure(self, user_service_factory):
        """deduct_credit when credits=0 returns (False, 0, msg) — same as before."""
        user_dict = _make_user_doc(credits=0)
        service, mock_db, mock_doc_ref, mock_snapshot, mock_transaction, mock_firestore_module = user_service_factory(user_dict)

        success, remaining, message = service.deduct_credit(
            "test@example.com", "job-broke"
        )

        assert success is False
        assert remaining == 0
        mock_transaction.update.assert_not_called()
