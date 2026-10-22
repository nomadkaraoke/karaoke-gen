"""YouTube comment auto-reply: song requests in channel comments -> requests board.

Viewers often comment "could you make <song>?" on Nomad Karaoke videos. This module
decides, for one top-level comment thread, whether to reply and with what:

- an already-published Nomad video of that song -> link to it;
- otherwise -> add the song to the requests board (or find the existing request)
  and link straight to it, so the commenter can sign in with an email and upvote it.

The reply is board-only on purpose (Andrew, 2026-10-22): the goal is turning an
anonymous YouTuber into a requests-board user with a real email address; the
board UI and follow-up emails do the gen conversion later.

Everything here is pure decision logic behind small injectable ports (classifier,
translator, board, channel index), shared by the local replay script
(``scripts/youtube-comments/replay.py``, the go-live gate) and the prod worker
(``backend/workers/youtube_comment_bot_worker.py``). Plan:
``docs/archive/2026-10-22-youtube-comment-autoreply-plan.md``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Protocol, Sequence
from urllib.parse import urlencode

from backend.services.match_judge.classifier import normalize_for_match

logger = logging.getLogger(__name__)

NOMAD_CHANNEL_ID = "UCroMk5b4XILdeP7ZCGFicpw"
BOARD_URL = "https://requests.nomadkaraoke.com/"
BOARD_UTM = {
    "utm_source": "youtube",
    "utm_medium": "comment_reply",
    "utm_campaign": "song_request",
}
MAX_SONGS_PER_COMMENT = 3
DEFAULT_MIN_CONFIDENCE = 0.8


def song_key(artist: str, title: str) -> str:
    """Same key shape as the board's ``_dedupe_key`` (artist|title, match-normalized)."""
    return f"{normalize_for_match(artist)}|{normalize_for_match(title)}"


def board_link(request_id: Optional[str] = None) -> str:
    params = {"request": request_id} if request_id else {}
    return f"{BOARD_URL}?{urlencode({**params, **BOARD_UTM})}"


