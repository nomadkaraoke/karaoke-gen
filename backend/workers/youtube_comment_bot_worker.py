"""
Scheduled worker: reply to song requests in Nomad Karaoke YouTube comments.

Each run reads new top-level comment threads on the channel (newest first, down
to a stored high-water mark), runs the shared decision core
(``backend.services.youtube_comment_bot.decide``) on each, and for a confident
song request adds the song to the requests board and replies with a link to it
(or to an existing Nomad video of the song).

Safety:
- Off unless ``YOUTUBE_COMMENT_AUTOREPLY_ENABLED=true``; ``dry_run`` computes and
  logs every action without writing to the board or posting anything.
- One action doc per comment (``youtube_comment_bot_actions/{comment_id}``),
  created with create-only semantics BEFORE posting, so an overlapping or
  retried run can never reply twice to the same comment.
- Never replies to comments published before the go-live time (the first real
  run's start, stored in state) or to threads the channel already answered.
- Uses its own OAuth token (secret ``youtube-comment-bot-credentials``, scopes
  youtube + youtube.force-ssl) so the upload pipeline's token is untouched.

Triggered hourly by Cloud Scheduler -> POST /api/internal/youtube-comments/run.
Plan: docs/archive/2026-10-22-youtube-comment-autoreply-plan.md
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import requests
from google.api_core import exceptions as gcloud_exceptions

from backend.config import get_settings
from backend.services import youtube_backfill as ytb
from backend.services import youtube_comment_bot as bot
from backend.services.firestore_service import get_firestore_client
from backend.services.youtube_quota_service import get_youtube_quota_service

logger = logging.getLogger(__name__)

STATE_COLLECTION = "youtube_comment_bot"
STATE_DOC = "state"
ACTIONS_COLLECTION = "youtube_comment_bot_actions"
CREDENTIALS_SECRET = "youtube-comment-bot-credentials"
FORCE_SSL_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
REPLY_COST = 50  # comments.insert quota units
# Re-read a little before the high-water mark: YouTube can surface a comment a
# few minutes after its publishedAt (held for review, then approved).
OVERLAP = timedelta(hours=6)
CHANNEL_INDEX_TTL = timedelta(hours=24)


class ServiceBoard:
    """BoardPort backed by the real requests board."""

    def __init__(self, service):
        self.service = service

    def canonicalize(self, artist: str, title: str):
        return asyncio.run(self.service._canonicalize(artist, title))

    def find_open_or_published(self, artist: str, title: str) -> Optional[bot.BoardMatch]:
        key = bot.song_key(artist, title)
        req = self.service._find_open_by_key(key) or self.service.find_published_by_key(key)
        if req is None:
            return None
        return bot.BoardMatch(req.id, req.artist, req.title, req.status, req.youtube_url)

    def add_from_comment(self, artist: str, title: str, thread: bot.CommentThread) -> bot.BoardMatch:
        req = self.service.add_from_youtube_comment(
            artist, title, comment_id=thread.comment_id, video_id=thread.video_id, author=thread.author)
        return bot.BoardMatch(req.id, req.artist, req.title, req.status, req.youtube_url)


class DryRunBoard(ServiceBoard):
    """Reads the real board, simulates adds (dry runs write nothing)."""

    def __init__(self, service):
        super().__init__(service)
        self.simulated: Dict[str, bot.BoardMatch] = {}

    def find_open_or_published(self, artist, title):
        return self.simulated.get(bot.song_key(artist, title)) or super().find_open_or_published(artist, title)

    def add_from_comment(self, artist, title, thread):
        match = bot.BoardMatch(f"dry-run-{thread.comment_id}", artist, title, "open")
        self.simulated[bot.song_key(artist, title)] = match
        return match


def _load_credentials(settings) -> Optional[Dict]:
    raw = settings.get_secret(CREDENTIALS_SECRET)
    return json.loads(raw) if raw else None


def _post_noise(settings, content: str) -> None:
    """Informational post to Discord #noise (falls back to #alerts, never dropped)."""
    for secret in ("discord-noise-webhook", "discord-alert-webhook"):
        try:
            url = settings.get_secret(secret)
            if url and requests.post(url, json={"content": content[:1900]}, timeout=10).ok:
                return
        except Exception:  # noqa: BLE001
            logger.warning("YOUTUBE_COMMENT_BOT: Discord post via %s failed", secret, exc_info=True)


