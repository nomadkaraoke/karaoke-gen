"""Log levels on the Spotify RapidAPI provider: ERROR pages a human, so a 403 on the
/lyrics step (the search just succeeded with the same key, other providers fill in)
is a WARNING, while 401/403 on /search stays the dead-key ERROR."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.lyrics.spotify import SpotifyProvider

SEARCH_OK = {"status": True, "errorId": "Success", "id": "track1"}


def _response(status, body=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = body or {}
    if status >= 400:
        err = requests.exceptions.HTTPError(f"{status} Client Error", response=resp)
        resp.raise_for_status.side_effect = err
    return resp


def _provider():
    return SpotifyProvider(LyricsProviderConfig(rapidapi_key="test-key"), logger=logging.getLogger("spotify-test"))


def _levels(caplog):
    return [r.levelno for r in caplog.records if r.name == "spotify-test"]


def test_lyrics_403_is_a_warning_with_the_body(caplog):
    long_body = "not subscribed " + "x" * 500
    with patch("requests.get", side_effect=[_response(200, SEARCH_OK), _response(403, text=long_body)]):
        with caplog.at_level(logging.INFO, logger="spotify-test"):
            assert _provider()._fetch_from_rapidapi("A", "T") is None
    assert logging.ERROR not in _levels(caplog)
    warning = next(r.message for r in caplog.records if r.levelno == logging.WARNING)
    assert "not subscribed" in warning
    assert "x" * 201 not in warning


@pytest.mark.parametrize("status", [401, 403])
def test_search_auth_failure_stays_an_error(caplog, status):
    with patch("requests.get", side_effect=[_response(status)]):
        with caplog.at_level(logging.INFO, logger="spotify-test"):
            assert _provider()._fetch_from_rapidapi("A", "T") is None
    assert logging.ERROR in _levels(caplog)


def test_lyrics_401_stays_an_error(caplog):
    with patch("requests.get", side_effect=[_response(200, SEARCH_OK), _response(401)]):
        with caplog.at_level(logging.INFO, logger="spotify-test"):
            assert _provider()._fetch_from_rapidapi("A", "T") is None
    assert logging.ERROR in _levels(caplog)


def test_lyrics_success_still_returns_data():
    with patch("requests.get", side_effect=[_response(200, SEARCH_OK), _response(200, {"lyrics": []})]):
        result = _provider()._fetch_from_rapidapi("A", "T")
    assert result["_rapidapi_source"] is True
