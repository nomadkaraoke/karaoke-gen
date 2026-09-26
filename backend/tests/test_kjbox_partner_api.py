"""Tests for the kjbox partner API (/api/kjbox/*).

Composed tests: the real kjbox router + real KjboxPartnerService + real
UserService, all over an in-memory Firestore fake. Only the true external
boundaries are mocked (email provider, disposable-email lookups, the welcome
credit AI evaluation).
"""
from __future__ import annotations

import hashlib
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core import exceptions as google_exceptions

from backend.api.routes import kjbox
from backend.services import kjbox_partner_service as kps
from backend.services.email_service import get_email_service
from backend.services.user_service import UserService, get_user_service

SECRET = "test-kjbox-secret"
EMAIL = "singer@example.com"


# ------------------------------------------------------------ Firestore fake

class FakeSnap:
    def __init__(self, ref, data):
        self.reference = ref
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeRef:
    def __init__(self, coll, doc_id):
        self.coll = coll
        self.id = doc_id

    @property
    def _docs(self):
        return self.coll.docs

    def get(self, transaction=None):
        data = self._docs.get(self.id)
        return FakeSnap(self, dict(data) if data is not None else None)

    def set(self, data, merge=False):
        if merge and self.id in self._docs:
            self._docs[self.id].update(dict(data))
        else:
            self._docs[self.id] = dict(data)

    def create(self, data):
        if self.id in self._docs:
            raise google_exceptions.AlreadyExists("exists")
        self._docs[self.id] = dict(data)

    def update(self, data):
        if self.id not in self._docs:
            raise google_exceptions.NotFound("missing")
        doc = self._docs[self.id]
        for key, value in data.items():
            if type(value).__name__ == "Increment":
                doc[key] = (doc.get(key) or 0) + value.value
            else:
                doc[key] = value

    def delete(self):
        self._docs.pop(self.id, None)


class FakeQuery:
    def __init__(self, coll, filters, limit=None):
        self.coll = coll
        self.filters = filters
        self._limit = limit

    def where(self, *args, filter=None, **kwargs):
        return FakeQuery(self.coll, self.filters + [filter], self._limit)

    def limit(self, n):
        return FakeQuery(self.coll, self.filters, n)

    def stream(self):
        out = []
        for doc_id, data in list(self.coll.docs.items()):
            if all(self._match(data, f) for f in self.filters):
                out.append(FakeSnap(FakeRef(self.coll, doc_id), dict(data)))
        return out[: self._limit] if self._limit else out

    @staticmethod
    def _match(data, f):
        value = data.get(f.field_path)
        if f.op_string == "==":
            return value == f.value
        if f.op_string == ">=":
            return value is not None and value >= f.value
        raise NotImplementedError(f.op_string)


class FakeCollection:
    _ids = itertools.count()

    def __init__(self):
        self.docs = {}

    def document(self, doc_id=None):
        return FakeRef(self, doc_id or f"auto-{next(self._ids)}")

    def where(self, *args, filter=None, **kwargs):
        return FakeQuery(self, [filter])


class FakeTransaction:
    """Applies writes immediately (the service only writes after all reads)."""

    def __init__(self, log):
        self.log = log

    def set(self, ref, data, merge=False):
        self.log.append(("set", ref.coll, ref.id))
        ref.set(data, merge=merge)

    def update(self, ref, data):
        self.log.append(("update", ref.coll, ref.id))
        ref.update(data)

    def create(self, ref, data):
        self.log.append(("create", ref.coll, ref.id))
        ref.create(data)

    def delete(self, ref):
        ref.delete()


class FakeDb:
    def __init__(self):
        self.collections = {}
        self.txn_writes = []
        self.txn_count = 0

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def user_service(db):
    svc = UserService.__new__(UserService)
    svc.db = db
    svc.settings = MagicMock()
    grant_calls = []

    def fake_grant(email, precomputed_eval=None):
        grant_calls.append({"email": email, "precomputed_eval": precomputed_eval})
        user = svc.get_user(email)
        if user.welcome_credits_granted:
            return False, "already_granted"
        svc.add_credits(email, svc.NEW_USER_FREE_CREDITS, "welcome_credit")
        svc.update_user(email, welcome_credits_granted=True)
        return True, "granted"

    svc.grant_welcome_credits_if_eligible = fake_grant
    svc.grant_calls = grant_calls
    return svc


