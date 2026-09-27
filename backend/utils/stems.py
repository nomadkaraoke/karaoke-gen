"""Stem-selection helpers shared by review routes and workers."""

from typing import Optional


def vocals_stem_path(job) -> Optional[str]:
    """The full-vocal-mix stem for the review waveform, or None if absent.

    Stem keys vary by separation model. We want the full vocal mix
    (lead + backing) so every word can be lined up against the waveform:
      - "vocals"       : full vocal from a 2-stem split (rare)
      - "vocals_clean" : full vocal from the primary vocal/instrumental
                         split (present on essentially all cloud jobs)
      - "lead_vocals"  : fallback (misses backing lines)
      - "vocals_derived": mix minus the user's own instrumental, for jobs that
                         skipped separation (backend/services/derived_vocals.py).
                         Waveform-only — never a deliverable stem.
    """
    stems = job.file_urls.get("stems", {}) if job.file_urls else {}
    for key in ("vocals", "vocals_clean", "lead_vocals", "vocals_derived"):
        if stems.get(key):
            return stems[key]
    return None