def video_link(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass
class CommentThread:
    """A top-level comment plus what we know about replies to it."""

    thread_id: str
    comment_id: str
    video_id: str
    author: str
    author_channel_id: str
    text: str
    published_at: str  # RFC 3339 from the API
    total_reply_count: int = 0
    # Text of replies written by the channel itself (Andrew answering by hand).
    owner_replies: List[str] = field(default_factory=list)

    @property
    def published(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.published_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None


def thread_from_api(item: Dict, channel_id: str = NOMAD_CHANNEL_ID) -> CommentThread:
    """Build a CommentThread from a ``commentThreads.list`` item (part=snippet,replies).

    ``replies`` holds at most 5 replies; callers that need the full list when
    ``total_reply_count`` exceeds that fetch them and pass ``owner_replies`` in.
    """
    snip = item.get("snippet", {})
    top = snip.get("topLevelComment", {})
    tsnip = top.get("snippet", {})
    owner_replies = [
        (r.get("snippet", {}).get("textOriginal") or r.get("snippet", {}).get("textDisplay") or "")
        for r in (item.get("replies", {}) or {}).get("comments", []) or []
        if (r.get("snippet", {}).get("authorChannelId") or {}).get("value") == channel_id
    ]
    return CommentThread(
        thread_id=item.get("id", ""),
        comment_id=top.get("id", item.get("id", "")),
        video_id=snip.get("videoId") or tsnip.get("videoId") or "",
        author=tsnip.get("authorDisplayName", ""),
        author_channel_id=(tsnip.get("authorChannelId") or {}).get("value", ""),
        text=tsnip.get("textOriginal") or tsnip.get("textDisplay") or "",
        published_at=tsnip.get("publishedAt", ""),
        total_reply_count=int(snip.get("totalReplyCount", 0) or 0),
        owner_replies=owner_replies,
    )


# ---------------------------------------------------------------------------
# Classifier (Gemini, injectable)
# ---------------------------------------------------------------------------
@dataclass
class SongAsk:
    artist: str
    title: str


@dataclass
class Classification:
    is_song_request: bool
    songs: List[SongAsk]
    language: str  # ISO 639-1, "en" when unsure
    confidence: float
    reason: str = ""
    is_vague_request: bool = False
    # The classifier call itself failed (timeout, 5xx, quota): retry later.
    error: bool = False


CLASSIFIER_SYSTEM_PROMPT = """\
You read YouTube comments left on karaoke videos published by the Nomad Karaoke channel.
Decide whether the comment asks the channel to MAKE a karaoke version of one or more
specific songs (a song request).

Counts as a song request:
- "Can you do <song> by <artist>?", "please make <song>", "next do <artist> - <song>",
  "would love a karaoke of <song>", or the same in any language.
- A request where the artist is implied but the song title is clear
  (e.g. "can you do Yellow next" on a Coldplay video) - fill in the artist when you are sure.

A bare song name with no ask at all ("<song> karaoke 🎤") is a weak signal: count it
only when it clearly relates to the video (same artist, e.g. "<artist> <other song>
karaoke songs" on that artist's video); on an unrelated video give it confidence 0.5.

Does NOT count:
- Praise, thanks, singing reports, jokes, lyric corrections, timing complaints.
- Questions about how the videos are made, software, pricing or downloads.
- Asking for a change to THIS video (key change, backing vocals, slower, duet version).
- Mentions of songs without asking for them to be made.
- Vague asks with no identifiable song ("do more 80s songs", "anything by Queen").

Vague asks get their own flag: set is_vague_request=true (and is_song_request=false,
songs=[]) when the comment asks the channel to make MORE songs without naming a
specific song: "more Johnnie please", "do more from this album", "can you do more
Taylor Swift". Plain praise like "love your channel, keep it up" is NOT vague_request.
If ANY specific song title is identifiable (even lowercase, abbreviated or with the
artist run in, e.g. "playa fly n god we trust karaoke"), it is a song request, not vague.

Return JSON only. confidence is 0..1 that your classification (request, vague
request, or neither) is right.
Give the artist and title as properly capitalised canonical names. List at most 3 songs.
language is the ISO 639-1 code of the comment's language ("en" if unsure)."""

CLASSIFIER_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_song_request": {"type": "BOOLEAN"},
        "is_vague_request": {"type": "BOOLEAN"},
        "songs": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "artist": {"type": "STRING"},
                    "title": {"type": "STRING"},
                },
                "required": ["artist", "title"],
            },
        },
        "language": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
        "reason": {"type": "STRING"},
    },
    "required": ["is_song_request", "songs", "language", "confidence"],
}

# (system_prompt, user_prompt, response_schema) -> parsed JSON dict
Generate = Callable[[str, str, Optional[Dict]], Dict]


MAX_NAME_LEN = 100
# Artist/title come from the model reading untrusted comment text and end up in a
# public reply posted as the channel + on the public board: never let a link, an
# @mention, markup or a multi-line blob through.
_UNSAFE_NAME_RE = re.compile(r"https?://|www\.|\.(com|net|org|io|ly|gg|xyz)\b|[@<>\r\n]", re.IGNORECASE)


def _unsafe_name(value: str) -> bool:
    return len(value) > MAX_NAME_LEN or bool(_UNSAFE_NAME_RE.search(value))


