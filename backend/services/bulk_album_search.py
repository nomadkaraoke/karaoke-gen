"""Bulk "By album": one album search for the whole batch instead of one per track.

flacfetch's ``POST /search/album`` returns the album's lossless torrents, each
with ``track_files`` mapping our track keys (job IDs) to a file in the torrent.
This module turns that response into each job's ``audio_search_results`` (the
same dict shape as ``AudioSearchResult.to_dict()``, so the selection modal,
``_validate_and_prepare_selection`` and the download worker work unchanged), and
decides which torrent the batch should use.
"""

from typing import Any, Dict, List, Optional

from backend.services.audio_search_service import _categorize_result, _is_confident_pick


def _result_for_track(torrent: Dict[str, Any], track_file: Dict[str, Any], index: int) -> Dict[str, Any]:
    """One torrent + one of its files as an audio search result dict."""
    return {
        "title": torrent.get("title"),
        "artist": torrent.get("artist"),
        "url": "",
        "provider": torrent.get("provider"),
        "duration": None,
        "quality": torrent.get("quality"),
        "quality_data": torrent.get("quality_data"),
        "is_lossless": torrent.get("is_lossless", False),
        "source_id": torrent.get("source_id"),
        "index": index,
        "seeders": torrent.get("seeders"),
        "size_bytes": torrent.get("size_bytes"),
        "formatted_size": torrent.get("formatted_size"),
        "year": torrent.get("year"),
        "label": torrent.get("label"),
        "edition_info": torrent.get("edition_info"),
        "release_type": torrent.get("release_type"),
        "target_file": track_file.get("target_file"),
        "target_file_size": track_file.get("target_file_size"),
        "match_score": track_file.get("match_score", 0.0),
        # Marks results that came from the batch's album search (diagnostics).
        "album_search": True,
    }


def _torrent_id(torrent: Dict[str, Any]) -> tuple:
    return (torrent.get("provider"), torrent.get("source_id"))


def choose_batch_torrent(torrents: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The torrent the whole batch should download from.

    flacfetch orders torrents by tracks covered, then non-vinyl, then seeders; take
    the first that is a BEST CHOICE source (lossless, non-vinyl, enough seeders),
    so every track comes from one torrent. None if no torrent qualifies.
    """
    for torrent in torrents:
        if _categorize_result(torrent) == "BEST CHOICE":
            return torrent
    return None


def build_track_results(
    torrents: List[Dict[str, Any]],
    track_key: str,
    batch_torrent: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """A job's search results from the album search: every torrent that has a file
    for this track, the batch torrent first, then flacfetch's order."""
    covering = [t for t in torrents if track_key in (t.get("track_files") or {})]
    if batch_torrent is not None:
        batch_id = _torrent_id(batch_torrent)
        covering.sort(key=lambda t: _torrent_id(t) != batch_id)  # stable: batch torrent first
    return [
        _result_for_track(t, t["track_files"][track_key], index)
        for index, t in enumerate(covering)
    ]


def pick_album_auto_selection(
    results: List[Dict[str, Any]],
    batch_torrent: Optional[Dict[str, Any]],
    search_title: str,
    search_artist: str,
) -> Optional[int]:
    """Auto-select this track's file in the batch torrent, if it's a confident pick.

    Unlike per-track ``pick_auto_selection`` (which takes the best-seeded BEST
    CHOICE result), album mode sticks to the batch torrent so the whole album
    downloads once. The same tier-1 checks apply to it: BEST CHOICE, the file
    names the track, the artist matches.
    """
    if batch_torrent is None or not results:
        return None
    if len((search_title or "").strip()) < 3:
        return None
    first = results[0]
    if _torrent_id(first) != _torrent_id(batch_torrent):
        return None  # the batch torrent has no file for this track
    if not _is_confident_pick(first, search_title, search_artist):
        return None
    return first.get("index")
