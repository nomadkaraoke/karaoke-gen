"""lrclib_client: registered-app auth headers + LRCLIB's rate-limit rules."""
import logging
from unittest.mock import MagicMock, patch

import pytest

from karaoke_gen.lyrics_transcriber.lyrics import lrclib_client


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(lrclib_client, "_credentials_rejected", False)
    monkeypatch.setenv("LRCLIB_APP_ID", "app-1")
    monkeypatch.setenv("LRCLIB_SECRET_KEY", "secret-1")
    sleeps = []
    monkeypatch.setattr(lrclib_client.time, "sleep", sleeps.append)
    return sleeps


def _resp(status, headers=None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    return r


def _get(*responses):
    session = MagicMock()
    session.get.side_effect = list(responses)
    response = lrclib_client.get("https://lrclib.net/api/get", {"q": "x"}, user_agent="ua",
                                 logger=logging.getLogger("t"), session=session)
    return response, session


def test_sends_app_id_and_secret_key_with_user_agent():
    response, session = _get(_resp(200))
    assert response.status_code == 200
    headers = session.get.call_args.kwargs["headers"]
    assert headers == {"User-Agent": "ua", "Lrclib-App-Id": "app-1", "Lrclib-Api-Key": "secret-1"}


def test_anonymous_when_not_configured(monkeypatch):
    monkeypatch.delenv("LRCLIB_APP_ID")
    monkeypatch.delenv("LRCLIB_SECRET_KEY")
    _, session = _get(_resp(200))
    assert session.get.call_args.kwargs["headers"] == {"User-Agent": "ua"}


def test_honours_retry_after_with_jitter_on_429(_reset):
    response, session = _get(_resp(429, {"Retry-After": "4"}), _resp(200))
    assert response.status_code == 200 and session.get.call_count == 2
    assert 4.0 <= _reset[0] <= 4.0 * 1.3


def test_retry_after_is_capped(_reset):
    _get(_resp(429, {"Retry-After": "600"}), _resp(200))
    assert _reset == [lrclib_client.MAX_RETRY_AFTER_S]


def test_second_429_is_returned_not_retried_forever():
    response, session = _get(_resp(429, {"Retry-After": "1"}), _resp(429, {"Retry-After": "1"}))
    assert response.status_code == 429 and session.get.call_count == 2


def test_server_errors_retry_twice_with_backoff(_reset):
    response, session = _get(_resp(503), _resp(503), _resp(200))
    assert response.status_code == 200 and session.get.call_count == 3
    assert _reset == list(lrclib_client.SERVER_ERROR_BACKOFF_S)


def test_persistent_server_error_is_returned():
    response, session = _get(_resp(503), _resp(502), _resp(503))
    assert response.status_code == 503 and session.get.call_count == 3


def test_rejected_credentials_fall_back_to_anonymous_for_the_process(caplog):
    with caplog.at_level(logging.WARNING, logger="t"):
        response, session = _get(_resp(401), _resp(200))
    assert response.status_code == 200
    assert "Lrclib-Api-Key" not in session.get.call_args.kwargs["headers"]
    assert lrclib_client.auth_headers() == {}
    assert "rejected our app credentials" in caplog.text


def test_anonymous_401_is_returned_not_looped(monkeypatch):
    monkeypatch.delenv("LRCLIB_APP_ID")
    monkeypatch.delenv("LRCLIB_SECRET_KEY")
    response, session = _get(_resp(401))
    assert response.status_code == 401 and session.get.call_count == 1


def test_provider_requests_go_through_the_client():
    from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import LyricsProviderConfig
    from karaoke_gen.lyrics_transcriber.lyrics.lrclib import LRCLIBProvider

    hit = _resp(200)
    hit.json.return_value = [{"trackName": "Iowa", "artistName": "Dar Williams", "plainLyrics": "x"}]
    with patch.object(lrclib_client, "requests") as req:
        req.get.return_value = hit
        result = LRCLIBProvider(LyricsProviderConfig(), logger=logging.getLogger("t"))._fetch_from_search("Dar Williams", "Iowa")
    assert result["trackName"] == "Iowa"
    assert req.get.call_args.kwargs["headers"]["Lrclib-Api-Key"] == "secret-1"
