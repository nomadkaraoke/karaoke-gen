"""Tests for the scheduled YouTube description backfill worker."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services.youtube_description import render_youtube_description
from backend.workers import youtube_description_backfill_worker as worker


# ----------------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------------
class FakeDocRef:
    def __init__(self, data=None):
        self.data = data
        self.set_calls = []

    def get(self):
        m = MagicMock()
        m.exists = self.data is not None
        m.to_dict.return_value = dict(self.data) if self.data else {}
        return m

    def set(self, d):
        self.data = dict(d)
        self.set_calls.append(dict(d))


def _fake_db(ref):
    db = MagicMock()
    db.collection.return_value.document.return_value = ref
    return db


def _fake_settings(**overrides):
    base = dict(
        youtube_backfill_enabled=True,
        default_youtube_description="TEMPLATE-V1 {title} {artist} {brand_code}",
        youtube_backfill_quota_reserve=3000,
        youtube_backfill_daily_max_updates=150,
        youtube_backfill_enrich_tags=True,
        youtube_backfill_report_email="andrew@nomadkaraoke.com",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _entry(vid, will_change=True, target=True):
    return {
        "video_id": vid,
        "yt_title": f"Artist - {vid} (Karaoke)",
        "artist": "Artist",
        "song_title": vid,
        "brand_code": "NOMAD-1603",
        "parse_confidence": "high",
        "kind": "terse_new",
        "is_karaoke": True,
        "eligible": True,
        "category_id": "10",
        "current_description": "old description",
        "target": target,
        "will_change": will_change,
        "in_skip_list": False,
        "forced_include": False,
    }


def _videos(vids):
    return {v: {"id": v, "snippet": {"title": f"Artist - {v} (Karaoke)", "categoryId": "10",
                                     "description": "old description", "tags": ["old"]}} for v in vids}


class _Ctx:
    """Bundle of patches; returns handles for assertions."""

    def __init__(self, entries, videos, settings=None, quota_remaining=100000):
        self.entries = entries
        self.videos = videos
        self.settings = settings or _fake_settings()
        self.ref = FakeDocRef()
        self.youtube = MagicMock()
        self.youtube.videos.return_value.update.return_value.execute.return_value = {}
        self.quota = MagicMock()
        self.quota.get_quota_stats.return_value = {"units_remaining": quota_remaining}
        self.email = MagicMock()

    def __enter__(self):
        self._patches = [
            patch.object(worker, "get_settings", return_value=self.settings),
            patch.object(worker.bf, "load_credentials_from_secret", return_value={"refresh_token": "x"}),
            patch.object(worker.bf, "build_youtube", return_value=self.youtube),
            patch.object(worker.bf, "fetch_all_channel_entries", return_value=(self.entries, self.videos)),
            patch.object(worker, "get_firestore_client", return_value=_fake_db(self.ref)),
            patch.object(worker, "get_youtube_quota_service", return_value=self.quota),
            patch.object(worker, "_email_service", return_value=self.email),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self._patches:
            p.stop()


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------
def test_disabled_short_circuits():
    with _Ctx([], {}, settings=_fake_settings(youtube_backfill_enabled=False)) as ctx:
        result = worker.run_backfill_sync()
    assert result["status"] == "disabled"
    ctx.youtube.videos.assert_not_called()


def test_full_drain_updates_all_and_sends_completion():
    vids = ["a", "b", "c"]
    entries = [_entry(v) for v in vids]
    with _Ctx(entries, _videos(vids)) as ctx:
        result = worker.run_backfill_sync()
    assert result["updated"] == 3
    assert result["pending_after"] == 0
    assert result["completed"] is True
    # 3 videos.update calls
    assert ctx.youtube.videos.return_value.update.call_count == 3
    # completion email sent, subject mentions complete
    subj = ctx.email.send_email.call_args.kwargs["subject"]
    assert "complete" in subj.lower()
    # state persisted with completed_notified
    assert ctx.ref.data["completed_notified"] is True
    assert ctx.ref.data["completed"] is True


def test_budget_cap_partial_and_progress_email():
    vids = ["a", "b", "c"]
    entries = [_entry(v) for v in vids]
    with _Ctx(entries, _videos(vids)) as ctx:
        result = worker.run_backfill_sync(max_updates=2)
    assert result["updated"] == 2
    assert result["pending_after"] == 1
    assert result["completed"] is False
    subj = ctx.email.send_email.call_args.kwargs["subject"]
    assert "remaining" in subj.lower()
    assert ctx.ref.data.get("completed_notified") in (None, False)


def test_quota_reserve_blocks_updates():
    vids = ["a", "b"]
    entries = [_entry(v) for v in vids]
    # remaining barely above reserve -> budget 0
    with _Ctx(entries, _videos(vids), quota_remaining=3010) as ctx:
        result = worker.run_backfill_sync()
    assert result["budget"] == 0
    assert result["updated"] == 0
    ctx.youtube.videos.return_value.update.assert_not_called()


def test_dry_run_makes_no_changes():
    vids = ["a", "b"]
    entries = [_entry(v) for v in vids]
    with _Ctx(entries, _videos(vids)) as ctx:
        result = worker.run_backfill_sync(dry_run=True)
    assert result["dry_run"] is True
    ctx.youtube.videos.return_value.update.assert_not_called()
    ctx.email.send_email.assert_not_called()
    assert ctx.ref.set_calls == []  # no state persisted on dry run


def test_nothing_pending_already_notified_no_email():
    # Channel already fully drained and previously notified.
    entries = [_entry("a", will_change=False)]
    ctx = _Ctx(entries, _videos(["a"]))
    ctx.ref = FakeDocRef({
        "template_fingerprint": worker._template_fingerprint(ctx.settings),
        "completed": True,
        "completed_notified": True,
        "cycle_index": 1,
    })
    with ctx:
        result = worker.run_backfill_sync()
    assert result["updated"] == 0
    assert result["completed"] is True
    ctx.email.send_email.assert_not_called()


def test_template_change_starts_new_cycle():
    entries = [_entry("a", will_change=False)]
    ctx = _Ctx(entries, _videos(["a"]))
    # Prior state has a DIFFERENT fingerprint -> new cycle should bump cycle_index.
    ctx.ref = FakeDocRef({
        "template_fingerprint": "STALEFINGERPRINT",
        "completed": True,
        "completed_notified": True,
        "cycle_index": 2,
    })
    with ctx:
        worker.run_backfill_sync()
    assert ctx.ref.data["cycle_index"] == 3
    assert ctx.ref.data["template_fingerprint"] == worker._template_fingerprint(ctx.settings)


def test_quota_exceeded_stops_run():
    vids = ["a", "b", "c"]
    entries = [_entry(v) for v in vids]
    ctx = _Ctx(entries, _videos(vids))
    # First update ok, second raises quotaExceeded.
    calls = {"n": 0}

    def _exec():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("quotaExceeded: The request cannot be completed")
        return {}

    with ctx:
        ctx.youtube.videos.return_value.update.return_value.execute.side_effect = _exec
        result = worker.run_backfill_sync()
    # 1 success, stopped on the 2nd (quota) before doing the 3rd
    assert result["updated"] == 1
    assert result["completed"] is False
