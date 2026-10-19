"""POST /api/kjbox/catalog/resolve: kjbox (X-Kjbox-Secret) and karaoke-decide (X-Partner-Secret)
share the verdict cache but have separate rate budgets."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import kjbox

KJ = {"X-Kjbox-Secret": "kj-secret"}
DECIDE = {"X-Partner-Secret": "decide-secret"}


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setattr(kjbox, "_resolve_cache", {})
    monkeypatch.setattr(kjbox, "_resolve_calls", [])
    monkeypatch.setattr(kjbox, "_resolve_calls_decide", [])
    seen = []

    async def fake_resolve(query):
        seen.append(query)
        return {"kind": "content", "confident": True, "canonical_artist": "A", "canonical_title": "T",
                "typed_artist": "", "typed_title": query, "alternatives": [], "engine": "ai", "reason": ""}

    from backend.services.match_judge import free_text
    monkeypatch.setattr(free_text, "resolve_and_tidy", fake_resolve)
    return seen


def _client(monkeypatch, kjbox_secret="kj-secret", decide_secret="decide-secret"):
    settings = SimpleNamespace(kjbox_partner_secret=kjbox_secret, decide_partner_secret=decide_secret,
                               fake_workers=False)
    monkeypatch.setattr(kjbox, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(kjbox.router, prefix="/api")
    return TestClient(app)


def _post(client, q, headers):
    return client.post("/api/kjbox/catalog/resolve", json={"query": q}, headers=headers)


def test_both_partners_accepted(monkeypatch, calls):
    c = _client(monkeypatch)
    assert _post(c, "q1", KJ).status_code == 200
    assert _post(c, "q2", DECIDE).status_code == 200


def test_wrong_or_missing_secret_is_403(monkeypatch, calls):
    c = _client(monkeypatch)
    assert _post(c, "q", {"X-Partner-Secret": "nope"}).status_code == 403
    assert _post(c, "q", {"X-Kjbox-Secret": "nope"}).status_code == 403
    assert _post(c, "q", {}).status_code == 403
    # each secret only works in its own header
    assert _post(c, "q", {"X-Kjbox-Secret": "decide-secret"}).status_code == 403
    assert _post(c, "q", {"X-Partner-Secret": "kj-secret"}).status_code == 403


def test_neither_configured_is_503(monkeypatch, calls):
    c = _client(monkeypatch, kjbox_secret="", decide_secret="")
    assert _post(c, "q", KJ).status_code == 503


def test_only_kjbox_configured_rejects_decide(monkeypatch, calls):
    c = _client(monkeypatch, decide_secret="")
    assert _post(c, "q", KJ).status_code == 200
    assert _post(c, "q2", {"X-Partner-Secret": ""}).status_code == 403
    assert _post(c, "q2", DECIDE).status_code == 403


def test_only_decide_configured(monkeypatch, calls):
    c = _client(monkeypatch, kjbox_secret="")
    assert _post(c, "q", DECIDE).status_code == 200
    assert _post(c, "q2", KJ).status_code == 403


def test_decide_bucket_exhausts_independently(monkeypatch, calls):
    c = _client(monkeypatch)
    for i in range(60):
        assert _post(c, f"d{i}", DECIDE).status_code == 200
    assert _post(c, "d60", DECIDE).status_code == 429
    assert _post(c, "k0", KJ).status_code == 200            # kjbox unaffected
    assert _post(c, "d0", DECIDE).status_code == 200        # cache hit is free


def test_kjbox_bucket_exhaustion_does_not_starve_decide(monkeypatch, calls):
    monkeypatch.setattr(kjbox, "_RESOLVE_PER_MINUTE", 2)
    c = _client(monkeypatch)
    assert _post(c, "k1", KJ).status_code == 200
    assert _post(c, "k2", KJ).status_code == 200
    assert _post(c, "k3", KJ).status_code == 429
    assert _post(c, "d1", DECIDE).status_code == 200


def test_cache_shared_between_partners(monkeypatch, calls):
    c = _client(monkeypatch)
    _post(c, "Same Query", KJ)
    r = _post(c, "same query", DECIDE)
    assert r.status_code == 200 and r.json()["canonical_title"] == "T"
    assert calls == ["Same Query"]
