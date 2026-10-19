"""MusixmatchProvider against the RapidAPI v2 endpoint (GET /lyrics?artist=&title=).

Response shapes are real ones captured 2026-10-19. The old /lyrics/{artist}/{title}/
path returned 404 for every song (0/287 for 30+ days)."""
import logging
from unittest.mock import MagicMock, patch

import requests

from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.lyrics.musixmatch import MusixmatchProvider

HIT = {
    "data": {
        "artistName": "Queen",
        "artworkUrl": "https://s.mxmcdn.net/images-storage/albums2/x_350_350.jpg",
        "lyrics": "Is this the real life? Is this just fantasy?\nCaught in a landslide, no escape from reality",
        "searchEngine": "Musixmatch",
        "trackId": "460875706",
        "trackName": "Bohemian Rhapsody",
    },
    "error": False,
    "status": 200,
}
MISS = {
    "data": {"error": "Internal Server Error", "path": "/api/v2/lyrics", "status": 500,
             "timestamp": "2026-10-19T20:07:23.350+00:00"},
    "error": False,
    "status": 200,
}


def _provider():
    return MusixmatchProvider(LyricsProviderConfig(rapidapi_key="k"), logger=logging.getLogger("test_mxm"))


def _response(body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.raise_for_status.side_effect = None if status < 400 else requests.exceptions.HTTPError(response=resp)
    return resp


def test_requests_v2_query_endpoint():
    with patch("karaoke_gen.lyrics_transcriber.lyrics.musixmatch.requests.get", return_value=_response(HIT)) as get:
        data = _provider()._fetch_data_from_source("Queen", "Bohemian Rhapsody")
    assert data["trackId"] == "460875706"
    url = get.call_args.args[0]
    assert url == "https://musixmatch-song-lyrics-api.p.rapidapi.com/lyrics"
    assert get.call_args.kwargs["params"] == {"artist": "Queen", "title": "Bohemian Rhapsody"}
    assert get.call_args.kwargs["headers"]["x-rapidapi-host"] == "musixmatch-song-lyrics-api.p.rapidapi.com"


def test_miss_reported_as_200_with_data_error_is_not_found(caplog):
    with patch("karaoke_gen.lyrics_transcriber.lyrics.musixmatch.requests.get", return_value=_response(MISS)):
        with caplog.at_level(logging.INFO, logger="test_mxm"):
            assert _provider()._fetch_data_from_source("Nobody", "Nothing") is None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_empty_lyrics_is_not_found():
    body = {"data": dict(HIT["data"], lyrics="  "), "error": False, "status": 200}
    with patch("karaoke_gen.lyrics_transcriber.lyrics.musixmatch.requests.get", return_value=_response(body)):
        assert _provider()._fetch_data_from_source("Queen", "Bohemian Rhapsody") is None


def test_no_key_skips_request():
    provider = MusixmatchProvider(LyricsProviderConfig(), logger=logging.getLogger("test_mxm"))
    with patch("karaoke_gen.lyrics_transcriber.lyrics.musixmatch.requests.get") as get:
        assert provider._fetch_data_from_source("Queen", "Bohemian Rhapsody") is None
    get.assert_not_called()


def test_converts_v2_data_to_lyrics():
    result = _provider()._convert_result_format(HIT["data"])
    assert result.source == "musixmatch"
    assert result.metadata.track_name == "Bohemian Rhapsody"
    assert result.metadata.artist_names == "Queen"
    assert result.metadata.lyrics_provider_id == "460875706"
    words = [w.text for seg in result.segments for w in seg.words]
    assert words[:4] == ["Is", "this", "the", "real"]
    assert len(result.segments) == 2


def test_converts_legacy_cached_raw_response():
    legacy = {"message": {"body": {"macro_calls": {
        "matcher.track.get": {"message": {"body": {"track": {"track_name": "Song", "artist_name": "Artist", "track_id": 7}}}},
        "track.lyrics.get": {"message": {"body": {"lyrics": {"lyrics_body": "line one\nline two"}}}},
    }}}}
    result = _provider()._convert_result_format(legacy)
    assert result.metadata.track_name == "Song" and result.metadata.lyrics_provider_id == "7"
    assert len(result.segments) == 2
