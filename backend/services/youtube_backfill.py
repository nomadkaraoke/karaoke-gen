"""
Shared logic for the YouTube description back-catalogue rewrite.

Used by BOTH:
  * the one-off CLI (scripts/youtube-descriptions/youtube_descriptions.py), and
  * the scheduled daily drain worker (backend/workers/youtube_description_backfill_worker.py)

so classification, targeting, rendering, and the update-body construction are
identical no matter how the rewrite is driven. Rendering itself delegates to
backend.services.youtube_description (single source of truth for the template).

Nothing here talks to the YouTube API — callers pass in already-fetched video
snippet dicts (as returned by videos.list items) and act on the results.
"""
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.services.youtube_description import build_youtube_tags, render_youtube_description

# Data files (skip / force-include lists) ship inside the backend package so they
# are available both locally and in the Cloud Run image.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "youtube_backfill"
SKIP_IDS_PATH = _DATA_DIR / "skip_ids.txt"
INCLUDE_IDS_PATH = _DATA_DIR / "include_ids.txt"

UPDATE_COST = 50  # videos.update quota units
DEFAULT_MUSIC_CATEGORY = "10"

# --- Classification patterns ---
# "Artist - Title (Karaoke)" and variants ("(Official Karaoke)", "(Karaoke Version)").
KARAOKE_TITLE_RE = re.compile(
    r"^\s*(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+?)\s*\(\s*[^)]*karaoke[^)]*\)\s*$",
    re.IGNORECASE,
)
FALLBACK_TITLE_RE = re.compile(r"^\s*(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+?)\s*$")
BRAND_CODE_RE = re.compile(r"\bNOMAD(?:NP)?-\d{3,}\b")

# Description substrings that signal "this is one of our karaoke uploads".
KARAOKE_DESC_MARKERS = (
    "brand code: nomad",
    "nomadkaraoke.com",
    "created with nomad karaoke",
    "vocals removed",
    "karaoke (instrumental) version",
    "fiverr.com",
    "discord.gg/divebar",
    "discord.nomadkaraoke.com",
    "karaokenerds.com",
    "ai-powered vocal separation",
)

# Only these snippet fields are sent back on update (others are read-only or would
# be wiped). categoryId + title are required by the API on a snippet update.
_MUTABLE_SNIPPET_FIELDS = {
    "title",
    "description",
    "categoryId",
    "tags",
    "defaultLanguage",
    "defaultAudioLanguage",
}


# ----------------------------------------------------------------------------
# List files
# ----------------------------------------------------------------------------
def _load_id_file(path: Path) -> set:
    ids = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ids.add(line.split("|", 1)[0].strip())
    return ids


def load_skip_ids() -> set:
    return _load_id_file(SKIP_IDS_PATH)


def load_include_overrides() -> Dict[str, Optional[Dict[str, str]]]:
    """Parse include_ids.txt.

    Each non-comment line is either a bare video ID, or
    ``<id> | <Artist> | <Title>`` to override the parsed artist/title (used for
    genuine tracks whose title was truncated on YouTube). Returns a map of
    id -> {"artist", "song_title"} or None (no override).
    """
    out: Dict[str, Optional[Dict[str, str]]] = {}
    if not INCLUDE_IDS_PATH.exists():
        return out
    for line in INCLUDE_IDS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            out[parts[0]] = {
                "artist": parts[1] if len(parts) > 1 else "",
                "song_title": parts[2] if len(parts) > 2 else "",
            }
        else:
            out[line] = None
    return out


# ----------------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------------
def classify(video: Dict) -> Dict:
    """Classify a videos.list item dict. Returns a plain dict of findings."""
    snip = video.get("snippet", {})
    title = snip.get("title", "") or ""
    desc = snip.get("description", "") or ""
    low_title = title.lower()
    low_desc = desc.lower()

    brand_match = BRAND_CODE_RE.search(desc)
    brand_code = brand_match.group(0) if brand_match else None

    has_karaoke_title = "karaoke" in low_title
    has_marker = any(m in low_desc for m in KARAOKE_DESC_MARKERS)
    is_karaoke = has_karaoke_title or bool(brand_code) or has_marker

    artist = song_title = None
    parse_conf = "none"
    m = KARAOKE_TITLE_RE.match(title)
    if m:
        artist = m.group("artist").strip()
        song_title = m.group("title").strip()
        parse_conf = "high"
    elif is_karaoke:
        m2 = FALLBACK_TITLE_RE.match(title)
        if m2:
            artist = m2.group("artist").strip()
            song_title = m2.group("title").strip()
            parse_conf = "medium"

    if "ai-powered vocal separation" in low_desc:
        kind = "terse_new"
    elif "fiverr.com" in low_desc:
        kind = "fiverr"
    elif "brand code: nomad" in low_desc or "nomadkaraoke.com" in low_desc:
        kind = "nomad_recent"
    elif is_karaoke:
        kind = "other_karaoke"
    else:
        kind = "non_karaoke"

    # Auto-eligible ONLY at high confidence (title explicitly ends in "(Karaoke)").
    # The medium fallback sweeps in demos/tutorials/appeals and "lyrics video WITH
    # vocals" uploads, so those are excluded and surfaced for review instead.
    eligible = bool(is_karaoke and artist and song_title and parse_conf == "high")

    return {
        "video_id": video.get("id"),
        "yt_title": title,
        "artist": artist,
        "song_title": song_title,
        "brand_code": brand_code,
        "parse_confidence": parse_conf,
        "kind": kind,
        "is_karaoke": is_karaoke,
        "eligible": eligible,
        "category_id": snip.get("categoryId"),
        "current_description": desc,
    }


