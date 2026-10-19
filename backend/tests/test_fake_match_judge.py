"""Fake match judge / free-text resolve under FAKE_WORKERS (local E2E stack; never calls Gemini)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import require_auth
from backend.api.routes import catalog, kjbox
from backend.services.fake_workers import config as fw_config
from backend.services.fake_workers.config import FakeConfig, set_fake_config
from backend.services.match_judge.verdict import MatchVerdict
from backend.tests.test_fake_workers import FakeDB

EMAIL = "mobile-test@example.com"


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    monkeypatch.setattr(fw_config, "_default_db", lambda: d)
    return d


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(catalog.router, prefix="/api")
    app.include_router(kjbox.router, prefix="/api")
    app.dependency_overrides[require_auth] = lambda: SimpleNamespace(user_email=EMAIL, token_id="t")
    monkeypatch.setattr(catalog, "_check_rate_limit", lambda *a, **k: None)
    return TestClient(app)


def _fake(monkeypatch, on):
    monkeypatch.setattr("backend.services.fake_workers.fake_workers_enabled", lambda settings=None: on)
    settings = SimpleNamespace(kjbox_partner_secret="s", decide_partner_secret="", fake_workers=on)
    monkeypatch.setattr(kjbox, "get_settings", lambda: settings)


def _judge(client):
    return client.post("/api/catalog/match-judge", json={"artist": "abba", "title": "danceing queen"})


def test_validate_rejects_unknown_judge_kind():
    with pytest.raises(ValueError, match="judge_kind"):
        FakeConfig(judge_kind="bogus").validate()
    for k in ("none", "cosmetic", "content", "ambiguous"):
        FakeConfig(judge_kind=k).validate()


def test_set_fake_config_rejects_unknown_judge_kind(db):
    with pytest.raises(ValueError):
        set_fake_config({"judge_kind": "maybe"}, db=db)


def test_match_judge_content_uses_per_user_config(monkeypatch, client, db):
    _fake(monkeypatch, True)
    set_fake_config({"judge_kind": "content", "judge_canonical_artist": "ABBA",
                     "judge_canonical_title": "Dancing Queen"}, db=db, email=EMAIL)
    with patch("backend.services.match_judge.service.judge_match", new_callable=AsyncMock) as real:
        r = _judge(client)
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "content"
    assert (data["canonical_artist"], data["canonical_title"]) == ("ABBA", "Dancing Queen")
    real.assert_not_called()


def test_match_judge_none_default(monkeypatch, client, db):
    _fake(monkeypatch, True)
    assert _judge(client).json()["kind"] == "none"
    set_fake_config({"judge_kind": "content"}, db=db, email="someone-else@example.com")
    assert _judge(client).json()["kind"] == "none"      # other user's override doesn't leak


def test_match_judge_ambiguous_has_alternatives(monkeypatch, client, db):
    _fake(monkeypatch, True)
    set_fake_config({"judge_kind": "ambiguous", "judge_canonical_artist": "A",
                     "judge_canonical_title": "T"}, db=db, email=EMAIL)
    data = _judge(client).json()
    assert data["kind"] == "ambiguous" and len(data["alternatives"]) == 2


def test_match_judge_real_path_when_fake_off(monkeypatch, client, db):
    _fake(monkeypatch, False)
    set_fake_config({"judge_kind": "content"}, db=db, email=EMAIL)
    verdict = MatchVerdict("cosmetic", True, "ABBA", "Dancing Queen", engine="catalog")
    with patch("backend.services.match_judge.service.judge_match", new_callable=AsyncMock,
               return_value=verdict) as real:
        r = _judge(client)
    real.assert_awaited_once()
    assert r.json()["kind"] == "cosmetic"


def test_resolve_uses_global_config_without_gemini(monkeypatch, client, db):
    _fake(monkeypatch, True)
    set_fake_config({"judge_kind": "content", "judge_canonical_artist": "ABBA",
                     "judge_canonical_title": "Dancing Queen"}, db=db)
    with patch("backend.services.match_judge.free_text.resolve_and_tidy", new_callable=AsyncMock) as real:
        r = client.post("/api/kjbox/catalog/resolve", json={"query": "abba danceing queen"},
                        headers={"X-Kjbox-Secret": "s"})
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "content" and data["confident"] is True
    assert (data["canonical_artist"], data["canonical_title"]) == ("ABBA", "Dancing Queen")
    assert data["typed_title"] == "abba danceing queen" and "needs_ai" not in data
    real.assert_not_called()


def test_resolve_none_and_not_cached_across_config_changes(monkeypatch, client, db):
    _fake(monkeypatch, True)
    h = {"X-Kjbox-Secret": "s"}
    assert client.post("/api/kjbox/catalog/resolve", json={"query": "zzz"}, headers=h).json()["kind"] == "none"
    set_fake_config({"judge_kind": "cosmetic"}, db=db)
    assert client.post("/api/kjbox/catalog/resolve", json={"query": "zzz"}, headers=h).json()["kind"] == "cosmetic"
