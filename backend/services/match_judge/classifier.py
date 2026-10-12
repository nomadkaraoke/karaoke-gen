"""Deterministic + catalog match classification (no AI, no network).

`classify_catalog_match` returns a confident :class:`MatchVerdict` when the
typed artist/title clearly correspond to a catalog candidate (a cosmetic
formatting fix, or nothing to change). When it cannot confidently decide, it
returns ``None`` — the caller should then escalate to the AI judge.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from karaoke_gen.utils import normalize_text

from backend.services.match_judge.verdict import (
    KIND_COSMETIC,
    KIND_NONE,
    MatchVerdict,
)

# Unicode-aware: keeps letters/digits of every script (Cyrillic, CJK, Arabic…).
# An ASCII-only class here erased all non-Latin text, so every non-Latin song
# normalized to "" and they all "matched" each other. "_" is word-class in
# regex, so it's stripped explicitly.
_NON_ALNUM = re.compile(r"[^\w\s]|_")
_WS = re.compile(r"\s+")

# Combining marks are folded only on Latin/Greek/Cyrillic bases, where they're
# diacritics ('é' -> 'e'). Elsewhere they carry meaning (Japanese voiced kana,
# Indic vowel signs), so they're kept.
_DIACRITIC_BASE_MAX = 0x052F


def _fold_accents(text: str) -> str:
    """Drop diacritics so 'Beyoncé' and 'beyonce' compare equal."""
    out: list[str] = []
    base_ord = 0
    for c in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(c):
            if base_ord <= _DIACRITIC_BASE_MAX:
                continue
        else:
            base_ord = ord(c)
        out.append(c)
    return unicodedata.normalize("NFC", "".join(out))


def normalize_for_match(text: str) -> str:
    """Aggressive key used only for equality comparison (never displayed).

    Folds casing, accents, punctuation, whitespace, and treats '&' as 'and'.
    Non-Latin letters are preserved. Can return "" (blank/punctuation-only
    input) — callers must not treat two empty keys as a match.
    """
    s = normalize_text(text or "")
    s = s.replace("&", " and ")
    s = _fold_accents(s)
    s = s.casefold()
    s = _NON_ALNUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def classify_catalog_match(
    artist: str, title: str, candidates: list[dict]
) -> Optional[MatchVerdict]:
    """Return a confident verdict from catalog candidates, or None to escalate.

    candidates: dicts with ``artist_name`` and ``track_name`` (Spotify/MB canonical).
    """
    key_artist = normalize_for_match(artist)
    key_title = normalize_for_match(title)

    for cand in candidates:
        cand_artist = cand.get("artist_name") or ""
        cand_title = cand.get("track_name") or ""
        if (
            normalize_for_match(cand_artist) == key_artist
            and normalize_for_match(cand_title) == key_title
        ):
            # Same song. Cosmetic fix unless the user already typed it exactly.
            if cand_artist == artist and cand_title == title:
                return MatchVerdict(
                    kind=KIND_NONE,
                    confident=True,
                    canonical_artist=artist,
                    canonical_title=title,
                    engine="catalog",
                    reason="already canonical",
                )
            return MatchVerdict(
                kind=KIND_COSMETIC,
                confident=True,
                canonical_artist=cand_artist,
                canonical_title=cand_title,
                engine="catalog",
                reason="formatting differs from catalog",
            )

    return None
