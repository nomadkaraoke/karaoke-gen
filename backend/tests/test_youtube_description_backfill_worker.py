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


class FakeDB:
    """Routes collection/document paths: state doc, known-IDs doc, quota ledger."""

    def __init__(self, state_ref, known_ref=None, ledger_docs=None):
        self.refs = {
            (worker.STATE_COLLECTION, worker.STATE_DOC): state_ref,
            (worker.STATE_COLLECTION, worker.KNOWN_IDS_DOC): known_ref or FakeDocRef(),
        }
        self.ledger_docs = ledger_docs or []

    def collection(self, name):
        coll = MagicMock()
        coll.document.side_effect = lambda doc: self.refs.setdefault((name, doc), FakeDocRef())
        if name == worker.QUOTA_LEDGER_COLLECTION:
            if self.ledger_docs == "fail":
                coll.stream.side_effect = RuntimeError("firestore unavailable")
                return coll
            docs = []
            for data in self.ledger_docs:
                d = MagicMock()
                d.to_dict.return_value = data
                docs.append(d)
            coll.stream.return_value = docs
        return coll


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

    def __init__(self, entries, videos, settings=None, quota_remaining=100000,
                 recovered_ids=(), gone_ids=(), listed_ids=None):
        self.entries = entries
        self.videos = videos
        self.channel = worker.bf.ChannelEnumeration(
            entries=entries,
            videos=videos,
            listed_ids=list(listed_ids if listed_ids is not None else videos),
            recovered_ids=list(recovered_ids),
            gone_ids=list(gone_ids),
        )
        self.known_ref = FakeDocRef()
        self.ledger_docs = []
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
            patch.object(worker.bf, "enumerate_channel", return_value=self.channel),
            patch.object(worker, "get_firestore_client",
                         return_value=FakeDB(self.ref, self.known_ref, self.ledger_docs)),
            patch.object(worker, "get_youtube_quota_service", return_value=self.quota),
            patch.object(worker, "_email_service", return_value=self.email),
        ]
        for p in self._patches:
            p.start()
        self.enumerate = worker.bf.enumerate_channel
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


# ----------------------------------------------------------------------------
# Known video IDs (the uploads playlist drops videos)
# ----------------------------------------------------------------------------
def test_first_run_seeds_known_ids_from_quota_ledger():
    ctx = _Ctx([_entry("a", will_change=False)], _videos(["a"]))
    ctx.ledger_docs = [
        {"pending_uploads": [{"job_id": "desc-backfill:old1", "units": 50},
                             {"job_id": "some-upload-job", "units": 1600}]},
        {"pending_uploads": [{"job_id": "desc-backfill:old2"}, {"job_id": "desc-backfill:old1"}]},
        {},
    ]
    with ctx:
        worker.run_backfill_sync()
        known_passed = ctx.enumerate.call_args.args[1]
    assert known_passed == ["old1", "old2"]


def test_existing_known_ids_are_passed_and_not_reseeded():
    ctx = _Ctx([_entry("a", will_change=False)], _videos(["a"]))
    ctx.known_ref = FakeDocRef({"ids": ["k1", "k2"]})
    ctx.ledger_docs = [{"pending_uploads": [{"job_id": "desc-backfill:ledger1"}]}]
    with ctx:
        worker.run_backfill_sync()
        known_passed = ctx.enumerate.call_args.args[1]
    assert known_passed == ["k1", "k2"]


def test_known_ids_saved_from_returned_videos():
    vids = ["a", "b"]
    ctx = _Ctx([_entry(v, will_change=False) for v in vids], _videos(vids), gone_ids=["deleted1"])
    ctx.known_ref = FakeDocRef({"ids": ["a", "deleted1"]})
    with ctx:
        worker.run_backfill_sync()
    assert ctx.known_ref.data["ids"] == ["a", "b"]


def test_dry_run_does_not_save_known_ids():
    ctx = _Ctx([_entry("a")], _videos(["a"]))
    with ctx:
        worker.run_backfill_sync(dry_run=True)
    assert ctx.known_ref.set_calls == []


def test_recovered_videos_are_updated_and_reported():
    """A video missing from the uploads playlist but known still gets rewritten."""
    vids = ["listed", "hidden"]
    ctx = _Ctx([_entry(v) for v in vids], _videos(vids),
               listed_ids=["listed"], recovered_ids=["hidden"])
    with ctx:
        result = worker.run_backfill_sync()
    assert result["updated"] == 2
    assert result["recovered_from_known"] == 1
    assert result["listed_in_playlist"] == 1
    updated_ids = [c.kwargs["body"]["id"] for c in ctx.youtube.videos.return_value.update.call_args_list]
    assert "hidden" in updated_ids


def test_progress_email_reports_recovered_count():
    vids = ["a", "b", "c"]
    ctx = _Ctx([_entry(v) for v in vids], _videos(vids), recovered_ids=["c"])
    with ctx:
        worker.run_backfill_sync(max_updates=2)
    html = ctx.email.send_email.call_args.kwargs["html_content"]
    assert "Missing from YouTube's uploads list (checked directly):</b> 1</li>" in html


def test_failed_seed_is_retried_next_run():
    """A ledger read failure must not persist a playlist-only known-IDs doc."""
    ctx = _Ctx([_entry("a", will_change=False)], _videos(["a"]))
    ctx.ledger_docs = "fail"
    with ctx:
        worker.run_backfill_sync()
        assert ctx.enumerate.call_args.args[1] == []
    assert ctx.known_ref.set_calls == []

    ctx2 = _Ctx([_entry("a", will_change=False)], _videos(["a"]))
    ctx2.known_ref = ctx.known_ref
    ctx2.ledger_docs = [{"pending_uploads": [{"job_id": "desc-backfill:hidden"}]}]
    with ctx2:
        worker.run_backfill_sync()
        assert ctx2.enumerate.call_args.args[1] == ["hidden"]
    assert ctx2.known_ref.set_calls
