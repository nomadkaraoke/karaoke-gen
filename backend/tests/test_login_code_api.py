"""Tests for gen email-code sign-in (/api/users/auth/send-code + verify-code).

Composed tests: the real login_code router + real LoginCodeStore + real
UserService (create_magic_link, sessions, verify) over the in-memory Firestore
fake shared with the kjbox tests. Only true external boundaries are mocked
(email provider, disposable-email lookups, the welcome-credit AI evaluation,
and the signup-cap query, which the fake can't order_by).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import login_code
from backend.services.email_service import EmailService, get_email_service
from backend.services.login_code_store import code_doc_id
from backend.services.user_service import MAGIC_LINKS_COLLECTION, get_user_service
from backend.tests.test_kjbox_partner_api import (  # noqa: F401  (fixtures + fakes)
    FakeDb,
    FakeTransaction,
    db,
    user_service,
    validation,
)

EMAIL = "newuser@example.com"
IP = "203.0.113.7"


@pytest.fixture
def email_service():
    svc = MagicMock()
    svc.is_configured.return_value = True
    svc.send_login_code.return_value = True
    svc.send_welcome_email.return_value = True
    svc.last_send_suppressed = False
    svc.build_magic_link_url.side_effect = (
        lambda token, locale="en", tenant_frontend_url=None, referral_code=None:
        f"{tenant_frontend_url or 'https://gen.test'}/{locale}/auth/verify?token={token}"
    )
    return svc


@pytest.fixture
def settings():
    return SimpleNamespace(
        login_code_pepper="test-pepper",
        admin_tokens="",
        login_codes_per_email_per_hour=5,
        login_codes_per_ip_per_hour=20,
    )


@pytest.fixture
def precompute(monkeypatch):
    calls = []
    monkeypatch.setattr(login_code, "_precompute_credit_eval", lambda *a: calls.append(a))

    class SyncThread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(login_code, "threading", SimpleNamespace(Thread=SyncThread))
    return calls


@pytest.fixture
def signup_limited(user_service):
    state = {"limited": False, "calls": []}

    def fake(ip_address=None, device_fingerprint=None, max_signups=None):
        state["calls"].append({"ip": ip_address, "fp": device_fingerprint})
        return state["limited"]

    user_service.is_signup_rate_limited = fake
    return state


@pytest.fixture
def client(monkeypatch, db, user_service, email_service, validation, settings, precompute, signup_limited):
    original_init = login_code.LoginCodeStore.__init__

    def init_with_fake_runner(self, *args, **kwargs):
        def runner(fn):
            db.txn_count += 1
            return fn(FakeTransaction(db.txn_writes))
        kwargs["transaction_runner"] = runner
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(login_code.LoginCodeStore, "__init__", init_with_fake_runner)
    monkeypatch.setattr(login_code, "get_settings", lambda: settings)
    monkeypatch.setattr(login_code, "get_email_validation_service", lambda: validation)
    monkeypatch.setattr(login_code, "is_production", lambda: False)
    app = FastAPI()
    app.include_router(login_code.router, prefix="/api")
    app.dependency_overrides[get_user_service] = lambda: user_service
    app.dependency_overrides[get_email_service] = lambda: email_service
    return TestClient(app)


H = {"X-Forwarded-For": IP}


def _send(client, email=EMAIL, headers=None, **extra):
    return client.post("/api/users/auth/send-code", json={"email": email, **extra}, headers={**H, **(headers or {})})


def _sent_code(email_service):
    return email_service.send_login_code.call_args.args[1]


def _verify(client, code, email=EMAIL, headers=None):
    return client.post("/api/users/auth/verify-code", json={"email": email, "code": code}, headers={**H, **(headers or {})})


def _code_doc(db, email=EMAIL):
    return db.collection(login_code.LOGIN_CODES_COLLECTION).docs[code_doc_id(email)]


def _wrong(code):
    return f"{(int(code) + 1) % 1_000_000:06d}"


# ------------------------------------------------------------------ send-code

class TestSendCode:
    def test_success_202_and_email_has_code_and_magic_link(self, client, db, email_service, user_service):
        resp = _send(client, locale="de-AT", device_fingerprint="fp-1")
        assert resp.status_code == 202
        assert resp.json() == {"status": "sent"}

        args, kwargs = email_service.send_login_code.call_args
        assert args[0] == EMAIL and len(args[1]) == 6 and args[1].isdigit()
        assert kwargs["expiry_minutes"] == 10 and kwargs["locale"] == "de"
        # The magic link in the email is a real, verifiable magic-link token
        token = kwargs["magic_link_url"].rsplit("token=", 1)[1]
        link = db.collection(MAGIC_LINKS_COLLECTION).docs[token]
        assert link["email"] == EMAIL and link["device_fingerprint"] == "fp-1"

        # Account created at send time with signup audit fields (magic-link parity)
        user = user_service.get_user(EMAIL)
        assert user.signup_ip == IP and user.device_fingerprint == "fp-1"

        doc = _code_doc(db)
        assert doc["locale"] == "de" and doc["ui_locale"] == "de"
        assert doc["ip_address"] == IP and doc["device_fingerprint"] == "fp-1"
        assert args[1] not in str(doc.values())

    def test_router_registered_in_main_app(self):
        from backend.main import app
        paths = {getattr(r, "path", "") for r in app.routes}
        assert {"/api/users/auth/send-code", "/api/users/auth/verify-code"} <= paths

    def test_precomputes_credit_eval_onto_code_and_magic_link(self, client, email_service, precompute):
        _send(client)
        token = email_service.send_login_code.call_args.kwargs["magic_link_url"].rsplit("token=", 1)[1]
        assert precompute == [(
            code_doc_id(EMAIL), EMAIL, login_code.LOGIN_CODES_COLLECTION,
            ((MAGIC_LINKS_COLLECTION, token),),
        )]

    def test_no_precompute_for_test_email_or_already_granted(self, client, user_service, precompute):
        _send(client, email="someone@inbox.testmail.app")
        user_service.get_or_create_user("old@example.com")
        user_service.update_user("old@example.com", welcome_credits_granted=True)
        _send(client, email="old@example.com")
        assert precompute == []

    def test_precompute_eval_helper_writes_all_targets(self, monkeypatch, db, user_service):
        from backend.api.routes import users as users_routes
        import backend.services.credit_evaluation_service as ces
        import backend.services.user_service as us

        db.collection("a").document("1").set({})
        db.collection("b").document("2").set({})
        evaluation = SimpleNamespace(decision="grant", reasoning="ok", error=None)
        monkeypatch.setattr(ces, "get_credit_evaluation_service",
                            lambda: SimpleNamespace(evaluate=lambda e, k: evaluation))
        monkeypatch.setattr(us, "get_user_service", lambda: user_service)
        users_routes._precompute_credit_eval("1", EMAIL, "a", (("b", "2"),))
        assert db.collection("a").docs["1"]["credit_eval_decision"] == "grant"
        assert db.collection("b").docs["2"]["credit_eval_decision"] == "grant"

    def test_locale_falls_back_to_accept_language(self, client, db, email_service):
        _send(client, headers={"Accept-Language": "es-ES,es;q=0.9"})
        assert email_service.send_login_code.call_args.kwargs["locale"] == "es"
        assert _code_doc(db)["locale"] == "es"

    def test_no_locale_does_not_persist_default(self, client, db, email_service):
        _send(client)
        assert email_service.send_login_code.call_args.kwargs["locale"] == "en"
        assert _code_doc(db)["locale"] is None  # don't clobber a stored locale at verify

    def test_invalid_email_422(self, client):
        assert _send(client, email="not-an-email").status_code == 422

    def test_disposable_domain_422(self, client, validation, email_service):
        validation.is_disposable_domain.return_value = True
        resp = _send(client)
        assert resp.status_code == 422
        email_service.send_login_code.assert_not_called()

    def test_blocked_email_pretends_success(self, client, validation, email_service, db, user_service):
        validation.is_email_blocked.return_value = True
        resp = _send(client)
        assert resp.status_code == 202 and resp.json() == {"status": "sent"}
        email_service.send_login_code.assert_not_called()
        assert user_service.get_user(EMAIL) is None
        assert db.collection(login_code.LOGIN_CODES_COLLECTION).docs == {}

    def test_blocked_ip_pretends_success(self, client, validation, email_service):
        validation.is_ip_blocked.side_effect = lambda ip: ip == IP
        resp = _send(client)
        assert resp.status_code == 202 and resp.json() == {"status": "sent"}
        email_service.send_login_code.assert_not_called()

    def test_new_user_signup_rate_limit_pretends_success(self, client, signup_limited, email_service, user_service):
        signup_limited["limited"] = True
        resp = _send(client, device_fingerprint="fp-9")
        assert resp.status_code == 202
        assert signup_limited["calls"] == [{"ip": IP, "fp": "fp-9"}]
        email_service.send_login_code.assert_not_called()
        assert user_service.get_user(EMAIL) is None

    def test_signup_rate_limit_skipped_for_existing_user(self, client, signup_limited, email_service, user_service):
        user_service.get_or_create_user(EMAIL)
        signup_limited["limited"] = True
        assert _send(client).status_code == 202
        assert signup_limited["calls"] == []
        email_service.send_login_code.assert_called_once()

    def test_production_without_email_provider_503(self, client, monkeypatch, email_service):
        monkeypatch.setattr(login_code, "is_production", lambda: True)
        email_service.is_configured.return_value = False
        assert _send(client).status_code == 503

    def test_dev_without_email_provider_still_sends(self, client, email_service):
        email_service.is_configured.return_value = False
        assert _send(client).status_code == 202
        email_service.send_login_code.assert_called_once()

    def test_send_failure_is_not_revealed(self, client, email_service):
        email_service.send_login_code.return_value = False
        assert _send(client).status_code == 202

    def test_per_email_throttle_429(self, client, settings, email_service):
        settings.login_codes_per_email_per_hour = 2
        assert _send(client).status_code == 202
        assert _send(client).status_code == 202
        resp = _send(client)
        assert resp.status_code == 429 and resp.json() == {"detail": "too_many_codes"}
        assert email_service.send_login_code.call_count == 2

    def test_per_ip_throttle_429_across_emails(self, client, settings, email_service, user_service):
        settings.login_codes_per_ip_per_hour = 2
        assert _send(client, email="a@example.com").status_code == 202
        assert _send(client, email="b@example.com").status_code == 202
        resp = _send(client, email="c@example.com")
        assert resp.status_code == 429 and resp.json() == {"detail": "too_many_codes"}
        # Throttled before the magic link (and so the account) was minted
        assert user_service.get_user("c@example.com") is None
        # Another IP is unaffected
        other = _send(client, email="c@example.com", headers={"X-Forwarded-For": "198.51.100.1"})
        assert other.status_code == 202

    def test_tenant_disallowed_email_pretends_success(self, client, monkeypatch, email_service):
        cfg = MagicMock()
        cfg.is_explicitly_allowlisted.return_value = False
        cfg.is_email_allowed.return_value = False
        import backend.middleware.tenant as tenant_mw
        monkeypatch.setattr(tenant_mw, "get_tenant_config_from_request", lambda r: cfg)
        monkeypatch.setattr(tenant_mw, "get_tenant_from_request", lambda r: "vocalstar")
        assert _send(client).status_code == 202
        email_service.send_login_code.assert_not_called()

    def test_tenant_sign_in_uses_tenant_branding_and_skips_credit(
        self, client, monkeypatch, email_service, db, precompute, validation, user_service,
    ):
        cfg = MagicMock()
        cfg.is_explicitly_allowlisted.return_value = True
        cfg.is_email_allowed.return_value = True
        cfg.get_frontend_url.return_value = "https://vs.example"
        cfg.get_sender_email.return_value = "noreply@vs.example"
        cfg.name = "Vocal Star"
        import backend.middleware.tenant as tenant_mw
        monkeypatch.setattr(tenant_mw, "get_tenant_config_from_request", lambda r: cfg)
        monkeypatch.setattr(tenant_mw, "get_tenant_from_request", lambda r: "vocalstar")
        validation.is_disposable_domain.return_value = True  # vouched by tenant allowlist
        assert _send(client).status_code == 202
        kwargs = email_service.send_login_code.call_args.kwargs
        assert kwargs["magic_link_url"].startswith("https://vs.example/")
        assert kwargs["sender_email"] == "noreply@vs.example" and kwargs["tenant_name"] == "Vocal Star"
        assert precompute == []
        assert _code_doc(db)["tenant_id"] == "vocalstar"

        import backend.services.tenant_service as ts
        monkeypatch.setattr(ts, "get_tenant_service", lambda: SimpleNamespace(
            get_tenant_config=lambda tid: SimpleNamespace(subdomain="vocalstar")))
        resp = _verify(client, _sent_code(email_service))
        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_subdomain"] == "vocalstar"
        assert body["credits_granted"] == 0 and body["credit_status"] == "not_applicable"
        assert user_service.grant_calls == []


# ---------------------------------------------------------------- verify-code

class TestVerifyCode:
    def test_success_shape_session_and_welcome_credit(self, client, email_service, user_service, db):
        _send(client, locale="es")
        resp = _verify(client, _sent_code(email_service))
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"status", "session_token", "user", "message", "credits_granted", "credit_status",
                             "tenant_subdomain", "redirect_path"}
        assert body["status"] == "success"
        assert body["credits_granted"] == user_service.NEW_USER_FREE_CREDITS
        assert body["credit_status"] == "granted"
        assert body["user"]["email"] == EMAIL
        assert body["user"]["credits"] == user_service.NEW_USER_FREE_CREDITS
        from backend.i18n import t
        assert body["message"] == t("es", "users.magicLinkSuccess")

        # Session is real and valid
        ok, user, _ = user_service.validate_session(body["session_token"])
        assert ok and user.email == EMAIL
        user = user_service.get_user(EMAIL)
        assert user.email_verified is True and user.last_login_at is not None
        assert user.locale == "es"
        # First login → welcome email in the user's locale
        email_service.send_welcome_email.assert_called_once()
        assert email_service.send_welcome_email.call_args.kwargs["locale"] == "es"

    def test_precomputed_eval_passed_to_grant(self, client, email_service, user_service, db):
        _send(client)
        doc = _code_doc(db)
        doc.update({"credit_eval_decision": "grant", "credit_eval_reasoning": "looks real"})
        _verify(client, _sent_code(email_service))
        assert user_service.grant_calls[-1]["precomputed_eval"] == {
            "credit_eval_decision": "grant", "credit_eval_reasoning": "looks real",
        }

    def test_returning_user_no_second_credit(self, client, email_service, user_service):
        _send(client)
        _verify(client, _sent_code(email_service))
        _send(client)
        body = _verify(client, _sent_code(email_service)).json()
        assert body["credits_granted"] == 0 and body["credit_status"] == "already_granted"
        assert email_service.send_welcome_email.call_count == 1

    def test_wrong_code_401(self, client, email_service):
        _send(client)
        resp = _verify(client, _wrong(_sent_code(email_service)))
        assert resp.status_code == 401 and resp.json() == {"detail": "invalid_code"}

    def test_no_code_issued_401_expired(self, client):
        resp = _verify(client, "123456")
        assert resp.status_code == 401 and resp.json() == {"detail": "expired"}

    def test_expired_code_401(self, client, email_service, db):
        _send(client)
        _code_doc(db)["expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
        resp = _verify(client, _sent_code(email_service))
        assert resp.status_code == 401 and resp.json() == {"detail": "expired"}

    def test_five_wrong_attempts_then_429(self, client, email_service):
        _send(client)
        code = _sent_code(email_service)
        for _ in range(5):
            assert _verify(client, _wrong(code)).status_code == 401
        resp = _verify(client, code)
        assert resp.status_code == 429 and resp.json() == {"detail": "too_many_attempts"}
        # Burned: even the right code no longer works
        assert _verify(client, code).json() == {"detail": "expired"}

    def test_code_is_single_use(self, client, email_service):
        _send(client)
        code = _sent_code(email_service)
        assert _verify(client, code).status_code == 200
        assert _verify(client, code).json() == {"detail": "expired"}

    def test_recreates_account_if_removed_after_send(self, client, email_service, user_service, db):
        _send(client, device_fingerprint="fp-2")
        db.collection("gen_users").docs.clear()
        assert user_service.get_user(EMAIL) is None
        resp = _verify(client, _sent_code(email_service))
        assert resp.status_code == 200
        user = user_service.get_user(EMAIL)
        assert user.signup_ip == IP and user.device_fingerprint == "fp-2"

    def test_session_gets_fingerprint_from_code_record(self, client, email_service, user_service, monkeypatch):
        seen = {}
        real = user_service.create_session

        def spy(email, **kw):
            seen.update(kw)
            return real(email, **kw)

        monkeypatch.setattr(user_service, "create_session", spy)
        _send(client, device_fingerprint="fp-3")
        _verify(client, _sent_code(email_service), headers={"User-Agent": "ua-x"})
        assert seen["device_fingerprint"] == "fp-3" and seen["ip_address"] == IP
        assert seen["user_agent"] == "ua-x" and seen["tenant_id"] is None


# ------------------------------------------------------------------- pepper

class TestPepper:
    def test_explicit_pepper(self, monkeypatch, settings):
        monkeypatch.setattr(login_code, "get_settings", lambda: settings)
        assert login_code._resolve_pepper() == "test-pepper"

    def test_derived_from_first_admin_token(self, monkeypatch, settings):
        settings.login_code_pepper = ""
        settings.admin_tokens = " tok-a , tok-b"
        monkeypatch.setattr(login_code, "get_settings", lambda: settings)
        assert login_code._resolve_pepper() == hashlib.sha256(b"gen-login-code-pepper:tok-a").hexdigest()

    def test_dev_fallback(self, monkeypatch, settings):
        settings.login_code_pepper, settings.admin_tokens = "", None
        monkeypatch.setattr(login_code, "get_settings", lambda: settings)
        monkeypatch.setattr(login_code, "is_production", lambda: False)
        assert login_code._resolve_pepper() == login_code._DEV_PEPPER

    def test_production_without_pepper_returns_503(self, client, monkeypatch, settings):
        settings.login_code_pepper, settings.admin_tokens = "", ""
        monkeypatch.setattr(login_code, "is_production", lambda: True)
        assert _send(client).status_code == 503
        assert _verify(client, "123456").status_code == 503

    def test_codes_from_one_pepper_fail_under_another(self, client, settings, email_service):
        _send(client)
        code = _sent_code(email_service)
        settings.login_code_pepper = "rotated"
        assert _verify(client, code).json() == {"detail": "invalid_code"}


# -------------------------------------------------------------------- email

class TestLoginCodeEmail:
    def _send(self, locale="en", **kw):
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True
        url = service.build_magic_link_url("tok123", locale=locale)
        result = service.send_login_code(
            "user@example.com", "042917", magic_link_url=url, expiry_minutes=10, locale=locale, **kw,
        )
        return result, url, service.provider.send_email.call_args.kwargs

    def test_contains_code_and_magic_link(self):
        result, url, kwargs = self._send()
        assert result is True
        assert url.endswith("/en/auth/verify?token=tok123")
        assert kwargs["to_email"] == "user@example.com"
        assert kwargs["subject"] == "Your Nomad Karaoke sign-in code: 042917"
        for body in (kwargs["html_content"], kwargs["text_content"]):
            assert "042917" in body
            assert "10 minutes" in body
            assert "/auth/verify?token=tok123" in body

    def test_localised(self):
        from backend.i18n import t
        _, url, kwargs = self._send(locale="de")
        assert "/de/auth/verify" in url
        assert kwargs["subject"] == t("de", "emails.loginCode.subject", brand_name="Nomad Karaoke", code="042917")
        assert "Anmelde" in kwargs["subject"]

    def test_tenant_branding(self):
        _, _, kwargs = self._send(sender_email="noreply@vs.example", tenant_name="Vocal Star")
        assert "Vocal Star" in kwargs["subject"]
        assert kwargs["from_email_override"] == "noreply@vs.example"
        assert "© " in kwargs["text_content"] and "Vocal Star" in kwargs["text_content"]

    def test_build_magic_link_url_matches_magic_link_email(self):
        service = EmailService()
        service.provider = Mock()
        service.provider.send_email.return_value = True
        service.send_magic_link("user@example.com", "tokX", locale="es", referral_code="ref1")
        html = service.provider.send_email.call_args.kwargs["html_content"]
        assert service.build_magic_link_url("tokX", locale="es", referral_code="ref1") in html
