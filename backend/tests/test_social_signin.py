"""Tests for Google / Apple ID-token sign-in (POST /api/users/auth/google|apple).

Tokens are real RS256 JWTs signed with a locally generated key; the JWKS client
is monkeypatched to serve its public half, so no network. The endpoints run the
real login_code router + real UserService over the in-memory Firestore fake
(shared with the email-code tests), ending in the same user/session code as
verify-code.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.api.routes import login_code
from backend.services import social_identity
from backend.services.social_identity import (
    InvalidIdentityToken,
    verify_apple_id_token,
    verify_google_id_token,
)
from backend.tests.test_login_code_api import (  # noqa: F401  (fixtures)
    H,
    _send,
    _sent_code,
    _verify,
    client,
    email_service,
    precompute,
    signup_limited,
)
from backend.tests.test_kjbox_partner_api import (  # noqa: F401  (fixtures + fakes)
    FakeDb,
    db,
    user_service,
    validation,
)

GOOGLE_AUD = "web-client.apps.googleusercontent.com"
APPLE_AUD = "com.nomadkaraoke.mobile"
EMAIL = "Signer@Example.com"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def jwks(monkeypatch):
    """Serve the local key's public half from every JWKS URL; record fetched URLs."""
    urls = []

    class FakeJwksClient:
        def __init__(self, url):
            self.url = url

        def get_signing_key_from_jwt(self, token):
            urls.append(self.url)
            return SimpleNamespace(key=_KEY.public_key())

    monkeypatch.setattr(social_identity, "_jwks_client", lambda url: FakeJwksClient(url))
    return urls


@pytest.fixture
def settings():
    return SimpleNamespace(
        login_code_pepper="test-pepper",
        admin_tokens="",
        login_codes_per_email_per_hour=5,
        login_codes_per_ip_per_hour=20,
        google_oauth_client_ids=f"{GOOGLE_AUD}, ios-client.apps.googleusercontent.com",
        apple_signin_audiences=APPLE_AUD,
    )


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch):
    state = {"on": False}
    monkeypatch.setattr(login_code, "fake_workers_enabled", lambda: state["on"])
    return state


def _token(iss="https://accounts.google.com", aud=GOOGLE_AUD, email=EMAIL, key=_KEY, exp_in=600, **claims):
    now = int(time.time())
    payload = {"iss": iss, "aud": aud, "sub": "sub-123", "iat": now, "exp": now + exp_in,
               "email": email, "email_verified": True, **claims}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "k1"})


def _apple_token(**kw):
    kw.setdefault("iss", "https://appleid.apple.com")
    kw.setdefault("aud", APPLE_AUD)
    kw.setdefault("email_verified", "true")
    return _token(**kw)


def _google(client, id_token, **extra):
    return client.post("/api/users/auth/google", json={"id_token": id_token, **extra}, headers=H)


def _apple(client, id_token, **extra):
    return client.post("/api/users/auth/apple", json={"id_token": id_token, **extra}, headers=H)


# ------------------------------------------------------------ token verification

