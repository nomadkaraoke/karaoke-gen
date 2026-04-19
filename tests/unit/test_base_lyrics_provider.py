"""Tests for BaseLyricsProvider, specifically the MAX_LYRICS_LENGTH truncation guard."""

import logging
from typing import Optional, Dict, Any
from unittest.mock import MagicMock

import requests

from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.types import LyricsData


class StubProvider(BaseLyricsProvider):
    """Minimal concrete subclass for testing base class methods."""

    def _fetch_data_from_source(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        return None

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        raise NotImplementedError


class TestCreateSegmentsWithWords:
    def _make_provider(self) -> StubProvider:
        config = LyricsProviderConfig()
        return StubProvider(config, logger=logging.getLogger("test"))

    def test_short_text_unchanged(self):
        provider = self._make_provider()
        text = "Hello world\nThis is a song\nLa la la"
        segments = provider._create_segments_with_words(text)
        assert len(segments) == 3
        assert segments[0].text == "Hello world"

    def test_text_at_limit_unchanged(self):
        provider = self._make_provider()
        # Build text exactly at the limit
        line = "A" * 49 + "\n"  # 50 chars per line including newline
        text = (line * (BaseLyricsProvider.MAX_LYRICS_LENGTH // 50)).rstrip()
        assert len(text) <= BaseLyricsProvider.MAX_LYRICS_LENGTH
        segments = provider._create_segments_with_words(text)
        # All lines should be present
        assert len(segments) == BaseLyricsProvider.MAX_LYRICS_LENGTH // 50

    def test_long_text_truncated(self):
        provider = self._make_provider()
        # Create text well over the limit (simulating a screenplay)
        lines = [f"Line number {i} of the screenplay" for i in range(500)]
        text = "\n".join(lines)
        assert len(text) > BaseLyricsProvider.MAX_LYRICS_LENGTH

        segments = provider._create_segments_with_words(text)
        # Should have far fewer segments than the original 500 lines
        total_text = "\n".join(s.text for s in segments)
        assert len(total_text) <= BaseLyricsProvider.MAX_LYRICS_LENGTH

    def test_truncation_preserves_complete_lines(self):
        provider = self._make_provider()
        lines = [f"Line {i}: some lyrics text here" for i in range(200)]
        text = "\n".join(lines)

        segments = provider._create_segments_with_words(text)
        # Each segment should be a complete line (not cut mid-word)
        for seg in segments:
            assert seg.text.startswith("Line ")

    def test_truncation_logs_warning(self, caplog):
        provider = self._make_provider()
        text = "x\n" * 5000  # Way over limit
        with caplog.at_level(logging.WARNING):
            provider._create_segments_with_words(text)
        assert "truncating" in caplog.text.lower()


class TestLogRequestException:
    """_log_request_exception must downgrade upstream-transient failures to WARNING.

    5xx / ConnectionError / Timeout aren't actionable bugs in our code — they're
    upstream availability. Logging them as ERROR creates alert noise in the
    production error monitor since the orchestrator already falls back to other
    providers.
    """

    def _make_provider(self) -> "StubProvider":
        return StubProvider(LyricsProviderConfig(), logger=logging.getLogger("test_log_request"))

    def _http_error(self, status_code: int) -> requests.exceptions.HTTPError:
        response = MagicMock()
        response.status_code = status_code
        return requests.exceptions.HTTPError(f"{status_code} error", response=response)

    def test_502_logs_warning(self, caplog):
        provider = self._make_provider()
        with caplog.at_level(logging.DEBUG):
            provider._log_request_exception(self._http_error(502), "Musixmatch API")
        records = [r for r in caplog.records if "Musixmatch API" in r.message]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING

    def test_503_logs_warning(self, caplog):
        provider = self._make_provider()
        with caplog.at_level(logging.DEBUG):
            provider._log_request_exception(self._http_error(503), "LRCLIB")
        records = [r for r in caplog.records if "LRCLIB" in r.message]
        assert records[0].levelno == logging.WARNING

    def test_404_logs_error(self, caplog):
        provider = self._make_provider()
        with caplog.at_level(logging.DEBUG):
            provider._log_request_exception(self._http_error(404), "Genius RapidAPI")
        records = [r for r in caplog.records if "Genius RapidAPI" in r.message]
        assert records[0].levelno == logging.ERROR

    def test_timeout_logs_warning(self, caplog):
        provider = self._make_provider()
        exc = requests.exceptions.Timeout("read timeout")
        with caplog.at_level(logging.DEBUG):
            provider._log_request_exception(exc, "Spotify RapidAPI")
        records = [r for r in caplog.records if "Spotify RapidAPI" in r.message]
        assert records[0].levelno == logging.WARNING

    def test_connection_error_logs_warning(self, caplog):
        provider = self._make_provider()
        exc = requests.exceptions.ConnectionError("DNS failure")
        with caplog.at_level(logging.DEBUG):
            provider._log_request_exception(exc, "Musixmatch API")
        records = [r for r in caplog.records if "Musixmatch API" in r.message]
        assert records[0].levelno == logging.WARNING

    def test_generic_request_exception_logs_error(self, caplog):
        provider = self._make_provider()
        exc = requests.exceptions.RequestException("unknown")
        with caplog.at_level(logging.DEBUG):
            provider._log_request_exception(exc, "Generic")
        records = [r for r in caplog.records if "Generic" in r.message]
        assert records[0].levelno == logging.ERROR
