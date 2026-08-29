"""Single source of truth for the auto instrumental-selection decision.

The backing decision is derived in three places that MUST agree:
- the executor, when auto-completing a job (live verdict object);
- ``POST /review/{id}/complete`` when a client sends ``instrumental_selection="auto"``
  (reads the stored ``processing_metadata.auto_approval`` verdict);
- the ``/correction-data`` response summary the frontend uses to decide whether to
  show the instrumental screen at all (per-screen skip, C1).

``backing_decision`` is a pure function over primitives so all three can call it with
either the live verdict or the persisted dict. It also folds in the user's up-front
``backing_preference`` (C3): ``clean`` forces a clean instrumental, ``review`` suppresses
the auto backing decision so a human always picks.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

BACKING_PREFERENCE_AUTO = "auto"  # retain backing vocals where confidently safe (default)
BACKING_PREFERENCE_CLEAN = "clean"  # always use the clean instrumental
BACKING_PREFERENCE_REVIEW = "review"  # always let a human pick the instrumental
VALID_BACKING_PREFERENCES = (
    BACKING_PREFERENCE_AUTO,
    BACKING_PREFERENCE_CLEAN,
    BACKING_PREFERENCE_REVIEW,
)


def has_custom_instrumental(job) -> bool:
    """True when the user supplied their own instrumental (tenant bulk uploads,
    the upload flow's existing-instrumental option, or a mute-region edit).

    For these jobs there is NO instrumental decision to make — the human
    complete-review flow submits ``instrumental_selection="custom"``.
    """
    stems = (job.file_urls or {}).get("stems", {}) if job.file_urls else {}
    return bool(
        getattr(job, "existing_instrumental_gcs_path", None)
        or stems.get("custom_instrumental")
    )


def backing_decision(
    *,
    backing_verdict: Optional[str],
    backing_non_subjective: bool,
    backing_preference: Optional[str],
    backing_keep_enabled: bool,
    custom_instrumental: bool,
) -> Dict[str, Any]:
    """Resolve the instrumental decision from a (live or stored) backing verdict.

    Returns ``{"ok": bool, "selection": Optional[str], "keep_backing": bool}``:
    - ``ok`` — the instrumental can be auto-selected (no human needed for this half).
    - ``selection`` — "custom" | "clean" | "with_backing" | None (None ⇒ human must pick).
    - ``keep_backing`` — informational: the confident verdict is a with-backing keep
      (used by the executor to require the with-backing stem).
    """
    if custom_instrumental:
        return {"ok": True, "selection": "custom", "keep_backing": False}

    pref = backing_preference or BACKING_PREFERENCE_AUTO
    if pref == BACKING_PREFERENCE_CLEAN:
        # User asked to always strip backing vocals.
        return {"ok": True, "selection": "clean", "keep_backing": False}
    if pref == BACKING_PREFERENCE_REVIEW:
        # User asked to always decide the instrumental themselves.
        return {"ok": False, "selection": None, "keep_backing": False}

    # pref == auto: rely on the non-subjective backing verdict.
    if not backing_non_subjective:
        return {"ok": False, "selection": None, "keep_backing": False}
    if backing_verdict == "with_backing":
        # Confident 3-stem keep — gated shadow-first behind its own flag.
        if not backing_keep_enabled:
            return {"ok": False, "selection": None, "keep_backing": True}
        return {"ok": True, "selection": "with_backing", "keep_backing": True}
    # Confident CLEAN ("no audible backing").
    return {"ok": True, "selection": "clean", "keep_backing": False}


def auto_approval_summary(job, settings) -> Dict[str, Any]:
    """Compact block for the ``/correction-data`` response (C1 per-screen skip).

    Reads the stored ``processing_metadata.auto_approval`` verdict + the job's
    ``backing_preference`` and tells the frontend whether each review half is
    confidently resolved (so it can skip that screen).
    """
    aa = (getattr(job, "processing_metadata", None) or {}).get("auto_approval") or {}
    backing = aa.get("backing") or {}
    lyrics = aa.get("lyrics") or {}
    custom = has_custom_instrumental(job)
    decision = backing_decision(
        backing_verdict=backing.get("verdict"),
        backing_non_subjective=bool(backing.get("non_subjective")),
        backing_preference=getattr(job, "backing_preference", BACKING_PREFERENCE_AUTO),
        backing_keep_enabled=bool(getattr(settings, "auto_approval_backing_keep_enabled", False)),
        custom_instrumental=custom,
    )
    return {
        "backing": {
            "verdict": backing.get("verdict"),
            "confident": decision["ok"],
            "resolved_selection": decision["selection"],
        },
        "lyrics": {
            "verdict": lyrics.get("verdict"),
            "confident": lyrics.get("verdict") == "auto",
        },
        "custom_instrumental": custom,
        "verdict_present": bool(aa),
    }


def resolve_auto_instrumental(job, settings) -> Optional[str]:
    """Map a client's ``instrumental_selection="auto"`` to a concrete selection
    from the stored verdict, or None when it isn't confidently resolvable."""
    return auto_approval_summary(job, settings)["backing"]["resolved_selection"]
