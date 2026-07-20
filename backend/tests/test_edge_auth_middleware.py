"""
Unit tests for EdgeAuthMiddleware (Cloudflare origin lock).

Exercises the off/warn/enforce modes, header validation, exemptions, and the
fail-open-on-misconfiguration behaviour end-to-end via a minimal ASGI app.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.middleware.edge_auth import EdgeAuthMiddleware, EDGE_AUTH_HEADER

SECRET = "s3cr3t-origin-value"


@pytest.fixture
def client():
    """Minimal app with the middleware installed and a few routes."""
    app = FastAPI()
    app.add_middleware(EdgeAuthMiddleware)

    @app.get("/")
    async def root():
        return {"ok": "root"}

    @app.get("/api/health")
    async def health():
        return {"ok": "health"}

    @app.get("/api/test")
    async def protected():
        return {"ok": "protected"}

    @app.post("/api/internal/recover-stuck-jobs")
    async def internal():
        return {"ok": "internal"}

    return TestClient(app)


def _set(monkeypatch, mode=None, secret=None):
    for key, val in (("EDGE_AUTH_MODE", mode), ("EDGE_ORIGIN_SECRET", secret)):
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)


# --------------------------------------------------------------------------- #
# off mode (default) — pure passthrough
# --------------------------------------------------------------------------- #
def test_off_mode_is_default_passthrough(client, monkeypatch):
    _set(monkeypatch, mode=None, secret=SECRET)  # unset mode -> defaults to off
    assert client.get("/api/test").status_code == 200


def test_invalid_mode_treated_as_off(client, monkeypatch):
    _set(monkeypatch, mode="bogus", secret=SECRET)
    assert client.get("/api/test").status_code == 200


# --------------------------------------------------------------------------- #
# enforce mode
# --------------------------------------------------------------------------- #
def test_enforce_blocks_missing_header(client, monkeypatch):
    _set(monkeypatch, mode="enforce", secret=SECRET)
    resp = client.get("/api/test")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden"


def test_enforce_blocks_wrong_header(client, monkeypatch):
    _set(monkeypatch, mode="enforce", secret=SECRET)
    resp = client.get("/api/test", headers={EDGE_AUTH_HEADER: "nope"})
    assert resp.status_code == 403


def test_enforce_allows_valid_header(client, monkeypatch):
    _set(monkeypatch, mode="enforce", secret=SECRET)
    resp = client.get("/api/test", headers={EDGE_AUTH_HEADER: SECRET})
    assert resp.status_code == 200
    assert resp.json()["ok"] == "protected"


def test_enforce_internal_path_requires_header(client, monkeypatch):
    """Internal endpoints traverse Cloudflare (public host) so they DO get the
    header — they are not exempt from the origin lock."""
    _set(monkeypatch, mode="enforce", secret=SECRET)
    assert client.post("/api/internal/recover-stuck-jobs").status_code == 403
    ok = client.post(
        "/api/internal/recover-stuck-jobs", headers={EDGE_AUTH_HEADER: SECRET}
    )
    assert ok.status_code == 200


# --------------------------------------------------------------------------- #
# exemptions — health/root probes hit the origin directly, no header
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/", "/api/health"])
def test_enforce_exempts_health_and_root(client, monkeypatch, path):
    _set(monkeypatch, mode="enforce", secret=SECRET)
    assert client.get(path).status_code == 200


# --------------------------------------------------------------------------- #
# warn mode — logs but allows
# --------------------------------------------------------------------------- #
def test_warn_allows_missing_header(client, monkeypatch):
    _set(monkeypatch, mode="warn", secret=SECRET)
    assert client.get("/api/test").status_code == 200


def test_warn_allows_valid_header(client, monkeypatch):
    _set(monkeypatch, mode="warn", secret=SECRET)
    assert client.get("/api/test", headers={EDGE_AUTH_HEADER: SECRET}).status_code == 200


# --------------------------------------------------------------------------- #
# misconfiguration — enforce but no secret set
# --------------------------------------------------------------------------- #
def test_enforce_without_secret_fails_closed(client, monkeypatch):
    # A security control must fail closed: enforce with no secret → 503, not open.
    _set(monkeypatch, mode="enforce", secret=None)
    resp = client.get("/api/test")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Origin authentication unavailable"


def test_enforce_without_secret_still_exempts_health(client, monkeypatch):
    # Health/root stay reachable even in the misconfigured state (probes).
    _set(monkeypatch, mode="enforce", secret=None)
    assert client.get("/api/health").status_code == 200


def test_warn_without_secret_allows(client, monkeypatch):
    # warn mode is observational only — never blocks, even with no secret.
    _set(monkeypatch, mode="warn", secret=None)
    assert client.get("/api/test").status_code == 200