@pytest.fixture
def email_service():
    svc = MagicMock()
    svc.is_configured.return_value = True
    svc.send_kjbox_login_code.return_value = True
    svc.send_welcome_email.return_value = True
    return svc


@pytest.fixture
def validation():
    v = MagicMock()
    v.is_disposable_domain.return_value = False
    v.is_email_blocked.return_value = False
    v.is_ip_blocked.return_value = False
    return v


@pytest.fixture
def settings():
    return SimpleNamespace(
        kjbox_partner_secret=SECRET,
        kjbox_signup_cap_per_24h=100,
        kjbox_codes_per_email_per_hour=5,
        kjbox_show_credits_per_user_per_24h=5,
    )


@pytest.fixture
def precompute(monkeypatch):
    """Run the welcome-credit precompute synchronously and record calls."""
    calls = []
    monkeypatch.setattr(kjbox, "_precompute_credit_eval", lambda *a: calls.append(a))

    class SyncThread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    # Only the kjbox module's view of threading — patching threading.Thread
    # globally would also break asyncio.to_thread's executor.
    monkeypatch.setattr(kjbox, "threading", SimpleNamespace(Thread=SyncThread))
    return calls


@pytest.fixture
def client(monkeypatch, db, user_service, email_service, validation, settings, precompute):
    def fake_run_transaction(self, fn):
        db.txn_count += 1
        return fn(FakeTransaction(db.txn_writes))

    monkeypatch.setattr(kps.KjboxPartnerService, "_run_transaction", fake_run_transaction)
    app = FastAPI()
    app.include_router(kjbox.router, prefix="/api")
    app.dependency_overrides[get_user_service] = lambda: user_service
    app.dependency_overrides[get_email_service] = lambda: email_service
    monkeypatch.setattr(kjbox, "get_settings", lambda: settings)
    monkeypatch.setattr(kjbox, "get_email_validation_service", lambda: validation)
    return TestClient(app)


H = {"X-Kjbox-Secret": SECRET}


def _send(client, email=EMAIL, **extra):
    return client.post("/api/kjbox/auth/send-code", json={"email": email, **extra}, headers=H)


def _sent_code(email_service):
    return email_service.send_kjbox_login_code.call_args.args[1]


def _verify(client, code, email=EMAIL):
    return client.post("/api/kjbox/auth/verify-code", json={"email": email, "code": code}, headers=H)


def _code_doc(db, email=EMAIL):
    return db.collection(kps.LOGIN_CODES_COLLECTION).docs[kps.code_doc_id(email)]


# ---------------------------------------------------------------- secret gate

class TestSecretGate:
    @pytest.mark.parametrize("path,body", [
        ("/api/kjbox/auth/send-code", {"email": EMAIL}),
        ("/api/kjbox/auth/verify-code", {"email": EMAIL, "code": "123456"}),
        ("/api/kjbox/credits/show-credit", {"idempotency_key": "k"}),
    ])
    def test_unconfigured_returns_503(self, client, settings, path, body):
        settings.kjbox_partner_secret = ""
        resp = client.post(path, json=body, headers=H)
        assert resp.status_code == 503
        assert resp.json() == {"detail": "not configured"}

    @pytest.mark.parametrize("path,body", [
        ("/api/kjbox/auth/send-code", {"email": EMAIL}),
        ("/api/kjbox/auth/verify-code", {"email": EMAIL, "code": "123456"}),
        ("/api/kjbox/credits/show-credit", {"idempotency_key": "k"}),
    ])
    def test_wrong_or_missing_secret_returns_403(self, client, path, body):
        assert client.post(path, json=body, headers={"X-Kjbox-Secret": "nope"}).status_code == 403
        assert client.post(path, json=body).status_code == 403

    def test_router_registered_in_main_app(self):
        from backend.main import app
        paths = {getattr(r, "path", "") for r in app.routes}
        assert {"/api/kjbox/auth/send-code", "/api/kjbox/auth/verify-code",
                "/api/kjbox/credits/show-credit"} <= paths


