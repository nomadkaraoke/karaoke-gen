"""Preview of the translated-lyrics layout for the job-creation option.

Renders one karaoke frame of the default theme (same renderer as production, via
``theme_preview_service``) with sample lyrics and their translation in the chosen
language. Translations come from ``translation_preview_samples`` (generated once,
committed), so the preview never calls the LLM.
"""
import base64
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

    style_params = theme_service.get_theme_style_params(theme_id)
    if style_params is None:
        raise ValueError(f"Theme not found: {theme_id}")
    styles = resolve_assets(style_params, theme_id)
    sample = SAMPLES[code]
    with tempfile.TemporaryDirectory(prefix="translation-preview-") as workdir:
        jpeg = render_karaoke_frame(styles, sample["lyrics"], workdir, translations=sample["translations"])
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    with _lock:
        _CACHE[key] = data_url
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return data_url
