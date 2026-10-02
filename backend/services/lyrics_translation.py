"""Translated lyrics: an LLM translation of each reviewed lyric line.

A job created with ``translation_language`` gets its final (post-review) lyrics
translated once, just before the karaoke video renders. The result is stored at
``jobs/{job_id}/lyrics/translations.json`` (format in
``karaoke_gen.lyrics_transcriber.output.translations``), which the renderers draw in
smaller text beneath each line. Translations aren't editable; they're regenerated
from the reviewed text, and identical requests are served from a GCS cache (so a
re-render costs no new LLM call).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from backend.config import get_settings

logger = logging.getLogger(__name__)

# Languages a user can pick: the app's UI locales (code -> English name for the prompt).
TRANSLATION_LANGUAGES: Dict[str, str] = {
    "ar": "Arabic",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "nb": "Norwegian (Bokmål)",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sv": "Swedish",
    "th": "Thai",
    "tl": "Filipino (Tagalog)",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Simplified Chinese",
}

TRANSLATIONS_GCS_NAME = "translations.json"
CACHE_PREFIX = "lyrics-translation-cache"
CACHE_VERSION = 1
MAX_ATTEMPTS = 2
BACKOFF_SECONDS = 2.0
# More than this share of lines coming back unchanged = the song is already in the
# target language, so a translation row would just repeat the lyrics.
SAME_LANGUAGE_THRESHOLD = 0.8

SYSTEM_PROMPT = """You translate song lyrics for karaoke videos. Each translated line is shown \
in small text directly beneath the original line while the singer sings it, so language \
learners understand what they are singing.

Rules:
- Return exactly one translation per input line, in the same order (same count).
- Translate the meaning naturally and idiomatically; it does not need to rhyme or be singable.
- A sentence often continues across lines: keep each translation aligned with the words of \
its own line where the target grammar allows, so the meaning lines up with what is being sung.
- Keep each translation about as short as its line; no explanations, notes, quotes or brackets.
- Keep names, places and brand names. Keep pure vocables (oh, ooh, la la, yeah, na na) as they are.
- If a line is already in the target language, return it unchanged.
"""


class LyricsTranslationError(Exception):
    """The lyrics couldn't be translated (model error or malformed output)."""


def normalize_language(code: Optional[str]) -> Optional[str]:
    """Supported language code for ``code`` (case/region-insensitive), else None."""
    if not code:
        return None
    base = str(code).strip().lower().replace("_", "-")
    if base in TRANSLATION_LANGUAGES:
        return base
    base = base.split("-")[0]
    if base == "no":
        base = "nb"
    if base == "fil":
        base = "tl"
    return base if base in TRANSLATION_LANGUAGES else None


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def is_same_language(lines: List[str], translations: List[str]) -> bool:
    """True when nearly every line came back unchanged (song already in that language)."""
    pairs = [(a, b) for a, b in zip(lines, translations) if _norm(a)]
    if not pairs:
        return False
    unchanged = sum(1 for a, b in pairs if _norm(a) == _norm(b))
    return unchanged / len(pairs) >= SAME_LANGUAGE_THRESHOLD


def _user_prompt(lines: List[str], language: str, artist: Optional[str], title: Optional[str]) -> str:
    song = " - ".join(p for p in (artist, title) if p) or "unknown song"
    numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    return (
        f"Song: {song}\n"
        f"Target language: {TRANSLATION_LANGUAGES[language]}\n"
        f"Translate these {len(lines)} lyric lines. Return JSON "
        f'{{"translations": [...]}} with exactly {len(lines)} strings, one per line, '
        f"without the line numbers.\n\n{numbered}"
    )


