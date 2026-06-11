"""Auto-correct suggestion service: one whole-song LLM call, validated output."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from google import genai
from google.genai import types

from backend.config import get_settings
from backend.services.auto_correct.prompts import (
    RESPONSE_SCHEMA,
    build_system_prompt,
    build_user_prompt,
)
from backend.services.auto_correct.settings import AutoCorrectSettings

logger = logging.getLogger(__name__)

VALID_OPS = {"replace", "delete", "insert_after"}
VALID_CATEGORIES = {
    "mishearing",
    "grammar",
    "adlib_removal",
    "repeated_chorus_fix",
    "formatting",
    "other",
}


class AutoCorrectServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Suggestion:
    id: str
    op: str  # replace | delete | insert_after
    word_ids: list[str]  # targeted words (replace/delete); [anchor] for insert_after
    segment_ids: list[str]  # segments containing the targeted words
    original_text: str  # current text of the targeted span ("" for insert_after)
    new_text: str  # replacement text ("" for delete)
    reason: str
    category: str
    confidence: float
    # Multi-model compare metadata. In single-model mode models has one
    # entry and consensus == total_models == 1.
    models: list[str] = field(default_factory=list)
    consensus: int = 1
    total_models: int = 1
    # Set when other suggestions target overlapping words with a different
    # outcome — the UI lets the reviewer pick at most one per group.
    conflict_group: Optional[str] = None


@dataclass
class AutoCorrectResult:
    suggestions: list[Suggestion]
    model: str  # comma-joined when compare mode queried several models
    elapsed_seconds: float
    settings_applied: AutoCorrectSettings
    warnings: list[str] = field(default_factory=list)
    # True when served from the per-job GCS cache (no LLM call was made).
    cached: bool = False


class AutoCorrectService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def suggest(
        self,
        *,
        job_id: str,
        segments: list[dict],
        reference_lyrics: dict[str, dict],
        artist: Optional[str],
        title: Optional[str],
        settings: AutoCorrectSettings,
    ) -> AutoCorrectResult:
        if not segments:
            raise AutoCorrectServiceError("segments is empty")
        if not reference_lyrics:
            raise AutoCorrectServiceError(
                "no reference lyrics available — auto-correction needs at least "
                "one internet/reference source to compare against",
                status_code=422,
            )

        # Flat word list mirrors the prompt's global indices.
        flat: list[tuple[str, str, str]] = []  # (word_id, word_text, segment_id)
        for seg in segments:
            if not seg.get("id"):
                raise AutoCorrectServiceError("all segments must have ids")
            for w in seg.get("words") or []:
                if not w.get("id"):
                    raise AutoCorrectServiceError("all words must have ids")
                flat.append((w["id"], w.get("text", ""), seg["id"]))

        system_prompt = build_system_prompt(settings)
        user_prompt = build_user_prompt(
            segments=segments,
            reference_lyrics=reference_lyrics,
            artist=artist,
            title=title,
        )

        models = self._models_for(settings)

        # Re-running on byte-identical input (lyrics, references, settings,
        # models) returns the previous result instead of paying for another
        # LLM call. Word ids are part of the key, so cached suggestions
        # always reference words that still exist.
        cache_path = self._cache_path(
            job_id, models, settings, flat, reference_lyrics, artist, title
        )
        cached = self._cache_get(cache_path)
        if cached is not None:
            logger.info(
                "auto-correct cache hit job=%s suggestions=%d", job_id,
                len(cached.suggestions),
            )
            return cached

        t0 = time.time()
        per_model: dict[str, list[Suggestion]] = {}
        warnings: list[str] = []
        if len(models) == 1:
            raw = self._call_model(models[0], system_prompt, user_prompt, job_id=job_id)
            suggestions, vw = self._validate(raw, flat, settings)
            per_model[models[0]] = suggestions
            warnings.extend(vw)
        else:
            import concurrent.futures as cf

            with cf.ThreadPoolExecutor(max_workers=len(models)) as ex:
                futures = {
                    ex.submit(
                        self._call_model, m, system_prompt, user_prompt, job_id=job_id
                    ): m
                    for m in models
                }
                for fut, m in futures.items():
                    try:
                        raw = fut.result()
                        suggestions, vw = self._validate(raw, flat, settings)
                    except AutoCorrectServiceError as exc:
                        # One model failing (call OR malformed output) must not
                        # abort compare mode; the rest still return results.
                        warnings.append(f"model {m} failed: {exc}")
                        continue
                    per_model[m] = suggestions
                    warnings.extend(f"[{m}] {w}" for w in vw)
            if not per_model:
                raise AutoCorrectServiceError(
                    "all AI models failed", status_code=502
                )

        merged = self._merge(per_model, flat)
        elapsed = time.time() - t0
        model_label = ", ".join(per_model.keys())
        logger.info(
            "auto-correct job=%s models=%s words=%d suggestions=%d dropped_warnings=%d elapsed=%.1fs",
            job_id, model_label, len(flat), len(merged), len(warnings), elapsed,
        )
        result = AutoCorrectResult(
            suggestions=merged,
            model=model_label,
            elapsed_seconds=round(elapsed, 1),
            settings_applied=settings,
            warnings=warnings,
        )
        self._cache_put(cache_path, result)
        return result

    # ---- suggestion cache (GCS, best-effort) ----

    def _get_storage(self):
        if not hasattr(self, "_storage"):
            from backend.services.storage_service import StorageService

            self._storage = StorageService()
        return self._storage

    @staticmethod
    def _cache_path(
        job_id: str,
        models: list[str],
        settings: AutoCorrectSettings,
        flat: list[tuple[str, str, str]],
        reference_lyrics: dict[str, dict],
        artist: Optional[str],
        title: Optional[str],
    ) -> str:
        payload = {
            "v": 1,
            "models": models,
            "settings": settings.to_dict(),
            "words": [[word_id, text] for word_id, text, _seg in flat],
            "references": {
                source: [seg.get("text", "") for seg in (ref.get("segments") or [])]
                for source, ref in sorted((reference_lyrics or {}).items())
            },
            "artist": artist,
            "title": title,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:24]
        return f"jobs/{job_id}/lyrics/auto_correct_cache/{digest}.json"

    def _cache_get(self, path: str) -> Optional[AutoCorrectResult]:
        """Best-effort: any storage/shape problem is a cache miss, never an error."""
        try:
            storage = self._get_storage()
            if not storage.bucket.blob(path).exists():
                return None
            data = storage.download_json(path)
            return AutoCorrectResult(
                suggestions=[Suggestion(**s) for s in data["suggestions"]],
                model=data["model"],
                elapsed_seconds=0.0,
                settings_applied=AutoCorrectSettings(**data["settings_applied"]),
                warnings=list(data.get("warnings") or []),
                cached=True,
            )
        except Exception:
            logger.warning("auto-correct cache read failed for %s", path, exc_info=True)
            return None

    def _cache_put(self, path: str, result: AutoCorrectResult) -> None:
        """Best-effort: a failed cache write never fails the request."""
        try:
            self._get_storage().upload_json(
                path,
                {
                    "suggestions": [asdict(s) for s in result.suggestions],
                    "model": result.model,
                    "settings_applied": result.settings_applied.to_dict(),
                    "warnings": result.warnings,
                    "elapsed_seconds": result.elapsed_seconds,
                },
            )
        except Exception:
            logger.warning("auto-correct cache write failed for %s", path, exc_info=True)

    def _models_for(self, settings: AutoCorrectSettings) -> list[str]:
        if settings.compare_models:
            configured = [
                m.strip()
                for m in self.settings.auto_correct_compare_models.replace(",", ";").split(";")
                if m.strip()
            ]
            # De-dupe while preserving order.
            seen: set[str] = set()
            models = [m for m in configured if not (m in seen or seen.add(m))]
            if len(models) >= 2:
                return models
            logger.warning(
                "compare_models requested but AUTO_CORRECT_COMPARE_MODELS has "
                "%d usable entries; falling back to single model", len(models),
            )
        return [self.settings.auto_correct_model]

    @staticmethod
    def _merge(
        per_model: dict[str, list[Suggestion]],
        flat: list[tuple[str, str, str]],
    ) -> list[Suggestion]:
        """Deduplicate identical suggestions across models, tag consensus,
        assign conflict groups to overlapping non-identical suggestions, and
        sort by document position."""
        total = len(per_model)
        merged: dict[tuple, Suggestion] = {}
        for model, suggestions in per_model.items():
            for s in suggestions:
                key = (s.op, tuple(s.word_ids), " ".join(s.new_text.lower().split()))
                existing = merged.get(key)
                if existing is None:
                    s.models = [model]
                    s.consensus = 1
                    s.total_models = total
                    merged[key] = s
                else:
                    existing.models.append(model)
                    existing.consensus += 1
                    existing.confidence = max(existing.confidence, s.confidence)

        result = list(merged.values())

        # Conflict groups: suggestions sharing any target word but not merged
        # above disagree about the same span — reviewer picks at most one.
        # (insert_after anchors are excluded: inserting after a word does not
        # conflict with editing that word.)
        by_word: dict[str, list[int]] = {}
        for i, s in enumerate(result):
            if s.op == "insert_after":
                continue
            for wid in s.word_ids:
                by_word.setdefault(wid, []).append(i)
        parent = list(range(len(result)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for overlapping in by_word.values():
            for other in overlapping[1:]:
                parent[find(other)] = find(overlapping[0])
        roots: dict[int, list[int]] = {}
        for i in range(len(result)):
            roots.setdefault(find(i), []).append(i)
        for members in roots.values():
            if len(members) < 2:
                continue
            group = str(uuid.uuid4())
            for i in members:
                result[i].conflict_group = group

        position = {word_id: i for i, (word_id, _, _) in enumerate(flat)}
        result.sort(key=lambda s: position.get(s.word_ids[0], 0) if s.word_ids else 0)
        return result

    # ---- internals ----

    def _call_model(
        self, model: str, system_prompt: str, user_prompt: str, *, job_id: str
    ) -> Any:
        if model.startswith("claude"):
            return self._call_anthropic(model, system_prompt, user_prompt, job_id=job_id)
        return self._call_gemini(model, system_prompt, user_prompt, job_id=job_id)

    def _call_anthropic(
        self, model: str, system_prompt: str, user_prompt: str, *, job_id: str
    ) -> Any:
        import os

        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            # No silent fallback to another provider — surface the
            # misconfiguration so it gets fixed, not masked.
            logger.error(
                "auto-correct model %s requires ANTHROPIC_API_KEY which is not set "
                "(job=%s)", model, job_id,
            )
            raise AutoCorrectServiceError(
                "AI model is not configured", status_code=502
            )

        # Structured outputs require additionalProperties: false on objects;
        # the Vertex variant of the schema omits it, so inject here. Deep-copy
        # because the google-genai SDK mutates schema dicts in place (it adds
        # 'property_ordering', which the Anthropic API rejects).
        import copy

        base = copy.deepcopy(RESPONSE_SCHEMA)
        for obj in (base, base["properties"]["suggestions"]["items"]):
            obj.pop("property_ordering", None)
            obj.pop("propertyOrdering", None)
            obj["additionalProperties"] = False
        strict_schema = base
        client = anthropic.Anthropic(timeout=120.0)
        try:
            response = client.messages.create(
                model=model,
                max_tokens=16000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                thinking={"type": "adaptive"},
                output_config={"format": {"type": "json_schema", "schema": strict_schema}},
            )
        except Exception as exc:
            logger.exception("auto-correct model call failed job=%s model=%s", job_id, model)
            raise AutoCorrectServiceError(
                "AI model call failed", status_code=502
            ) from exc
        try:
            text = next(b.text for b in response.content if b.type == "text")
            return json.loads(text)
        except (StopIteration, json.JSONDecodeError, TypeError) as exc:
            logger.exception(
                "auto-correct model returned non-JSON job=%s model=%s", job_id, model
            )
            raise AutoCorrectServiceError(
                "AI returned non-JSON output", status_code=502
            ) from exc

    def _call_gemini(
        self, model: str, system_prompt: str, user_prompt: str, *, job_id: str
    ) -> Any:
        client = genai.Client(
            vertexai=True,
            project=self.settings.google_cloud_project,
            location="global",
            # Milliseconds; bounds the call so a hung model never strands the UI.
            http_options=types.HttpOptions(timeout=120_000),
        )
        # Deep-copy: the google-genai SDK mutates the schema dict in place,
        # which would poison the shared module-level constant for other
        # providers running in the same process.
        import copy

        try:
            response = client.models.generate_content(
                model=model,
                contents=[user_prompt],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=copy.deepcopy(RESPONSE_SCHEMA),
                ),
            )
        except Exception as exc:  # surface as 502, never a stuck job
            logger.exception("auto-correct model call failed job=%s model=%s", job_id, model)
            raise AutoCorrectServiceError(
                "AI model call failed", status_code=502
            ) from exc
        try:
            return json.loads(response.text)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.exception(
                "auto-correct model returned non-JSON job=%s model=%s", job_id, model
            )
            raise AutoCorrectServiceError(
                "AI returned non-JSON output", status_code=502
            ) from exc

    def _validate(
        self,
        raw: Any,
        flat: list[tuple[str, str, str]],
        settings: AutoCorrectSettings,
    ) -> tuple[list[Suggestion], list[str]]:
        if not isinstance(raw, dict) or not isinstance(raw.get("suggestions"), list):
            raise AutoCorrectServiceError(
                "AI response missing 'suggestions' list", status_code=502
            )
        n = len(flat)
        out: list[Suggestion] = []
        warnings: list[str] = []
        for i, s in enumerate(raw["suggestions"]):
            problem = self._check(s, n, settings)
            if problem:
                warnings.append(f"dropped suggestion {i}: {problem}")
                continue
            start, end = int(s["start_idx"]), int(s["end_idx"])
            if s["op"] == "insert_after":
                word_ids = [flat[start][0]]
                segment_ids = [flat[start][2]]
                original_text = ""
            else:
                span = flat[start : end + 1]
                word_ids = [w[0] for w in span]
                segment_ids = sorted({w[2] for w in span})
                original_text = " ".join(w[1] for w in span)
            out.append(
                Suggestion(
                    id=str(uuid.uuid4()),
                    op=s["op"],
                    word_ids=word_ids,
                    segment_ids=segment_ids,
                    original_text=original_text,
                    new_text="" if s["op"] == "delete" else str(s["new_text"]).strip(),
                    reason=str(s.get("reason", "")),
                    category=s["category"] if s.get("category") in VALID_CATEGORIES else "other",
                    confidence=max(0.0, min(1.0, float(s["confidence"]))),
                )
            )
        return out, warnings

    @staticmethod
    def _check(s: Any, n_words: int, settings: AutoCorrectSettings) -> Optional[str]:
        if not isinstance(s, dict):
            return "not an object"
        op = s.get("op")
        if op not in VALID_OPS:
            return f"invalid op {op!r}"
        try:
            start, end = int(s["start_idx"]), int(s["end_idx"])
            confidence = float(s["confidence"])
        except (KeyError, TypeError, ValueError):
            return "missing/invalid indices or confidence"
        if not (0 <= start <= end < n_words):
            return f"indices out of range ({start}..{end} of {n_words})"
        if op == "insert_after" and not settings.allow_insertions:
            return "insertions disabled by settings"
        if op == "insert_after" and start != end:
            return "insert_after requires start_idx == end_idx"
        new_text = str(s.get("new_text", "")).strip()
        if op in ("replace", "insert_after") and not new_text:
            return f"{op} with empty new_text"
        if op == "delete" and new_text:
            return "delete with non-empty new_text"
        if confidence < settings.min_confidence:
            return f"confidence {confidence:.2f} below threshold"
        if (
            s.get("category") == "adlib_removal"
            and not settings.suggest_adlib_removal
        ):
            return "adlib removal disabled by settings"
        return None


_service_instance: Optional[AutoCorrectService] = None


def get_auto_correct_service() -> AutoCorrectService:
    global _service_instance
    if _service_instance is None:
        _service_instance = AutoCorrectService()
    return _service_instance
