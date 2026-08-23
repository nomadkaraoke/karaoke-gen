"""Tests for full-locale capture (admin language visibility feature).

`get_full_locale_from_request` must return the user's real UI language subtag
(any of 33), NOT the narrowed en/es/de used for email rendering.
"""
from types import SimpleNamespace

import pytest

from backend.i18n import get_full_locale_from_request, get_locale_from_request


def _req(accept_language: str | None):
    headers = {}
    if accept_language is not None:
        headers["accept-language"] = accept_language
    return SimpleNamespace(headers=headers)


@pytest.mark.parametrize(
    "header,expected",
    [
        ("pt-BR,pt;q=0.9,en;q=0.8", "pt"),
        ("ja", "ja"),
        ("zh-Hans-CN", "zh"),
        ("EN-US", "en"),
        ("de-DE,de;q=0.9", "de"),
        ("ko,en;q=0.5", "ko"),
        ("  fr ; q=1.0 ", "fr"),
    ],
)
def test_returns_full_primary_subtag(header, expected):
    assert get_full_locale_from_request(_req(header)) == expected


def test_missing_header_returns_none():
    assert get_full_locale_from_request(_req(None)) is None
    assert get_full_locale_from_request(_req("")) is None


def test_wildcard_only_returns_none():
    assert get_full_locale_from_request(_req("*")) is None


def test_does_not_narrow_to_email_locales():
    # A Portuguese user: full capture keeps "pt"; the email helper collapses to "en".
    req = _req("pt-BR,pt;q=0.9")
    assert get_full_locale_from_request(req) == "pt"
    assert get_locale_from_request(req) == "en"


def test_skips_unparseable_leading_entry():
    # Leading empty/garbage token is skipped in favour of the next valid one.
    assert get_full_locale_from_request(_req(",,vi-VN")) == "vi"
