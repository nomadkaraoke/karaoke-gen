"""
Tempo-adjustment labeling.

When a user changes the tempo in the audio editor, every published output must
say so — a singer who picks "Song Title" and gets a 90%-speed backing track is
in for a nasty surprise. Every output (title/end screens, CDG title screen,
final filenames, Dropbox folder, GDrive/kjbox mirror, YouTube title/description,
emails, downloads) reads ``job.title``, so at audio-edit submit we fold a
"(90% Tempo)" suffix into ``job.title`` — the same way display-title overrides
already work — and pin the original title into ``lyrics_title`` so lyrics
search keeps matching the real song.

Mirrors frontend/lib/tempo.ts — keep the label format in sync.
"""

import math
import re
from typing import Iterable, Mapping, Optional

# Matches a trailing " (90% Tempo)" suffix we added (and only that).
_TEMPO_SUFFIX_RE = re.compile(r"\s*\((\d{1,3})% Tempo\)\s*$")


def cumulative_tempo_factor(edit_stack: Iterable[Mapping]) -> float:
    """Product of all tempo edits in an audio edit stack (1.0 = original speed)."""
    factor = 1.0
    for entry in edit_stack or []:
        if entry.get("operation") != "tempo":
            continue
        try:
            value = float((entry.get("params") or {}).get("factor"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            factor *= value
    return factor


def tempo_percent(factor: float) -> int:
    """Tempo as a whole percentage of the original (0.9 -> 90).

    Rounds half up, like the editor's ``Math.round`` (frontend/lib/tempo.ts) —
    Python's banker's ``round`` would label 94.5% as 94 while the UI promised 95.
    """
    return int(math.floor(factor * 100 + 0.5))


def is_tempo_adjusted(factor: Optional[float]) -> bool:
    """True if the factor is far enough from 1.0 to show as something other than 100%."""
    return factor is not None and factor > 0 and tempo_percent(factor) != 100


def tempo_suffix(factor: float) -> str:
    return f"({tempo_percent(factor)}% Tempo)"


def strip_tempo_suffix(title: Optional[str]) -> Optional[str]:
    """Remove a tempo suffix previously added by :func:`apply_tempo_to_title`."""
    if not title:
        return title
    return _TEMPO_SUFFIX_RE.sub("", title)


def tempo_percent_from_title(title: Optional[str]) -> Optional[int]:
    """The percentage from a title's tempo label, or None if it has none."""
    match = _TEMPO_SUFFIX_RE.search(title or "")
    return int(match.group(1)) if match else None


def apply_tempo_to_title(title: Optional[str], factor: Optional[float]) -> Optional[str]:
    """Return ``title`` labeled for ``factor`` (idempotent; replaces any existing label)."""
    base = strip_tempo_suffix(title)
    if not base or not is_tempo_adjusted(factor):
        return base
    return f"{base} {tempo_suffix(factor)}"


def tempo_description_notice(title: Optional[str]) -> str:
    """One-line notice for published descriptions of a tempo-labeled title, else ""."""
    pct = tempo_percent_from_title(title)
    if pct is None or pct == 100:
        return ""
    direction = "slowed down" if pct < 100 else "sped up"
    return (
        f"Note: this karaoke version has been {direction} to {pct}% of the original "
        f"song's tempo (same key)."
    )
