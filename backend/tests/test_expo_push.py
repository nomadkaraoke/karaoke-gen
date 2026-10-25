"""Tests for Expo push: token registration, per-type prefs, sending and job triggers.

Composed over the in-memory Firestore fake from the kjbox partner tests: the real
ExpoPushService, UserService, PushNotificationService and JobManager trigger code.
Only the true boundaries (Expo HTTP, pywebpush) are faked.
"""
import json
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import require_auth
from backend.api.routes import push as push_routes
from backend.api.routes import users as users_routes
from backend.models.job import Job, JobStatus
from backend.services import expo_push_service as eps
from backend.services.auth_service import AuthResult, UserType
from backend.services.expo_push_service import ExpoPushService
from backend.services.fake_workers import outbox
from backend.services.job_manager import JobManager
from backend.services.push_notification_service import PushNotificationService
from backend.services.user_service import UserService, get_user_service
from backend.tests.test_kjbox_partner_api import FakeDb, FakeTransaction

A = "alice@example.com"
B = "bob@example.com"
TOKEN = "ExponentPushToken[abc123]"
TOKEN2 = "ExponentPushToken[def456]"


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def user_service(db):
    svc = UserService.__new__(UserService)
    svc.db = db
    svc.settings = MagicMock()
    for email in (A, B):
        db.collection("gen_users").docs[email] = {"email": email}
    return svc


def _service(db, user_service, http_post=None):
    return ExpoPushService(
        user_service=user_service,
        transaction_runner=lambda fn: fn(FakeTransaction(db.txn_writes)),
        http_post=http_post,
    )


@pytest.fixture
def expo(db, user_service):
    return _service(db, user_service)


def _doc(db, email):
    return db.collection("gen_users").docs[email]


def _tokens(db, email):
    return [e["token"] for e in _doc(db, email).get("expo_push_tokens", [])]


@pytest.fixture
def client(db, user_service, expo):
    app = FastAPI()
    app.include_router(push_routes.router, prefix="/api")
    app.include_router(users_routes.router, prefix="/api")
    current = {"email": A}
    app.dependency_overrides[require_auth] = lambda: AuthResult(
        is_valid=True, user_type=UserType.UNLIMITED, remaining_uses=-1,
        message="ok", user_email=current["email"], is_admin=False,
    )
    app.dependency_overrides[get_user_service] = lambda: user_service
    with patch.object(push_routes, "get_expo_push_service", return_value=expo):
        c = TestClient(app)
        c.as_user = lambda email: current.update(email=email)
        yield c


def _register(client, token=TOKEN, device_id="dev-1", platform="ios"):
    return client.post("/api/push/expo-token", json={"token": token, "platform": platform, "device_id": device_id})


class TestTokenRoutes:
    def test_register_token_and_replace_per_device(self, client, db):
        assert _register(client).status_code == 204
        assert _register(client, token=TOKEN2).status_code == 204
        entries = _doc(db, A)["expo_push_tokens"]
        assert [e["token"] for e in entries] == [TOKEN2]
        assert entries[0]["device_id"] == "dev-1" and entries[0]["platform"] == "ios"

    def test_second_device_adds_entry(self, client, db):
        _register(client)
        _register(client, token=TOKEN2, device_id="dev-2", platform="android")
        assert _tokens(db, A) == [TOKEN, TOKEN2]

    def test_reregistering_same_token_keeps_one_entry(self, client, db):
        _register(client)
        _register(client)
        assert _tokens(db, A) == [TOKEN]

    def test_register_moves_token_from_other_user(self, client, db):
        _register(client)
        client.as_user(B)
        assert _register(client, device_id="dev-b").status_code == 204
        assert _tokens(db, A) == []
        assert _doc(db, A)["expo_push_token_values"] == []
        assert _tokens(db, B) == [TOKEN]

    def test_delete_token(self, client, db):
        _register(client)
        _register(client, token=TOKEN2, device_id="dev-2")
        r = client.request("DELETE", "/api/push/expo-token", json={"token": TOKEN})
        assert r.status_code == 204
        assert _tokens(db, A) == [TOKEN2]

    def test_delete_cannot_remove_another_users_token(self, client, db):
        _register(client)
        client.as_user(B)
        assert client.request("DELETE", "/api/push/expo-token", json={"token": TOKEN}).status_code == 204
        assert _tokens(db, A) == [TOKEN]

    def test_delete_unknown_token_is_noop(self, client):
        assert client.request("DELETE", "/api/push/expo-token", json={"token": TOKEN}).status_code == 204

    @pytest.mark.parametrize("bad", ["not-a-token", "ExponentPushToken[]", "fcm:abc", "ExponentPushToken[a b]"])
    def test_rejects_non_expo_token(self, client, bad):
        assert _register(client, token=bad).status_code == 422

    def test_rejects_unknown_platform(self, client):
        assert _register(client, platform="web").status_code == 422