# ------------------------------------------------------------------ send-code

class TestSendCode:
    def test_new_email_gets_code_but_no_account_until_verified(self, client, user_service, db, email_service, precompute):
        resp = _send(client, locale="es-MX", venue="The Dive Bar")
        assert resp.status_code == 200
        assert resp.json() == {"status": "sent"}

        # No account and no signup-cap consumption for an unverified address
        assert user_service.get_user(EMAIL) is None
        assert db.collection(kps.SIGNUPS_COLLECTION).docs == {}
        # Attribution rides on the code record until verify
        doc = _code_doc(db)
        assert doc["venue"] == "The Dive Bar" and doc["locale"] == "es" and doc["ui_locale"] == "es"

        # Code email localised + welcome-credit eval precomputed onto the code doc
        args, kwargs = email_service.send_kjbox_login_code.call_args
        assert args[0] == EMAIL and len(args[1]) == 6 and args[1].isdigit()
        assert kwargs == {"expiry_minutes": 10, "locale": "es"}
        assert precompute == [(kps.code_doc_id(EMAIL), EMAIL, kps.LOGIN_CODES_COLLECTION)]

    def test_throttle_and_code_write_happen_in_one_transaction(self, client, db):
        _send(client)
        assert db.txn_count == 1
        assert db.txn_writes == [("set", db.collection(kps.LOGIN_CODES_COLLECTION), kps.code_doc_id(EMAIL))]

    def test_code_stored_only_as_hash(self, client, db, email_service):
        _send(client)
        code = _sent_code(email_service)
        doc = _code_doc(db)
        assert code not in str(doc.values())
        assert doc["code_hash"] and len(doc["code_hash"]) == 64
        assert doc["attempts"] == 0
        expires = doc["expires_at"] - doc["created_at"]
        assert expires == timedelta(minutes=10)

    def test_existing_user_not_recreated_or_counted(self, client, user_service, db, precompute):
        user_service.get_or_create_user(EMAIL)
        user_service.update_user(EMAIL, welcome_credits_granted=True)
        assert _send(client).status_code == 200
        assert db.collection(kps.SIGNUPS_COLLECTION).docs == {}
        assert user_service.get_user(EMAIL).signup_source is None
        assert precompute == []  # already had a welcome credit → nothing to evaluate

    def test_email_normalised(self, client, db, email_service):
        assert _send(client, email="  Singer@Example.COM ").status_code == 200
        assert _code_doc(db)["email"] == EMAIL
        assert _verify(client, _sent_code(email_service), email="SINGER@example.com ").status_code == 200

    def test_invalid_email_422(self, client):
        assert _send(client, email="not-an-email").status_code == 422

    def test_disposable_domain_422_same_detail_as_magic_link(self, client, validation, user_service):
        from backend.i18n import t
        validation.is_disposable_domain.return_value = True
        resp = _send(client)
        assert resp.status_code == 422
        assert resp.json()["detail"] == t("en", "users.disposableEmailBlocked")
        assert user_service.get_user(EMAIL) is None

    @pytest.mark.parametrize("blocked", ["email", "ip"])
    def test_blocked_email_or_ip_pretends_success(self, client, validation, email_service, user_service, blocked):
        getattr(validation, f"is_{blocked}_blocked").return_value = True
        resp = _send(client)
        assert resp.status_code == 200 and resp.json() == {"status": "sent"}
        email_service.send_kjbox_login_code.assert_not_called()
        assert user_service.get_user(EMAIL) is None

    def test_per_email_throttle(self, client, email_service):
        codes = [_send(client).status_code for _ in range(6)]
        assert codes == [200] * 5 + [429]
        assert _send(client).json() == {"detail": "too_many_codes"}
        assert email_service.send_kjbox_login_code.call_count == 5

    def test_throttle_window_is_rolling_hour(self, client, db):
        _send(client)
        doc = _code_doc(db)
        doc["send_times"] = [datetime.now(timezone.utc) - timedelta(minutes=61)] * 10
        assert _send(client).status_code == 200

    def test_no_per_ip_signup_cap(self, client, user_service, email_service):
        # gen's magic-link path allows 2 signups/IP/24h; a venue shares one IP.
        user_service.is_signup_rate_limited = MagicMock(return_value=True)
        for i in range(5):
            email = f"s{i}@example.com"
            assert _send(client, email=email).status_code == 200
            assert _verify(client, _sent_code(email_service), email=email).status_code == 200

    def test_signup_cap_not_consumed_by_unverified_sends(self, client, settings, db, email_service):
        settings.kjbox_signup_cap_per_24h = 1
        for i in range(5):
            assert _send(client, email=f"fake{i}@example.com").status_code == 200
        assert db.collection(kps.SIGNUPS_COLLECTION).docs == {}
        _send(client)
        assert _verify(client, _sent_code(email_service)).status_code == 200


