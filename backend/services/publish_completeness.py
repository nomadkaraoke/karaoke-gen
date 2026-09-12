"""Publish-boundary completeness invariant (incident-hardening G1), shadow-first.

Incident NOMAD-1632 published a public track to Google Drive **missing its 720p
variant** while the pipeline reported success: ``GCEEncodingBackend.encode()``
misclassified ``… (Final Karaoke Lossy 720p).mp4`` (title contained "Portrait")
so ``mp4_720p`` was never set, and ``upload_to_public_share`` *silently skipped*
the ``None`` path. Nothing inline noticed; the daily validator caught it ~24h
later.

This module computes, at the publish boundary, the set of outputs a public
GDrive release *should* have shipped and compares it against what was actually
distributed (``result.gdrive_files``). The orchestrator logs + alerts on any
shortfall **in shadow mode** (never fails the job) — the right altitude for the
check (publish-aware of what *should* ship) and the rollout discipline the
original fix skipped.

The compute function is intentionally pure (config + result in, list of missing
outputs out) so it is trivially unit-testable and side-effect free.
"""

from __future__ import annotations

from typing import Any

# gdrive_files keys produced by GDriveService.upload_to_public_share(), mapped to
# a human-readable label for alerts.
_LOSSY_4K_KEY = "mp4"
_720P_KEY = "mp4_720p"
_CDG_KEY = "cdg"

_LABELS = {
    _LOSSY_4K_KEY: "lossy 4K MP4",
    _720P_KEY: "720p MP4",
    _CDG_KEY: "CDG zip",
}


def expected_gdrive_outputs(config: Any) -> list[str]:
    """The gdrive_files keys a public-share release is expected to contain.

    Always the lossy 4K MP4 and the 720p MP4; the CDG zip only when the job has
    CDG enabled. Mirrors what ``_upload_to_gdrive`` feeds into
    ``upload_to_public_share``.
    """
    expected = [_LOSSY_4K_KEY, _720P_KEY]
    if getattr(config, "enable_cdg", False):
        expected.append(_CDG_KEY)
    return expected


def compute_publish_shortfall(config: Any, result: Any) -> list[str]:
    """Return human-readable labels for expected-but-missing public outputs.

    Empty list == complete (or not a public-share release). Only meaningful for
    the public GDrive distribution path (``config.gdrive_folder_id`` set); returns
    ``[]`` for any other job so callers can invoke it unconditionally.
    """
    if not getattr(config, "gdrive_folder_id", None):
        return []

    distributed = getattr(result, "gdrive_files", None) or {}
    missing: list[str] = []
    for key in expected_gdrive_outputs(config):
        # A key is satisfied only if present AND truthy (an empty id/url means the
        # upload didn't actually land).
        if not distributed.get(key):
            missing.append(_LABELS.get(key, key))
    return missing
