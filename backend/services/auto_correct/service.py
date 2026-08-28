"""Auto-correct suggestion service: one whole-song LLM call, validated output."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from google import genai
from google.genai import types

from backend.config import get_settings
from backend.services.auto_correct.deterministic import deterministic_suggestions
from backend.services.auto_correct.pricing import TokenUsage, estimate_cost_usd
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
    def __init__(
        self, message: str, *, status_code: int = 400, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        # True for transient provider conditions (rate limits, overload) the
        # caller may safely retry; drives backoff in _call_model.
        self.retryable = retryable


# Retry knobs for a single model call. Vertex 429 RESOURCE_EXHAUSTED is a
# recurring, transient condition; a short app-level backoff lets quota recover
# where the SDK's fast internal retry cannot. Kept small so a request never
# stalls the review UI for long. Parsed defensively: a malformed env override
# clamps to a safe value with a warning rather than crashing the backend at
# import or breaking the retry loop.
def _env_number(name: str, default: float, *, minimum: float, is_int: bool) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        val: float = int(raw) if is_int else float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using default %s", name, raw, default)
        return default
    import math as _math

    if not _math.isfinite(val) or val < minimum:
        logger.warning(
            "%s=%s out of range (min %s); clamping to %s", name, val, minimum, minimum
        )
        return minimum
    return val


_MODEL_CALL_MAX_ATTEMPTS = int(
    _env_number("AUTO_CORRECT_MODEL_MAX_ATTEMPTS", 3, minimum=1, is_int=True)
)
_MODEL_CALL_BACKOFF_SECONDS = float(
    _env_number("AUTO_CORRECT_MODEL_BACKOFF_SECONDS", 2.0, minimum=0.0, is_int=False)
)


def _is_transient_model_error(exc: BaseException) -> bool:
    """True when a provider exception is a retryable rate-limit/overload.

    Matches both structured status codes (google-genai ClientError/ServerError
    expose ``code``; Anthropic errors expose ``status_code``) and, as a
    fallback, the textual markers Vertex/Anthropic use — so detection survives
    SDK exception-class churn.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in {429, 500, 502, 503, 529}:
        return True
    text = str(exc).upper()
    markers = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "OVERLOADED", "429", "503", "529")
    return any(m in text for m in markers)


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


_NORM_TOKEN_RE = re.compile(r"^[^\w]+|[^\w]+$")


def _norm_tokens(text: str) -> set:
    return {t for t in (_NORM_TOKEN_RE.sub("", w.lower()) for w in (text or "").split()) if t}