class TestPrefsRoute:
    def test_prefs_patch_partial(self, client, db):
        r = client.patch("/api/users/me/push-prefs", json={"review": False})
        assert r.status_code == 200
        assert r.json() == {"ready": True, "review": False, "problems": True}
        r = client.patch("/api/users/me/push-prefs", json={"problems": False})
        assert r.json() == {"ready": True, "review": False, "problems": False}
        assert _doc(db, A)["push_prefs"] == {"ready": True, "review": False, "problems": False}

    def test_prefs_patch_empty_returns_current(self, client):
        assert client.patch("/api/users/me/push-prefs", json={}).json() == {
            "ready": True, "review": True, "problems": True}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": self._data}


def _seed_tokens(db, email, tokens):
    _doc(db, email)["expo_push_tokens"] = [
        {"token": t, "platform": "ios", "device_id": f"d{i}"} for i, t in enumerate(tokens)]
    _doc(db, email)["expo_push_token_values"] = list(tokens)


class TestSend:
    def test_sends_message_shape(self, db, user_service):
        _seed_tokens(db, A, [TOKEN])
        calls = []
        svc = _service(db, user_service, lambda url, json, **kw: calls.append((url, json)) or _Resp([{"status": "ok"}]))
        assert svc.send(A, "ready", "T", "B", "/job/1") == 1
        url, msgs = calls[0]
        assert url == "https://exp.host/--/api/v2/push/send"
        assert msgs == [{"to": TOKEN, "title": "T", "body": "B", "sound": "default",
                         "channelId": "jobs", "data": {"url": "/job/1", "kind": "ready"}}]

    def test_device_not_registered_removes_token(self, db, user_service):
        _seed_tokens(db, A, [TOKEN, TOKEN2])
        resp = _Resp([{"status": "ok"},
                      {"status": "error", "message": "gone", "details": {"error": "DeviceNotRegistered"}}])
        svc = _service(db, user_service, lambda *a, **k: resp)
        assert svc.send(A, "ready", "T", "B", "/job/1") == 1
        assert _tokens(db, A) == [TOKEN]
        assert _doc(db, A)["expo_push_token_values"] == [TOKEN]

    def test_other_ticket_error_keeps_token(self, db, user_service):
        _seed_tokens(db, A, [TOKEN])
        svc = _service(db, user_service, lambda *a, **k: _Resp(
            [{"status": "error", "message": "rate", "details": {"error": "MessageRateExceeded"}}]))
        assert svc.send(A, "ready", "T", "B", "/x") == 0
        assert _tokens(db, A) == [TOKEN]

    def test_batches_over_100(self, db, user_service):
        tokens = [f"ExponentPushToken[t{i}]" for i in range(250)]
        _seed_tokens(db, A, tokens)
        sizes = []

        channels = set()

        def post(url, json, **kw):
            sizes.append(len(json))
            channels.update(m.get("channelId") for m in json)
            return _Resp([{"status": "ok"}] * len(json))

        assert _service(db, user_service, post).send(A, "ready", "T", "B", "/x") == 250
        assert sizes == [100, 100, 50]
        assert channels == {"jobs"}  # every message, every batch, on the app's "jobs" channel

    def test_http_failure_never_raises(self, db, user_service):
        _seed_tokens(db, A, [TOKEN])

        def boom(*a, **k):
            raise RuntimeError("network")

        assert _service(db, user_service, boom).send(A, "ready", "T", "B", "/x") == 0
        assert _tokens(db, A) == [TOKEN]

    def test_pref_off_suppresses(self, db, user_service):
        _seed_tokens(db, A, [TOKEN])
        _doc(db, A)["push_prefs"] = {"ready": False}
        post = MagicMock()
        svc = _service(db, user_service, post)
        assert svc.send(A, "ready", "T", "B", "/x") == 0
        post.assert_not_called()
        assert svc.send(A, "review", "T", "B", "/x") == 0  # on, but post returns a MagicMock: no raise
        post.assert_called_once()

    def test_no_tokens_or_unknown_user(self, db, user_service):
        svc = _service(db, user_service, MagicMock())
        assert svc.send(A, "ready", "T", "B", "/x") == 0
        assert svc.send("nobody@example.com", "ready", "T", "B", "/x") == 0

    def test_fake_workers_mode_records_to_outbox(self, db, user_service, monkeypatch):
        _seed_tokens(db, A, [TOKEN])
        monkeypatch.setattr(eps, "fake_workers_enabled", lambda: True)
        post = MagicMock()
        outbox.clear_pushes()
        assert _service(db, user_service, post).send(A, "problems", "T", "B", "/job/9") == 1
        post.assert_not_called()
        assert outbox.list_pushes(A)[0]["url"] == "/job/9"
        outbox.clear_pushes()