# ---------------------------------------------------------------- verify-code

class TestVerifyCode:
    def test_success_returns_session_and_grants_welcome_credit(self, client, db, user_service, email_service):
        _send(client, locale="de", venue="The Dive Bar")
        # Simulate the background eval having finished
        _code_doc(db).update({"credit_eval_decision": "grant", "credit_eval_reasoning": "clean"})

        resp = _verify(client, _sent_code(email_service))
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"session_token", "user", "credits_granted", "credit_status"}
        assert body["credits_granted"] == 1 and body["credit_status"] == "granted"
        assert body["user"]["email"] == EMAIL and body["user"]["credits"] == 1

        # Precomputed eval handed to the grant (instant verify)
        assert user_service.grant_calls[-1]["precomputed_eval"] == {
            "credit_eval_decision": "grant", "credit_eval_reasoning": "clean"}

        # Account created on verify with kjbox attribution + counted as a signup
        created = user_service.get_user(EMAIL)
        assert created.signup_source == "kjbox" and created.signup_venue == "The Dive Bar"
        assert created.signup_ip is None
        assert [d["email"] for d in db.collection(kps.SIGNUPS_COLLECTION).docs.values()] == [EMAIL]

        # Session is a real, valid gen session for the user
        valid, user, _ = user_service.validate_session(body["session_token"])
        assert valid and user.email == EMAIL
        assert user.email_verified and user.last_login_at is not None
        assert user.locale == "de" and user.ui_locale == "de"

        email_service.send_welcome_email.assert_called_once_with(EMAIL, 1, locale="de")

    def test_verified_signup_cap(self, client, settings, db, user_service, email_service):
        settings.kjbox_signup_cap_per_24h = 2
        for email in ("a@example.com", "b@example.com"):
            _send(client, email=email)
            assert _verify(client, _sent_code(email_service), email=email).status_code == 200
        _send(client, email="c@example.com")
        code_c = _sent_code(email_service)
        resp = _verify(client, code_c, email="c@example.com")
        assert resp.status_code == 429 and resp.json() == {"detail": "signup_cap"}
        assert user_service.get_user("c@example.com") is None
        # Cap checked before consuming the code: once it lifts, the same code works
        settings.kjbox_signup_cap_per_24h = 3
        assert _verify(client, code_c, email="c@example.com").status_code == 200
        # Existing accounts are never blocked by the new-account cap
        settings.kjbox_signup_cap_per_24h = 0
        _send(client, email="a@example.com")
        assert _verify(client, _sent_code(email_service), email="a@example.com").status_code == 200

    def test_signup_cap_counts_only_last_24h(self, client, settings, db, email_service):
        settings.kjbox_signup_cap_per_24h = 1
        db.collection(kps.SIGNUPS_COLLECTION).document("old").set(
            {"email": "old@example.com", "created_at": datetime.now(timezone.utc) - timedelta(hours=25)}
        )
        _send(client)
        assert _verify(client, _sent_code(email_service)).status_code == 200

    def test_returning_user_gets_no_second_welcome(self, client, user_service, email_service):
        _send(client)
        assert _verify(client, _sent_code(email_service)).status_code == 200
        _send(client)
        body = _verify(client, _sent_code(email_service)).json()
        assert body["credits_granted"] == 0 and body["credit_status"] == "already_granted"
        assert email_service.send_welcome_email.call_count == 1

    def test_wrong_code_401_and_counts_attempt(self, client, db, email_service):
        _send(client)
        code = _sent_code(email_service)
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        resp = _verify(client, wrong)
        assert resp.status_code == 401 and resp.json() == {"detail": "invalid_code"}
        assert _code_doc(db)["attempts"] == 1
        assert _verify(client, code).status_code == 200  # still usable

    def test_too_many_attempts_burns_code(self, client, email_service):
        _send(client)
        code = _sent_code(email_service)
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        statuses = [_verify(client, wrong).status_code for _ in range(5)]
        assert statuses == [401] * 5
        resp = _verify(client, code)  # correct code, but attempts exhausted
        assert resp.status_code == 429 and resp.json() == {"detail": "too_many_attempts"}
        assert _verify(client, code).status_code == 401  # burned

    def test_expired_code(self, client, db, email_service):
        _send(client)
        _code_doc(db)["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        resp = _verify(client, _sent_code(email_service))
        assert resp.status_code == 401 and resp.json() == {"detail": "expired"}

    def test_missing_code(self, client):
        resp = _verify(client, "123456")
        assert resp.status_code == 401 and resp.json() == {"detail": "expired"}

    def test_code_single_use(self, client, email_service):
        _send(client)
        code = _sent_code(email_service)
        assert _verify(client, code).status_code == 200
        resp = _verify(client, code)
        assert resp.status_code == 401 and resp.json() == {"detail": "expired"}

    def test_new_code_invalidates_previous(self, client, email_service):
        _send(client)
        first = _sent_code(email_service)
        _send(client)
        second = _sent_code(email_service)
        if first != second:
            assert _verify(client, first).status_code == 401
        assert _verify(client, second).status_code == 200

    def test_concurrent_use_marker_blocks_replay(self, client, db, email_service):
        _send(client)
        code_id = _code_doc(db)["code_id"]
        db.collection(kps.LOGIN_CODE_USES_COLLECTION).document(code_id).create({"email": EMAIL})
        resp = _verify(client, _sent_code(email_service))
        assert resp.status_code == 401 and resp.json() == {"detail": "expired"}


# --------------------------------------------------------------- show-credit

def _session(client, email_service, email=EMAIL):
    _send(client, email=email)
    return _verify(client, _sent_code(email_service), email=email).json()["session_token"]


def _show(client, token, key, **extra):
    return client.post(
        "/api/kjbox/credits/show-credit",
        json={"idempotency_key": key, **extra},
        headers={**H, "Authorization": f"Bearer {token}"},
    )


class TestShowCredit:
    def test_grants_one_credit_quietly(self, client, email_service, user_service):
        token = _session(client, email_service)  # welcome credit → 1
        resp = _show(client, token, "job-abc", venue="The Dive Bar")
        assert resp.status_code == 200 and resp.json() == {"granted": True, "credits": 2}
        user = user_service.get_user(EMAIL)
        assert user.credits == 2
        assert user.credit_transactions[-1].reason == "kjbox show credit"
        email_service.send_credits_added.assert_not_called()

    def test_idempotent_repeat(self, client, email_service, user_service):
        token = _session(client, email_service)
        assert _show(client, token, "job-abc").json() == {"granted": True, "credits": 2}
        assert _show(client, token, "job-abc").json() == {"granted": False, "credits": 2}
        assert user_service.get_user(EMAIL).credits == 2

    def test_key_stored_hashed_with_user(self, client, email_service, db):
        token = _session(client, email_service)
        _show(client, token, "a/b:c")
        doc_id = hashlib.sha256(b"a/b:c").hexdigest()
        assert db.collection(kps.SHOW_CREDITS_COLLECTION).docs[doc_id]["email"] == EMAIL

    def test_daily_cap(self, client, email_service, settings):
        settings.kjbox_show_credits_per_user_per_24h = 2
        token = _session(client, email_service)
        assert _show(client, token, "k1").status_code == 200
        assert _show(client, token, "k2").status_code == 200
        resp = _show(client, token, "k3")
        assert resp.status_code == 429 and resp.json() == {"detail": "show_credit_cap"}
        # An already-processed key still answers idempotently at the cap
        assert _show(client, token, "k1").json()["granted"] is False

    def test_cap_is_rolling_24h(self, client, email_service, settings, db):
        settings.kjbox_show_credits_per_user_per_24h = 1
        token = _session(client, email_service)
        counter = db.collection(kps.SHOW_CREDIT_USERS_COLLECTION).document(hashlib.sha256(EMAIL.encode()).hexdigest())
        counter.set({"email": EMAIL, "grant_times": [datetime.now(timezone.utc) - timedelta(hours=25)]})
        assert _show(client, token, "k1").json()["granted"] is True
        assert _show(client, token, "k2").status_code == 429  # the fresh grant counts

    @pytest.mark.parametrize("auth", [None, "Bearer ", "Bearer nope", "Basic abc"])
    def test_bad_session_401(self, client, auth):
        headers = dict(H)
        if auth is not None:
            headers["Authorization"] = auth
        resp = client.post("/api/kjbox/credits/show-credit", json={"idempotency_key": "k"}, headers=headers)
        assert resp.status_code == 401 and resp.json() == {"detail": "invalid_session"}

    def test_idempotency_key_length_validated(self, client, email_service):
        token = _session(client, email_service)
        assert _show(client, token, "x" * 129).status_code == 422
        assert _show(client, token, "").status_code == 422

    def test_only_if_empty_skips_when_user_has_credit(self, client, email_service, db, user_service):
        token = _session(client, email_service)  # welcome credit → 1
        resp = _show(client, token, "k1", only_if_empty=True)
        assert resp.json() == {"granted": False, "credits": 1}
        # Key NOT claimed on a non-grant: the later job-time call can still use it
        assert db.collection(kps.SHOW_CREDITS_COLLECTION).docs == {}
        user_service.update_user(EMAIL, credits=0)  # e.g. welcome credit spent elsewhere
        assert _show(client, token, "k1").json() == {"granted": True, "credits": 1}

    def test_only_if_empty_then_same_key_grants_once(self, client, email_service, user_service):
        token = _session(client, email_service)
        user_service.update_user(EMAIL, credits=0)  # e.g. welcome credit denied
        assert _show(client, token, "k1", only_if_empty=True).json() == {"granted": True, "credits": 1}
        # Job-time call with the same key must not stack a second credit
        assert _show(client, token, "k1").json() == {"granted": False, "credits": 1}
        assert user_service.get_user(EMAIL).credits == 1

    def test_non_grant_does_not_count_toward_cap(self, client, email_service, settings):
        settings.kjbox_show_credits_per_user_per_24h = 1
        token = _session(client, email_service)
        for i in range(3):
            assert _show(client, token, f"k{i}", only_if_empty=True).json()["granted"] is False
        assert _show(client, token, "k9").json()["granted"] is True

    def test_grant_is_one_transaction(self, client, email_service, db):
        token = _session(client, email_service)
        before_count, before_writes = db.txn_count, len(db.txn_writes)
        _show(client, token, "k1")
        assert db.txn_count == before_count + 1
        kinds = sorted(w[0] for w in db.txn_writes[before_writes:])
        assert kinds == ["create", "set", "update"]  # key claim, cap counter, credits


class TestTransactionContention:
    def _contend(self, monkeypatch, db):
        calls = {"n": 0}
        real = kps.KjboxPartnerService._run_transaction

        def flaky(self, fn):
            calls["n"] += 1
            if calls["n"] == calls.get("fail_on", -1):
                raise kps.TransactionContention()
            return real(self, fn)

        monkeypatch.setattr(kps.KjboxPartnerService, "_run_transaction", flaky)
        return calls

    def test_send_code_contention_is_throttled(self, client, monkeypatch, db, email_service):
        calls = self._contend(monkeypatch, db)
        calls["fail_on"] = 1
        resp = _send(client)
        assert resp.status_code == 429 and resp.json() == {"detail": "too_many_codes"}
        email_service.send_kjbox_login_code.assert_not_called()

    def test_show_credit_contention_is_retryable(self, client, monkeypatch, db, email_service, user_service):
        token = _session(client, email_service)
        calls = self._contend(monkeypatch, db)
        calls["fail_on"] = 1
        resp = _show(client, token, "k1")
        assert resp.status_code == 409 and resp.json() == {"detail": "busy_retry"}
        assert user_service.get_user(EMAIL).credits == 1  # nothing granted
        assert _show(client, token, "k1").json() == {"granted": True, "credits": 2}

    def test_real_runner_maps_firestore_giveup(self, monkeypatch):
        svc = kps.KjboxPartnerService(MagicMock(), pepper="p")

        def give_up(fn):
            def wrapped(transaction):
                raise ValueError("Failed to commit transaction in 10 attempts.")
            return wrapped

        monkeypatch.setattr(kps.firestore, "transactional", give_up)
        with pytest.raises(kps.TransactionContention):
            svc._run_transaction(lambda t: None)


# ------------------------------------------------------------- review link

class TestReviewLink:
    @pytest.fixture
    def jobs(self, monkeypatch):
        from backend.services import job_manager as jm
        from backend.services import job_notification_service as jns
        store = {}

        class FakeJobManager:
            def get_job(self, job_id):
                return store.get(job_id)

        monkeypatch.setattr(jm, "JobManager", FakeJobManager)
        minted = []

        def fake_url(job_id, email, locale="en", frontend_url=None):
            minted.append((job_id, email, locale))
            return f"https://gen.example/{locale}/auth/verify?token=t-{job_id}"

        monkeypatch.setattr(jns, "build_review_login_url", fake_url)
        store["minted"] = minted
        return store

    def _job(self, status, email=EMAIL, state_data=None):
        return SimpleNamespace(job_id="job1", user_email=email, status=status,
                               state_data=state_data or {})

    def _link(self, client, token, job_id="job1", **body):
        return client.post(f"/api/kjbox/jobs/{job_id}/review-link", json=body,
                           headers={**H, "Authorization": f"Bearer {token}"})

    def test_owner_gets_sign_in_link_while_awaiting_review(self, client, email_service, jobs):
        token = _session(client, email_service)
        jobs["job1"] = self._job("awaiting_review")
        resp = self._link(client, token, locale="es")
        assert resp.status_code == 200
        assert resp.json() == {"url": "https://gen.example/es/auth/verify?token=t-job1",
                               "status": "awaiting_review", "review_started_by": None}
        assert jobs["minted"] == [("job1", EMAIL, "es")]

    def test_in_review_reports_who_started(self, client, email_service, jobs):
        token = _session(client, email_service)
        jobs["job1"] = self._job("in_review", state_data={"review_started_by": "admin"})
        body = self._link(client, token).json()
        assert body["status"] == "in_review" and body["review_started_by"] == "admin"

    def test_unknown_locale_falls_back_to_en(self, client, email_service, jobs):
        token = _session(client, email_service)
        jobs["job1"] = self._job("awaiting_review")
        self._link(client, token, locale="xx")
        assert jobs["minted"][-1][2] == "en"

    def test_other_users_job_is_404(self, client, email_service, jobs):
        token = _session(client, email_service)
        jobs["job1"] = self._job("awaiting_review", email="someone@else.com")
        assert self._link(client, token).status_code == 404
        assert self._link(client, token, job_id="nope").status_code == 404
        assert jobs["minted"] == []

    def test_not_in_review_is_409(self, client, email_service, jobs):
        token = _session(client, email_service)
        jobs["job1"] = self._job("rendering_video")
        resp = self._link(client, token)
        assert resp.status_code == 409 and resp.json()["detail"] == "not_in_review"

    def test_requires_session_and_secret(self, client, jobs):
        jobs["job1"] = self._job("awaiting_review")
        assert client.post("/api/kjbox/jobs/job1/review-link", json={}, headers=H).status_code == 401
        assert client.post("/api/kjbox/jobs/job1/review-link", json={},
                           headers={"Authorization": "Bearer x"}).status_code == 403
