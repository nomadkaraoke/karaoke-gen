"""Preview of the translated-lyrics layout for the job-creation option.

Renders one karaoke frame of the default theme (same renderer as production, via
``theme_preview_service``) with sample lyrics and their translation in the chosen
language. Translations come from ``translation_preview_samples`` (generated once,
committed), so the preview never calls the LLM.

Each frame is a 4K ffmpeg render (~20s, ~1 GB peak). Two concurrent renders for
different languages got the API instance OOM-killed (10-09, 10-16), 503ing every
in-flight request. So renders are serialised process-wide, and each rendered frame
is kept in GCS (keyed by a digest of the theme style + sample) so a language is
rendered once per theme change, not once per cold instance.
"""
import base64
import hashlib
import json
import logging
import tempfile
import threading
from collections import OrderedDict
from typing import Optional

from backend.services.lyrics_translation import normalize_language
from backend.services.theme_preview_service import render_karaoke_frame, resolve_assets
from backend.services.theme_service import get_theme_service
from backend.services.translation_preview_samples import SAMPLES

logger = logging.getLogger(__name__)

FALLBACK_THEME_ID = "nomad"
_CACHE: "OrderedDict[tuple, str]" = OrderedDict()
_CACHE_MAX = 40
_lock = threading.Lock()
_key_locks: "dict[tuple, threading.Lock]" = {}  # bounded: themes x 33 languages
_render_slot = threading.BoundedSemaphore(1)  # one 4K render at a time per instance
GCS_PREFIX = "themes/_translation_previews"


def render_translation_preview(language: str, theme_id: Optional[str] = None) -> str:
    """JPEG data URL of a karaoke frame with translations in ``language``."""
    code = normalize_language(language)
    if code is None or code not in SAMPLES:
        raise ValueError(f"Unsupported language: {language}")
    theme_service = get_theme_service()
    theme_id = theme_id or theme_service.get_default_theme_id() or FALLBACK_THEME_ID
    key = (theme_id, code)
    with _lock:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
        key_lock = _key_locks.setdefault(key, threading.Lock())
    # One render per key: concurrent cold requests wait for it instead of each running ffmpeg
    with key_lock:
        with _lock:
            if key in _CACHE:
                _CACHE.move_to_end(key)
                return _CACHE[key]
        return _render_and_cache(key, theme_service, theme_id, code)


def _gcs_path(theme_id: str, code: str, style_params: dict) -> str:
    digest = hashlib.sha256(
        json.dumps([style_params, SAMPLES[code]], sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return f"{GCS_PREFIX}/{theme_id}/{code}-{digest}.jpg"


def _gcs_get(path: str) -> Optional[bytes]:
    try:
        from google.cloud.exceptions import NotFound

        from backend.services.storage_service import StorageService

        try:
            return StorageService().bucket.blob(path).download_as_bytes()
        except NotFound:
            return None
    except Exception as e:  # cache read is best-effort; fall back to rendering
        logger.warning(f"Translation preview cache read failed for {path}: {e}")
        return None


def _gcs_put(path: str, jpeg: bytes) -> None:
    try:
        from backend.services.storage_service import StorageService

        StorageService().bucket.blob(path).upload_from_string(jpeg, content_type="image/jpeg")
    except Exception as e:  # best-effort; the next cold instance just renders again
        logger.warning(f"Translation preview cache write failed for {path}: {e}")


def _render_and_cache(key: tuple, theme_service, theme_id: str, code: str) -> str:
    style_params = theme_service.get_theme_style_params(theme_id)
    if style_params is None:
        raise ValueError(f"Theme not found: {theme_id}")
    path = _gcs_path(theme_id, code, style_params)
    jpeg = _gcs_get(path)
    if jpeg is None:
        styles = resolve_assets(style_params, theme_id)
        sample = SAMPLES[code]
        with _render_slot:
            with tempfile.TemporaryDirectory(prefix="translation-preview-") as workdir:
                jpeg = render_karaoke_frame(styles, sample["lyrics"], workdir, translations=sample["translations"])
        _gcs_put(path, jpeg)
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    with _lock:
        _CACHE[key] = data_url
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return data_url