class TestTestSupportOutbox:
    def test_push_outbox_endpoints(self):
        from backend.api.routes import test_support
        app = FastAPI()
        app.include_router(test_support.router, prefix="/api")
        c = TestClient(app)
        outbox.clear_pushes()
        outbox.record_push(A, "ready", "T", "B", "/job/1")
        outbox.record_push(B, "review", "T", "B", "/review/2")
        assert [p["to"] for p in c.get("/api/test/push-outbox").json()["pushes"]] == [B, A]
        assert len(c.get(f"/api/test/push-outbox?to={A}").json()["pushes"]) == 1
        assert c.delete("/api/test/push-outbox").status_code == 200
        assert c.get("/api/test/push-outbox").json()["pushes"] == []


# ------------------------------------------------------------------ job triggers

def _job(status, **kw):
    return Job(job_id="job1", user_email=A, artist="Test Artist", title="Test Song",
               status=status, created_at=datetime(2026, 1, 1),
               updated_at=datetime(2026, 1, 1), **kw)


@pytest.fixture
def triggers(db, user_service, monkeypatch):
    """Run JobManager._trigger_state_notifications with real Expo + Web Push services over the fake DB."""
    _seed_tokens(db, A, [TOKEN])
    monkeypatch.setattr(eps, "fake_workers_enabled", lambda: True)
    outbox.clear_pushes()
    expo = _service(db, user_service)

    settings = SimpleNamespace(enable_push_notifications=True, max_push_subscriptions_per_user=5,
                               vapid_subject="mailto:x@example.com",
                               get_secret=lambda name: "k")
    with patch("backend.services.push_notification_service.get_settings", return_value=settings):
        web = PushNotificationService(db=db)
    _doc(db, A)["push_subscriptions"] = [{"endpoint": "https://push.example/1", "keys": {"p256dh": "x", "auth": "y"}}]
    webpush = MagicMock()
    monkeypatch.setattr("backend.services.push_notification_service.webpush", webpush)

    def run(status, action=None, **job_kw):
        """Fire notifications for ``status`` — or run ``action(mgr)`` (e.g. the real fail_job)."""
        job = _job(status, **job_kw)
        mgr = JobManager.__new__(JobManager)
        mgr.get_job = lambda job_id: job
        mgr._schedule_completion_email = lambda j: None
        mgr._schedule_idle_reminder = lambda j, s: None
        mgr._refund_credit_for_job = MagicMock()
        mgr.firestore = MagicMock()

        def write_status(job_id, status, **kw):
            job.status = status
            if "error_details" in kw:
                job.error_details = kw["error_details"]
        mgr.firestore.update_job_status.side_effect = write_status

        def write_fields(job_id, updates):
            for key, value in updates.items():
                if key.startswith("state_data."):
                    job.state_data[key.split(".", 1)[1]] = value
                else:
                    setattr(job, key, value)
        mgr.firestore.update_job.side_effect = write_fields
        threads = []
        real_thread = threading.Thread

        class Rec(real_thread):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                if getattr(k.get("target"), "__name__", "") == "run_in_thread":
                    threads.append(self)

        with patch("backend.services.expo_push_service.get_expo_push_service", return_value=expo), \
             patch("backend.services.push_notification_service.get_push_notification_service", return_value=web), \
             patch("threading.Thread", Rec):
            if action is None:
                mgr._trigger_state_notifications("job1", status)
            else:
                action(mgr)
            for th in threads:
                th.join(timeout=10)
        return outbox.list_pushes(A), webpush

    yield run
    outbox.clear_pushes()