def _assign_conflict_groups(result: list[Suggestion]) -> None:
    """(Re)compute conflict groups over a suggestion list, in place.

    Two rules union suggestions into a pick-at-most-one group:
    - Non-insert suggestions sharing any target word disagree about that span.
    - An ``insert_after`` whose inserted token(s) also appear in another
      suggestion's new_text targeting (or anchored on) the same word is the P1
      self-conflict signature — two overlapping suggestions both adding the
      same token (corpus f6439692: ``insert_after "fire" -> "you're"`` plus
      ``replace "fire" -> "fire, you're"`` produced "you're you're"). Grouping
      them lets accept-all pick one winner instead of double-applying.
      A plain insert next to an edit with disjoint tokens stays independent.
    """
    for s in result:
        s.conflict_group = None

    parent = list(range(len(result)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(b)] = find(a)

    by_word: dict[str, list[int]] = {}
    for i, s in enumerate(result):
        if s.op == "insert_after":
            continue
        for wid in s.word_ids:
            by_word.setdefault(wid, []).append(i)
    for overlapping in by_word.values():
        for other in overlapping[1:]:
            union(overlapping[0], other)

    for i, s in enumerate(result):
        if s.op != "insert_after" or not s.word_ids:
            continue
        anchor = s.word_ids[0]
        tokens = _norm_tokens(s.new_text)
        if not tokens:
            continue
        for j, r in enumerate(result):
            if j == i:
                continue
            anchored = (
                anchor in r.word_ids
                if r.op != "insert_after"
                else bool(r.word_ids) and r.word_ids[0] == anchor
            )
            if anchored and tokens & _norm_tokens(r.new_text):
                union(i, j)

    roots: dict[int, list[int]] = {}
    for i in range(len(result)):
        roots.setdefault(find(i), []).append(i)
    for members in roots.values():
        if len(members) < 2:
            continue
        group = str(uuid.uuid4())
        for i in members:
            result[i].conflict_group = group


def _sort_by_position(result: list[Suggestion], flat: list[tuple[str, str, str]]) -> None:
    position = {word_id: i for i, (word_id, _, _) in enumerate(flat)}
    result.sort(key=lambda s: position.get(s.word_ids[0], 0) if s.word_ids else 0)


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
        correction_data: Optional[dict] = None,
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
        # Token usage per successful model call (None when the provider's
        # usage object couldn't be read). Drives the cost log below.
        per_model_usage: dict[str, Optional[TokenUsage]] = {}
        warnings: list[str] = []
        if len(models) == 1:
            raw, usage = self._call_model(
                models[0], system_prompt, user_prompt, job_id=job_id
            )
            suggestions, vw = self._validate(raw, flat, settings)
            per_model[models[0]] = suggestions
            per_model_usage[models[0]] = usage
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
                        raw, usage = fut.result()
                        suggestions, vw = self._validate(raw, flat, settings)
                    except AutoCorrectServiceError as exc:
                        # One model failing (call OR malformed output) must not
                        # abort compare mode; the rest still return results.
                        warnings.append(f"model {m} failed: {exc}")
                        continue
                    per_model[m] = suggestions
                    per_model_usage[m] = usage
                    warnings.extend(f"[{m}] {w}" for w in vw)
            if not per_model:
                raise AutoCorrectServiceError(
                    "all AI models failed", status_code=502
                )

        merged = self._merge(per_model, flat)
        merged = self._add_deterministic(
            merged, job_id, segments, reference_lyrics, correction_data, settings, flat
        )
        elapsed = time.time() - t0
        model_label = ", ".join(per_model.keys())
        logger.info(
            "auto-correct job=%s models=%s words=%d suggestions=%d dropped_warnings=%d elapsed=%.1fs",
            job_id, model_label, len(flat), len(merged), len(warnings), elapsed,
        )
        self._log_usage(job_id, per_model_usage)
        result = AutoCorrectResult(
            suggestions=merged,
            model=model_label,
            elapsed_seconds=round(elapsed, 1),
            settings_applied=settings,
            warnings=warnings,
        )
        self._cache_put(cache_path, result)
        return result

    @staticmethod
    def _log_usage(
        job_id: str, per_model_usage: dict[str, Optional[TokenUsage]]
    ) -> None:
        """Emit greppable per-model and total cost lines for one suggest() call.

        Token counts are ground truth; the USD figures are derived from
        ``pricing.MODEL_PRICING`` and use ESTIMATED Gemini rates (see that
        module). Query in Cloud Logging via ``auto-correct usage`` /
        ``auto-correct cost``. Observability only — never raises.
        """
        total_cost = 0.0
        any_cost = False
        for model, usage in per_model_usage.items():
            cost = estimate_cost_usd(model, usage)
            if cost is not None:
                total_cost += cost
                any_cost = True
            if usage is None:
                logger.info(
                    "auto-correct usage job=%s model=%s tokens=unavailable",
                    job_id, model,
                )
                continue
            thinking = usage.get("thinking_tokens")
            logger.info(
                "auto-correct usage job=%s model=%s input_tokens=%d "
                "output_tokens=%d thinking_tokens=%s cache_read_tokens=%d "
                "est_cost_usd=%s",
                job_id,
                model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                "na" if thinking is None else thinking,
                usage.get("cache_read_tokens", 0),
                "unknown" if cost is None else f"{cost:.4f}",
            )
        logger.info(
            "auto-correct cost job=%s models=%s total_est_cost_usd=%s",
            job_id,
            ", ".join(per_model_usage.keys()),
            f"{total_cost:.4f}" if any_cost else "unknown",
        )

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
        # v2: deterministic suggestions (P4/P1/P5 fixers) joined the pipeline —
        # older cached results predate them, so they must not be served.
        payload = {
            "v": 2,
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
        _assign_conflict_groups(result)
        _sort_by_position(result, flat)
        return result

    def _add_deterministic(
        self,
        merged: list[Suggestion],
        job_id: str,
        segments: list[dict],
        reference_lyrics: dict[str, dict],
        correction_data: Optional[dict],
        settings: AutoCorrectSettings,
        flat: list[tuple[str, str, str]],
    ) -> list[Suggestion]:
        """Fold the deterministic (P4/P5) suggestions into the LLM set.

        A deterministic suggestion identical to an LLM one just tags the
        existing suggestion; new ones are appended. Conflict groups are then
        recomputed over the combined set so a deterministic fix that disagrees
        with an LLM suggestion becomes a pick-one conflict, not a double-apply.
        """
        if correction_data is None:
            # The proactive worker passes corrections.json in; the review-UI
            # route doesn't have it, so fetch it (best-effort) — it holds the
            # gap/anchor alignment the generators key off.
            try:
                path = f"jobs/{job_id}/lyrics/corrections.json"
                storage = self._get_storage()
                if storage.bucket.blob(path).exists():
                    correction_data = storage.download_json(path)
            except Exception:
                logger.warning(
                    "auto-correct: corrections.json unavailable for %s; "
                    "skipping deterministic suggestions", job_id, exc_info=True,
                )
        raw = deterministic_suggestions(segments, reference_lyrics, correction_data)
        deterministic = [
            Suggestion(**s)
            for s in raw
            if float(s.get("confidence", 0.0)) >= settings.min_confidence
        ]
        if not deterministic:
            return merged

        by_key = {
            (s.op, tuple(s.word_ids), " ".join(s.new_text.lower().split())): s
            for s in merged
        }
        added = 0
        for s in deterministic:
            key = (s.op, tuple(s.word_ids), " ".join(s.new_text.lower().split()))
            existing = by_key.get(key)
            if existing is not None:
                existing.models.append("deterministic")
                existing.confidence = max(existing.confidence, s.confidence)
                continue
            merged.append(s)
            added += 1
        logger.info(
            "auto-correct deterministic job=%s generated=%d added=%d",
            job_id, len(deterministic), added,
        )
        _assign_conflict_groups(merged)
        _sort_by_position(merged, flat)
        return merged

    # ---- internals ----

    def _call_model(
        self, model: str, system_prompt: str, user_prompt: str, *, job_id: str
    ) -> tuple[Any, Optional[TokenUsage]]:
        """Return (parsed_json, token_usage). Usage is None if unreadable.

        Transient provider errors (rate limits / overload, flagged
        ``retryable``) are retried with exponential backoff. Permanent errors
        (misconfig, malformed output) fail fast.
        """
        for attempt in range(1, _MODEL_CALL_MAX_ATTEMPTS + 1):
            try:
                if model.startswith("claude"):
                    return self._call_anthropic(
                        model, system_prompt, user_prompt, job_id=job_id
                    )
                return self._call_gemini(
                    model, system_prompt, user_prompt, job_id=job_id
                )
            except AutoCorrectServiceError as exc:
                if not exc.retryable or attempt == _MODEL_CALL_MAX_ATTEMPTS:
                    raise
                backoff = _MODEL_CALL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "auto-correct model call transient failure job=%s model=%s "
                    "attempt=%d/%d; retrying in %.1fs (%s)",
                    job_id, model, attempt, _MODEL_CALL_MAX_ATTEMPTS, backoff, exc,
                )
                time.sleep(backoff)
        # Unreachable: the loop either returns or raises on the final attempt.
        raise AssertionError("retry loop exited without returning or raising")

    @staticmethod
    def _anthropic_usage(response: Any) -> Optional[TokenUsage]:
        """Best-effort usage from an Anthropic response.

        ``output_tokens`` already includes adaptive thinking tokens (billed as
        output); they are not separable here, so thinking_tokens is None.
        Returns None on any shape problem — usage must never break the call.
        """
        try:
            u = response.usage
            return TokenUsage(
                input_tokens=int(getattr(u, "input_tokens", 0) or 0),
                output_tokens=int(getattr(u, "output_tokens", 0) or 0),
                cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
                thinking_tokens=None,
            )
        except Exception:
            logger.debug("auto-correct: could not read Anthropic usage", exc_info=True)
            return None

    @staticmethod
    def _gemini_usage(response: Any) -> Optional[TokenUsage]:
        """Best-effort usage from a Gemini (Vertex) response.

        Gemini reports thinking separately (``thoughts_token_count``); it is
        billed at the output rate, so it's folded into output_tokens and also
        reported separately for visibility. Returns None on any shape problem.
        """
        try:
            u = response.usage_metadata
            prompt = int(getattr(u, "prompt_token_count", 0) or 0)
            candidates = int(getattr(u, "candidates_token_count", 0) or 0)
            thoughts = int(getattr(u, "thoughts_token_count", 0) or 0)
            cached = int(getattr(u, "cached_content_token_count", 0) or 0)
            return TokenUsage(
                input_tokens=prompt,
                output_tokens=candidates + thoughts,
                cache_read_tokens=cached,
                thinking_tokens=thoughts,
            )
        except Exception:
            logger.debug("auto-correct: could not read Gemini usage", exc_info=True)
            return None

    def _call_anthropic(
        self, model: str, system_prompt: str, user_prompt: str, *, job_id: str
    ) -> tuple[Any, Optional[TokenUsage]]:
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
            if _is_transient_model_error(exc):
                logger.warning(
                    "auto-correct model rate-limited/overloaded job=%s model=%s (%s)",
                    job_id, model, exc,
                )
                raise AutoCorrectServiceError(
                    "AI model is temporarily rate-limited",
                    status_code=429,
                    retryable=True,
                ) from exc
            logger.exception("auto-correct model call failed job=%s model=%s", job_id, model)
            raise AutoCorrectServiceError(
                "AI model call failed", status_code=502
            ) from exc
        usage = self._anthropic_usage(response)
        try:
            text = next(b.text for b in response.content if b.type == "text")
            return json.loads(text), usage
        except (StopIteration, json.JSONDecodeError, TypeError) as exc:
            logger.exception(
                "auto-correct model returned non-JSON job=%s model=%s", job_id, model
            )
            raise AutoCorrectServiceError(
                "AI returned non-JSON output", status_code=502
            ) from exc

    def _call_gemini(
        self, model: str, system_prompt: str, user_prompt: str, *, job_id: str
    ) -> tuple[Any, Optional[TokenUsage]]:
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
            if _is_transient_model_error(exc):
                logger.warning(
                    "auto-correct model rate-limited/overloaded job=%s model=%s (%s)",
                    job_id, model, exc,
                )
                raise AutoCorrectServiceError(
                    "AI model is temporarily rate-limited",
                    status_code=429,
                    retryable=True,
                ) from exc
            logger.exception("auto-correct model call failed job=%s model=%s", job_id, model)
            raise AutoCorrectServiceError(
                "AI model call failed", status_code=502
            ) from exc
        usage = self._gemini_usage(response)
        try:
            return json.loads(response.text), usage
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
