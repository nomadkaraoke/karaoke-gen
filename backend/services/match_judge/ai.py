"""The light-AI layer of the match judge.

Invoked only when the deterministic+catalog classifier can't decide. Uses a
small Vertex Gemini model to judge whether the typed artist/title is a real
song, what its canonical formatting is, and whether any change is cosmetic,
content-level, or ambiguous. The model call is injectable for testing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from backend.services.match_judge.verdict import (
    KIND_AMBIGUOUS,
    KIND_CONTENT,
    KIND_COSMETIC,
    KIND_NONE,
    MatchVerdict,
)

logger = logging.getLogger(__name__)

_VALID_KINDS = {KIND_COSMETIC, KIND_CONTENT, KIND_AMBIGUOUS}

Generate = Callable[[str, str, str], Awaitable[dict]]

_SYSTEM_PROMPT = (
    "You normalise song metadata for a karaoke service. Given what a user typed "
    "for an artist and title (plus optional catalog candidates), decide whether it "
    "refers to a real, released song and how its artist/title should be formatted.\n"
    "Return JSON with: kind, confident, canonical_artist, canonical_title, "
    "alternatives, reason.\n"
    "kind:\n"
    "  'cosmetic'  – same song, only formatting/casing/punctuation/diacritics differ.\n"
    "  'content'   – the user made a typo or used a wrong/variant title; the correct "
    "song text differs in actual characters.\n"
    "  'ambiguous' – more than one plausible song matches; list them in alternatives.\n"
    "  'none'      – already correct, or you cannot confidently suggest anything.\n"
    "confident: true only when you are sure. Never invent songs. Prefer the user's "
    "input when unsure (kind='none'). canonical_artist/canonical_title must use "
    "official formatting. alternatives is a list of {artist,title}."
)

# OpenAPI-style schema handed to the Vertex genai client at runtime.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["cosmetic", "content", "ambiguous", "none"]},
        "confident": {"type": "boolean"},
        "canonical_artist": {"type": "string"},
        "canonical_title": {"type": "string"},
        "alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                },
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["kind", "confident"],
}


def _model() -> str:
    try:
        from backend.config import settings
        return getattr(settings, "match_judge_model", "gemini-3.5-flash")
    except Exception:  # pragma: no cover - config import guard
        return "gemini-3.5-flash"


def build_prompts(
    artist: str, title: str, candidates: list[dict], audio_tier: Optional[int]
) -> tuple[str, str]:
    lines = [f'User typed: artist="{artist}" title="{title}"']
    if audio_tier is not None:
        lines.append(
            f"Audio-search confidence tier: {audio_tier} "
            "(1 = strong match found, 3 = weak/no results — a weak tier hints the "
            "title may be misspelled)."
        )
    if candidates:
        lines.append("Catalog candidates (canonical formatting):")
        for c in candidates:
            lines.append(f'- {c.get("artist_name", "")} — {c.get("track_name", "")}')
    else:
        lines.append("No catalog candidates were found.")
    return _SYSTEM_PROMPT, "\n".join(lines)


def _verdict_from_response(
    data: object, *, fallback_artist: str, fallback_title: str
) -> MatchVerdict:
    if not isinstance(data, dict):
        data = {}
    kind = data.get("kind")
    if kind not in _VALID_KINDS:
        return MatchVerdict(
            KIND_NONE, True, fallback_artist, fallback_title,
            engine="ai", reason="no suggestion",
        )
    canonical_artist = (data.get("canonical_artist") or "").strip() or fallback_artist
    canonical_title = (data.get("canonical_title") or "").strip() or fallback_title
    alternatives = []
    for alt in data.get("alternatives") or []:
        if isinstance(alt, dict) and alt.get("artist") and alt.get("title"):
            alternatives.append(
                {"artist": str(alt["artist"]), "title": str(alt["title"])}
            )
    return MatchVerdict(
        kind=kind,
        confident=bool(data.get("confident", False)),
        canonical_artist=canonical_artist,
        canonical_title=canonical_title,
        alternatives=alternatives,
        engine="ai",
        reason=str(data.get("reason") or ""),
    )


async def ai_judge_match(
    artist: str,
    title: str,
    candidates: list[dict],
    audio_tier: Optional[int],
    *,
    model: Optional[str] = None,
    generate: Optional[Generate] = None,
) -> MatchVerdict:
    model = model or _model()
    gen = generate or _default_generate
    system_prompt, user_prompt = build_prompts(artist, title, candidates, audio_tier)
    data = await gen(model, system_prompt, user_prompt)
    return _verdict_from_response(
        data, fallback_artist=artist, fallback_title=title
    )


async def _default_generate(model: str, system_prompt: str, user_prompt: str) -> dict:
    return await asyncio.to_thread(_blocking_generate, model, system_prompt, user_prompt)


def _blocking_generate(model: str, system_prompt: str, user_prompt: str) -> dict:
    import copy

    from google import genai
    from google.genai import types

    from backend.config import settings

    timeout_ms = int(getattr(settings, "match_judge_timeout_ms", 3000))
    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location="global",
        http_options=types.HttpOptions(timeout=timeout_ms),
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