def parse_classification(data: Dict) -> Classification:
    """Validate the model's JSON; anything malformed becomes a non-request."""
    if not isinstance(data, dict):
        return Classification(False, [], "en", 0.0, "malformed classifier output")
    songs: List[SongAsk] = []
    for s in data.get("songs") or []:
        if not isinstance(s, dict):
            continue
        artist = str(s.get("artist") or "").strip()
        title = str(s.get("title") or "").strip()
        if artist and title and not _unsafe_name(artist) and not _unsafe_name(title):
            songs.append(SongAsk(artist, title))
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    language = str(data.get("language") or "en").strip().lower()[:5] or "en"
    is_request = bool(data.get("is_song_request")) and bool(songs)
    return Classification(
        is_song_request=is_request,
        songs=songs[:MAX_SONGS_PER_COMMENT],
        language=language,
        confidence=max(0.0, min(1.0, confidence)),
        reason=str(data.get("reason") or ""),
        is_vague_request=bool(data.get("is_vague_request")) and not is_request,
    )


def classify_comment(text: str, video_title: str, generate: Generate) -> Classification:
    user_prompt = f"Video title: {video_title or '(unknown)'}\nComment: {text}"
    try:
        data = generate(CLASSIFIER_SYSTEM_PROMPT, user_prompt, CLASSIFIER_RESPONSE_SCHEMA)
    except Exception:  # noqa: BLE001 - a classifier failure must never post anything
        logger.warning("youtube-comment-bot: classifier call failed", exc_info=True)
        return Classification(False, [], "en", 0.0, "classifier error", error=True)
    return parse_classification(data)


def gemini_generate(model: Optional[str] = None, timeout_ms: int = 20000) -> Generate:
    """Default Generate backed by the Gemini Developer API (never Vertex)."""

    def _generate(system_prompt: str, user_prompt: str, schema: Optional[Dict]) -> Dict:
        import copy

        from google.genai import types

        from backend.config import get_settings
        from backend.services.gemini_client import get_genai_client

        client = get_genai_client(timeout_ms=timeout_ms)
        config = dict(
            system_instruction=system_prompt,
            temperature=0,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        )
        if schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = copy.deepcopy(schema)
        response = client.models.generate_content(
            model=model or get_settings().match_judge_model,
            contents=[user_prompt],
            config=types.GenerateContentConfig(**config),
        )
        if schema is None:
            return {"text": response.text or ""}
        return json.loads(response.text)

    return _generate


# ---------------------------------------------------------------------------
# Channel index + board port
# ---------------------------------------------------------------------------
class ChannelIndex:
    """song_key -> video_id for Nomad's own published karaoke videos."""

    def __init__(self, by_key: Optional[Dict[str, str]] = None, titles: Optional[Dict[str, str]] = None):
        self.by_key: Dict[str, str] = dict(by_key or {})
        self.titles: Dict[str, str] = dict(titles or {})  # video_id -> YouTube title

    @classmethod
    def from_entries(cls, entries: Sequence[Dict]) -> "ChannelIndex":
        """Build from ``youtube_backfill.classify()`` results (high-confidence titles only)."""
        idx = cls()
        for e in entries:
            vid = e.get("video_id")
            if vid and e.get("yt_title"):
                idx.titles[vid] = e["yt_title"]
            if not (vid and e.get("artist") and e.get("song_title")):
                continue
            if e.get("parse_confidence") != "high" or e.get("translation_language_name"):
                continue
            idx.by_key.setdefault(song_key(e["artist"], e["song_title"]), vid)
        return idx

    def find(self, artist: str, title: str) -> Optional[str]:
        return self.by_key.get(song_key(artist, title))


@dataclass
class BoardMatch:
    request_id: str
    artist: str
    title: str
    status: str  # board RequestStatus
    youtube_url: Optional[str] = None


class BoardPort(Protocol):
    def canonicalize(self, artist: str, title: str) -> "tuple[str, str]": ...

    def find_open_or_published(self, artist: str, title: str) -> Optional[BoardMatch]: ...

    def add_from_comment(self, artist: str, title: str, thread: CommentThread) -> BoardMatch: ...


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------
@dataclass
class SongAction:
    artist: str
    title: str
    kind: str  # existing_video | board_published | board_existing | board_new
    url: str
    request_id: Optional[str] = None
    video_id: Optional[str] = None


