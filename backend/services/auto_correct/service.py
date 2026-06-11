"""Auto-correct suggestion service: one whole-song LLM call, validated output."""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
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


@dataclass
class AutoCorrectResult:
    suggestions: list[Suggestion]
    model: str
    elapsed_seconds: float
    settings_applied: AutoCorrectSettings
    warnings: list[str] = field(default_factory=list)


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
            for w in seg.get("words") or []:
                if not w.get("id"):
                    raise AutoCorrectServiceError("all words must have ids")
                flat.append((w["id"], w.get("text", ""), seg.get("id", "")))

        system_prompt = build_system_prompt(settings)
        user_prompt = build_user_prompt(
            segments=segments,
            reference_lyrics=reference_lyrics,
            artist=artist,
            title=title,
        )

        t0 = time.time()
        model = self.settings.auto_correct_model
        raw = self._call_model(model, system_prompt, user_prompt)
        elapsed = time.time() - t0

        suggestions, warnings = self._validate(raw, flat, settings)
        logger.info(
            "auto-correct job=%s model=%s words=%d suggestions=%d dropped_warnings=%d elapsed=%.1fs",
            job_id, model, len(flat), len(suggestions), len(warnings), elapsed,
        )
        return AutoCorrectResult(
            suggestions=suggestions,
            model=model,
            elapsed_seconds=round(elapsed, 1),
            settings_applied=settings,
            warnings=warnings,
        )

    # ---- internals ----

    def _call_model(self, model: str, system_prompt: str, user_prompt: str) -> Any:
        client = genai.Client(
            vertexai=True,
            project=self.settings.google_cloud_project,
            location="global",
        )
        try:
            response = client.models.generate_content(
                model=model,
                contents=[user_prompt],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                ),
            )
        except Exception as exc:  # surface as 502, never a stuck job
            raise AutoCorrectServiceError(
                f"AI model call failed: {exc}", status_code=502
            ) from exc
        try:
            return json.loads(response.text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise AutoCorrectServiceError(
                f"AI returned non-JSON output: {exc}", status_code=502
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