def _is_transient(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in {429, 500, 502, 503, 504}:
        return True
    text = str(exc).upper()
    return any(m in text for m in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE", "429", "503"))


class LyricsTranslationService:
    def __init__(self, storage=None, model: Optional[str] = None):
        self.settings = get_settings()
        self.model = model or self.settings.lyrics_translation_model
        self._storage = storage

    def _get_storage(self):
        if self._storage is None:
            from backend.services.storage_service import StorageService

            self._storage = StorageService()
        return self._storage

    # ---- cache (GCS, best-effort) ----

    def _cache_path(self, lines: List[str], language: str, artist: Optional[str], title: Optional[str]) -> str:
        payload = {"v": CACHE_VERSION, "model": self.model, "language": language,
                   "lines": lines, "artist": artist, "title": title}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:32]
        return f"{CACHE_PREFIX}/{digest}.json"

    def _cache_get(self, path: str, n: int) -> Optional[List[str]]:
        try:
            storage = self._get_storage()
            if not storage.file_exists(path):
                return None
            data = storage.download_json(path)
            translations = data.get("translations")
            if isinstance(translations, list) and len(translations) == n:
                return [str(t) for t in translations]
        except Exception:
            logger.warning("lyrics translation cache read failed for %s", path, exc_info=True)
        return None

    def _cache_put(self, path: str, translations: List[str]) -> None:
        try:
            self._get_storage().upload_json(path, {"model": self.model, "translations": translations})
        except Exception:
            logger.warning("lyrics translation cache write failed for %s", path, exc_info=True)

    # ---- model ----

    def _call_gemini(self, user_prompt: str) -> Any:
        from google.genai import types

        from backend.services.gemini_client import get_genai_client

        client = get_genai_client(timeout_ms=90_000)  # 2 attempts: render waits <= ~3 min
        response = client.models.generate_content(
            model=self.model,
            contents=[user_prompt],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
                response_mime_type="application/json",
                response_schema={
                    "type": "object",
                    "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
                    "required": ["translations"],
                },
            ),
        )
        if response.text is None:
            raise LyricsTranslationError("model returned an empty response")
        return json.loads(response.text)

    def _translate_once(self, lines: List[str], language: str, artist, title) -> List[str]:
        data = self._call_gemini(_user_prompt(lines, language, artist, title))
        translations = data.get("translations") if isinstance(data, dict) else None
        if not isinstance(translations, list) or len(translations) != len(lines):
            got = len(translations) if isinstance(translations, list) else "no"
            raise LyricsTranslationError(f"expected {len(lines)} translations, got {got}")
        return [" ".join(str(t).split()) for t in translations]

    def translate_lines(
        self,
        lines: List[str],
        language: str,
        *,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> List[str]:
        """One translation per line (same order). Raises LyricsTranslationError."""
        lang = normalize_language(language)
        if lang is None:
            raise LyricsTranslationError(f"unsupported language {language!r}")
        if not lines:
            return []
        cache_path = self._cache_path(lines, lang, artist, title)
        cached = self._cache_get(cache_path, len(lines))
        if cached is not None:
            logger.info("lyrics translation cache hit (%d lines, %s)", len(lines), lang)
            return cached

        last_error: Optional[BaseException] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                started = time.time()
                translations = self._translate_once(lines, lang, artist, title)
                logger.info(
                    "lyrics translation: %d lines -> %s via %s in %.1fs",
                    len(lines), lang, self.model, time.time() - started,
                )
                self._cache_put(cache_path, translations)
                return translations
            except (LyricsTranslationError, ValueError) as exc:  # malformed output: retry
                last_error = exc
            except Exception as exc:
                from backend.services.gemini_client import note_gemini_failure

                if note_gemini_failure("lyrics_translation", exc):
                    # Quota/credit/key problem: don't retry; the render goes ahead
                    # without the translation row.
                    raise LyricsTranslationError(
                        "translation service unavailable (Gemini quota/billing) — rendered without translation"
                    ) from exc
                if not _is_transient(exc):
                    raise LyricsTranslationError(f"translation model error: {exc}") from exc
                last_error = exc
            if attempt < MAX_ATTEMPTS:
                backoff = BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "lyrics translation attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt, MAX_ATTEMPTS, last_error, backoff,
                )
                time.sleep(backoff)
        raise LyricsTranslationError(f"translation failed after {MAX_ATTEMPTS} attempts: {last_error}")


