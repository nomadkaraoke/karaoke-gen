"""Glyph-coverage font fallback for PIL-rendered text (title/end cards, text measurement).

Theme fonts (Avenir Next, Montserrat, ...) are Latin-only. libass falls back to a
system font per glyph via fontconfig, but PIL does not — it draws the theme font's
.notdef box ("tofu") for every missing glyph. That is how a Hebrew title rendered as
a row of "?" boxes on the title card.

``resolve_font_for_text`` returns the configured font when it covers every character
of the text, otherwise a system font (via fontconfig ``charset=``) that does.
"""
from __future__ import annotations

import functools
import logging
import os
import subprocess
import unicodedata
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Characters that never need a glyph: whitespace, controls, format chars (bidi marks,
# ZWJ/ZWNJ), and variation selectors.
_IGNORED_CATEGORIES = {"Zs", "Zl", "Zp", "Cc", "Cf"}


def _needs_glyph(ch: str) -> bool:
    if unicodedata.category(ch) in _IGNORED_CATEGORIES:
        return False
    cp = ord(ch)
    return not (0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF)


@functools.lru_cache(maxsize=64)
def _font_codepoints(font_path: str) -> Optional[FrozenSet[int]]:
    """Return the set of codepoints a font file maps, or None if it can't be read."""
    try:
        from fontTools.ttLib import TTFont

        kwargs = {"fontNumber": 0} if font_path.lower().endswith((".ttc", ".otc")) else {}
        with TTFont(font_path, lazy=True, **kwargs) as font:
            return frozenset(font.getBestCmap() or {})
    except Exception as e:  # corrupt/unsupported font — treat coverage as unknown
        logger.debug(f"Could not read cmap from {font_path}: {e}")
        return None


def missing_codepoints(font_path: Optional[str], text: Optional[str]) -> FrozenSet[int]:
    """Codepoints in ``text`` that ``font_path`` has no glyph for.

    Unreadable/nonexistent fonts are treated as covering everything (we can't tell,
    so don't override the caller's choice). ``None`` means PIL's default font, which
    only covers Latin (plus typographic punctuation like ’ “ ” – …).
    """
    if not text:
        return frozenset()
    needed = {ord(ch) for ch in text if _needs_glyph(ch)}
    if font_path is None:
        return frozenset(cp for cp in needed if cp >= 0x0250 and not 0x2000 <= cp <= 0x206F)
    if not os.path.exists(font_path):
        return frozenset()
    cmap = _font_codepoints(font_path)
    if cmap is None:
        return frozenset()
    return frozenset(needed - cmap)


def covers_char(font_path: Optional[str], ch: str) -> bool:
    """True if ``font_path`` has a glyph for ``ch`` (or ``ch`` needs none)."""
    return not missing_codepoints(font_path, ch)


@functools.lru_cache(maxsize=64)
def ass_font_scale(font_path: str) -> float:
    """Ratio of PIL point size to ASS ``Fontsize`` for this font.

    libass scales a font so ascender+descender (OS/2 usWin*, else hhea) equals the ASS
    font size, whereas PIL sizes by em. For Avenir Next Bold this is 0.732; for Arial
    0.895 — a single constant mis-measures fallback-font text by 20%+.
    """
    try:
        from fontTools.ttLib import TTFont

        kwargs = {"fontNumber": 0} if font_path.lower().endswith((".ttc", ".otc")) else {}
        with TTFont(font_path, lazy=True, **kwargs) as font:
            upem = font["head"].unitsPerEm
            os2 = font["OS/2"] if "OS/2" in font else None
            if os2 is not None and (os2.usWinAscent + os2.usWinDescent) > 0:
                extent = os2.usWinAscent + os2.usWinDescent
            else:
                extent = font["hhea"].ascent - font["hhea"].descent
            return upem / extent if extent > 0 else 0.70
    except Exception as e:
        logger.debug(f"Could not read metrics from {font_path}: {e}")
        return 0.70


def font_covers_text(font_path: Optional[str], text: Optional[str]) -> bool:
    return not missing_codepoints(font_path, text)


# Successful lookups only: a transient fontconfig failure (e.g. a timeout on a cold
# font cache) must not disable fallback for the rest of the process.
_FALLBACK_CACHE: Dict[Tuple[FrozenSet[int], bool], str] = {}


def _covers(font_path: str, codepoints: FrozenSet[int]) -> bool:
    return bool(font_path) and os.path.exists(font_path) and not (codepoints - (_font_codepoints(font_path) or frozenset()))


def _fontconfig(cmd: List[str], timeout: float) -> Optional[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"fontconfig lookup failed ({e}); cannot find fallback font")
        return None
    return result.stdout if result.returncode == 0 else None


def find_font_covering(codepoints: FrozenSet[int], bold: bool = True) -> Optional[str]:
    """Ask fontconfig for a sans font (bold if ``bold``) covering all ``codepoints``.

    fc-match always returns *something*, so its answer's cmap is verified; only if it
    doesn't cover everything do we scan every font fc-list reports for the charset.
    Returns None when no installed font covers all of them.
    """
    if not codepoints:
        return None
    key = (codepoints, bold)
    if key in _FALLBACK_CACHE:
        return _FALLBACK_CACHE[key]

    charset = " ".join(f"{cp:x}" for cp in sorted(codepoints))
    weight = ":weight=bold" if bold else ""
    found = None
    match = _fontconfig(["fc-match", "--format=%{file}", f"sans-serif{weight}:charset={charset}"], timeout=5)
    if match and _covers(match.strip(), codepoints):
        found = match.strip()
    else:
        listing = _fontconfig(["fc-list", f":charset={charset}", "file"], timeout=10) or ""
        files = sorted(line.split(":")[0].strip() for line in listing.splitlines() if line.strip())
        # Prefer Noto Sans (installed in the Cloud Run + encoding worker images) in the
        # requested weight
        files.sort(key=lambda f: ("noto" not in f.lower(), ("bold" in f.lower()) != bold, "sans" not in f.lower()))
        found = next((f for f in files if _covers(f, codepoints)), None)

    if found:
        _FALLBACK_CACHE[key] = found
    return found


find_font_covering.cache_clear = _FALLBACK_CACHE.clear  # type: ignore[attr-defined]


def resolve_font_for_text(font_path: Optional[str], text: Optional[str], bold: bool = True) -> Optional[str]:
    """Return ``font_path`` if it covers ``text``, else a covering system font.

    Falls back to ``font_path`` unchanged when no installed font covers the text.
    """
    missing = missing_codepoints(font_path, text)
    if not missing:
        return font_path
    # PIL draws the whole string with one font, so the fallback must cover all of it
    # (e.g. "Omer Adam - שני משוגעים"), not just the characters the theme font lacks.
    needed = frozenset(ord(ch) for ch in text if _needs_glyph(ch))
    fallback = find_font_covering(needed, bold=bold)
    if fallback:
        logger.info(f"Font {font_path} lacks glyphs for {len(missing)} char(s) of {text[:30]!r}; using {fallback}")
        return fallback
    logger.warning(f"No installed font covers {text[:30]!r}; glyphs may render as boxes")
    return font_path
