"""
Emulator integration tests for IapService (RevenueCat in-app purchases).

Runs against a REAL Firestore emulator so the exactly-once guarantee rests on
real transactions and create()-only markers, not a fake: concurrent duplicate
deliveries of one store transaction grant once, and a refund debits once.
Run with: scripts/run-emulator-tests.sh
"""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from google.cloud import firestore

from backend.services import iap_service as iap
from backend.services.login_code_store import TransactionContention
from backend.services.user_service import UserService

from .conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh",
)


@pytest.fixture
def db():
    return firestore.Client()


@pytest.fixture
def svc(db):
    user_service = UserService.__new__(UserService)
    user_service.db = db
    user_service.settings = MagicMock()
    referral = MagicMock()
    referral.record_earning.return_value = None
    return iap.IapService(db, user_service, referral_service=referral)


def _seed_user(db, credits=0):
    email = f"iap-{uuid.uuid4().hex[:8]}@example.com"
    app_user_id = str(uuid.uuid4())
    ref = db.collection("gen_users").document(email)
    ref.set({"email": email, "credits": credits, "app_user_id": app_user_id, "credit_transactions": []})
    return email, app_user_id, ref


def _event(app_user_id, transaction_id, **over):
    event = {
        "type": "NON_RENEWING_PURCHASE",
        "app_user_id": app_user_id,
        "product_id": "nomad_credits_3",
        "transaction_id": transaction_id,
        "store": "APP_STORE",
        "price": 23.99,
        "currency": "USD",
        "takehome_percentage": 0.85,
        "environment": "PRODUCTION",
    }
    event.update(over)
    return event


def _handle_with_retry(svc, event):
    # RevenueCat retries a 5xx (contention → 503); emulate that redelivery.
    for _ in range(20):
        try:
            return svc.handle_event(event).action
        except TransactionContention:
            time.sleep(0.05)
    return "contention"


def test_concurrent_duplicates_grant_once(svc, db):
    email, app_user_id, ref = _seed_user(db)
    event = _event(app_user_id, f"txn-{uuid.uuid4().hex}")

    with ThreadPoolExecutor(max_workers=5) as pool:
        actions = list(pool.map(lambda _: _handle_with_retry(svc, dict(event)), range(5)))

    assert actions.count("granted") == 1
    assert set(actions) <= {"granted", "duplicate"}
    user = ref.get().to_dict()
    assert user["credits"] == 3
    assert [t["reason"] for t in user["credit_transactions"]] == ["iap_purchase"]
    assert svc._referral_service.record_earning.call_count == 1


def test_concurrent_refunds_debit_once(svc, db):
    email, app_user_id, ref = _seed_user(db)
    txn = f"txn-{uuid.uuid4().hex}"
    assert svc.handle_event(_event(app_user_id, txn, product_id="nomad_credits_1", price=9.99)).action == "granted"
    ref.update({"credits": 0})  # the credit is spent

    refund = _event(app_user_id, txn, type="CANCELLATION", cancel_reason="CUSTOMER_SUPPORT",
                    product_id="nomad_credits_1", price=-9.99)
    with ThreadPoolExecutor(max_workers=5) as pool:
        actions = list(pool.map(lambda _: _handle_with_retry(svc, dict(refund)), range(5)))

    assert actions.count("debited") == 1
    assert ref.get().to_dict()["credits"] == -1
    payment = db.collection(iap.STORE_PAYMENTS_COLLECTION).document(f"APP_STORE:{txn}").get().to_dict()
    assert payment["status"] == "refunded" and payment["refund_amount"] == 999


def test_find_by_app_user_id_real_query(svc, db):
    email, app_user_id, _ = _seed_user(db)
    assert svc.user_service.find_by_app_user_id(app_user_id).email == email
    assert svc.user_service.find_by_app_user_id(str(uuid.uuid4())) is None