def load_final_segments(storage, job_id: str):
    """The reviewed lyric segments the video renders (corrections + review edits)."""
    from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
    from karaoke_gen.lyrics_transcriber.types import CorrectionResult

    base = CorrectionResult.from_dict(storage.download_json(f"jobs/{job_id}/lyrics/corrections.json"))
    updated_path = f"jobs/{job_id}/lyrics/corrections_updated.json"
    if storage.file_exists(updated_path):
        updated = storage.download_json(updated_path)
        if isinstance(updated, dict) and "corrections" in updated:
            return CorrectionOperations.update_correction_result_with_data(base, updated).corrected_segments
    return base.corrected_segments


def prepare_job_translations(
    job_id: str, job, storage, job_manager=None, service: Optional[LyricsTranslationService] = None
) -> Optional[str]:
    """Translate a job's final lyrics and store ``translations.json``.

    Returns the GCS path (relative to the bucket) when the video should show
    translations, else None: no language requested, the song is already in that
    language, or translation failed. A failure never blocks the render; it's logged as
    an error and recorded in ``state_data.lyrics_translation`` so an admin can
    re-render once fixed.
    """
    stored_path = f"jobs/{job_id}/lyrics/{TRANSLATIONS_GCS_NAME}"
    language = normalize_language(getattr(job, "translation_language", None))
    if not language:
        _remove_stale(storage, stored_path)
        if job_manager is not None and (getattr(job, "state_data", None) or {}).get("lyrics_translation"):
            # Translation was turned off since a previous render: clear the old outcome
            # so the republished video isn't labelled "(With Translation into X)".
            try:
                job_manager.update_state_data(job_id, "lyrics_translation", {"status": "off"})
            except Exception:
                logger.warning(f"[job:{job_id}] could not clear lyrics_translation status", exc_info=True)
        return None
    status: Dict[str, Any] = {"language": language}
    path: Optional[str] = None
    try:
        segments = [s for s in load_final_segments(storage, job_id) if (s.text or "").strip()]
        lines = [s.text.strip() for s in segments]
        service = service or LyricsTranslationService(storage=storage)
        translations = service.translate_lines(lines, language, artist=job.artist, title=job.title)
        status.update(model=service.model, lines=len(lines))
        if is_same_language(lines, translations):
            logger.info(f"[job:{job_id}] Lyrics already in {language}; no translation row")
            status["status"] = "same_language"
        else:
            path = stored_path
            storage.upload_json(path, {
                "language": language,
                "language_name": TRANSLATION_LANGUAGES[language],
                "model": service.model,
                "lines": [
                    {"segment_id": s.id, "text": line, "translation": tr}
                    for s, line, tr in zip(segments, lines, translations)
                ],
            })
            status["status"] = "translated"
    except Exception as exc:
        logger.error(f"[job:{job_id}] Lyrics translation to {language} failed; rendering without it: {exc}", exc_info=True)
        status.update(status="failed", error=str(exc)[:500])
        path = None
    if path is None:
        # The encoder also reads lyrics/translations.json for the portrait video
        _remove_stale(storage, stored_path)
    if job_manager is not None:
        try:
            job_manager.update_state_data(job_id, "lyrics_translation", status)
        except Exception:
            logger.warning(f"[job:{job_id}] could not record lyrics_translation status", exc_info=True)
    return path


def _remove_stale(storage, path: str) -> None:
    """Delete a translations file left by an earlier render (best-effort)."""
    try:
        storage.delete_file(path, ignore_missing=True)
    except Exception:
        logger.warning("could not delete stale %s", path, exc_info=True)
