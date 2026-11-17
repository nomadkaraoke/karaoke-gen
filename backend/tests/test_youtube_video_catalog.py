"""Tests for the Firestore YouTube video catalogue (soft-delete + change history)."""
import itertools
from datetime import datetime, timedelta, timezone

from backend.services import youtube_video_catalog as catalog


# ----------------------------------------------------------------------------
# Minimal in-memory Firestore
# ----------------------------------------------------------------------------
class _Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class MemDoc:
    def __init__(self, store, path):
        self.store, self.path = store, path
        self.id = path.split("/")[-1]

    def collection(self, name):
        return MemColl(self.store, f"{self.path}/{name}")

    def get(self):
        return _Snap(self.id, self.store.docs.get(self.path))


class MemColl:
    _auto = itertools.count()

    def __init__(self, store, path):
        self.store, self.path = store, path

    def document(self, doc_id=None):
        doc_id = doc_id or f"auto{next(self._auto):06d}"
        return MemDoc(self.store, f"{self.path}/{doc_id}")

    def docs(self):
        prefix = self.path + "/"
        return {
            p[len(prefix):]: d
            for p, d in self.store.docs.items()
            if p.startswith(prefix) and "/" not in p[len(prefix):]
        }


class MemBatch:
    def __init__(self, store):
        self.store, self.ops = store, []

    def set(self, ref, data, merge=False):
        self.ops.append((ref.path, dict(data), merge))

    def commit(self):
        self.store.commits += 1
        for path, data, merge in self.ops:
            if merge and path in self.store.docs:
                self.store.docs[path] = {**self.store.docs[path], **data}
            else:
                self.store.docs[path] = data
        self.ops = []


class MemDB:
    def __init__(self):
        self.docs = {}
        self.commits = 0

    def collection(self, name):
        return MemColl(self, name)

    def batch(self):
        return MemBatch(self)

    def get_all(self, refs):
        return [r.get() for r in refs]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)


def _video(vid, title=None, desc="desc", privacy="public", tags=("karaoke",)):
    return {
        "id": vid,
        "snippet": {
            "title": title or f"Artist - {vid} (Karaoke)",
            "description": desc,
            "tags": list(tags),
            "publishedAt": "2023-06-12T02:49:47Z",
            "categoryId": "10",
        },
        "status": {"privacyStatus": privacy, "uploadStatus": "processed", "license": "youtube",
                   "embeddable": True, "madeForKids": False},
        "contentDetails": {"duration": "PT3M30S"},
    }


def _entry(vid):
    return {"video_id": vid, "artist": "Artist", "song_title": vid, "brand_code": "NOMAD-0001",
            "kind": "nomad_recent", "target": True}


def _sync(db, videos, now, listed=None, gone=(), pre=None):
    return catalog.sync_catalog(
        db, videos, [_entry(v) for v in videos],
        listed_ids=list(videos) if listed is None else listed,
        gone_ids=list(gone), now=now, backfill_pre_update=pre,
    )


def _doc(db, vid):
    return db.docs[f"{catalog.COLLECTION}/{vid}"]


def _history(db, vid):
    return list(db.collection(catalog.COLLECTION).document(vid).collection("history").docs().values())


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------
def test_first_sync_creates_docs_with_metadata_and_first_seen_history():
    db = MemDB()
    stats = _sync(db, {"a": _video("a"), "b": _video("b")}, T0, listed=["a"])
    assert stats["new"] == 2
    a = _doc(db, "a")
    assert a["title"] == "Artist - a (Karaoke)"
    assert a["privacy_status"] == "public"
    assert a["duration"] == "PT3M30S"
    assert a["tags"] == ["karaoke"]
    assert a["brand_code"] == "NOMAD-0001" and a["song_title"] == "a"
    assert a["first_seen_at"] == T0 and a["last_seen_at"] == T0
    assert a["deleted"] is False
    assert a["in_uploads_playlist"] is True
    assert _doc(db, "b")["in_uploads_playlist"] is False
    assert [h["event"] for h in _history(db, "a")] == ["first_seen"]


def test_unchanged_video_same_day_is_not_rewritten():
    db = MemDB()
    _sync(db, {"a": _video("a")}, T0)
    before = dict(_doc(db, "a"))
    stats = _sync(db, {"a": _video("a")}, T0 + timedelta(hours=2))
    assert stats["touched"] == 0 and stats["changed"] == 0
    assert _doc(db, "a") == before
    assert len(_history(db, "a")) == 1


def test_unchanged_video_next_day_refreshes_last_seen_only():
    db = MemDB()
    _sync(db, {"a": _video("a")}, T0)
    t1 = T0 + timedelta(days=1)
    stats = _sync(db, {"a": _video("a")}, t1)
    assert stats["touched"] == 1 and stats["changed"] == 0
    assert _doc(db, "a")["last_seen_at"] == t1
    assert _doc(db, "a")["last_changed_at"] == T0
    assert len(_history(db, "a")) == 1