class TestJobTriggers:
    @pytest.mark.parametrize("status,kind,url", [
        (JobStatus.COMPLETE, "ready", "/job/job1"),
        (JobStatus.AWAITING_REVIEW, "review", "/review/job1"),
        (JobStatus.AWAITING_DURATION_CONFIRM, "review", "/job/duration/job1"),
        (JobStatus.AWAITING_AUDIO_EDIT, "review", "/job/job1"),
        (JobStatus.AWAITING_INSTRUMENTAL_SELECTION, "review", "/job/job1"),
        (JobStatus.FAILED, "problems", "/job/job1"),
    ])
    def test_status_maps_to_kind_and_url(self, triggers, status, kind, url):
        pushes, _ = triggers(status)
        assert [(p["kind"], p["url"]) for p in pushes] == [(kind, url)]
        assert "Test Song" in pushes[0]["body"] or status == JobStatus.COMPLETE

    def test_complete_sends_ready_with_url(self, triggers):
        pushes, webpush = triggers(JobStatus.COMPLETE)
        assert pushes[0]["kind"] == "ready" and pushes[0]["url"] == "/job/job1"
        assert "Test Song" in pushes[0]["body"]
        webpush.assert_called_once()

    def test_failed_sends_problems_and_webpush(self, triggers):
        pushes, webpush = triggers(JobStatus.FAILED)
        assert pushes[0]["kind"] == "problems"
        webpush.assert_called_once()
        assert "problem" in json.loads(webpush.call_args.kwargs["data"])["title"].lower()

    def test_audio_selection_is_not_a_problem_push(self, triggers):
        # The normal interactive search step; stalled downloads end FAILED instead.
        pushes, webpush = triggers(JobStatus.AWAITING_AUDIO_SELECTION)
        assert pushes == []
        webpush.assert_not_called()

    def test_stalled_download_body_offers_to_keep_trying(self, triggers):
        pushes, _ = triggers(JobStatus.FAILED, error_details={"code": "audio_download_stalled"})
        assert "keep trying" in pushes[0]["body"]
        pushes, _ = triggers(JobStatus.FAILED, error_details={"code": "other"})
        assert "keep trying" not in pushes[0]["body"]

    def test_pref_off_suppresses_expo_and_webpush(self, triggers, db):
        _doc(db, A)["push_prefs"] = {"problems": False}
        pushes, webpush = triggers(JobStatus.FAILED)
        assert pushes == []
        webpush.assert_not_called()
        # other types unaffected
        pushes, webpush = triggers(JobStatus.COMPLETE)
        assert len(pushes) == 1
        webpush.assert_called_once()

    def test_expo_failure_does_not_stop_web_push_or_raise(self, triggers, monkeypatch):
        monkeypatch.setattr(ExpoPushService, "send", MagicMock(side_effect=RuntimeError("boom")))
        _, webpush = triggers(JobStatus.COMPLETE)
        webpush.assert_called_once()

    def test_expo_still_sent_when_web_push_disabled(self, triggers):
        with patch.object(PushNotificationService, "is_enabled", return_value=False):
            pushes, webpush = triggers(JobStatus.COMPLETE)
        assert len(pushes) == 1
        webpush.assert_not_called()


def _fail(mgr):
    assert mgr.fail_job("job1", "Audio download failed: boom", {"stage": "audio_download"}) is True


def _mark_failed(mgr):
    mgr.mark_job_failed("job1", "Render crashed", {"stage": "render"})


def _pending(minutes):
    from datetime import timedelta, timezone
    expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return {"cloud_run_retry_pending": {"expires_at": expires.isoformat(), "expected_attempt": 1}}


