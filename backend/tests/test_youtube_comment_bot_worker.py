"""Unit tests for the YouTube comment bot worker (Firestore, YouTube and Gemini faked)."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.api_core import exceptions as gcloud_exceptions

from backend.services import youtube_comment_bot as bot
from backend.workers import youtube_comment_bot_worker as w

SCOPES = ["https://www.googleapis.com/auth/youtube", w.FORCE_SSL_SCOPE]
REQUEST = {"is_song_request": True, "songs": [{"artist": "The Lanterns", "title": "Paper Moons"}],
           "language": "en", "confidence": 0.95}
NOT_REQUEST = {"is_song_request": False, "songs": [], "language": "en", "confidence": 0.95}


class FakeDoc:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get(self):
        data = self.store.get(self.key)
        return SimpleNamespace(exists=data is not None, to_dict=lambda: dict(data or {}))

    def set(self, data, merge=False):
        self.store[self.key] = {**(self.store.get(self.key) or {}), **data} if merge else dict(data)

    def create(self, data):
        if self.key in self.store:
            raise gcloud_exceptions.Conflict("exists")
        self.store[self.key] = dict(data)

    def update(self, data):
        self.store[self.key].update(data)


class FakeDB:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return SimpleNamespace(document=lambda doc_id: FakeDoc(self.store, (name, doc_id)))


def _item(cid, text="can you make Paper Moons by The Lanterns?", published="2026-10-21T12:00:00Z"):
    return {"id": f"t-{cid}", "snippet": {"videoId": "vid1", "totalReplyCount": 0, "topLevelComment": {
        "id": cid, "snippet": {"authorDisplayName": "Viewer", "authorChannelId": {"value": "UCviewer"},
                               "textOriginal": text, "publishedAt": published}}}, "replies": {"comments": []}}


class FakeYouTube:
    def __init__(self, items):
        self.items = items
        self.inserted = []
        self._kw = {}

    def commentThreads(self):
        return self

    def comments(self):
        return self

    def list(self, **kw):
        self._kw = kw
        return self

    def insert(self, **kw):
        self.inserted.append(kw["body"]["snippet"])
        self._kw = {"insert": True}
        return self

    def execute(self):
        if self._kw.get("insert"):
            return {"id": f"reply{len(self.inserted)}"}
        return {"items": self.items}


class FakeBoard:
    def __init__(self, *a):
        self.added = []

    def canonicalize(self, artist, title):
        return artist, title

    def find_open_or_published(self, artist, title):
        return None

    def add_from_comment(self, artist, title, thread):
        self.added.append(thread.comment_id)
        return bot.BoardMatch(f"r-{thread.comment_id}", artist, title, "open")


@pytest.fixture
def env(monkeypatch):
    settings = SimpleNamespace(
        youtube_comment_autoreply_enabled=True, youtube_comment_autoreply_max_replies=20,
        youtube_comment_autoreply_min_confidence=0.8, youtube_comment_autoreply_start="2026-10-01",
        get_secret=lambda name: json.dumps({"refresh_token": "x", "scopes": SCOPES})
        if name == w.CREDENTIALS_SECRET else None,
    )
    db = FakeDB()
    yt = FakeYouTube([_item("c1"), _item("c2", text="love this")])
    quota = MagicMock()
    quota.check_quota_available.return_value = (True, 9000, "")
    boards = []

    def make_board(service):
        b = FakeBoard()
        boards.append(b)
        return b

    responses = {"can you make Paper Moons by The Lanterns?": REQUEST, "love this": NOT_REQUEST}

    def gen(system_prompt, user_prompt, schema):
        return responses[user_prompt.split("Comment: ", 1)[1]]

    monkeypatch.setattr(w, "get_settings", lambda: settings)
    monkeypatch.setattr(w, "get_firestore_client", lambda: db)
    monkeypatch.setattr(w, "get_youtube_quota_service", lambda: quota)
    monkeypatch.setattr(w.ytb, "build_youtube", lambda creds: yt)
    monkeypatch.setattr(w, "_channel_index", lambda db, youtube: bot.ChannelIndex())
    monkeypatch.setattr(w, "ServiceBoard", make_board)
    monkeypatch.setattr(w, "DryRunBoard", make_board)
    monkeypatch.setattr(w.bot, "gemini_generate", lambda: gen)
    monkeypatch.setattr(w, "_post_noise", MagicMock())
    monkeypatch.setattr(w, "_alert", MagicMock())
    with patch("backend.services.song_request_service.SongRequestService"):
        yield SimpleNamespace(settings=settings, db=db, yt=yt, quota=quota, boards=boards)


def _actions(db):
    return {k[1]: v for k, v in db.store.items() if k[0] == w.ACTIONS_COLLECTION}


def test_disabled_does_nothing(env):
    env.settings.youtube_comment_autoreply_enabled = False
    assert w.run_comment_bot_sync() == {"status": "disabled"}
    assert env.yt.inserted == []


def test_replies_once_and_records_actions(env):
    summary = w.run_comment_bot_sync()
    assert summary["replied"] == 1 and summary["skipped"] == {"not_a_request": 1}
    assert len(env.yt.inserted) == 1 and env.yt.inserted[0]["parentId"] == "c1"
    assert "request=r-c1" in env.yt.inserted[0]["textOriginal"]
    acts = _actions(env.db)
    assert acts["c1"]["action"] == "replied" and acts["c1"]["reply_id"] == "reply1"
    assert acts["c2"]["action"] == "skip"
    env.quota.record_upload.assert_called_once_with("comment-reply:c1", units=w.REPLY_COST)
    w._post_noise.assert_called_once()


def test_second_run_never_double_replies(env):
    w.run_comment_bot_sync()
    w.run_comment_bot_sync()
    assert len(env.yt.inserted) == 1


def test_concurrent_claim_skips_post(env):
    env.db.store[(w.ACTIONS_COLLECTION, "c1")] = {"action": "replying"}
    w.run_comment_bot_sync()
    assert env.yt.inserted == []


def test_dry_run_writes_and_posts_nothing_even_when_disabled(env):
    env.settings.youtube_comment_autoreply_enabled = False
    summary = w.run_comment_bot_sync(dry_run=True)
    assert summary["actions"][0]["comment_id"] == "c1"
    assert env.yt.inserted == [] and env.db.store == {}


def test_go_live_start_fixed_on_first_run_and_old_comments_ignored(env):
    env.yt.items = [_item("old", published="2026-09-01T00:00:00Z")]
    summary = w.run_comment_bot_sync()
    assert summary["replied"] == 0 and env.yt.inserted == []
    state = env.db.store[(w.STATE_COLLECTION, w.STATE_DOC)]
    assert state["go_live_at"] == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_max_replies_cap_leaves_rest_for_next_run(env):
    env.yt.items = [_item("c1"), _item("c3", published="2026-10-21T11:00:00Z")]
    summary = w.run_comment_bot_sync(max_replies=1)
    assert summary["replied"] == 1 and "c3" not in _actions(env.db)


def test_quota_exhausted_stops_without_posting(env):
    env.quota.check_quota_available.return_value = (False, 10, "low")
    w.run_comment_bot_sync()
    assert env.yt.inserted == [] and "c1" not in _actions(env.db)


def test_missing_scope_alerts_and_stops(env):
    env.settings.get_secret = lambda name: json.dumps({"scopes": SCOPES[:1]})
    assert w.run_comment_bot_sync()["status"] == "missing_scope"
    w._alert.assert_called_once()


def test_post_failure_marks_and_alerts(env):
    def boom(**kw):
        raise RuntimeError("403 forbidden")
    env.yt.insert = boom
    summary = w.run_comment_bot_sync()
    assert summary["errors"] == 1 and _actions(env.db)["c1"]["action"] == "reply_failed"
    w._alert.assert_called_once()


def test_classifier_error_is_retried_next_run(env):
    def flaky(system_prompt, user_prompt, schema):
        raise RuntimeError("503 UNAVAILABLE")
    with patch.object(w.bot, "gemini_generate", lambda: flaky):
        summary = w.run_comment_bot_sync()
    assert summary["skipped"] == {"classifier_error": 2} and _actions(env.db) == {}
    state = env.db.store[(w.STATE_COLLECTION, w.STATE_DOC)]
    assert state["high_water"] <= datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    summary = w.run_comment_bot_sync()  # Gemini back: replies now
    assert summary["replied"] == 1


def test_configured_start_parsing():
    s = SimpleNamespace(youtube_comment_autoreply_start="2026-04-22")
    assert w._configured_start(s) == datetime(2026, 4, 22, tzinfo=timezone.utc)
    assert w._configured_start(SimpleNamespace(youtube_comment_autoreply_start="")) is None
    assert w._configured_start(SimpleNamespace(youtube_comment_autoreply_start="nope")) is None
