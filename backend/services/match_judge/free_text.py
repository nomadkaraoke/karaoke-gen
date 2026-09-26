"""Free-text song resolution for the kjbox singer search box.

gen's match judge takes a separate artist + title; a karaoke singer types one
line ("the strokes max picu"). One small Gemini call splits the query into
artist/title AND corrects typos in the same step, returning the match judge's
verdict shape (kind cosmetic/content/ambiguous/none) plus the typed split, so
kjbox can show gen's "Corrected to X — you typed Y. Undo".
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Awaitable, Callable, Optional

from backend.services.match_judge.ai import _model
from backend.services.match_judge.verdict import KIND_NONE

logger = logging.getLogger(__name__)

Generate = Callable[[str, str, str], Awaitable[dict]]

_VALID_KINDS = {"cosmetic", "content", "ambiguous"}

_SYSTEM_PROMPT = (
    "You resolve a karaoke singer's free-text song search. The query is one line "
    "holding an artist and/or a song title in any order, often lazily typed or "
    "misspelled (e.g. 'the strokes max picu' = The Strokes — Machu Picchu).\n"
    "Return JSON with: kind, confident, typed_artist, typed_title, canonical_artist, "
    "canonical_title, alternatives, reason.\n"
    "typed_artist/typed_title: how the query splits as typed (either may be empty).\n"
    "kind:\n"
    "  'cosmetic'  – the query already names one real song; only casing/punctuation "
    "differ.\n"
    "  'content'   – the query names one real song but with typos or a wrong/variant "
    "title; canonical_* is the song meant.\n"
    "  'ambiguous' – several real songs are plausible; list them in alternatives.\n"
    "  'none'      – not a recognisable song, or you are not sure.\n"
    "confident: true only when you are sure which released song is meant. Never "
    "invent songs; prefer kind='none' when unsure. canonical_artist/canonical_title "
    "must use official formatting. alternatives is a list of {artist,title} (max 4)."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["cosmetic", "content", "ambiguous", "none"]},
        "confident": {"type": "boolean"},
        "typed_artist": {"type": "string"},
        "typed_title": {"type": "string"},
        "canonical_artist": {"type": "string"},
        "canonical_title": {"type": "string"},
        "alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"artist": {"type": "string"}, "title": {"type": "string"}},
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["kind", "confident"],
}


def _none(query: str, reason: str = "no suggestion") -> dict:
    return {
        "kind": KIND_NONE, "confident": False, "typed_artist": "", "typed_title": query,
        "canonical_artist": "", "canonical_title": "", "alternatives": [],
        "engine": "ai", "reason": reason,
    }


def verdict_from_response(data: object, query: str) -> dict:
    """Validate/normalise the model's JSON into the resolve response shape.
    Anything malformed (wrong kinds or field types) → kind 'none'."""
    try:
        return _verdict_from_response(data, query)
    except (TypeError, ValueError, AttributeError):
        return _none(query, "malformed response")


def _verdict_from_response(data: object, query: str) -> dict:
    if not isinstance(data, dict) or data.get("kind") not in _VALID_KINDS:
        return _none(query)
    for key in ("typed_artist", "typed_title", "canonical_artist", "canonical_title", "reason"):
        if data.get(key) is not None and not isinstance(data[key], str):
            return _none(query, "malformed response")
    if data.get("alternatives") is not None and not isinstance(data["alternatives"], list):
        return _none(query, "malformed response")
    s = lambda k: str(data.get(k) or "").strip()  # noqa: E731
    alternatives = [
        {"artist": str(a["artist"]).strip(), "title": str(a["title"]).strip()}
        for a in (data.get("alternatives") or [])
        if isinstance(a, dict) and isinstance(a.get("artist"), str) and isinstance(a.get("title"), str)
        and a["artist"].strip() and a["title"].strip()
    ][:4]
    canonical_artist, canonical_title = s("canonical_artist"), s("canonical_title")
    kind = data["kind"]
    if kind in ("cosmetic", "content") and not (canonical_artist and canonical_title):
        return _none(query, "incomplete suggestion")
    if kind == "ambiguous" and not alternatives:
        return _none(query, "ambiguous without alternatives")
    return {
        "kind": kind,
        "confident": bool(data.get("confident", False)),
        "typed_artist": s("typed_artist"),
        "typed_title": s("typed_title") or (query if not s("typed_artist") else ""),
        "canonical_artist": canonical_artist,
        "canonical_title": canonical_title,
        "alternatives": alternatives,
        "engine": "ai",
        "reason": s("reason"),
    }


async def resolve_free_text(query: str, *, generate: Optional[Generate] = None,
                            model: Optional[str] = None) -> dict:
    """Split + correct a free-text song query. Never raises: failures → kind 'none'."""
    try:
        data = await (generate or _default_generate)(
            model or _model(), _SYSTEM_PROMPT, f'Singer typed: "{query}"')
    except Exception as e:
        logger.warning(f"resolve_free_text failed: {e}")
        return _none(query, "unavailable")
    return verdict_from_response(data, query)


async def _default_generate(model: str, system_prompt: str, user_prompt: str) -> dict:
    return await asyncio.to_thread(_blocking_generate, model, system_prompt, user_prompt)


def _blocking_generate(model: str, system_prompt: str, user_prompt: str) -> dict:
    from google import genai
    from google.genai import types

    from backend.config import settings

    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location="global",
        http_options=types.HttpOptions(timeout=int(getattr(settings, "match_judge_timeout_ms", 12000))),
    )
    response = client.models.generate_content(
        model=model,
        contents=[user_prompt],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=copy.deepcopy(RESPONSE_SCHEMA),
            temperature=0,
        ),
    )
    return json.loads(response.text)