@dataclass
class Decision:
    action: str  # "reply" | "skip"
    reason: str
    classification: Optional[Classification] = None
    songs: List[SongAction] = field(default_factory=list)
    reply_text: str = ""
    reply_text_en: str = ""


def _quote(s: SongAction) -> str:
    return f'"{s.title}" by {s.artist}'


def render_reply_en(songs: Sequence[SongAction]) -> str:
    """Short, casual, board-only English reply (Andrew's style: brief + emoji)."""
    made = [s for s in songs if s.kind in ("existing_video", "board_published")]
    board = [s for s in songs if s.kind in ("board_existing", "board_new")]
    parts: List[str] = []
    if len(made) == 1:
        parts.append(f"Already made {_quote(made[0])} 🥳 {made[0].url}")
    elif made:
        parts.append("Already made these 🥳\n" + "\n".join(f"{_quote(s)}: {s.url}" for s in made))
    if len(board) == 1:
        s = board[0]
        parts.append(f"Added {_quote(s)} to our requests board 🥳 upvote it here and we'll "
                     f"email you when it's made: {s.url}")
    elif board:
        parts.append("Added these to our requests board 🥳 upvote them and we'll email you "
                     "when they're made:\n" + "\n".join(f"{_quote(s)}: {s.url}" for s in board))
    return "\n\n".join(parts)


def render_vague_reply_en() -> str:
    return f"Request any song you'd like on our requests board 🥳 {board_link()}"


TRANSLATE_SYSTEM_PROMPT = """\
Translate this short YouTube reply from a karaoke channel into the target language.
Keep it warm and casual. Keep every URL, song title and artist name exactly as written.
Return only the translated text."""

_URL_RE = re.compile(r"https?://\S+")


def translate_reply(text_en: str, language: str, generate: Generate) -> str:
    """Translate the reply for non-English comments; English on any doubt."""
    if not language or language.startswith("en"):
        return text_en
    try:
        out = generate(TRANSLATE_SYSTEM_PROMPT, f"Target language: {language}\n\n{text_en}", None)
        translated = str((out or {}).get("text") or "").strip()
    except Exception:  # noqa: BLE001
        logger.warning("youtube-comment-bot: translation failed; replying in English", exc_info=True)
        return text_en
    # Every link must survive verbatim, or we fall back to English.
    if not translated or sorted(_URL_RE.findall(translated)) != sorted(_URL_RE.findall(text_en)):
        return text_en
    return translated


def decide(
    thread: CommentThread,
    *,
    video_title: str,
    index: ChannelIndex,
    board: BoardPort,
    generate: Generate,
    channel_id: str = NOMAD_CHANNEL_ID,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    not_before: Optional[datetime] = None,
) -> Decision:
    """Decide what (if anything) to reply to one top-level comment thread.

    ``board.add_from_comment`` is only called for a confident request, so the
    replay's simulated board and the prod board see exactly the same calls.
    """
    if thread.author_channel_id == channel_id:
        return Decision("skip", "own_comment")
    if thread.owner_replies:
        return Decision("skip", "already_replied")
    if not_before is not None:
        published = thread.published
        if published is None or published < not_before:
            return Decision("skip", "too_old")
    if not thread.text.strip():
        return Decision("skip", "empty")

    classification = classify_comment(thread.text, video_title, generate)
    if classification.error:
        return Decision("skip", "classifier_error", classification)
    if classification.is_vague_request:
        if classification.confidence < min_confidence:
            return Decision("skip", "low_confidence", classification)
        reply_en = render_vague_reply_en()
        reply = translate_reply(reply_en, classification.language, generate)
        return Decision("reply", "vague_request", classification, [], reply, reply_en)
    if not classification.is_song_request:
        return Decision("skip", "not_a_request", classification)
    if classification.confidence < min_confidence:
        return Decision("skip", "low_confidence", classification)

    actions: List[SongAction] = []
    seen: set = set()
    for ask in classification.songs:
        artist, title = board.canonicalize(ask.artist, ask.title)
        key = song_key(artist, title)
        if key in seen:
            continue
        seen.add(key)
        vid = index.find(artist, title) or index.find(ask.artist, ask.title)
        if vid:
            actions.append(SongAction(artist, title, "existing_video", video_link(vid), video_id=vid))
            continue
        match = board.find_open_or_published(artist, title)
        if match and match.status == "published" and match.youtube_url:
            actions.append(SongAction(match.artist, match.title, "board_published",
                                      match.youtube_url, request_id=match.request_id))
            continue
        if match:
            actions.append(SongAction(match.artist, match.title, "board_existing",
                                      board_link(match.request_id), request_id=match.request_id))
            continue
        created = board.add_from_comment(artist, title, thread)
        actions.append(SongAction(created.artist, created.title, "board_new",
                                  board_link(created.request_id), request_id=created.request_id))

    if not actions:
        return Decision("skip", "no_songs", classification)

    reply_en = render_reply_en(actions)
    reply = translate_reply(reply_en, classification.language, generate)
    return Decision("reply", "song_request", classification, actions, reply, reply_en)