def test_title_and_privacy_change_recorded_as_observed():
    db = MemDB()
    _sync(db, {"a": _video("a", title="Old (Karaoke)", privacy="private")}, T0)
    t1 = T0 + timedelta(days=1)
    stats = _sync(db, {"a": _video("a", title="New (Karaoke)")}, t1)
    assert stats["changed"] == 1
    hist = [h for h in _history(db, "a") if h["event"] == "changed"]
    assert len(hist) == 1
    assert hist[0]["source"] == "observed"
    assert hist[0]["changes"]["title"] == {"old": "Old (Karaoke)", "new": "New (Karaoke)"}
    assert hist[0]["changes"]["privacy_status"] == {"old": "private", "new": "public"}
    assert "description" not in hist[0]["changes"]
    assert _doc(db, "a")["title"] == "New (Karaoke)"
    assert _doc(db, "a")["last_changed_at"] == t1


def test_backfill_rewrite_attributed_to_backfill():
    db = MemDB()
    _sync(db, {"a": _video("a", desc="old template")}, T0)
    _sync(db, {"a": _video("a", desc="new template")}, T0 + timedelta(days=1),
          pre={"a": _video("a", desc="old template")})
    hist = [h for h in _history(db, "a") if h["event"] == "changed"]
    assert len(hist) == 1
    assert hist[0]["source"] == "backfill"
    assert hist[0]["changes"]["description"]["old"] == "old template"


def test_studio_edit_and_backfill_rewrite_in_one_run_are_logged_separately():
    db = MemDB()
    _sync(db, {"a": _video("a", title="Old (Karaoke)", desc="old template")}, T0)
    # Someone renamed it in Studio; then this run rewrote the description.
    before = _video("a", title="New (Karaoke)", desc="old template")
    after = _video("a", title="New (Karaoke)", desc="new template")
    _sync(db, {"a": after}, T0 + timedelta(days=1), pre={"a": before})
    hist = {h["source"]: h for h in _history(db, "a") if h["event"] == "changed"}
    assert set(hist["observed"]["changes"]) == {"title"}
    assert set(hist["backfill"]["changes"]) == {"description"}


def test_gone_video_is_soft_deleted_once_and_keeps_metadata():
    db = MemDB()
    _sync(db, {"a": _video("a"), "b": _video("b")}, T0)
    t1 = T0 + timedelta(days=1)
    stats = _sync(db, {"a": _video("a")}, t1, gone=["b"])
    assert stats["deleted"] == 1
    b = _doc(db, "b")
    assert b["deleted"] is True and b["deleted_at"] == t1
    assert b["title"] == "Artist - b (Karaoke)"  # record retained
    # Still gone the next day: no second delete event.
    stats = _sync(db, {"a": _video("a")}, t1 + timedelta(days=1), gone=["b"])
    assert stats["deleted"] == 0
    assert [h["event"] for h in _history(db, "b")].count("deleted") == 1


def test_gone_video_never_catalogued_gets_a_stub():
    db = MemDB()
    _sync(db, {}, T0, gone=["ghost"])
    ghost = _doc(db, "ghost")
    assert ghost["deleted"] is True and ghost["video_id"] == "ghost"
    assert [h["event"] for h in _history(db, "ghost")] == ["deleted"]


def test_deleted_video_that_reappears_is_restored():
    db = MemDB()
    _sync(db, {"a": _video("a")}, T0)
    _sync(db, {}, T0 + timedelta(days=1), gone=["a"])
    t2 = T0 + timedelta(days=2)
    stats = _sync(db, {"a": _video("a", privacy="unlisted")}, t2)
    assert stats["restored"] == 1
    a = _doc(db, "a")
    assert a["deleted"] is False and a["deleted_at"] is None
    restored = [h for h in _history(db, "a") if h["event"] == "restored"]
    assert restored and restored[0]["changes"]["privacy_status"]["new"] == "unlisted"


def test_playlist_membership_change_updates_doc_without_history():
    db = MemDB()
    _sync(db, {"a": _video("a")}, T0, listed=["a"])
    stats = _sync(db, {"a": _video("a")}, T0 + timedelta(hours=1), listed=[])
    assert stats["touched"] == 1 and stats["changed"] == 0
    assert _doc(db, "a")["in_uploads_playlist"] is False
    assert len(_history(db, "a")) == 1


def test_large_sync_commits_in_multiple_batches_without_splitting_pairs():
    db = MemDB()
    batches = []
    orig_commit = MemBatch.commit

    def _spy(self):
        batches.append([p for p, _, _ in self.ops])
        orig_commit(self)

    MemBatch.commit = _spy
    try:
        videos = {f"v{i}": _video(f"v{i}") for i in range(450)}
        stats = _sync(db, videos, T0)
    finally:
        MemBatch.commit = orig_commit
    assert stats["new"] == 450
    assert len(batches) == 3 and all(len(b) <= catalog._BATCH_LIMIT for b in batches)
    # every video's doc and its first_seen history land in the same batch
    for b in batches:
        docs = {p.split("/")[1] for p in b if p.count("/") == 1}
        hist = {p.split("/")[1] for p in b if "/history/" in p}
        assert docs == hist
    assert len(db.collection(catalog.COLLECTION).docs()) == 450


def test_new_video_rewritten_in_same_run_keeps_original_in_history():
    db = MemDB()
    _sync(db, {"a": _video("a", desc="new template")}, T0, pre={"a": _video("a", desc="upload description")})
    events = sorted((h["event"], h["source"]) for h in _history(db, "a"))
    assert events == [("changed", "backfill"), ("first_seen", "observed")]
    changed = [h for h in _history(db, "a") if h["event"] == "changed"][0]
    assert changed["changes"]["description"] == {"old": "upload description", "new": "new template"}
    assert _doc(db, "a")["description"] == "new template"
