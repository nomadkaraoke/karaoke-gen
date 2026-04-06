"""
GCS-backed translation cache for Nomad Karaoke i18n.

Caches translations by sha256(english_string)[:16] per locale.
Shared across all repos via a GCS bucket.

Usage:
    cache = TranslationCache(bucket_name="nomadkaraoke-translation-cache")
    cache.download("es")
    result = cache.lookup("Loading...", "es")
    cache.store("New string", "es", "Nueva cadena")
    cache.upload("es")
"""

import hashlib
import json

try:
    from google.cloud import storage
    from google.api_core.exceptions import NotFound
except ImportError:
    storage = None
    NotFound = Exception


def string_hash(text: str) -> str:
    """Return first 16 hex chars of SHA-256 of the text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class TranslationCache:
    def __init__(self, bucket_name: str = "nomadkaraoke-translation-cache", enabled: bool = True):
        self._bucket_name = bucket_name
        self._enabled = enabled and storage is not None
        self._data: dict[str, dict[str, str]] = {}
        self._stats: dict[str, dict[str, int]] = {}
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = storage.Client()
        return self._client

    def download(self, locale: str) -> None:
        if not self._enabled:
            return
        try:
            client = self._get_client()
            bucket = client.bucket(self._bucket_name)
            blob = bucket.blob(f"cache/{locale}.json")
            data = json.loads(blob.download_as_text())
            self._data.setdefault(locale, {}).update(data)
        except NotFound:
            pass
        except Exception as e:
            print(f"  Warning: Could not download cache for {locale}: {e}")

    def upload(self, locale: str) -> None:
        if not self._enabled:
            return
        if locale not in self._data:
            return
        try:
            client = self._get_client()
            bucket = client.bucket(self._bucket_name)
            blob = bucket.blob(f"cache/{locale}.json")
            blob.upload_from_string(
                json.dumps(self._data[locale], ensure_ascii=False, sort_keys=True),
                content_type="application/json",
            )
        except Exception as e:
            print(f"  Warning: Could not upload cache for {locale}: {e}")

    def lookup(self, english_text: str, locale: str) -> str | None:
        h = string_hash(english_text)
        locale_data = self._data.get(locale, {})
        result = locale_data.get(h)
        stats = self._stats.setdefault(locale, {"hits": 0, "misses": 0})
        if result is not None:
            stats["hits"] += 1
        else:
            stats["misses"] += 1
        return result

    def store(self, english_text: str, locale: str, translation: str) -> None:
        h = string_hash(english_text)
        self._data.setdefault(locale, {})[h] = translation

    def stats(self, locale: str) -> dict[str, int]:
        return self._stats.get(locale, {"hits": 0, "misses": 0})
