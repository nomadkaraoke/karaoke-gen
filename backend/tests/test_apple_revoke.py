"""Tests for Sign in with Apple revocation (httpx mocked; throwaway P-256 key)."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from backend.services import apple_revoke_service as mod
from backend.services.apple_revoke_service import AppleRevoker


@pytest.fixture(scope="module")
def keypair():
    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return priv, pub


def _settings(priv="PEM", kid="KEY123"):
    return SimpleNamespace(
        apple_siwa_key_p8=priv, apple_siwa_key_id=kid,
        apple_team_id="55B76HN73D", apple_client_id="com.nomadkaraoke.mobile",
    )


def _patch_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


def test_builds_client_secret_claims(keypair):
    priv, pub = keypair
    secret = AppleRevoker(_settings(priv))._client_secret()
    assert jwt.get_unverified_header(secret) == {"alg": "ES256", "kid": "KEY123", "typ": "JWT"}
    claims = jwt.decode(secret, pub, algorithms=["ES256"], audience="https://appleid.apple.com")
    assert claims["iss"] == "55B76HN73D"
    assert claims["sub"] == "com.nomadkaraoke.mobile"
    assert 0 < claims["exp"] - claims["iat"] <= 300


@pytest.mark.asyncio
async def test_token_then_revoke_calls(keypair, monkeypatch):
    calls = []

    def handler(req: httpx.Request):
        form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
        calls.append((str(req.url), form))
        if req.url.path == "/auth/token":
            return httpx.Response(200, json={"access_token": "acc", "refresh_token": "ref"})
        return httpx.Response(200)

    _patch_http(monkeypatch, handler)
    assert await AppleRevoker(_settings(keypair[0])).revoke("code-1") is True
    (u1, f1), (u2, f2) = calls
    assert u1 == "https://appleid.apple.com/auth/token"
    assert f1["grant_type"] == "authorization_code" and f1["code"] == "code-1"
    assert f1["client_id"] == "com.nomadkaraoke.mobile"
    assert u2 == "https://appleid.apple.com/auth/revoke"
    assert f2["token"] == "ref" and f2["token_type_hint"] == "refresh_token"


@pytest.mark.asyncio
async def test_revoke_falls_back_to_access_token(keypair, monkeypatch):
    seen = {}

    def handler(req):
        if req.url.path == "/auth/token":
            return httpx.Response(200, json={"access_token": "acc"})
        seen["body"] = req.content.decode()
        return httpx.Response(200)

    _patch_http(monkeypatch, handler)
    assert await AppleRevoker(_settings(keypair[0])).revoke("c") is True
    assert "token=acc" in seen["body"] and "access_token" in seen["body"]


@pytest.mark.asyncio
async def test_no_key_returns_false(caplog):
    with caplog.at_level(logging.WARNING):
        assert await AppleRevoker(_settings(priv=None, kid=None)).revoke("c") is False
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_apple_error_returns_false_and_warns(keypair, monkeypatch, caplog):
    _patch_http(monkeypatch, lambda req: httpx.Response(400, json={"error": "invalid_grant"}))
    with caplog.at_level(logging.WARNING):
        assert await AppleRevoker(_settings(keypair[0])).revoke("stale") is False
    assert any("token exchange failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_revoke_error_returns_false(keypair, monkeypatch):
    def handler(req):
        if req.url.path == "/auth/token":
            return httpx.Response(200, json={"refresh_token": "r"})
        return httpx.Response(500)

    _patch_http(monkeypatch, handler)
    assert await AppleRevoker(_settings(keypair[0])).revoke("c") is False


@pytest.mark.asyncio
async def test_network_error_never_raises(keypair, monkeypatch):
    def handler(req):
        raise httpx.ConnectError("boom")

    _patch_http(monkeypatch, handler)
    assert await AppleRevoker(_settings(keypair[0])).revoke("c") is False


@pytest.mark.asyncio
async def test_bad_pem_never_raises():
    assert await AppleRevoker(_settings(priv="not a pem")).revoke("c") is False
