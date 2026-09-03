"""
YouTube description + tags rendering (single source of truth).

Both the live upload pipeline (video_worker_orchestrator, youtube_queue_processor)
and the bulk-rewrite maintenance tool (scripts/youtube-descriptions) render
descriptions through this module so on-channel descriptions stay consistent with
newly published ones.

The canonical template lives in ``Settings.default_youtube_description``
(``backend/config.py``, overridable via the ``DEFAULT_YOUTUBE_DESCRIPTION`` env
var). It may contain these placeholders, all optional:

    {title}           - song title
    {artist}          - artist name
    {artist_hashtag}  - artist name reduced to a valid hashtag (e.g. "#Coldplay")
    {brand_code}      - the NOMAD-#### brand code line

Templates without placeholders still work: legacy/tenant-custom templates that
predate placeholders keep the historical behaviour of appending
``\n\nBrand Code: <code>`` when a brand code is available.
"""
import re
from typing import List, Optional

# Total length budget for the YouTube ``tags`` field (API rejects > 500 chars,
# counted as the sum of tag lengths plus separators). Stay comfortably under.
_MAX_TAGS_TOTAL_CHARS = 460


def hashtagify(text: Optional[str]) -> str:
    """Reduce a string to the characters that are valid inside a hashtag.

    Keeps unicode word characters (letters, digits, underscore) and drops
    everything else, so ``"P!nk"`` -> ``"Pnk"`` and ``"Twenty One Pilots"`` ->
    ``"TwentyOnePilots"``. Returns ``""`` when nothing usable remains.
    """
    if not text:
        return ""
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE)


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of 3+ newlines down to a blank-line separator."""
    return re.sub(r"\n{3,}", "\n\n", text)


def render_youtube_description(
    artist: Optional[str] = None,
    title: Optional[str] = None,
    brand_code: Optional[str] = None,
    template: Optional[str] = None,
) -> str:
    """Render a YouTube description from the template + per-video values.

    Args:
        artist: Artist name (may be empty/unknown).
        title: Song title (may be empty/unknown).
        brand_code: NOMAD-#### style code, or falsy to omit the brand-code line.
        template: Override template. Defaults to
            ``Settings.default_youtube_description``.

    Returns:
        The fully rendered description string, stripped of trailing whitespace.
    """
    if template is None:
        # Lazy import to avoid any import-time coupling with config.
        from backend.config import get_settings

        template = get_settings().default_youtube_description or ""

    result = template

    # Simple token substitution (avoid str.format so stray braces in custom
    # templates never raise).
    result = result.replace("{artist}", artist or "")
    result = result.replace("{title}", title or "")

    artist_tag = hashtagify(artist)
    if artist_tag:
        result = result.replace("{artist_hashtag}", artist_tag)
    else:
        # Drop the artist hashtag entirely, including a leading separator space.
        result = result.replace(" #{artist_hashtag}", "")
        result = result.replace("#{artist_hashtag}", "")
        result = result.replace("{artist_hashtag}", "")

    if "{brand_code}" in result:
        if brand_code:
            result = result.replace("{brand_code}", brand_code)
        else:
            # Remove any line carrying the placeholder so we don't leave a
            # dangling "Brand Code:" label.
            result = "\n".join(
                line for line in result.split("\n") if "{brand_code}" not in line
            )
    elif brand_code:
        # Legacy template without a placeholder: preserve historical append.
        result = f"{result}\n\nBrand Code: {brand_code}"

    return _collapse_blank_lines(result).strip()


def build_youtube_tags(artist: Optional[str], title: Optional[str]) -> List[str]:
    """Build a de-duplicated, length-capped YouTube tags list.

    Richer than the historical ``["karaoke", artist, title]`` for discoverability,
    while respecting YouTube's ~500 char total tags budget.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()

    candidates = [
        "karaoke",
        "instrumental",
        "lyrics",
        "karaoke version",
        "sing along",
        "backing track",
        artist,
        title,
        f"{artist} karaoke" if artist else "",
        f"{artist} {title} karaoke" if artist and title else "",
    ]

    tags: List[str] = []
    seen = set()
    total = 0
    for tag in candidates:
        tag = tag.strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        # +1 approximates the separator cost the API counts between tags.
        cost = len(tag) + 1
        if total + cost > _MAX_TAGS_TOTAL_CHARS:
            continue
        seen.add(key)
        tags.append(tag)
        total += cost

    return tags