class TestVerifyTokens:
    def test_google_both_issuers(self):
        for iss in ("accounts.google.com", "https://accounts.google.com"):
            ident = verify_google_id_token(_token(iss=iss), [GOOGLE_AUD])
            assert (ident.email, ident.provider, ident.subject) == ("signer@example.com", "google", "sub-123")

    def test_google_uses_google_jwks(self, jwks):
        verify_google_id_token(_token(), [GOOGLE_AUD])
        assert jwks == [social_identity.GOOGLE_JWKS_URL]

    @pytest.mark.parametrize("bad", [
        {"iss": "https://evil.example"},
        {"aud": "someone-else"},
        {"exp_in": -120},
        {"email_verified": False},
        {"email_verified": "false"},
        {"email": None},
        {"key": _OTHER_KEY},
    ])
    def test_google_rejects(self, bad):
        with pytest.raises(InvalidIdentityToken):
            verify_google_id_token(_token(**bad), [GOOGLE_AUD])

    def test_google_rejects_garbage_and_empty_audiences(self):
        with pytest.raises(InvalidIdentityToken):
            verify_google_id_token("not-a-jwt", [GOOGLE_AUD])
        with pytest.raises(InvalidIdentityToken):
            verify_google_id_token(_token(), [])

    def test_google_rejects_unsigned_token(self):
        now = int(time.time())
        unsigned = jwt.encode({"iss": "accounts.google.com", "aud": GOOGLE_AUD, "sub": "x", "iat": now,
                               "exp": now + 60, "email": EMAIL, "email_verified": True}, None, algorithm="none")
        with pytest.raises(InvalidIdentityToken):
            verify_google_id_token(unsigned, [GOOGLE_AUD])

    def test_apple_string_email_verified_and_jwks(self, jwks):
        ident = verify_apple_id_token(_apple_token(), [APPLE_AUD])
        assert ident.provider == "apple" and ident.email == "signer@example.com"
        assert jwks == [social_identity.APPLE_JWKS_URL]

    def test_apple_rejects_google_issuer(self):
        with pytest.raises(InvalidIdentityToken):
            verify_apple_id_token(_apple_token(iss="https://accounts.google.com"), [APPLE_AUD])

    def test_jwks_client_cached_per_url(self, monkeypatch):
        monkeypatch.undo()  # real _jwks_client (no network: PyJWKClient fetches lazily)
        monkeypatch.setattr(social_identity, "_JWKS_CLIENTS", {})
        a = social_identity._jwks_client(social_identity.GOOGLE_JWKS_URL)
        assert social_identity._jwks_client(social_identity.GOOGLE_JWKS_URL) is a
        assert social_identity._jwks_client(social_identity.APPLE_JWKS_URL) is not a


# ------------------------------------------------------------------- endpoints

