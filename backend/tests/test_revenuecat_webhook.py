"""Tests for in-app purchases: POST /api/users/webhooks/revenuecat + IapService.

Composed over the in-memory Firestore fake from test_kjbox_partner_api: the real
users router, real IapService and real UserService. Only the referral service is
a mock where a test asserts on what it receives.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import users as users_routes
from backend.config import get_settings
from backend.services import iap_service as iap
from backend.services.referral_service import REFERRAL_EARNINGS_COLLECTION, REFERRAL_LINKS_COLLECTION, ReferralService
from backend.services.stripe_service import CREDIT_PACKAGES
from backend.services.user_service import UserService
from backend.tests.test_kjbox_partner_api import FakeDb, FakeTransaction

AUTH = "Bearer rc-test-secret"
EMAIL = "buyer@example.com"
APP_ID = "6f1c1b7e-1f0a-4d8e-9a39-0c4f7a1d2b3c"


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def user_service(db):
    svc = UserService.__new__(UserService)
    svc.db = db
    svc.settings = MagicMock()
    return svc


@pytest.fixture
def referral():
    ref = MagicMock()
    ref.record_earning.return_value = None
    return ref


@pytest.fixture
def service(db, user_service, referral):
    return iap.IapService(
        db, user_service, referral_service=referral,
        transaction_runner=lambda fn: fn(FakeTransaction(db.txn_writes)),
    )


@pytest.fixture
def client(monkeypatch, service):
    monkeypatch.setattr(get_settings(), "revenuecat_webhook_auth", AUTH)
    app = FastAPI()
    app.include_router(users_routes.router, prefix="/api")
    app.dependency_overrides[iap.get_iap_service] = lambda: service
    return TestClient(app)


def _seed_user(db, credits=0, **extra):
    db.collection("gen_users").document(EMAIL).set({
        "email": EMAIL, "credits": credits, "app_user_id": APP_ID, "credit_transactions": [], **extra,
    })


def _user(db):
    return db.collection("gen_users").docs[EMAIL]


def _event(**over):
    event = {
        "type": "NON_RENEWING_PURCHASE",
        "app_user_id": APP_ID,
        "product_id": "nomad_credits_3",
        "transaction_id": "2000000123456789",
        "store": "APP_STORE",
        "price": 23.99,
        "currency": "USD",
        "country_code": "US",
        "takehome_percentage": 0.85,
        "environment": "PRODUCTION",
    }
    event.update(over)
    return {"event": event, "api_version": "1.0"}


def _post(client, body, auth=AUTH):
    headers = {"Authorization": auth} if auth is not None else {}
    return client.post("/api/users/webhooks/revenuecat", json=body, headers=headers)


class TestAuth:
    def test_rejects_bad_auth(self, client, db):
        _seed_user(db)
        assert _post(client, _event(), auth=None).status_code == 401
        assert _post(client, _event(), auth="Bearer wrong").status_code == 401
        assert _user(db)["credits"] == 0

    def test_503_when_secret_unset(self, client, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "revenuecat_webhook_auth", "")
        _seed_user(db)
        assert _post(client, _event(), auth="").status_code == 503
        assert _post(client, _event()).status_code == 503
        assert _user(db)["credits"] == 0


class TestGrant:
    def test_grants_package_credits(self, client, db):
        _seed_user(db, credits=1)
        resp = _post(client, _event())
        assert resp.status_code == 200
        assert resp.json()["action"] == "granted"
        user = _user(db)
        assert user["credits"] == 4
        assert user["credit_transactions"][-1]["reason"] == "iap_purchase"
        assert user["credit_transactions"][-1]["amount"] == 3
        payment = db.collection("store_payments").docs["APP_STORE:2000000123456789"]
        assert payment["source"] == "app_store"
        assert payment["amount_total"] == 2399
        assert payment["net_amount"] == 2039  # 2399 × 0.85 = 2039.15, rounded down
        assert payment["unmatched"] is False
        assert payment["customer_email"] == EMAIL
        assert "APP_STORE:2000000123456789" in db.collection("processed_revenuecat_transactions").docs

    def test_replay_is_noop(self, client, db):
        _seed_user(db)
        assert _post(client, _event()).json()["action"] == "granted"
        resp = _post(client, _event())
        assert resp.status_code == 200
        assert resp.json()["action"] == "duplicate"
        assert _user(db)["credits"] == 3
        assert len(_user(db)["credit_transactions"]) == 1

    def test_play_store_is_a_separate_namespace(self, client, db):
        _seed_user(db)
        _post(client, _event(store="PLAY_STORE", transaction_id="GPA.1234-5678"))
        assert _user(db)["credits"] == 3
        assert db.collection("store_payments").docs["PLAY_STORE:GPA.1234-5678"]["source"] == "play_store"

    def test_commit_race_already_exists_is_duplicate(self, service, db):
        """A concurrent delivery creating the marker between read and commit → duplicate, no grant."""
        _seed_user(db)

        def racing_runner(fn):
            from google.api_core import exceptions as google_exceptions
            raise google_exceptions.AlreadyExists("marker")

        service._transaction_runner = racing_runner
        result = service.handle_event(_event()["event"])
        assert result.action == "duplicate"
        assert _user(db)["credits"] == 0
        assert "APP_STORE:2000000123456789" not in db.collection("store_payments").docs

    def test_contention_is_503_so_revenuecat_retries(self, client, db, service, monkeypatch):
        from google.api_core import exceptions as google_exceptions

        _seed_user(db)

        def lock_timeout(_db, _fn):
            raise google_exceptions.Aborted("Transaction lock timeout.")

        service._transaction_runner = None
        monkeypatch.setattr(iap, "run_firestore_transaction", lock_timeout)
        assert _post(client, _event()).status_code == 503
        assert _user(db)["credits"] == 0
        assert db.collection("store_payments").docs == {}

    def test_sandbox_grants_but_is_test_and_no_referral(self, client, db, referral):
        _seed_user(db)
        _post(client, _event(environment="SANDBOX"))
        assert _user(db)["credits"] == 3
        assert db.collection("store_payments").docs["APP_STORE:2000000123456789"]["is_test"] is True
        referral.record_earning.assert_not_called()

    def test_anonymous_app_user_id_falls_back_to_alias(self, client, db):
        _seed_user(db)
        resp = _post(client, _event(app_user_id="$RCAnonymousID:abc", aliases=["$RCAnonymousID:abc", APP_ID]))
        assert resp.json()["action"] == "granted"
        assert _user(db)["credits"] == 3


class TestRefund:
    def test_refund_debits_and_can_go_negative(self, client, db):
        _seed_user(db)
        _post(client, _event(product_id="nomad_credits_1", price=9.99))
        assert _user(db)["credits"] == 1
        db.collection("gen_users").document(EMAIL).update({"credits": 0})  # the credit is spent on a job

        refund = _event(type="CANCELLATION", cancel_reason="CUSTOMER_SUPPORT",
                        product_id="nomad_credits_1", price=-9.99)
        resp = _post(client, refund)
        assert resp.status_code == 200 and resp.json()["action"] == "debited"
        user = _user(db)
        assert user["credits"] == -1
        assert user["credit_transactions"][-1]["reason"] == "iap_refund"
        assert user["credit_transactions"][-1]["amount"] == -1
        payment = db.collection("store_payments").docs["APP_STORE:2000000123456789"]
        assert payment["status"] == "refunded" and payment["refund_amount"] == 999

        replay = _post(client, refund)
        assert replay.json()["action"] == "duplicate"
        assert _user(db)["credits"] == -1

    def test_refund_alias_type(self, client, db):
        _seed_user(db)
        _post(client, _event())
        resp = _post(client, _event(type="REFUND", price=-23.99))
        assert resp.json()["action"] == "debited"
        assert _user(db)["credits"] == 0

    def test_refund_without_granted_purchase_debits_nothing(self, client, db):
        _seed_user(db, credits=2)
        resp = _post(client, _event(type="CANCELLATION", price=-23.99))
        assert resp.status_code == 200 and resp.json()["action"] == "ignored"
        assert _user(db)["credits"] == 2

    def test_refund_after_account_deleted_is_200_noop(self, client, db):
        _seed_user(db)
        _post(client, _event())
        db.collection("gen_users").document(EMAIL).delete()
        resp = _post(client, _event(type="CANCELLATION", price=-23.99))
        assert resp.status_code == 200 and resp.json()["action"] == "ignored"
        assert db.collection("store_payments").docs["APP_STORE:2000000123456789"]["status"] == "refunded"

    def test_refund_debits_the_account_that_was_granted(self, client, db):
        """The debit follows the purchase marker's email, not the refund event's app_user_id."""
        _seed_user(db)
        _post(client, _event())
        resp = _post(client, _event(type="CANCELLATION", price=-23.99, app_user_id="someone-else"))
        assert resp.json()["action"] == "debited"
        assert _user(db)["credits"] == 0


class TestUnmatched:
    def test_unknown_user_records_and_200(self, client, db, caplog):
        _seed_user(db)
        with caplog.at_level(logging.WARNING, logger="backend.services.iap_service"):
            resp = _post(client, _event(app_user_id="not-a-known-id"))
        assert resp.status_code == 200 and resp.json()["action"] == "unknown_user"
        assert _user(db)["credits"] == 0
        assert any(r.levelno == logging.WARNING and "unknown app_user_id" in r.getMessage() for r in caplog.records)
        payment = db.collection("store_payments").docs["APP_STORE:2000000123456789"]
        assert payment["unmatched"] is True
        assert payment["credits_granted"] == 0
        assert "APP_STORE:2000000123456789" not in db.collection("processed_revenuecat_transactions").docs

    def test_unknown_product_200_no_grant(self, client, db):
        _seed_user(db)
        resp = _post(client, _event(product_id="nomad_credits_999"))
        assert resp.status_code == 200 and resp.json()["action"] == "unknown_product"
        assert _user(db)["credits"] == 0
        assert db.collection("store_payments").docs == {}

    def test_other_event_types_ignored(self, client, db):
        _seed_user(db)
        assert _post(client, _event(type="TEST")).json()["action"] == "ignored"
        assert _post(client, _event(store="PROMOTIONAL")).json()["action"] == "ignored"
        assert _user(db)["credits"] == 0

    def test_malformed_body_400(self, client):
        resp = client.post("/api/users/webhooks/revenuecat", content=b"not json",
                           headers={"Authorization": AUTH, "Content-Type": "application/json"})
        assert resp.status_code == 400
        assert _post(client, {"api_version": "1.0"}).status_code == 400


class TestReferral:
    def test_referral_earning_on_net(self, client, db, referral):
        _seed_user(db)
        _post(client, _event(product_id="nomad_credits_10", price=59.99, takehome_percentage=0.85))
        referral.record_earning.assert_called_once_with(
            referred_email=EMAIL,
            source_key="APP_STORE:2000000123456789",
            purchase_amount_cents=5099,
            source="app_store",
        )

    def test_referral_earning_defaults_takehome(self, client, db, referral):
        _seed_user(db)
        event = _event(product_id="nomad_credits_10", price=59.99)
        del event["event"]["takehome_percentage"]
        _post(client, event)
        assert referral.record_earning.call_args.kwargs["purchase_amount_cents"] == 5099

    def test_replay_records_no_second_earning(self, client, db, referral):
        _seed_user(db)
        _post(client, _event())
        _post(client, _event())
        assert referral.record_earning.call_count == 1

    def test_record_earning_stores_store_source(self, db):
        """Real ReferralService: store rows get source + store_transaction_id, Stripe rows keep stripe_session_id."""
        from datetime import datetime

        db.collection("gen_users").document(EMAIL).set({
            "email": EMAIL, "referred_by_code": "abc12345", "referred_at": datetime.utcnow(),
        })
        db.collection(REFERRAL_LINKS_COLLECTION).document("abc12345").set({
            "code": "abc12345", "owner_email": "referrer@example.com", "kickback_percent": 20,
            "stats": {"purchases": 0, "total_earned_cents": 0},
        })
        svc = ReferralService(db=db)
        store = svc.record_earning(EMAIL, source_key="APP_STORE:1", purchase_amount_cents=5099, source="app_store")
        stripe_row = svc.record_earning(EMAIL, source_key="cs_test_1", purchase_amount_cents=1000)
        earnings = db.collection(REFERRAL_EARNINGS_COLLECTION).docs
        s = earnings[store["earning_id"]]
        assert s["source"] == "app_store" and s["store_transaction_id"] == "APP_STORE:1"
        assert s["stripe_session_id"] is None and s["earning_amount_cents"] == 1019
        p = earnings[stripe_row["earning_id"]]
        assert p["source"] == "stripe" and p["stripe_session_id"] == "cs_test_1"


class TestPackages:
    def test_packages_include_store_ids_and_web_link(self, client):
        body = client.get("/api/users/credits/packages").json()
        by_id = {p["id"]: p for p in body["packages"]}
        assert by_id["1_credit"]["store_product_id"] == "nomad_credits_1"
        assert by_id["3_credits"]["store_product_id"] == "nomad_credits_3"
        assert by_id["5_credits"]["store_product_id"] == "nomad_credits_5"
        assert by_id["10_credits"]["store_product_id"] == "nomad_credits_10"
        assert body["web_link"] == {
            "show_for": {"ios": ["USA"], "android": []},
            "copy_key": "credits.webLink.default",
        }

    def test_every_store_product_maps_to_one_package(self):
        ids = [p["store_product_id"] for p in CREDIT_PACKAGES.values()]
        assert sorted(ids) == ["nomad_credits_1", "nomad_credits_10", "nomad_credits_3", "nomad_credits_5"]
        assert iap.package_for_product("nomad_credits_5") == ("5_credits", CREDIT_PACKAGES["5_credits"])


class TestFindByAppUserId:
    def test_finds_and_misses(self, db, user_service):
        _seed_user(db)
        assert user_service.find_by_app_user_id(APP_ID).email == EMAIL
        assert user_service.find_by_app_user_id("nope") is None
        assert user_service.find_by_app_user_id("") is None


class TestNegativeBalance:
    def test_make_refused_when_negative(self, db, user_service):
        """A refunded-after-spend account (credits < 0) gets 402 on job create."""
        from backend.exceptions import InsufficientCreditsError
        from backend.main import insufficient_credits_exception_handler
        from backend.models.job import JobCreate
        from backend.services.job_manager import JobManager

        _seed_user(db, credits=-1)
        with patch("backend.services.job_manager.FirestoreService") as fs, \
                patch("backend.services.job_manager.StorageService"), \
                patch("backend.services.user_service.get_user_service", return_value=user_service):
            manager = JobManager()
            app = FastAPI()
            app.add_exception_handler(InsufficientCreditsError, insufficient_credits_exception_handler)

            @app.post("/make")
            def make():
                manager.create_job(
                    JobCreate(artist="Test", title="Song", theme_id="nomad", user_email=EMAIL), is_admin=False,
                )
                return {"ok": True}

            resp = TestClient(app).post("/make")
        assert resp.status_code == 402
        assert resp.json()["credits_available"] == -1
        fs.return_value.create_job.assert_not_called()
        assert _user(db)["credits"] == -1


class TestFakeIapRoutes:
    @pytest.fixture
    def fake_client(self, monkeypatch, service, user_service):
        from backend.api.routes import test_support

        monkeypatch.setattr(iap, "get_iap_service", lambda: service)
        monkeypatch.setattr("backend.services.user_service.get_user_service", lambda: user_service)
        monkeypatch.setattr(user_service, "ensure_app_user_id", lambda email: APP_ID)
        app = FastAPI()
        app.include_router(test_support.router, prefix="/api")
        return TestClient(app)

    def test_purchase_then_refund(self, fake_client, db):
        _seed_user(db)
        bought = fake_client.post("/api/test/fake-iap/purchase",
                                  json={"email": EMAIL, "product_id": "nomad_credits_5"}).json()
        assert bought["action"] == "granted" and bought["credits"] == 5
        payment = db.collection("store_payments").docs[f"APP_STORE:{bought['transaction_id']}"]
        assert payment["amount_total"] == 3499 and payment["is_test"] is True

        refunded = fake_client.post("/api/test/fake-iap/refund", json={
            "transaction_id": bought["transaction_id"], "product_id": "nomad_credits_5",
        }).json()
        assert refunded["action"] == "debited" and refunded["credits"] == 0

    def test_purchase_needs_a_user(self, fake_client):
        resp = fake_client.post("/api/test/fake-iap/purchase", json={"product_id": "nomad_credits_1"})
        assert resp.status_code == 422


class TestAdminRevenue:
    def test_revenue_summary_includes_store_payments(self, client, db, monkeypatch):
        from datetime import datetime
        from types import SimpleNamespace

        from backend.services import stripe_admin_service as sas
        from backend.services.stripe_admin_service import StripeAdminService

        # Other test modules can leave google.cloud.firestore_v1 mocked when this
        # module is first imported; give the Firestore fake a real-shaped filter.
        monkeypatch.setattr(sas, "FieldFilter", lambda f, op, v: SimpleNamespace(field_path=f, op_string=op, value=v))

        _seed_user(db)
        _post(client, _event())  # $23.99 store purchase, 85% takehome
        db.collection("stripe_payments").document("cs_1").set({
            "amount_total": 1000, "stripe_fee": 59, "net_amount": 941, "refund_amount": 0,
            "order_type": "credit_purchase", "is_test": False, "created_at": datetime.utcnow(),
        })
        admin = StripeAdminService.__new__(StripeAdminService)
        admin.db = db
        admin._revenue_cache = None
        admin._revenue_cache_time = 0
        admin._revenue_cache_ttl = 300

        summary = admin.get_revenue_summary(days=30)
        assert summary["total_gross"] == 1000 + 2399
        assert summary["total_fees"] == 59 + (2399 - 2039)
        assert summary["total_net"] == 941 + 2039
        assert summary["transaction_count"] == 2
        assert summary["revenue_by_type"]["app_store_credit_purchase"] == 2399

        by_day = admin.get_revenue_by_period(days=30)
        assert sum(b["gross"] for b in by_day) == 3399
