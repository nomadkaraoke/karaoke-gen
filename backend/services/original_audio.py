"""Helpers for locating and naming a job's original input audio.

The original downloaded/uploaded audio (the source used for separation,
transcription and the With Vocals video) is stored in GCS but is no longer
copied into the final output folder by the distributed pipeline. These helpers
let the orchestrator (and the backfill script) restore it to the Dropbox track
folder with the historical source-tagged name, e.g.::

    <artist> - <title> (flacfetch).flac
    <artist> - <title> (uploaded).mp3
    <artist> - <title> (YouTube).webm

The naming mirrors the legacy monolith convention.
"""

import os
from typing import Optional

# Map a job's audio_source_type to the historical filename suffix.
_SOURCE_TAGS = {
    "file_upload": "uploaded",
    "youtube_url": "YouTube",
    "audio_search": "flacfetch",
}


def original_audio_source_tag(job) -> str:
    """Return the parenthesised source suffix for a job's original audio.

    Falls back gracefully for older jobs that predate ``audio_source_type``.
    """
    tag = _SOURCE_TAGS.get(getattr(job, "audio_source_type", None))
    if tag:
        return tag
    # Fallbacks for legacy jobs without an explicit source type.
    if getattr(job, "url", None):
        return "YouTube"
    if getattr(job, "filename", None) or getattr(job, "input_media_gcs_path", None):
        return "uploaded"
    return "input"


def original_audio_gcs_path(job) -> Optional[str]:
    """Return the GCS blob path of the original input audio, or None.

    Prefers ``input_media_gcs_path`` (uploads are persisted under jobs/ and URL
    jobs record their downloaded file here), then falls back to
    ``file_urls['input']`` which may be a plain string or a nested
    ``{'audio': ...}`` dict depending on the code path that wrote it.
    """
    path = getattr(job, "input_media_gcs_path", None)
    if path:
        return path
    file_urls = getattr(job, "file_urls", None) or {}
    inp = file_urls.get("input")
    if isinstance(inp, dict):
        return inp.get("audio")
    if isinstance(inp, str):
        return inp
    return None


def original_audio_output_filename(job, base_name: str) -> Optional[str]:
    """Return the output filename for the original audio, or None if absent.

    e.g. ``"<artist> - <title> (flacfetch).flac"``. The extension is preserved
    from the GCS path.
    """
    gcs_path = original_audio_gcs_path(job)
    if not gcs_path:
        return None
    ext = os.path.splitext(gcs_path)[1]
    return f"{base_name} ({original_audio_source_tag(job)}){ext}"
