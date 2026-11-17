"""
Firestore catalogue of every video on the Nomad Karaoke YouTube channel.

Kept up to date by the daily description backfill worker, which already fetches
every video's snippet/status, so this costs no extra YouTube quota.

Layout:
- `youtube_videos/{video_id}`: latest metadata plus bookkeeping
  (`first_seen_at`, `last_seen_at`, `last_changed_at`, `in_uploads_playlist`,
  `deleted`, `deleted_at`).
- `youtube_videos/{video_id}/history/{auto_id}`: one entry per observed change,
  `{at, event, changes: {field: {old, new}}, source}`. `event` is `first_seen`,
  `changed`, `deleted` or `restored`. `source` is `backfill` when this job's own
  description rewrite caused the change, otherwise `observed` (someone else,
  e.g. YouTube Studio, or a new upload).

Videos are NEVER removed: when YouTube stops returning a known video it is
soft-deleted (`deleted=True`) and keeps being checked, so a reappearance is
recorded as `restored`.

Writes only happen when something changed (plus `last_seen_at`, refreshed at
most once a day per video), so a normal day costs ~1 read per video and a
handful of writes.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Mapping, Optional, Set

logger = logging.getLogger(__name__)

COLLECTION = "youtube_videos"
HISTORY_SUBCOLLECTION = "history"
_BATCH_LIMIT = 400  # Firestore allows 500 writes per batch
_READ_CHUNK = 300
_LAST_SEEN_REFRESH = timedelta(hours=20)

# Fields whose changes are recorded in history.
TRACKED_FIELDS = (
    "title",
    "description",
    "tags",
    "published_at",
    "privacy_status",
    "upload_status",
    "category_id",
    "duration",
    "default_language",
    "made_for_kids",
    "license",
    "embeddable",
)


def metadata_from_video(video: Dict) -> Dict:
    """Flatten a videos.list item into the stored (tracked) metadata."""
    snip = video.get("snippet") or {}
    status = video.get("status") or {}
    details = video.get("contentDetails") or {}
    return {
        "title": snip.get("title") or "",
        "description": snip.get("description") or "",
        "tags": list(snip.get("tags") or []),
        "published_at": snip.get("publishedAt"),
        "privacy_status": status.get("privacyStatus"),
        "upload_status": status.get("uploadStatus"),
        "category_id": snip.get("categoryId"),
        "duration": details.get("duration"),
        "default_language": snip.get("defaultLanguage"),
        "made_for_kids": status.get("madeForKids"),
        "license": status.get("license"),
        "embeddable": status.get("embeddable"),
    }


def _parsed_fields(entry: Optional[Dict]) -> Dict:
    """Backfill classification of the video (informational, not tracked)."""
    entry = entry or {}
    return {
        "artist": entry.get("artist"),
        "song_title": entry.get("song_title"),
        "brand_code": entry.get("brand_code"),
        "kind": entry.get("kind"),
        "backfill_target": bool(entry.get("target")),
    }


def _diff(old: Dict, new: Dict) -> Dict:
    return {
        f: {"old": old.get(f), "new": new.get(f)}
        for f in TRACKED_FIELDS
        if old.get(f) != new.get(f)
    }


def _chunks(seq: List, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class _Writer:
    """Batches writes; a group (doc + its history) never spans two commits."""

    def __init__(self, db):
        self.db = db
        self.batch = db.batch()
        self.pending = 0

    def group(self, ops):
        """ops: list of (ref, data, merge). Committed together, atomically."""
        if self.pending and self.pending + len(ops) > _BATCH_LIMIT:
            self._commit()
        for ref, data, merge in ops:
            self.batch.set(ref, data, merge=merge)
        self.pending += len(ops)

    def _commit(self):
        self.batch.commit()
        self.batch = self.db.batch()
        self.pending = 0

    def flush(self):
        if self.pending:
            self._commit()


def sync_catalog(
    db,
    videos: Dict[str, Dict],
    entries: Iterable[Dict],
    listed_ids: Iterable[str],
    gone_ids: Iterable[str],
    now: datetime,
    backfill_pre_update: Optional[Mapping[str, Dict]] = None,
) -> Dict[str, int]:
    """Upsert every returned video and soft-delete the gone ones.

    `videos` must reflect the state AFTER this run's own description updates.
    `backfill_pre_update` maps each video this run rewrote to its videos.list
    item from BEFORE the rewrite: changes up to that point are logged as
    `observed`, and the rewrite itself as `backfill`.
    """
    coll = db.collection(COLLECTION)
    entries_by_id = {e["video_id"]: e for e in entries}
    listed: Set[str] = set(listed_ids)
    pre_update: Mapping[str, Dict] = backfill_pre_update or {}
    gone = [vid for vid in dict.fromkeys(gone_ids) if vid not in videos]

    all_ids = list(videos.keys()) + gone
    existing: Dict[str, Dict] = {}
    for chunk in _chunks(all_ids, _READ_CHUNK):
        for snap in db.get_all([coll.document(vid) for vid in chunk]):
            if snap.exists:
                existing[snap.id] = snap.to_dict() or {}

    stats = {"new": 0, "changed": 0, "deleted": 0, "restored": 0, "touched": 0}
    writer = _Writer(db)

    def _history(vid, event, changes, source):
        return (
            coll.document(vid).collection(HISTORY_SUBCOLLECTION).document(),
            {"at": now, "event": event, "changes": changes, "source": source},
            False,
        )

    for vid, video in videos.items():
        meta = metadata_from_video(video)
        doc = {
            "video_id": vid,
            **meta,
            **_parsed_fields(entries_by_id.get(vid)),
            "in_uploads_playlist": vid in listed,
            "last_seen_at": now,
            "deleted": False,
            "deleted_at": None,
        }
        prev = existing.get(vid)
        if prev is None:
            doc.update(first_seen_at=now, last_changed_at=now)
            ops = [(coll.document(vid), doc, False), _history(vid, "first_seen", {}, "observed")]
            if vid in pre_update:
                # Rewritten in the same run it was first seen: keep the original.
                ours = _diff(metadata_from_video(pre_update[vid]), meta)
                if ours:
                    ops.append(_history(vid, "changed", ours, "backfill"))
            writer.group(ops)
            stats["new"] += 1
            continue

        if vid in pre_update:
            before = metadata_from_video(pre_update[vid])
            observed = _diff(prev, before)
            ours = _diff(before, meta)
        else:
            observed, ours = _diff(prev, meta), {}
        changes = {**observed, **ours}
        restored = bool(prev.get("deleted"))
        bookkeeping_changed = any(
            prev.get(k) != doc[k]
            for k in ("in_uploads_playlist", "artist", "song_title", "brand_code", "kind", "backfill_target")
        )
        last_seen = prev.get("last_seen_at")
        stale_seen = not isinstance(last_seen, datetime) or now - last_seen >= _LAST_SEEN_REFRESH

        if changes or restored:
            doc["last_changed_at"] = now
        ops = []
        if changes or restored or bookkeeping_changed or stale_seen:
            ops.append((coll.document(vid), doc, True))
            stats["touched"] += 1
        if restored:
            ops.append(_history(vid, "restored", observed, "observed"))
            stats["restored"] += 1
        elif observed:
            ops.append(_history(vid, "changed", observed, "observed"))
        if ours:
            ops.append(_history(vid, "changed", ours, "backfill"))
        if observed or ours:
            stats["changed"] += 1
        if ops:
            writer.group(ops)

    for vid in gone:
        prev = existing.get(vid)
        if prev is None:
            # Known ID we never catalogued (e.g. deleted before the catalogue
            # existed): keep a stub so the record survives.
            writer.group([
                (coll.document(vid),
                 {"video_id": vid, "deleted": True, "deleted_at": now,
                  "first_seen_at": now, "last_changed_at": now}, False),
                _history(vid, "deleted", {}, "observed"),
            ])
            stats["deleted"] += 1
        elif not prev.get("deleted"):
            writer.group([
                (coll.document(vid),
                 {"deleted": True, "deleted_at": now, "last_changed_at": now,
                  "in_uploads_playlist": False}, True),
                _history(vid, "deleted", {}, "observed"),
            ])
            stats["deleted"] += 1

    writer.flush()
    return stats