def _alert(key: str, message: str) -> None:
    from backend.services.ops_alerts import send_throttled_ops_alert

    send_throttled_ops_alert(f"youtube-comment-bot:{key}", message, throttle_minutes=360)


def _channel_index(db, youtube) -> bot.ChannelIndex:
    """song_key -> video_id for the channel, cached in Firestore for a day (~40 units)."""
    ref = db.collection(STATE_COLLECTION).document("channel_index")
    snap = ref.get()
    now = datetime.now(timezone.utc)
    if snap.exists:
        data = snap.to_dict()
        built = data.get("built_at")
        if built and now - built < CHANNEL_INDEX_TTL:
            return bot.ChannelIndex(data.get("by_key") or {}, data.get("titles") or {})
    entries, _ = ytb.fetch_all_channel_entries(youtube)
    index = bot.ChannelIndex.from_entries(entries)
    try:
        ref.set({"built_at": now, "by_key": index.by_key, "titles": index.titles})
    except Exception:  # noqa: BLE001 - cache only
        logger.warning("YOUTUBE_COMMENT_BOT: could not cache channel index", exc_info=True)
    return index


def run_comment_bot_sync(dry_run: bool = False, max_replies: Optional[int] = None) -> Dict:
    """One pass over new comments. Safe to call repeatedly. Returns a summary."""
    settings = get_settings()
    if not settings.youtube_comment_autoreply_enabled and not dry_run:
        return {"status": "disabled"}
    max_replies = settings.youtube_comment_autoreply_max_replies if max_replies is None else max_replies

    creds = _load_credentials(settings)
    if not creds:
        _alert("no-credentials", f"YouTube comment bot: secret `{CREDENTIALS_SECRET}` is missing or empty, "
                                 "so no song-request replies are being posted. Re-run the OAuth setup in "
                                 "docs/REQUESTS-BOARD.md § YouTube comment auto-reply.")
        return {"status": "no_credentials"}
    if FORCE_SSL_SCOPE not in (creds.get("scopes") or []):
        _alert("missing-scope", f"YouTube comment bot: `{CREDENTIALS_SECRET}` lacks the youtube.force-ssl "
                                "scope, so it can't read or reply to comments. Re-run the OAuth setup.")
        return {"status": "missing_scope"}

    db = get_firestore_client()
    state_ref = db.collection(STATE_COLLECTION).document(STATE_DOC)
    snap = state_ref.get()
    state = snap.to_dict() if snap.exists else {}
    now = datetime.now(timezone.utc)
    # Go-live time: only comments after the first real run are ever replied to.
    go_live = state.get("go_live_at") or now
    high_water = state.get("high_water") or go_live
    stop_before = max(go_live, high_water - OVERLAP)

    try:
        youtube = ytb.build_youtube(creds)
        index = _channel_index(db, youtube)
    except Exception as exc:  # noqa: BLE001
        _alert("auth", f"YouTube comment bot: couldn't connect to YouTube ({exc}). No replies this run.")
        logger.exception("YOUTUBE_COMMENT_BOT: YouTube client/index failed")
        return {"status": "youtube_error"}

    from backend.services.song_request_service import SongRequestService

    service = SongRequestService()
    board = DryRunBoard(service) if dry_run else ServiceBoard(service)
    generate = bot.gemini_generate()
    quota = get_youtube_quota_service()

    summary = {"status": "ok", "dry_run": dry_run, "threads": 0, "replied": 0, "skipped": {},
               "errors": 0, "actions": []}
    newest = high_water
    for item in bot.iter_comment_threads(youtube, stop_before=stop_before):
        thread = bot.load_thread(youtube, item)
        summary["threads"] += 1
        if thread.published and thread.published > newest:
            newest = thread.published
        action_ref = db.collection(ACTIONS_COLLECTION).document(thread.comment_id)
        if action_ref.get().exists:
            continue
        decision = bot.decide(thread, video_title=index.titles.get(thread.video_id, ""), index=index,
                              board=board, generate=generate, not_before=go_live,
                              min_confidence=settings.youtube_comment_autoreply_min_confidence)
        record = {
            "comment_id": thread.comment_id, "thread_id": thread.thread_id, "video_id": thread.video_id,
            "author": thread.author[:100], "text": thread.text[:2000], "published_at": thread.published_at,
            "action": decision.action, "reason": decision.reason,
            "songs": [s.__dict__ for s in decision.songs], "reply_text": decision.reply_text,
            "decided_at": now,
        }
        if decision.action != "reply":
            summary["skipped"][decision.reason] = summary["skipped"].get(decision.reason, 0) + 1
            if not dry_run and decision.reason not in ("own_comment", "too_old"):
                _create(action_ref, record)
            continue
        summary["actions"].append({k: record[k] for k in ("comment_id", "songs", "reply_text")})
        if dry_run:
            logger.info("YOUTUBE_COMMENT_BOT dry-run would reply to %s: %s", thread.comment_id, decision.reply_text)
            continue
        if summary["replied"] >= max_replies:
            # Leave no action doc: the next run picks it up (high-water stays below it).
            newest = min(newest, thread.published or newest)
            continue
        ok, remaining, _ = quota.check_quota_available(REPLY_COST)
        if not ok:
            logger.warning("YOUTUBE_COMMENT_BOT: quota low (%s left); stopping", remaining)
            newest = min(newest, thread.published or newest)
            break
        # Claim the comment BEFORE posting so a concurrent run can't double-reply.
        if not _create(action_ref, {**record, "action": "replying"}):
            continue
        try:
            reply_id = bot.post_reply(youtube, thread.comment_id, decision.reply_text)
            quota.record_upload(f"comment-reply:{thread.comment_id}", units=REPLY_COST)
            action_ref.update({"action": "replied", "reply_id": reply_id, "replied_at": datetime.now(timezone.utc)})
            summary["replied"] += 1
            songs = ", ".join(f'"{s.title}" by {s.artist} ({s.kind})' for s in decision.songs)
            _post_noise(settings, f"YouTube comment bot replied to {thread.author} on "
                                  f"https://www.youtube.com/watch?v={thread.video_id}&lc={thread.comment_id} "
                                  f"for {songs}")
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            action_ref.update({"action": "reply_failed", "error": str(exc)[:500]})
            logger.exception("YOUTUBE_COMMENT_BOT: reply to %s failed", thread.comment_id)
    if summary["errors"]:
        _alert("reply-failed", f"YouTube comment bot: {summary['errors']} replies failed this run "
                               f"(see Firestore {ACTIONS_COLLECTION}, action=reply_failed, and logs "
                               f"YOUTUBE_COMMENT_BOT).")

    if not dry_run:
        state_ref.set({"go_live_at": go_live, "high_water": newest, "last_run_at": now,
                       "last_summary": {k: summary[k] for k in ("threads", "replied", "skipped", "errors")}},
                      merge=True)
    logger.info("YOUTUBE_COMMENT_BOT run complete: %s", {k: v for k, v in summary.items() if k != "actions"})
    return summary


def _create(ref, data: Dict) -> bool:
    """Create-only write; False if the doc already exists (another run got there)."""
    try:
        ref.create(data)
        return True
    except gcloud_exceptions.Conflict:
        return False