class TestGoogleSignIn:
    def test_google_valid_token_creates_user_and_session(self, client, user_service, email_service):
        resp = _google(client, _token(), locale="es", device_fingerprint="fp-9",
                       attribution={"utm_source": "app"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success" and body["session_token"]
        assert body["user"]["email"] == "signer@example.com"
        assert body["credits_granted"] == user_service.NEW_USER_FREE_CREDITS
        assert body["credit_status"] == "granted"

        user = user_service.get_user("signer@example.com")
        assert user.email_verified and user.last_login_at
        assert user.signup_ip == H["X-Forwarded-For"] and user.device_fingerprint == "fp-9"
        assert user.locale == "es"
        valid, session_user, _ = user_service.validate_session(body["session_token"])
        assert valid and session_user.email == "signer@example.com"
        email_service.send_welcome_email.assert_called_once()

    def test_google_existing_email_same_user_no_second_welcome_credit(self, client, user_service, email_service):
        # Signed up by email code first...
        _send(client, email="signer@example.com")
        first = _verify(client, _sent_code(email_service), email="signer@example.com").json()
        assert first["credits_granted"] == user_service.NEW_USER_FREE_CREDITS
        credits = user_service.get_user("signer@example.com").credits

        # ...then Google with the same address: same account, same balance.
        resp = _google(client, _token())
        assert resp.status_code == 200
        body = resp.json()
        assert body["credits_granted"] == 0 and body["credit_status"] == "already_granted"
        assert body["user"]["email"] == "signer@example.com"
        assert user_service.get_user("signer@example.com").credits == credits
        assert email_service.send_welcome_email.call_count == 1

    def test_google_unverified_email_401(self, client, user_service):
        resp = _google(client, _token(email_verified=False))
        assert resp.status_code == 401 and resp.json()["detail"] == "invalid_token"
        assert user_service.get_user("signer@example.com") is None

    def test_google_wrong_audience_401(self, client, user_service):
        resp = _google(client, _token(aud="another-app.apps.googleusercontent.com"))
        assert resp.status_code == 401 and resp.json()["detail"] == "invalid_token"
        assert user_service.get_user("signer@example.com") is None

    def test_google_ios_client_audience_accepted(self, client):
        assert _google(client, _token(aud="ios-client.apps.googleusercontent.com")).status_code == 200

    def test_google_expired_401(self, client):
        resp = _google(client, _token(exp_in=-600))
        assert resp.status_code == 401 and resp.json()["detail"] == "invalid_token"

    def test_google_not_configured_503(self, client, settings):
        settings.google_oauth_client_ids = " "
        resp = _google(client, _token())
        assert resp.status_code == 503 and resp.json()["detail"] == "google_not_configured"

    def test_social_signin_respects_blocklist(self, client, validation, user_service, signup_limited):
        validation.is_email_blocked.return_value = True
        resp = _google(client, _token())
        assert resp.status_code == 422
        assert user_service.get_user("signer@example.com") is None

        validation.is_email_blocked.return_value = False
        validation.is_ip_blocked.return_value = True
        assert _google(client, _token()).status_code == 422

        validation.is_ip_blocked.return_value = False
        validation.is_disposable_domain.return_value = True
        assert _google(client, _token()).status_code == 422
        assert user_service.get_user("signer@example.com") is None

    def test_new_user_signup_cap_429(self, client, signup_limited, user_service):
        signup_limited["limited"] = True
        resp = _google(client, _token())
        assert resp.status_code == 429 and resp.json()["detail"] == "signup_rate_limited"
        assert user_service.get_user("signer@example.com") is None

    def test_signup_cap_skipped_for_existing_user(self, client, signup_limited, user_service):
        user_service.get_or_create_user("signer@example.com")
        signup_limited["limited"] = True
        assert _google(client, _token()).status_code == 200

    def test_referral_code_from_body(self, client, monkeypatch):
        seen = {}

        def fake_complete(user, **kw):
            seen.update(kw)
            raise RuntimeError("stop")

        monkeypatch.setattr(login_code, "complete_verified_login", fake_complete)
        with pytest.raises(RuntimeError):
            _google(client, _token(), referral_code="FRIEND10")
        assert seen["referral_code"] == "FRIEND10" and seen["grant_welcome_credit"] is True


class TestAppleSignIn:
    def test_apple_valid_token_string_email_verified(self, client, user_service):
        resp = _apple(client, _apple_token())
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["email"] == "signer@example.com"

    def test_apple_relay_email_allowed(self, client, validation, user_service):
        relay = "abc123xyz@privaterelay.appleid.com"
        # Even if a disposable-domain lookup flagged Apple's relay domain…
        validation.is_disposable_domain.side_effect = lambda e: e.endswith("@privaterelay.appleid.com")
        resp = _apple(client, _apple_token(email=relay))
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["email"] == relay
        assert user_service.get_user(relay) is not None

    def test_apple_wrong_audience_401(self, client):
        resp = _apple(client, _apple_token(aud="com.someone.else"))
        assert resp.status_code == 401 and resp.json()["detail"] == "invalid_token"

    def test_apple_unverified_401(self, client):
        assert _apple(client, _apple_token(email_verified="false")).status_code == 401


class TestFakeTokens:
    def test_fake_token_accepted_in_fake_mode(self, client, fake_mode, jwks, user_service, settings):
        fake_mode["on"] = True
        settings.google_oauth_client_ids = ""  # fake tokens work without real config
        for route, email in (("google", "g@example.com"), ("apple", "a@example.com")):
            resp = client.post(f"/api/users/auth/{route}", json={"id_token": f"fake:{email.upper()}"}, headers=H)
            assert resp.status_code == 200, resp.text
            assert resp.json()["user"]["email"] == email
        assert jwks == []  # no JWKS (network) lookups

    def test_fake_token_rejected_outside_fake_mode(self, client, user_service):
        for route in ("google", "apple"):
            resp = client.post(f"/api/users/auth/{route}", json={"id_token": "fake:x@example.com"}, headers=H)
            assert resp.status_code == 401 and resp.json()["detail"] == "invalid_token"
        assert user_service.get_user("x@example.com") is None

    def test_fake_token_needs_an_email(self, client, fake_mode):
        fake_mode["on"] = True
        assert _google(client, "fake:not-an-email").status_code == 401

    def test_routes_registered_in_main_app(self):
        from backend.main import app

        paths = {getattr(r, "path", "") for r in app.routes}
        assert {"/api/users/auth/google", "/api/users/auth/apple"} <= paths
