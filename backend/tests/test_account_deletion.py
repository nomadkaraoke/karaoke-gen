"""Tests for self-serve account deletion (DELETE /api/users/me) and the kjbox queue.

Composed over the in-memory Firestore fake from the kjbox partner tests (extended
here with dotted field paths and DELETE_FIELD): the real routes, the real
AccountDeletionService and the real UserService. Only true boundaries are faked:
decide's HTTP API, RevenueCat, Apple, GCS objects, Dropbox and job cancellation.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core import exceptions as google_exceptions
from google.cloud.firestore_v1 import DELETE_FIELD

from backend.api.dependencies import require_auth
from backend.api.routes import kjbox as kjbox_routes
from backend.api.routes import users as users_routes
from backend.services import account_deletion_service as ads
from backend.services.account_deletion_service import AccountDeletionService, get_account_deletion_service
from backend.services.auth_service import AuthResult, UserType
from backend.services.decide_partner_client import DecidePartnerClient, DecidePartnerError
from backend.services.user_service import UserService, get_user_service
from backend.tests.test_kjbox_partner_api import (
    FakeCollection,
    FakeDb,
    FakeQuery,
    FakeRef,
    FakeSnap,
    FakeTransaction,
)

EMAIL = "singer@example.com"
OTHER = "other@example.com"
SHA = hashlib.sha256(EMAIL.encode()).hexdigest()
KJBOX_SECRET = "kjbox-secret"
APP_USER_ID = "6f0c8a52-0000-4000-8000-000000000001"


# ---------------------------------------------- Firestore fake with dotted paths

def _get_path(data, path):
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


class DotSnap(FakeSnap):
    @property
    def id(self):
        return self.reference.id


class DotRef(FakeRef):
    def get(self, transaction=None):
        data = self._docs.get(self.id)
        return DotSnap(self, dict(data) if data is not None else None)

    def update(self, data):
        if self.id not in self._docs:
            raise google_exceptions.NotFound("missing")
        doc = self._docs[self.id]
        for key, value in data.items():
            *parents, leaf = key.split(".")
            target = doc
            for part in parents:
                target = target.setdefault(part, {})
            if value is DELETE_FIELD:
                target.pop(leaf, None)
            elif type(value).__name__ == "Increment":
                target[leaf] = (target.get(leaf) or 0) + value.value
            else:
                target[leaf] = value


class DotQuery(FakeQuery):
    def where(self, *args, filter=None, **kwargs):
        return DotQuery(self.coll, self.filters + [filter], self._limit)

    def limit(self, n):
        return DotQuery(self.coll, self.filters, n)

    def stream(self, transaction=None):
        out = [
            DotSnap(DotRef(self.coll, doc_id), dict(data))
            for doc_id, data in list(self.coll.docs.items())
            if all(self._dot_match(data, f) for f in self.filters)
        ]
        return out[: self._limit] if self._limit else out


    @staticmethod
    def _dot_match(data, f):
        value = _get_path(data, f.field_path)
        if f.op_string == "array_contains":
            return isinstance(value, list) and f.value in value
        return value == f.value


class DotCollection(FakeCollection):
    def document(self, doc_id=None):
        return DotRef(self, doc_id or f"auto-{next(self._ids)}")

    def where(self, *args, filter=None, **kwargs):
        return DotQuery(self, [filter])


class DotDb(FakeDb):
    def collection(self, name):
        return self.collections.setdefault(name, DotCollection())


# ------------------------------------------------------------- other boundaries

class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def delete(self):
        if self.name not in self.bucket.objects:
            raise google_exceptions.NotFound("gone")
        self.bucket.objects.discard(self.name)
        self.bucket.deleted.append(self.name)


class FakeBucket:
    def __init__(self, names=()):
        self.objects = set(names)
        self.deleted = []
        self.fail_prefixes = set()

    def list_blobs(self, prefix):
        if prefix in self.fail_prefixes:
            raise google_exceptions.ServiceUnavailable("gcs down")
        return [FakeBlob(self, n) for n in sorted(self.objects) if n.startswith(prefix)]


class Recorder:
    """Records the order boundary calls happen in."""

    def __init__(self):
        self.calls = []


class FakeHttp:
    def __init__(self, calls, label, status=200, payload=None):
        self.calls, self.label, self.status, self.payload = calls, label, status, payload or {}
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        self.calls.append(self.label)
        if isinstance(self.status, Exception):
            raise self.status
        return SimpleNamespace(status_code=self.status, json=lambda: self.payload)


# ------------------------------------------------------------------- fixtures

@pytest.fixture
def db():
    return DotDb()


@pytest.fixture
def settings():
    return SimpleNamespace(
        account_deletion_enabled=True,
        revenuecat_secret_api_key="rc-secret",
        firestore_collection="jobs",
        kjbox_partner_secret=KJBOX_SECRET,
    )


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def user_service(db):
    svc = UserService.__new__(UserService)
    svc.db = db
    svc.settings = MagicMock()
    return svc


@pytest.fixture
def bucket():
    return FakeBucket()


@pytest.fixture
def decide_http(rec):
    return FakeHttp(rec.calls, "decide", payload={"deleted": {"decide_users": 1}})


@pytest.fixture
def rc_http(rec):
    return FakeHttp(rec.calls, "revenuecat")


@pytest.fixture
def dropbox(rec):
    box = MagicMock()
    box.revoke_shared_link.side_effect = lambda url: rec.calls.append(("dropbox_revoke", url))
    return box


@pytest.fixture
def apple(rec):
    m = MagicMock(return_value=True)
    m.side_effect = lambda code: rec.calls.append(("apple", code)) or True
    return m


@pytest.fixture
def cancelled(db, rec):
    log = []

    def cancel(job_id):
        log.append(job_id)
        rec.calls.append(("cancel", job_id))
        db.collection("jobs").docs[job_id]["status"] = "cancelled"
        return True

    cancel.log = log
    return cancel


@pytest.fixture
def svc(db, settings, rec, bucket, decide_http, rc_http, dropbox, apple, cancelled):
    service = AccountDeletionService(
        db,
        settings=settings,
        cancel_job=cancelled,
        bucket=bucket,
        dropbox=dropbox,
        decide=DecidePartnerClient(base_url="https://decide.test", secret="partner", http_client=decide_http),
        http_client=rc_http,
        apple_revoke=apple,
        transaction_runner=lambda fn: fn(FakeTransaction(db.txn_writes)),
    )
    # Record when the in-process steps run (they call through to the real code).
    for name, label in (("_enqueue_kjbox", "kjbox"), ("_scrub_jobs", "files"), ("_delete_records", "records")):
        real = getattr(service, name)

        def wrapped(*a, _real=real, _label=label, **kw):
            rec.calls.append(_label)
            return _real(*a, **kw)

        setattr(service, name, wrapped)
    return service


@pytest.fixture
def client(monkeypatch, db, settings, user_service, svc):
    app = FastAPI()
    app.include_router(users_routes.router, prefix="/api")
    app.include_router(kjbox_routes.router, prefix="/api")
    current = {"email": EMAIL, "is_admin": False}
    app.dependency_overrides[require_auth] = lambda: AuthResult(
        is_valid=True, user_type=UserType.UNLIMITED, remaining_uses=-1, message="ok",
        user_email=current["email"], is_admin=current["is_admin"],
    )
    app.dependency_overrides[get_user_service] = lambda: user_service
    app.dependency_overrides[get_account_deletion_service] = lambda: svc
    monkeypatch.setattr(users_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(kjbox_routes, "get_settings", lambda: settings)
    c = TestClient(app)
    c.current = current
    return c


def _seed_user(db, email=EMAIL, **over):
    doc = {
        "email": email,
        "role": "user",
        "credits": 3,
        "credit_transactions": [{"id": "t1", "amount": 3, "reason": "purchase", "created_at": "2026-10-01T00:00:00"}],
        "total_jobs_created": 2,
        "total_jobs_completed": 1,
        "total_spent": 2399,
        "display_name": "Sam Singer",
        "app_user_id": APP_USER_ID,
        "expo_push_tokens": [{"token": "ExponentPushToken[x]", "platform": "ios", "device_id": "d1"}],
        "expo_push_token_values": ["ExponentPushToken[x]"],
        "push_subscriptions": [{"endpoint": "https://push.example/1", "keys": {}}],
        "created_at": "2026-09-01T00:00:00",
        "is_active": True,
    }
    doc.update(over)
    db.collection("gen_users").docs[email] = doc
    return doc


def _seed_job(db, job_id, status="complete", email=EMAIL, **over):
    doc = {
        "job_id": job_id,
        "status": status,
        "user_email": email,
        "artist": "Invented Band",
        "title": "Made Up Song",
        "creation_ip": "203.0.113.9",
        "request_metadata": {"client_ip": "203.0.113.9", "environment": "production"},
        "state_data": {
            "customer_email": email,
            "customer_name": "Sam Singer",
            "notification_email": email,
            "display_name": "Sam",
            "brand_code": "NOMAD-1234",
            "youtube_url": "https://youtube.com/watch?v=abc",
        },
        "output_files": {"final_karaoke_lossy_720p_mp4": f"jobs/{job_id}/finals/lossy_720p.mp4"},
        "dropbox_path": "/Karaoke/Tracks-Organized",
    }
    doc.update(over)
    db.collection("jobs").docs[job_id] = doc
    return doc


def _session(user_service, email=EMAIL):
    return user_service.create_session(email).token


def _delete(client, **body):
    return client.request("DELETE", "/api/users/me", json=body or None)


def _job(db, job_id):
    return db.collection("jobs").docs[job_id]


# ------------------------------------------------------------------- the flag

class TestFlag:
    def test_flag_off_404(self, client, db, settings):
        _seed_user(db)
        settings.account_deletion_enabled = False
        assert _delete(client).status_code == 404
        assert client.get("/api/users/me/deletion-preview").status_code == 404
        assert EMAIL in db.collection("gen_users").docs

    def test_routes_registered_in_main_app(self):
        from backend.main import app

        routes = {(getattr(r, "path", ""), m) for r in app.routes for m in getattr(r, "methods", set())}
        assert ("/api/users/me", "DELETE") in routes
        assert ("/api/users/me/deletion-preview", "GET") in routes
        assert ("/api/kjbox/account-deletions", "GET") in routes
        assert ("/api/kjbox/account-deletions/{entry_id}/ack", "POST") in routes


# --------------------------------------------------------------- refusals (409)

class TestRefusals:
    def _assert_refused(self, client, db, rec, code):
        resp = _delete(client)
        assert resp.status_code == 409
        assert resp.json() == {"code": code}
        assert rec.calls == []  # nothing ran
        assert "deletion_id" not in db.collection("gen_users").docs[EMAIL]

    def test_admin_role(self, client, db, rec):
        _seed_user(db, role="admin")
        self._assert_refused(client, db, rec, "admin")

    def test_admin_token(self, client, db, rec):
        _seed_user(db)
        client.current["is_admin"] = True
        self._assert_refused(client, db, rec, "admin")

    def test_unpaid_referral_earnings(self, client, db, rec):
        _seed_user(db)
        db.collection("referral_earnings").docs["e1"] = {"referrer_email": EMAIL, "status": "pending"}
        self._assert_refused(client, db, rec, "referral_balance")

    def test_paid_referral_earnings_do_not_block(self, client, db):
        _seed_user(db)
        db.collection("referral_earnings").docs["e1"] = {"referrer_email": EMAIL, "status": "paid"}
        assert client.get("/api/users/me/deletion-preview").json()["blocked"] is None

    def test_stripe_connect_account(self, client, db, rec):
        _seed_user(db, stripe_connect_account_id="acct_123")
        self._assert_refused(client, db, rec, "connect_account")

    def test_made_for_you_order_in_progress(self, client, db, rec):
        _seed_user(db)
        _seed_job(db, "mfy1", status="awaiting_review", email="admin@nomadkaraoke.com",
                  made_for_you=True, customer_email=EMAIL)
        self._assert_refused(client, db, rec, "order_in_progress")

    def test_finished_made_for_you_order_does_not_block(self, client, db):
        _seed_user(db)
        _seed_job(db, "mfy1", status="complete", email="admin@nomadkaraoke.com",
                  made_for_you=True, customer_email=EMAIL)
        assert client.get("/api/users/me/deletion-preview").json()["blocked"] is None


# --------------------------------------------------------------------- preview

def test_preview_counts(client, db):
    _seed_user(db, credits=4)
    _seed_job(db, "j1", status="awaiting_review")
    _seed_job(db, "j2", status="downloading")
    _seed_job(db, "j3", status="complete")
    _seed_job(db, "j4", status="awaiting_review", email=OTHER)
    resp = client.get("/api/users/me/deletion-preview")
    assert resp.status_code == 200
    assert resp.json() == {"credits": 4, "jobs_in_progress": 2, "blocked": None}


def test_preview_negative_balance_shows_zero(client, db):
    _seed_user(db, credits=-2)
    assert client.get("/api/users/me/deletion-preview").json()["credits"] == 0


def test_preview_reports_block(client, db):
    _seed_user(db, stripe_connect_account_id="acct_1")
    assert client.get("/api/users/me/deletion-preview").json()["blocked"] == "connect_account"


# ------------------------------------------------------------------ happy path

def test_happy_path_order(client, db, rec, user_service, bucket, rc_http, decide_http):
    _seed_user(db)
    _seed_job(db, "j1", status="complete")
    token = _session(user_service)
    db.collection("magic_links").docs["ml1"] = {"email": EMAIL, "token": "ml1"}
    db.collection("magic_links").docs["ml2"] = {"email": OTHER, "token": "ml2"}
    db.collection("login_codes").docs[SHA] = {"code_hash": "h"}
    db.collection("kjbox_login_codes").docs[SHA] = {"code_hash": "h"}
    db.collection("kjbox_signups").docs["s1"] = {"email": EMAIL, "venue": "Bar"}
    db.collection("stripe_payments").docs["p1"] = {"customer_email": EMAIL, "amount": 2399}
    db.collection("store_payments").docs["sp1"] = {"user_email": EMAIL, "amount_cents": 2399}

    resp = _delete(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    deletion_id = body["summary"]["deletion_id"]

    # decide → revenuecat → kjbox enqueue → files → records, then decide again (R10)
    assert rec.calls == ["decide", "revenuecat", "kjbox", "files", "records", "decide"]
    method, url, kwargs = rc_http.requests[0]
    assert (method, url) == ("DELETE", f"https://api.revenuecat.com/v1/subscribers/{APP_USER_ID}")
    assert kwargs["headers"]["Authorization"] == "Bearer rc-secret"
    method, url, kwargs = decide_http.requests[0]
    assert (method, url) == ("DELETE", "https://decide.test/api/partner/gen/users")
    assert kwargs["json"] == {"email": EMAIL} and kwargs["headers"]["X-Partner-Secret"] == "partner"

    assert EMAIL not in db.collection("gen_users").docs
    tomb = db.collection("deleted_users").docs[deletion_id]
    assert tomb["email_sha256"] == SHA
    assert EMAIL not in repr(tomb) and "Sam" not in repr(tomb)
    assert tomb["credit_transactions"][0]["id"] == "t1"
    assert tomb["total_spent"] == 2399 and tomb["credits_forfeited"] == 3

    # sessions revoked, magic links / login codes / kjbox signups gone, others kept
    assert user_service.validate_session(token)[0] is False
    assert not [d for d in db.collection("sessions").docs.values() if d.get("user_email") == EMAIL]
    assert set(db.collection("magic_links").docs) == {"ml2"}
    assert SHA not in db.collection("login_codes").docs
    assert SHA not in db.collection("kjbox_login_codes").docs
    assert db.collection("kjbox_signups").docs == {}
    assert "p1" in db.collection("stripe_payments").docs and "sp1" in db.collection("store_payments").docs

    job = _job(db, "j1")
    assert job["user_email"] == f"deleted:{deletion_id}"
    for key in ("customer_email", "customer_name", "notification_email", "display_name"):
        assert key not in job["state_data"]
    assert job["creation_ip"] is None and "client_ip" not in job["request_metadata"]
    assert EMAIL not in repr(job)

    queued = db.collection("account_deletions").docs[SHA]
    assert queued["email_hash"] == SHA and queued["targets"] == {"kjbox": "pending"}


def test_revenuecat_skipped_without_key(client, db, rec, settings):
    _seed_user(db)
    settings.revenuecat_secret_api_key = ""
    resp = _delete(client)
    assert resp.status_code == 200
    assert resp.json()["summary"]["revenuecat"] == "skipped"
    assert "revenuecat" not in rec.calls


def test_revenuecat_404_counts_as_deleted(client, db, rc_http):
    _seed_user(db)
    rc_http.status = 404
    assert _delete(client).status_code == 200


def test_revenuecat_failure_503(client, db, rc_http):
    _seed_user(db)
    rc_http.status = 500
    resp = _delete(client)
    assert resp.status_code == 503 and resp.json() == {"step": "revenuecat"}
    assert EMAIL in db.collection("gen_users").docs


def test_second_decide_failure_still_200(client, db, decide_http):
    _seed_user(db)
    original = decide_http.request
    count = {"n": 0}

    def flaky(method, url, **kw):
        count["n"] += 1
        if count["n"] == 2:
            raise __import__("httpx").ConnectError("down")
        return original(method, url, **kw)

    decide_http.request = flaky
    assert _delete(client).status_code == 200
    assert count["n"] == 2


# ------------------------------------------------------------ failure + retry

def test_decide_failure_503_and_user_intact(client, db, rec, user_service, decide_http, rc_http):
    _seed_user(db)
    _seed_job(db, "j1", status="complete")
    token = _session(user_service)
    decide_http.status = 500

    resp = _delete(client)
    assert resp.status_code == 503
    assert resp.json() == {"step": "decide"}
    assert EMAIL in db.collection("gen_users").docs
    assert user_service.validate_session(token)[0] is True
    assert _job(db, "j1")["user_email"] == EMAIL
    assert rc_http.requests == [] and db.collection("account_deletions").docs == {}
    first_id = db.collection("gen_users").docs[EMAIL]["deletion_id"]

    decide_http.status = 200
    resp = _delete(client)
    assert resp.status_code == 200
    assert resp.json()["summary"]["deletion_id"] == first_id  # R3: reused on retry
    assert EMAIL not in db.collection("gen_users").docs
    assert _job(db, "j1")["user_email"] == f"deleted:{first_id}"


def test_decide_unconfigured_503(client, db, svc):
    _seed_user(db)
    svc._decide = DecidePartnerClient(base_url="https://decide.test", secret="")
    resp = _delete(client)
    assert resp.status_code == 503 and resp.json() == {"step": "decide"}


def test_files_failure_then_retry_completes(client, db, rec, bucket, cancelled):
    _seed_user(db)
    _seed_job(db, "j1", status="awaiting_review")
    _seed_job(db, "j2", status="complete")
    bucket.objects |= {"uploads/j1/audio/mix.flac", "uploads/j2/audio/mix.flac"}
    bucket.fail_prefixes.add("uploads/j2/")

    resp = _delete(client)
    assert resp.status_code == 503 and resp.json() == {"step": "files"}
    assert EMAIL in db.collection("gen_users").docs
    deletion_id = db.collection("gen_users").docs[EMAIL]["deletion_id"]
    queued_at = db.collection("account_deletions").docs[SHA]["created_at"]

    bucket.fail_prefixes.clear()
    rec.calls.clear()
    resp = _delete(client)
    assert resp.status_code == 200
    assert cancelled.log == ["j1"]  # no double cancel
    assert ("cancel", "j1") not in rec.calls
    assert resp.json()["summary"]["kjbox"] == "already_queued"  # no double enqueue
    assert db.collection("account_deletions").docs[SHA]["created_at"] == queued_at
    assert len(db.collection("account_deletions").docs) == 1
    assert bucket.objects == set()
    for job_id in ("j1", "j2"):
        assert _job(db, job_id)["user_email"] == f"deleted:{deletion_id}"
    assert list(db.collection("deleted_users").docs) == [deletion_id]


def test_redeletion_of_recreated_account_requeues_kjbox(client, db):
    _seed_user(db)
    assert _delete(client).status_code == 200
    db.collection("account_deletions").docs[SHA]["targets"]["kjbox"] = "done"
    _seed_user(db)  # signed up again with the same email
    assert _delete(client).status_code == 200
    assert db.collection("account_deletions").docs[SHA]["targets"]["kjbox"] == "pending"
    assert len(db.collection("deleted_users").docs) == 2


# --------------------------------------------------------------- inputs only

def test_outputs_never_deleted(client, db, bucket, dropbox, rec):
    _seed_user(db)
    _seed_job(db, "j1", status="complete", state_data={
        "customer_email": EMAIL,
        "dropbox_link": "https://www.dropbox.com/scl/fo/abc/share",
        "brand_code": "NOMAD-1234",
        "youtube_url": "https://youtube.com/watch?v=abc",
        "stems": {"instrumental_clean": "jobs/j1/stems/instrumental_clean.flac"},
    })
    inputs = {
        "uploads/j1/audio/mix.flac",
        "uploads/j1/lyrics/user_lyrics.txt",
        "jobs/j1/input/audio/mix.flac",
        "jobs/j1/input/audio/existing_instrumental.flac",
        "jobs/j1/uploads/custom_instrumental_source.wav",
        "jobs/j1/audio_edit/upload_1234.wav",
        "jobs/j1/audio_edit/edit_5678.flac",
        "jobs/j1/style/karaoke_background.png",
        "jobs/j1/style/font.ttf",
        "jobs/j1/custom_instrumental.wav",
        "jobs/j1/existing_instrumental.mp3",
    }
    outputs = {
        "jobs/j1/stems/custom_instrumental.flac",
        "jobs/j1/finals/lossy_720p.mp4",
        "jobs/j1/finals/lossless_4k.mkv",
        "jobs/j1/stems/instrumental_clean.flac",
        "jobs/j1/stems/vocals.flac",
        "jobs/j1/videos/with_vocals.mkv",
        "jobs/j1/packages/cdg.zip",
        "jobs/j1/inputs_note.txt",  # not under input/
    }
    bucket.objects |= inputs | outputs | {"uploads/j9/audio/someone_else.flac"}

    assert _delete(client).status_code == 200

    assert bucket.objects == outputs | {"uploads/j9/audio/someone_else.flac"}
    assert set(bucket.deleted) == inputs
    dropbox.revoke_shared_link.assert_called_once_with("https://www.dropbox.com/scl/fo/abc/share")
    dropbox.delete_folder.assert_not_called()
    job = _job(db, "j1")
    assert job["output_files"] == {"final_karaoke_lossy_720p_mp4": "jobs/j1/finals/lossy_720p.mp4"}
    assert job["dropbox_path"] == "/Karaoke/Tracks-Organized"
    assert "dropbox_link" not in job["state_data"]
    assert job["state_data"]["stems"] == {"instrumental_clean": "jobs/j1/stems/instrumental_clean.flac"}
    assert job["state_data"]["youtube_url"] == "https://youtube.com/watch?v=abc"
    assert job["state_data"]["brand_code"] == "NOMAD-1234"


def test_dropbox_revoke_failure_503_keeps_link_for_retry(client, db, dropbox):
    _seed_user(db)
    _seed_job(db, "j1", state_data={"dropbox_link": "https://www.dropbox.com/scl/fo/x"})
    dropbox.revoke_shared_link.side_effect = RuntimeError("dropbox down")
    resp = _delete(client)
    assert resp.status_code == 503 and resp.json() == {"step": "files"}
    assert _job(db, "j1")["state_data"]["dropbox_link"] == "https://www.dropbox.com/scl/fo/x"
    assert _job(db, "j1")["user_email"] == EMAIL


def test_completed_made_for_you_order_anonymised(client, db):
    _seed_user(db)
    _seed_job(db, "mfy1", status="complete", email="admin@nomadkaraoke.com",
              made_for_you=True, customer_email=EMAIL, customer_notes="Please make it in E flat")
    body = _delete(client).json()
    job = _job(db, "mfy1")
    assert job["user_email"] == "admin@nomadkaraoke.com"
    assert job["customer_email"] == f"deleted:{body['summary']['deletion_id']}"
    assert job["customer_notes"] is None


def test_other_users_jobs_untouched(client, db):
    _seed_user(db)
    _seed_job(db, "j9", email=OTHER)
    before = dict(_job(db, "j9"))
    assert _delete(client).status_code == 200
    assert _job(db, "j9") == before


# -------------------------------------------------------------- active jobs

def test_active_jobs_cancelled(client, db, cancelled, rec):
    _seed_user(db)
    _seed_job(db, "j1", status="awaiting_review")
    _seed_job(db, "j2", status="rendering_video")
    _seed_job(db, "j3", status="complete")
    _seed_job(db, "j4", status="failed")
    _seed_job(db, "j5", status="awaiting_review", email=OTHER)
    resp = _delete(client)
    assert resp.status_code == 200
    assert sorted(cancelled.log) == ["j1", "j2"]
    assert resp.json()["summary"]["jobs"] == 2
    # cancellation runs before decide
    assert rec.calls.index("decide") > max(rec.calls.index(("cancel", j)) for j in ("j1", "j2"))


def test_default_cancel_does_not_refund(monkeypatch):
    jm = MagicMock()
    monkeypatch.setattr("backend.services.job_manager.JobManager", lambda: jm)
    ads._default_cancel_job("j1")
    jm.cancel_job.assert_called_once_with("j1", reason="account deleted", refund=False)


# ------------------------------------------------------------------- Apple

def test_apple_code_passed_to_revoker(client, db, apple, rec):
    _seed_user(db)
    resp = _delete(client, apple_authorization_code="c0de")
    assert resp.status_code == 200
    apple.assert_called_once_with("c0de")
    assert resp.json()["summary"]["apple"] == "revoked"
    assert rec.calls.index("revenuecat") < rec.calls.index(("apple", "c0de")) < rec.calls.index("kjbox")


def test_apple_not_called_without_code(client, db, apple):
    _seed_user(db)
    assert _delete(client).status_code == 200
    apple.assert_not_called()


def test_apple_revoke_false_is_not_a_failure(client, db, apple):
    _seed_user(db)
    apple.side_effect = lambda code: False
    resp = _delete(client, apple_authorization_code="stale")
    assert resp.status_code == 200
    assert resp.json()["summary"]["apple"] == "failed"
    assert EMAIL not in db.collection("gen_users").docs


# ------------------------------------------------------------ welcome credit

def test_resignup_no_second_welcome_credit(client, db, user_service):
    email = "deleteme@inbox.testmail.app"  # a test email: skips the AI credit evaluation
    from backend.utils.test_data import is_test_email

    assert is_test_email(email)
    client.current["email"] = email
    _seed_user(db, email=email, welcome_credits_granted=True)
    assert _delete(client).status_code == 200

    user_service.get_or_create_user(email)
    granted, status = user_service.grant_welcome_credits_if_eligible(email)
    assert (granted, status) == (False, "already_granted")
    assert user_service.get_user(email).credits == 0

    # A brand-new email still gets one.
    other = "fresh@inbox.testmail.app"
    user_service.get_or_create_user(other)
    assert user_service.grant_welcome_credits_if_eligible(other) == (True, "granted")


# ------------------------------------------------------------- kjbox queue

H = {"X-Kjbox-Secret": KJBOX_SECRET}


def test_kjbox_queue_list_and_ack_requires_secret(client, db, settings):
    now = datetime.now(timezone.utc)
    q = db.collection("account_deletions").docs
    q["old"] = {"email_hash": "old", "created_at": now - timedelta(days=2), "targets": {"kjbox": "pending"}}
    q["new"] = {"email_hash": "new", "created_at": now - timedelta(minutes=5), "targets": {"kjbox": "pending"}}
    q["done"] = {"email_hash": "done", "created_at": now, "targets": {"kjbox": "done"}}

    for headers in ({}, {"X-Kjbox-Secret": "wrong"}):
        assert client.get("/api/kjbox/account-deletions", headers=headers).status_code == 403
        assert client.post("/api/kjbox/account-deletions/new/ack", headers=headers).status_code == 403

    resp = client.get("/api/kjbox/account-deletions", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert [d["id"] for d in body["deletions"]] == ["old", "new"]
    assert set(body["deletions"][0]) == {"id", "email_hash", "created_at"}
    assert datetime.fromisoformat(body["deletions"][1]["created_at"]) == q["new"]["created_at"]

    since = (now - timedelta(days=1)).isoformat()
    resp = client.get("/api/kjbox/account-deletions", params={"since": since}, headers=H)
    assert [d["id"] for d in resp.json()["deletions"]] == ["new"]

    resp = client.post("/api/kjbox/account-deletions/new/ack", headers=H)
    assert resp.status_code == 204 and resp.content == b""
    assert q["new"]["targets"]["kjbox"] == "done"
    assert [d["id"] for d in client.get("/api/kjbox/account-deletions", headers=H).json()["deletions"]] == ["old"]
    assert client.post("/api/kjbox/account-deletions/new/ack", headers=H).status_code == 204  # idempotent
    assert client.post("/api/kjbox/account-deletions/missing/ack", headers=H).status_code == 404

    settings.kjbox_partner_secret = ""
    assert client.get("/api/kjbox/account-deletions", headers=H).status_code == 503


def test_deleted_account_appears_in_kjbox_queue(client, db):
    _seed_user(db)
    assert _delete(client).status_code == 200
    entries = client.get("/api/kjbox/account-deletions", headers=H).json()["deletions"]
    assert [(e["id"], e["email_hash"]) for e in entries] == [(SHA, SHA)]
    assert EMAIL not in repr(entries)


# ------------------------------------------------------------ decide client

class TestDecidePartnerClient:
    def test_raises_on_non_2xx(self):
        http = FakeHttp([], "decide", status=401)
        with pytest.raises(DecidePartnerError):
            DecidePartnerClient(base_url="https://d", secret="s", http_client=http).delete_user(EMAIL)

    def test_raises_when_unconfigured(self):
        with pytest.raises(DecidePartnerError):
            DecidePartnerClient(base_url="", secret="s").delete_user(EMAIL)

    def test_returns_payload_and_lowercases_email(self):
        http = FakeHttp([], "decide", payload={"deleted": {"user_songs": 3}})
        client = DecidePartnerClient(base_url="https://d/", secret="s", http_client=http)
        assert client.delete_user("Singer@Example.com") == {"deleted": {"user_songs": 3}}
        assert http.requests[0][1] == "https://d/api/partner/gen/users"
        assert http.requests[0][2]["json"] == {"email": EMAIL}

    def test_default_host_is_decide_main_api(self, monkeypatch):
        from backend.config import get_settings

        assert DecidePartnerClient().base_url == get_settings().karaoke_decide_api_url.rstrip("/")


# ------------------------------------------------- review round 1 additions

def test_singer_with_show_credits_can_delete_and_records_anonymised(client, db):
    _seed_user(db)
    db.collection("kjbox_show_credit_users").docs[SHA] = {"email": EMAIL, "grant_times": ["t"]}
    db.collection("kjbox_show_credits").docs["k1"] = {"email": EMAIL, "idempotency_key": "x", "venue": "Bar"}
    db.collection("kjbox_show_credits").docs["k2"] = {"email": OTHER, "idempotency_key": "y"}
    assert client.get("/api/users/me/deletion-preview").json()["blocked"] is None

    resp = _delete(client)
    assert resp.status_code == 200, resp.text
    tomb = f"deleted:{resp.json()['summary']['deletion_id']}"
    assert db.collection("kjbox_show_credit_users").docs[SHA] == {"email": tomb, "grant_times": ["t"]}
    assert db.collection("kjbox_show_credits").docs["k1"]["email"] == tomb
    assert db.collection("kjbox_show_credits").docs["k1"]["venue"] == "Bar"
    assert db.collection("kjbox_show_credits").docs["k2"]["email"] == OTHER


def test_kept_records_anonymised_and_email_log_deleted(client, db, svc):
    _seed_user(db)
    db.collection("referral_links").docs["sam-sings"] = {
        "code": "sam-sings", "owner_email": EMAIL, "display_name": "Sam", "custom_message": "Hi from Sam", "enabled": True}
    db.collection("referral_links").docs["other"] = {"code": "other", "owner_email": OTHER, "enabled": True}
    db.collection("referral_vanity_requests").docs[EMAIL] = {
        "id": EMAIL, "owner_email": EMAIL, "current_code": "abc", "desired_code": "sam", "status": "pending"}
    db.collection("credit_evaluations").docs["ce1"] = {
        "id": "ce1", "email": EMAIL, "decision": "grant", "reasoning": f"Evaluation error: {EMAIL}"}
    db.collection("song_requests").docs["r1"] = {
        "submitted_by": EMAIL, "owner_email": OTHER, "attempted_owners": [EMAIL, OTHER]}
    db.collection("song_requests").docs["r2"] = {
        "submitted_by": OTHER, "owner_email": EMAIL, "attempted_owners": [OTHER, EMAIL]}
    db.collection("song_request_votes").docs[f"{EMAIL}__2026-10-20"] = {
        "voter_email": EMAIL, "voted_date": "2026-10-20", "request_id": "r1", "value": 1}
    db.collection("email_log").docs["m1"] = {"recipient": EMAIL, "subject": "Your video is ready"}
    db.collection("email_log").docs["m2"] = {"recipient": OTHER, "subject": "Hi"}

    resp = _delete(client)
    assert resp.status_code == 200, resp.text
    tomb = f"deleted:{resp.json()['summary']['deletion_id']}"

    link = db.collection("referral_links").docs["sam-sings"]
    assert link["owner_email"] == tomb and link["enabled"] is False
    assert link["display_name"] is None and link["custom_message"] is None
    assert db.collection("referral_links").docs["other"]["owner_email"] == OTHER
    vanity = db.collection("referral_vanity_requests").docs
    assert EMAIL not in vanity and vanity[tomb]["owner_email"] == tomb and vanity[tomb]["desired_code"] == "sam"
    ce = db.collection("credit_evaluations").docs["ce1"]
    assert ce["email"] == tomb and EMAIL not in ce["reasoning"]
    r1, r2 = (db.collection("song_requests").docs[k] for k in ("r1", "r2"))
    assert r1 == {"submitted_by": tomb, "owner_email": OTHER, "attempted_owners": [tomb, OTHER]}
    assert r2 == {"submitted_by": OTHER, "owner_email": tomb, "attempted_owners": [OTHER, tomb]}
    votes = db.collection("song_request_votes").docs
    assert list(votes) == [f"{tomb}__2026-10-20"] and votes[f"{tomb}__2026-10-20"]["voter_email"] == tomb
    assert set(db.collection("email_log").docs) == {"m2"}

    # Idempotent: a second pass over the same email changes nothing.
    snapshot = {name: {k: dict(v) for k, v in c.docs.items()} for name, c in db.collections.items()}
    assert all(v == 0 for v in svc._anonymise_kept_records(EMAIL, "x").values())
    assert {name: {k: dict(v) for k, v in c.docs.items()} for name, c in db.collections.items()} == snapshot


def test_collection_names_match_their_owning_services():
    from backend.services import credit_evaluation_service, postmark_admin_service, song_request_service

    assert ads.CREDIT_EVALUATIONS_COLLECTION == credit_evaluation_service.CREDIT_EVALUATIONS_COLLECTION
    assert ads.SONG_REQUESTS_COLLECTION == song_request_service.REQUESTS_COLLECTION
    assert ads.SONG_REQUEST_VOTES_COLLECTION == song_request_service.VOTES_COLLECTION
    assert ads.EMAIL_LOG_COLLECTION == postmark_admin_service.EMAIL_LOG_COLLECTION


def test_preconditions_firestore_failure_503(client, db, svc):
    _seed_user(db)

    def boom(email):
        raise google_exceptions.ServiceUnavailable("firestore blip")

    svc._load_user = boom
    resp = _delete(client)
    assert resp.status_code == 503 and resp.json() == {"step": "preconditions"}


def test_deletion_id_failure_503_preconditions(client, db, svc):
    _seed_user(db)
    svc._transaction_runner = lambda fn: (_ for _ in ()).throw(google_exceptions.Aborted("contention"))
    resp = _delete(client)
    assert resp.status_code == 503 and resp.json() == {"step": "preconditions"}


def test_unrevocable_dropbox_link_does_not_block(client, db, dropbox):
    _seed_user(db)
    _seed_job(db, "j1", state_data={"dropbox_link": "https://www.dropbox.com/weird"})
    dropbox.revoke_shared_link.side_effect = lambda url: False
    resp = _delete(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["files"]["dropbox_links_revoked"] == 0
    assert "dropbox_link" not in _job(db, "j1")["state_data"]


class TestDropboxRevoke:
    def _service(self, error):
        from dropbox.exceptions import ApiError

        from backend.services.dropbox_service import DropboxService

        svc = DropboxService()
        svc._client = MagicMock()
        if error is not None:
            svc._client.sharing_revoke_shared_link.side_effect = ApiError("req", error, None, None)
        return svc

    def test_revoked(self):
        assert self._service(None).revoke_shared_link("https://x") is True

    def test_not_found_counts_as_revoked(self):
        from dropbox.sharing import RevokeSharedLinkError

        assert self._service(RevokeSharedLinkError.shared_link_not_found).revoke_shared_link("https://x") is True

    @pytest.mark.parametrize("tag", ["shared_link_malformed", "unsupported_link_type"])
    def test_unrevocable_is_warning_not_failure(self, tag, caplog):
        from dropbox.sharing import RevokeSharedLinkError

        with caplog.at_level("WARNING"):
            assert self._service(getattr(RevokeSharedLinkError, tag)).revoke_shared_link("https://x") is False
        assert any(r.levelname == "WARNING" for r in caplog.records)

    def test_access_denied_raises(self):
        from dropbox.exceptions import ApiError
        from dropbox.sharing import RevokeSharedLinkError

        with pytest.raises(ApiError):
            self._service(RevokeSharedLinkError.shared_link_access_denied).revoke_shared_link("https://x")


class TestFailureLogLevels:
    def _levels(self, caplog):
        return [r.levelname for r in caplog.records if r.name == ads.logger.name and "step" in r.getMessage()]

    def test_retryable_http_failure_is_warning(self, client, db, decide_http, caplog):
        _seed_user(db)
        decide_http.status = 502
        with caplog.at_level("INFO", logger=ads.logger.name):
            assert _delete(client).status_code == 503
        assert self._levels(caplog) == ["WARNING"]

    def test_unexpected_failure_is_error(self, client, db, cancelled, svc, caplog):
        _seed_user(db)
        _seed_job(db, "j1", status="awaiting_review")

        def broken(job_id):
            raise KeyError("bug")

        svc._cancel_job = broken
        with caplog.at_level("INFO", logger=ads.logger.name):
            resp = _delete(client)
        assert resp.status_code == 503 and resp.json() == {"step": "jobs"}
        assert self._levels(caplog) == ["ERROR"]
