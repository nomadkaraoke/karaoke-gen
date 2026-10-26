"""Confident audio-source pick for unattended (daily community pick) jobs.

Nobody is there to eyeball the result, so we only auto-pick when the source is
unmistakably the requested track: the exact artist AND the exact track title (version
suffixes like "- Orchestral Version" included) on a lossless source we trust. Anything else
(no match, several different recordings, only YouTube) returns None and the job is parked
for the owner to choose in the gen UI, exactly like the normal interactive flow.

History: the picker used to call ``AudioSearchService.select_best``, which silently fell
back to result #0 whenever the (remote) results were plain dicts. That made unattended jobs
use whatever flacfetch listed first, e.g. the wrong "Lemon Drop" recording on 2026-10-24.
"""
import re
from typing import Any, Dict, List, Optional

from backend.services.match_judge.classifier import normalize_for_match

TORRENT_PROVIDERS = {"red", "ops"}
# A torrent needs at least this many seeders to be worth waiting for unattended.
MIN_TORRENT_SEEDERS = 3
# Distinct recordings of the "same" title differ in length; above this we can't tell which.
MAX_DURATION_SPREAD_SECONDS = 5

_AUDIO_EXT = re.compile(r"\.(flac|mp3|m4a|ogg|opus|wav|aac)$", re.IGNORECASE)
_TRACK_NUMBER = re.compile(r"^\s*(?:disc\s*\d+\s*[-.]\s*)?\d{1,3}\s*[-.)]?\s+", re.IGNORECASE)


def _track_title(result: Dict[str, Any]) -> str:
    """The track's own title: the file/track name, minus extension and track number."""
    raw = (result.get("target_file") or "").split("/")[-1]
    raw = _AUDIO_EXT.sub("", raw)
    return _TRACK_NUMBER.sub("", raw).strip()


def _artist_matches(want: str, have: str) -> bool:
    want_key = normalize_for_match(want)
    if not want_key:
        return False
    if normalize_for_match(have) == want_key:
        return True
    # "A, B", "A & B", "A feat. B": the requested artist may be any one of the credited names.
    for part in re.split(r",|&|\bfeat\.?\b|\bfeaturing\b|\bwith\b|\bx\b", have, flags=re.IGNORECASE):
        if normalize_for_match(part) == want_key:
            return True
    return False


def _is_exact_match(result: Dict[str, Any], artist: str, title: str) -> bool:
    if not _artist_matches(artist, result.get("artist") or ""):
        return False
    track = _track_title(result)
    want = normalize_for_match(title)
    return bool(want) and normalize_for_match(track) == want


def pick_confident_audio(results: List[Dict[str, Any]], artist: str, title: str) -> Optional[int]:
    """Index of the result to download unattended, or None when we aren't confident."""
    spotify: List[Dict[str, Any]] = []
    torrents: List[Dict[str, Any]] = []
    for r in results:
        provider = (r.get("provider") or "").lower()
        if provider == "spotify" and _is_exact_match(r, artist, title):
            spotify.append(r)
        elif provider in TORRENT_PROVIDERS and _is_exact_match(r, artist, title):
            if int(r.get("seeders") or 0) >= MIN_TORRENT_SEEDERS:
                torrents.append(r)

    # Several recordings under one title (live/remaster/radio edit with identical names)
    # can't be told apart from here, so don't guess between them.
    durations = [int(r["duration"]) for r in spotify if r.get("duration")]
    if durations and max(durations) - min(durations) > MAX_DURATION_SPREAD_SECONDS:
        return None

    if torrents:
        return int(max(torrents, key=lambda r: int(r.get("seeders") or 0))["index"])
    if spotify:
        return int(spotify[0]["index"])
    return None
