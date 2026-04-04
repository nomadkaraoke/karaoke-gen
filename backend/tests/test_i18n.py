"""Tests for backend.i18n translation utility."""

import pytest
from unittest.mock import MagicMock

from backend.i18n import t, get_locale_from_request, get_locale_prefix, SUPPORTED_LOCALES, DEFAULT_LOCALE, _load_translations


class TestTranslationFunction:
    """Tests for t() function."""

    def test_returns_english_string(self):
        result = t("en", "jobs.notFound")
        assert result == "Job not found"

    def test_dotted_key_path(self):
        result = t("en", "pushNotifications.reviewLyricsTitle")
        assert result == "Review Lyrics"

    def test_interpolation(self):
        result = t("en", "jobs.notCompleted", status="downloading")
        assert result == "Only completed jobs can be edited. Current status: downloading"

    def test_multiple_interpolation_vars(self):
        result = t("en", "jobs.durationMismatch", upload_duration="120", original_duration="180")
        assert "120s" in result
        assert "180s" in result

    def test_fallback_to_english_for_missing_key_in_other_locale(self):
        # es.json is a copy of en.json right now, but if a key were missing it should fall back
        result = t("es", "jobs.notFound")
        assert result == "Job not found"

    def test_returns_key_for_nonexistent_key(self):
        result = t("en", "nonexistent.key.path")
        assert result == "nonexistent.key.path"

    def test_unsupported_locale_falls_back_to_english(self):
        result = t("fr", "jobs.notFound")
        assert result == "Job not found"

    def test_empty_locale_falls_back_to_english(self):
        result = t("", "jobs.notFound")
        assert result == "Job not found"

    def test_missing_interpolation_var_returns_template(self):
        # If kwargs are missing, return template string as-is
        result = t("en", "jobs.notCompleted")
        assert "{status}" in result

    def test_all_supported_locales_load(self):
        for locale in SUPPORTED_LOCALES:
            translations = _load_translations(locale)
            assert isinstance(translations, dict)
            assert len(translations) > 0


class TestGetLocaleFromRequest:
    """Tests for get_locale_from_request()."""

    def _make_request(self, accept_language: str = "") -> MagicMock:
        request = MagicMock()
        request.headers = {"accept-language": accept_language}
        return request

    def test_simple_locale(self):
        request = self._make_request("de")
        assert get_locale_from_request(request) == "de"

    def test_locale_with_region(self):
        request = self._make_request("es-MX")
        assert get_locale_from_request(request) == "es"

    def test_quality_weighted_list(self):
        request = self._make_request("fr;q=0.9,de;q=0.8,en;q=0.7")
        # fr is not supported, de is next
        assert get_locale_from_request(request) == "de"

    def test_complex_accept_language(self):
        request = self._make_request("en-US,en;q=0.9,de;q=0.8")
        assert get_locale_from_request(request) == "en"

    def test_unsupported_locale_returns_default(self):
        request = self._make_request("ja")
        assert get_locale_from_request(request) == DEFAULT_LOCALE

    def test_empty_header_returns_default(self):
        request = self._make_request("")
        assert get_locale_from_request(request) == DEFAULT_LOCALE

    def test_missing_header_returns_default(self):
        request = MagicMock()
        request.headers = {}
        assert get_locale_from_request(request) == DEFAULT_LOCALE


class TestGetLocalePrefix:
    """Tests for get_locale_prefix()."""

    def test_english(self):
        assert get_locale_prefix("en") == "/en"

    def test_spanish(self):
        assert get_locale_prefix("es") == "/es"

    def test_german(self):
        assert get_locale_prefix("de") == "/de"

    def test_unsupported_returns_english(self):
        assert get_locale_prefix("fr") == "/en"

    def test_empty_returns_english(self):
        assert get_locale_prefix("") == "/en"