class TestWorkerFailurePaths:
    """Workers fail jobs via fail_job / mark_job_failed, not transition_to_state."""

    @pytest.mark.parametrize("action", [_fail, _mark_failed])
    def test_worker_failure_pushes_once(self, triggers, action):
        pushes, webpush = triggers(JobStatus.DOWNLOADING_AUDIO, action=action)
        assert [(p["kind"], p["url"]) for p in pushes] == [("problems", "/job/job1")]
        webpush.assert_called_once()

    def test_fail_job_stalled_download_uses_keep_trying_body(self, triggers):
        def fail_stalled(mgr):
            mgr.fail_job("job1", "stalled", {"code": "audio_download_stalled"})
        pushes, _ = triggers(JobStatus.DOWNLOADING_AUDIO, action=fail_stalled)
        assert "keep trying" in pushes[0]["body"]

    @pytest.mark.parametrize("action", [_fail, _mark_failed])
    def test_second_worker_failing_the_same_run_does_not_push_again(self, triggers, action):
        def fail_twice(mgr):
            action(mgr)
            action(mgr)
        pushes, webpush = triggers(JobStatus.DOWNLOADING_AUDIO, action=fail_twice)
        assert len(pushes) == 1
        webpush.assert_called_once()

    def test_already_notified_failure_does_not_push_again(self, triggers):
        pushes, webpush = triggers(JobStatus.FAILED, action=_fail, state_data={"failure_push_sent": True})
        assert pushes == []
        webpush.assert_not_called()

    def test_cloud_run_retry_then_final_failure_pushes_once(self, triggers):
        """Attempt 1 fails with a retry coming (no push); attempt 2 runs while the job
        is still FAILED, clears the marker and fails for good: that one pushes."""
        def two_attempts(mgr):
            mgr.fail_job("job1", "attempt 1", {"stage": "audio_download"})
            mgr.get_job("job1").state_data["cloud_run_retry_pending"] = None
            mgr.fail_job("job1", "attempt 2", {"stage": "audio_download"})
        pushes, webpush = triggers(JobStatus.DOWNLOADING_AUDIO, action=two_attempts, state_data=_pending(10))
        assert len(pushes) == 1
        webpush.assert_called_once()

    def test_no_push_while_cloud_run_auto_retry_pending(self, triggers):
        pushes, webpush = triggers(JobStatus.DOWNLOADING_AUDIO, action=_fail, state_data=_pending(10))
        assert pushes == []
        webpush.assert_not_called()

    def test_expired_retry_marker_still_pushes(self, triggers):
        pushes, _ = triggers(JobStatus.DOWNLOADING_AUDIO, action=_fail, state_data=_pending(-10))
        assert len(pushes) == 1

    @pytest.mark.parametrize("notify_customer,expected", [(False, 0), (True, 1)])
    def test_admin_rerender_failure_respects_notify_customer(self, triggers, notify_customer, expected):
        pushes, _ = triggers(
            JobStatus.RENDERING_VIDEO,
            action=_fail, state_data={"admin_rerender": {"notify_customer": notify_customer}},
        )
        assert len(pushes) == expected

    def test_push_failure_never_breaks_fail_job(self, triggers, monkeypatch):
        monkeypatch.setattr(JobManager, "_send_push_notification", MagicMock(side_effect=RuntimeError("boom")))
        triggers(JobStatus.DOWNLOADING_AUDIO, action=_fail)  # _fail asserts fail_job returned True

    def test_transition_to_failed_with_notify_false_stays_silent(self, triggers):
        def transition(mgr):
            mgr.validate_state_transition = lambda *a, **k: True
            mgr.transition_to_state("job1", JobStatus.FAILED, notify=False)
        pushes, webpush = triggers(JobStatus.DOWNLOADING_AUDIO, action=transition)
        assert pushes == []
        webpush.assert_not_called()


class TestTenantJobs:
    def test_tenant_job_skips_expo_push(self, triggers):
        pushes, _ = triggers(JobStatus.COMPLETE, tenant_id="acme")
        assert pushes == []

    def test_tenant_job_failure_skips_expo_push(self, triggers):
        pushes, _ = triggers(JobStatus.DOWNLOADING_AUDIO, action=_fail, tenant_id="acme")
        assert pushes == []