# ---------------------------------------------------------------------------
# YouTube API helpers (shared by the replay script and the worker)
# ---------------------------------------------------------------------------
def iter_comment_threads(youtube, channel_id: str = NOMAD_CHANNEL_ID,
                         stop_before: Optional[datetime] = None, max_pages: int = 500):
    """Yield ``commentThreads.list`` items for the whole channel, newest first.

    1 quota unit per page of 100. Stops once a page's oldest thread is older than
    ``stop_before`` (order=time is by top-level comment publish time).
    """
    token = None
    for _ in range(max_pages):
        resp = (
            youtube.commentThreads()
            .list(part="snippet,replies", allThreadsRelatedToChannelId=channel_id,
                  order="time", maxResults=100, textFormat="plainText", pageToken=token)
            .execute()
        )
        items = resp.get("items", [])
        reached_end = False
        for item in items:
            if stop_before is not None:
                published = thread_from_api(item, channel_id).published
                if published is not None and published < stop_before:
                    reached_end = True
                    break
            yield item
        token = resp.get("nextPageToken")
        if reached_end or not token:
            return


def fetch_owner_replies(youtube, parent_id: str, channel_id: str = NOMAD_CHANNEL_ID) -> List[str]:
    """All replies by the channel on a thread (``comments.list``, 1 unit per page)."""
    out: List[str] = []
    token = None
    while True:
        resp = (
            youtube.comments()
            .list(part="snippet", parentId=parent_id, maxResults=100,
                  textFormat="plainText", pageToken=token)
            .execute()
        )
        for c in resp.get("items", []):
            s = c.get("snippet", {})
            if (s.get("authorChannelId") or {}).get("value") == channel_id:
                out.append(s.get("textOriginal") or s.get("textDisplay") or "")
        token = resp.get("nextPageToken")
        if not token:
            return out


def load_thread(youtube, item: Dict, channel_id: str = NOMAD_CHANNEL_ID) -> CommentThread:
    """thread_from_api, completing owner_replies when the API truncated replies."""
    thread = thread_from_api(item, channel_id)
    inline = len((item.get("replies", {}) or {}).get("comments", []) or [])
    if not thread.owner_replies and thread.total_reply_count > inline:
        thread.owner_replies = fetch_owner_replies(youtube, thread.comment_id, channel_id)
    return thread


def post_reply(youtube, parent_comment_id: str, text: str) -> str:
    """Post a reply as the channel (needs the youtube.force-ssl scope; 50 units)."""
    resp = (
        youtube.comments()
        .insert(part="snippet", body={"snippet": {"parentId": parent_comment_id, "textOriginal": text}})
        .execute()
    )
    return resp.get("id", "")
