"""
Lightweight i18n utility for backend translations.

Usage:
    from backend.i18n import t, get_locale_from_request, SUPPORTED_LOCALES

    # In a route handler:
    locale = get_locale_from_request(request)
    message = t(locale, "auth.userNotFound")
    message = t(locale, "jobs.notFound", job_id="abc123")
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from starlette.requests import Request

SUPPORTED_LOCALES = ("en", "es", "de")
DEFAULT_LOCALE = "en"
TRANSLATIONS_DIR = Path(__file__).parent / "translations"


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def _load_translations(locale: str) -> dict[str, Any]:
    """Load and cache translation file for a locale."""
    path = TRANSLATIONS_DIR / f"{locale}.json"
    if not path.exists():
        if locale != DEFAULT_LOCALE:
            return _load_translations(DEFAULT_LOCALE)
        return {}
    with open(path, encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
        return result


def t(locale: str, key: str, **kwargs: Any) -> str:
    """Get a translated string by dotted key path.

    Args:
        locale: Language code (en, es, de)
        key: Dotted path like "auth.userNotFound" or "emails.magicLink.subject"
        **kwargs: Interpolation variables (e.g., job_id="abc123")

    Returns:
        Translated string with variables interpolated, or the key itself if not found.
    """
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE

    translations = _load_translations(locale)

    # Walk the dotted path
    value: Any = translations
    for part in key.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            # Key not found — fall back to English
            if locale != DEFAULT_LOCALE:
                return t(DEFAULT_LOCALE, key, **kwargs)
            return key  # Return the key itself as last resort

    if not isinstance(value, str):
        return key

    result: str = value
    # Interpolate variables
    if kwargs:
        try:
            result = result.format(**kwargs)
        except KeyError:
            pass  # Missing variable — return template as-is

    return result


def get_locale_from_request(request: Request) -> str:
    """Extract locale from Accept-Language header.

    Returns the first supported locale found, or DEFAULT_LOCALE.
    """
    accept_lang = request.headers.get("accept-language", "")

    # Simple parsing — Accept-Language can be "de", "de-DE", "en-US,en;q=0.9,de;q=0.8"
    for part in accept_lang.split(","):
        lang = part.split(";")[0].strip().split("-")[0].lower()
        if lang in SUPPORTED_LOCALES:
            return lang

    return DEFAULT_LOCALE


def get_locale_prefix(locale: str) -> str:
    """Get URL locale prefix for frontend URLs (e.g., '/en', '/es')."""
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE
    return f"/{locale}"