def render_for(entry: Dict) -> str:
    return render_youtube_description(
        artist=entry.get("artist"),
        title=entry.get("song_title"),
        brand_code=entry.get("brand_code"),
    )


def build_entries(
    videos_by_id: Dict[str, Dict],
    ordered_ids: List[str],
    skip_ids: Optional[set] = None,
    include_overrides: Optional[Dict] = None,
) -> List[Dict]:
    """Classify every video and decide which are rewrite targets.

    Adds `in_skip_list`, `forced_include`, `target`, and `will_change` to each
    entry. `will_change` is the authoritative "should we update this" flag: it's
    True only for a target whose freshly-rendered description actually differs.
    """
    skip_ids = skip_ids if skip_ids is not None else load_skip_ids()
    include_overrides = include_overrides if include_overrides is not None else load_include_overrides()

    entries: List[Dict] = []
    for vid in ordered_ids:
        v = videos_by_id.get(vid)
        if not v:
            continue
        entry = classify(v)
        entry["in_skip_list"] = vid in skip_ids
        entry["forced_include"] = vid in include_overrides
        override = include_overrides.get(vid)
        if override:
            if override.get("artist"):
                entry["artist"] = override["artist"]
            if override.get("song_title"):
                entry["song_title"] = override["song_title"]
            entry["parse_confidence"] = "override"

        is_target = (
            (entry["eligible"] or entry["forced_include"])
            and not entry["in_skip_list"]
            and bool(entry["artist"])
            and bool(entry["song_title"])
        )
        entry["target"] = is_target
        if is_target:
            new_desc = render_for(entry)
            entry["will_change"] = new_desc.strip() != entry["current_description"].strip()
        else:
            entry["will_change"] = False
        entries.append(entry)
    return entries


def build_update_snippet(current_snippet: Dict, entry: Dict, enrich_tags: bool) -> Dict:
    """Build the snippet body for videos.update.

    Copies the existing mutable snippet fields (so title/category/language are
    preserved), swaps in the new description, and optionally refreshes tags.
    """
    snippet = {k: v for k, v in current_snippet.items() if k in _MUTABLE_SNIPPET_FIELDS}
    snippet["description"] = render_for(entry)
    if not snippet.get("categoryId"):
        snippet["categoryId"] = DEFAULT_MUSIC_CATEGORY
    if enrich_tags:
        snippet["tags"] = build_youtube_tags(entry.get("artist"), entry.get("song_title"))
    return snippet


# ----------------------------------------------------------------------------
# YouTube API helpers (shared by the CLI and the scheduled worker)
# ----------------------------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/youtube"]


def load_credentials_from_secret() -> Optional[Dict]:
    """Load the youtube-oauth-credentials dict from Secret Manager (or None)."""
    from backend.services.youtube_service import get_youtube_service

    return get_youtube_service().get_credentials_dict()


def build_youtube(creds_dict: Dict):
    """Build an authorized youtube v3 client from a credentials dict."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=creds_dict.get("token"),
        refresh_token=creds_dict.get("refresh_token"),
        token_uri=creds_dict.get("token_uri"),
        client_id=creds_dict.get("client_id"),
        client_secret=creds_dict.get("client_secret"),
        scopes=creds_dict.get("scopes", SCOPES),
    )
    if not creds.token or creds.expired:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def get_uploads_playlist(youtube) -> str:
    resp = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("No channel found for these credentials.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def iter_all_video_ids(youtube, playlist_id: str) -> List[str]:
    ids: List[str] = []
    token = None
    while True:
        resp = (
            youtube.playlistItems()
            .list(part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=token)
            .execute()
        )
        for it in resp.get("items", []):
            ids.append(it["contentDetails"]["videoId"])
        token = resp.get("nextPageToken")
        if not token:
            break
    return ids


def _chunks(seq: List, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def fetch_snippets(youtube, ids: List[str]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for batch in _chunks(ids, 50):
        resp = youtube.videos().list(part="snippet,status", id=",".join(batch)).execute()
        for it in resp.get("items", []):
            out[it["id"]] = it
    return out


def fetch_all_channel_entries(youtube) -> Tuple[List[Dict], Dict[str, Dict]]:
    """Enumerate the whole channel; return (classified entries, raw videos-by-id).

    Read-only. ~1 quota unit per 50 videos plus a couple for enumeration. The raw
    videos map is returned too so callers can reuse the already-fetched full
    snippets when applying updates (no extra reads).
    """
    playlist_id = get_uploads_playlist(youtube)
    ids = iter_all_video_ids(youtube, playlist_id)
    videos = fetch_snippets(youtube, ids)
    return build_entries(videos, ids), videos
