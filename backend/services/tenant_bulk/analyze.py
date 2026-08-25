"""Filename analysis for tenant bulk upload.

Turns a flat list of filenames (as a browser folder-pick produces) into a set of
proposed karaoke jobs: each pairs a **Mixed** audio file (the full song, has
vocals) with its **Instrumental** counterpart and extracts Artist/Title.

Two passes:
  1. **Regex fast path** — handles the machine-friendly convention Vocal Star
     uses, e.g. ``S1100-1 Eddy Grant - I Don't Wanna Dance Guide.mp3`` paired
     with ``S1100-2 Eddy Grant - I Don't Wanna Dance BV.mp3``.
  2. **LLM pass** (optional, injectable) — reasons about whatever the regex could
     not confidently pair (odd labels, missing pairs, future tenant conventions).
     Mirrors the ``match_judge`` Vertex-Gemini client pattern; injectable so unit
     tests stay deterministic without a live model call.

Pure analysis: no uploads, no state written, no audio read — filenames only.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Audio extensions we treat as candidate track files. Anything else (images,
# text, etc.) is surfaced under "ignored" rather than guessed at.
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".aiff", ".aif"}

# Labels that mark a file as the MIXED (has-vocals) version.
_MIXED_LABELS = ("guide", "lead vocal", "lead", "vocals", "vocal", "with vocals")
# Labels that mark a file as the INSTRUMENTAL (backing) version.
_INSTRUMENTAL_LABELS = (
    "instrumental",
    "instru",
    "backing vocals",
    "backing",
    "karaoke",
    "bv",
    "off",
)

# All labels, longest-first so multi-word labels match before their prefixes.
_ALL_LABELS = sorted(
    set(_MIXED_LABELS) | set(_INSTRUMENTAL_LABELS), key=len, reverse=True
)
_LABEL_ALTERNATION = "|".join(re.escape(lbl) for lbl in _ALL_LABELS)

# ``S1100-1 Eddy Grant - I Don't Wanna Dance Guide`` (extension already stripped).
#   group("code") -> "1100"   group("slot") -> "1"
#   group("body") -> "Eddy Grant - I Don't Wanna Dance"   group("label") -> "Guide"
_CODED_RE = re.compile(
    r"^S?(?P<code>\d+)\s*-\s*(?P<slot>[12])\s+(?P<body>.+?)"
    rf"(?:\s+(?P<label>{_LABEL_ALTERNATION}))?$",
    re.IGNORECASE,
)

# Fallback for filenames with no S-code prefix, e.g. ``Adele - Hello (Guide)``.
_LABELLED_RE = re.compile(
    rf"^(?P<body>.+?)\s*[\(\[]?\s*(?P<label>{_LABEL_ALTERNATION})\s*[\)\]]?$",
    re.IGNORECASE,
)

MIXED = "mixed"
INSTRUMENTAL = "instrumental"


@dataclass
class ProposedRow:
    """A confident Mixed+Instrumental pair the operator can submit as one job."""

    artist: str
    title: str
    mixed_filename: str
    instrumental_filename: str
    confidence: str = "high"  # "high" | "medium" | "low"
    warning: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "artist": self.artist,
            "title": self.title,
            "mixed_filename": self.mixed_filename,
            "instrumental_filename": self.instrumental_filename,
            "confidence": self.confidence,
            "warning": self.warning,
        }


@dataclass
class UnpairedFile:
    """An audio file we could not confidently pair (surfaced as a warning)."""

    filename: str
    reason: str  # "no_instrumental" | "no_mixed" | "unparseable" | "duplicate"
    artist: Optional[str] = None
    title: Optional[str] = None
    role: Optional[str] = None  # "mixed" | "instrumental" | None

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "reason": self.reason,
            "artist": self.artist,
            "title": self.title,
            "role": self.role,
        }


@dataclass
class IgnoredFile:
    """A non-audio file that is not part of any job."""

    filename: str
    reason: str = "non_audio"

    def to_dict(self) -> dict:
        return {"filename": self.filename, "reason": self.reason}


@dataclass
class BulkAnalysis:
    rows: list[ProposedRow] = field(default_factory=list)
    unpaired: list[UnpairedFile] = field(default_factory=list)
    ignored: list[IgnoredFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "unpaired": [u.to_dict() for u in self.unpaired],
            "ignored": [i.to_dict() for i in self.ignored],
        }


# A parsed audio filename, before pairing.
@dataclass
class _Parsed:
    filename: str
    code: Optional[str]
    role: Optional[str]  # MIXED | INSTRUMENTAL | None (unknown)
    artist: str
    title: str


# generate(system_prompt, user_prompt) -> dict (parsed JSON matching RESPONSE_SCHEMA)
Generate = Callable[[str, str], dict]


def _label_role(label: Optional[str], slot: Optional[str]) -> Optional[str]:
    """Classify a file as mixed/instrumental from its label, then slot number.

    The label takes precedence over the ``-1``/``-2`` slot: a ``-2 ... Guide``
    file (the S1102 anomaly) is a *mixed* file that happens to sit in slot 2, so
    it should be reported as mixed-only + unpaired rather than silently treated
    as an instrumental.
    """
    if label:
        lbl = label.lower()
        if lbl in _MIXED_LABELS:
            return MIXED
        if lbl in _INSTRUMENTAL_LABELS:
            return INSTRUMENTAL
    if slot == "1":
        return MIXED
    if slot == "2":
        return INSTRUMENTAL
    return None


def _split_artist_title(body: str) -> tuple[str, str]:
    """Split ``Artist - Title`` on the first ` - `; whole string is the title if none."""
    body = body.strip()
    if " - " in body:
        artist, title = body.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", body


def _parse_one(filename: str) -> Optional[_Parsed]:
    stem = Path(filename).stem.strip()

    m = _CODED_RE.match(stem)
    if m:
        role = _label_role(m.group("label"), m.group("slot"))
        artist, title = _split_artist_title(m.group("body"))
        return _Parsed(filename, m.group("code"), role, artist, title)

    m = _LABELLED_RE.match(stem)
    if m:
        role = _label_role(m.group("label"), None)
        artist, title = _split_artist_title(m.group("body"))
        return _Parsed(filename, None, role, artist, title)

    return None


def _pair_key(p: _Parsed) -> str:
    """Group files that belong together. Prefer the S-code; else artist+title."""
    if p.code:
        return f"code:{p.code}"
    return f"song:{p.artist.lower()}|{p.title.lower()}"


def _regex_analyze(audio_filenames: list[str]) -> tuple[list[ProposedRow], list[str]]:
    """Regex fast path. Returns (confident rows, filenames left unpaired)."""
    parsed: list[_Parsed] = []
    unparseable: list[str] = []
    for fn in audio_filenames:
        p = _parse_one(fn)
        if p is None or p.role is None:
            unparseable.append(fn)
        else:
            parsed.append(p)

    # Group and pair mixed<->instrumental within each group.
    groups: dict[str, list[_Parsed]] = {}
    for p in parsed:
        groups.setdefault(_pair_key(p), []).append(p)

    rows: list[ProposedRow] = []
    leftover: list[str] = list(unparseable)
    for members in groups.values():
        mixed = [p for p in members if p.role == MIXED]
        instrumental = [p for p in members if p.role == INSTRUMENTAL]
        # Pair up as many as we can, one-to-one.
        for m, i in zip(mixed, instrumental):
            artist = m.artist or i.artist
            title = m.title or i.title
            rows.append(
                ProposedRow(
                    artist=artist,
                    title=title,
                    mixed_filename=m.filename,
                    instrumental_filename=i.filename,
                    confidence="high",
                )
            )
        # Anything without a partner is left over for the LLM / warnings.
        leftover.extend(p.filename for p in mixed[len(instrumental):])
        leftover.extend(p.filename for p in instrumental[len(mixed):])

    return rows, leftover


# ---------------------------------------------------------------------------
# LLM pass (optional)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You organise karaoke audio files for bulk import. You are given a list of "
    "audio filenames that a first-pass matcher could not confidently handle. "
    "Each song usually has TWO files: a MIXED version (the full song, contains "
    "lead vocals — often labelled Guide, Lead, Vocal) and an INSTRUMENTAL "
    "version (backing track, no lead vocals — often labelled Instrumental, "
    "Instru, BV, Backing, Karaoke). Pair the mixed and instrumental versions of "
    "the SAME song, and extract the artist and title from the filename.\n"
    "Return JSON with `rows` (confident mixed+instrumental pairs) and `unpaired` "
    "(files with no partner). Only use filenames exactly as given. Never invent "
    "files. A file may appear in at most one row. If unsure, put it in `unpaired`."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                    "mixed_filename": {"type": "string"},
                    "instrumental_filename": {"type": "string"},
                    "confidence": {"type": "string"},
                    "warning": {"type": "string"},
                },
                "required": ["mixed_filename", "instrumental_filename"],
            },
        },
        "unpaired": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["filename"],
            },
        },
    },
    "required": ["rows"],
}


def _llm_analyze(
    filenames: list[str], generate: Generate
) -> tuple[list[ProposedRow], list[str]]:
    """Run the LLM over leftover filenames. Returns (rows, still-unpaired)."""
    user_prompt = "Filenames:\n" + "\n".join(f"- {fn}" for fn in filenames)
    data = generate(_SYSTEM_PROMPT, user_prompt)
    if not isinstance(data, dict):
        return [], list(filenames)

    available = set(filenames)
    used: set[str] = set()
    rows: list[ProposedRow] = []
    for raw in data.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        mixed = (raw.get("mixed_filename") or "").strip()
        instrumental = (raw.get("instrumental_filename") or "").strip()
        # Guard against hallucinated / duplicate filenames.
        if mixed not in available or instrumental not in available:
            continue
        if mixed == instrumental or mixed in used or instrumental in used:
            continue
        used.add(mixed)
        used.add(instrumental)
        confidence = str(raw.get("confidence") or "medium").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        rows.append(
            ProposedRow(
                artist=str(raw.get("artist") or "").strip(),
                title=str(raw.get("title") or "").strip(),
                mixed_filename=mixed,
                instrumental_filename=instrumental,
                confidence=confidence,
                warning=(str(raw["warning"]).strip() if raw.get("warning") else None),
            )
        )

    still_unpaired = [fn for fn in filenames if fn not in used]
    return rows, still_unpaired


def _describe_unpaired(filename: str) -> UnpairedFile:
    """Best-effort artist/title + reason for a file with no partner."""
    p = _parse_one(filename)
    if p is None:
        return UnpairedFile(filename=filename, reason="unparseable")
    if p.role == MIXED:
        reason = "no_instrumental"
    elif p.role == INSTRUMENTAL:
        reason = "no_mixed"
    else:
        reason = "unparseable"
    return UnpairedFile(
        filename=filename,
        reason=reason,
        artist=p.artist or None,
        title=p.title or None,
        role=p.role,
    )


def analyze_filenames(
    filenames: list[str],
    *,
    generate: Optional[Generate] = None,
) -> BulkAnalysis:
    """Analyse a flat list of filenames into proposed jobs.

    Args:
        filenames: filenames from the operator's folder pick (no paths needed).
        generate: optional LLM callable (system_prompt, user_prompt) -> dict.
            When None, only the deterministic regex pass runs (used by tests and
            as the resilient fallback when the model is unavailable).
    """
    seen: set[str] = set()
    audio: list[str] = []
    ignored: list[IgnoredFile] = []
    for fn in filenames:
        fn = (fn or "").strip()
        if not fn or fn in seen:
            continue
        seen.add(fn)
        ext = Path(fn).suffix.lower()
        if ext in AUDIO_EXTENSIONS:
            audio.append(fn)
        else:
            ignored.append(IgnoredFile(filename=fn))

    rows, leftover = _regex_analyze(audio)

    if leftover and generate is not None:
        try:
            llm_rows, leftover = _llm_analyze(leftover, generate)
            rows.extend(llm_rows)
        except Exception as e:  # pragma: no cover - resilience guard
            logger.warning("Bulk-analyze LLM pass failed, using regex result: %s", e)

    unpaired = [_describe_unpaired(fn) for fn in leftover]
    return BulkAnalysis(rows=rows, unpaired=unpaired, ignored=ignored)


# ---------------------------------------------------------------------------
# Default Vertex-Gemini generate (mirrors backend.services.match_judge.ai)
# ---------------------------------------------------------------------------

def _default_model() -> str:
    try:
        from backend.config import settings

        return getattr(settings, "match_judge_model", "gemini-3.5-flash")
    except Exception:  # pragma: no cover - config import guard
        return "gemini-3.5-flash"


def default_generate(system_prompt: str, user_prompt: str) -> dict:
    """Blocking Vertex-Gemini call producing schema-constrained JSON.

    Wrap in ``asyncio.to_thread`` when calling from async code.
    """
    from google import genai
    from google.genai import types

    from backend.config import settings

    timeout_ms = int(getattr(settings, "tenant_bulk_timeout_ms", 30000))
    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location="global",
        http_options=types.HttpOptions(timeout=timeout_ms),
    )
    response = client.models.generate_content(
        model=_default_model(),
        contents=[user_prompt],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=copy.deepcopy(RESPONSE_SCHEMA),
            temperature=0,
        ),
    )
    return json.loads(response.text)
