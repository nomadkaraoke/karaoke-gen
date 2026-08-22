"""Vertex-Gemini layer for batch karaoke-filename parsing.

Turns messy karaoke *filenames* (YouTube titles, community/producer naming,
KaraFun-reversed order) into canonical {artist, title, confidence}. Distinct
from match_judge (which judges an already-split artist/title). The model call
is injectable for tests.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

Generate = Callable[[str, str, str], Awaitable[dict]]

_SYSTEM_PROMPT = (
    "You extract canonical song metadata from karaoke video/file names for a "
    "karaoke library. Each item has an id and a filename (sometimes a channel or "
    "source). Filenames are noisy: they carry karaoke markers ('(Karaoke Version)', "
    "'KARAOKE', '[karaoke]', 'Instrumental', producer/brand tags), YouTube ids or "
    "channel names, and inconsistent separators. Crucially the artist/title ORDER "
    "is inconsistent — most are 'Artist - Title' but some sources (e.g. KaraFun) are "
    "'Title - Artist'. Use your knowledge of real songs to put artist and title in "
    "the correct fields.\n"
    "Return JSON: {\"results\": [{\"id\", \"artist\", \"title\", \"confidence\"}]}. "
    "Return exactly one result per input id, echoing the id verbatim.\n"
    "artist/title: official formatting, karaoke noise removed, no brand codes or "
    "YouTube ids. If you cannot identify one field, return it as an empty string.\n"
    "confidence: 0.0-1.0 — your certainty the artist/title (and their order) are "
    "correct. Be honest; low confidence for ambiguous or unknown songs. Never invent "
    "a song; if unsure, return best-effort split with low confidence."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["id"],
            },
        }
    },
    "required": ["results"],
}


def _model() -> str:
    try:
        from backend.config import settings
        return getattr(settings, "parse_titles_model", "gemini-3.5-flash")
    except Exception:  # pragma: no cover - config guard
        return "gemini-3.5-flash"


def build_prompts(items: list[dict]) -> tuple[str, str]:
    lines = ["Parse these karaoke filenames:"]
    for it in items:
        parts = [f'id={it.get("id")!r}', f'filename={it.get("filename", "")!r}']
        if it.get("channel"):
            parts.append(f'channel={it["channel"]!r}')
        if it.get("source"):
            parts.append(f'source={it["source"]!r}')
        lines.append("- " + " ".join(parts))
    return _SYSTEM_PROMPT, "\n".join(lines)


def _clean(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ""


def _conf(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def parse_map_from_response(data: object, items: list[dict]) -> list[dict]:
    """Return one id-aligned result per input, filling any misses with blanks."""
    by_id: dict[str, dict] = {}
    if isinstance(data, dict):
        for r in data.get("results") or []:
            if isinstance(r, dict) and r.get("id") is not None:
                by_id[str(r["id"])] = r
    out = []
    for it in items:
        rid = str(it.get("id"))
        r = by_id.get(rid, {})
        out.append({
            "id": rid,
            "artist": _clean(r.get("artist")),
            "title": _clean(r.get("title")),
            "confidence": _conf(r.get("confidence")),
        })
    return out


async def ai_parse(
    items: list[dict], *, model: Optional[str] = None,
    generate: Optional[Generate] = None,
) -> list[dict]:
    gen = generate or _default_generate
    system, user = build_prompts(items)
    data = await gen(model or _model(), system, user)
    return parse_map_from_response(data, items)


async def _default_generate(model: str, system: str, user: str) -> dict:
    return await asyncio.to_thread(_blocking_generate, model, system, user)


def _blocking_generate(model: str, system: str, user: str) -> dict:
    from google import genai
    from google.genai import types

    from backend.config import settings

    timeout_ms = int(getattr(settings, "parse_titles_timeout_ms", 20000))
    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location="global",
        http_options=types.HttpOptions(timeout=timeout_ms),
    )
    response = client.models.generate_content(
        model=model,
        contents=[user],
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=copy.deepcopy(RESPONSE_SCHEMA),
            temperature=0,
        ),
    )
    return json.loads(response.text)
