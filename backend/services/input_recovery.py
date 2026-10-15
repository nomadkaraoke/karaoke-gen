"""
Recover a job's missing input audio before a render/encode needs it.

Jobs created before v0.268.2 (2026-10) kept their uploaded mix and
bring-your-own instrumental only under ``uploads/{job_id}/``, which had a 7-day
lifecycle delete, so those inputs are gone. A re-render of such a job failed in
the encoder with a 404 on ``uploads/.../<mix>.mp3`` (2026-10-15, Vocal Star job
5fca18dd). What survives, and how we recover:

- the mix (``input_media_gcs_path``): first a persisted copy under
  ``jobs/{id}/input/``; otherwise the audio track of the job's with-vocals render
  (``finals/with_vocals_mp4.mp4`` / ``videos/with_vocals.*``). That render's audio
  is the original mix (re-encoded to AAC), shifted by any countdown padding,
  which is trimmed off again. It's decoded to FLAC at ``jobs/{id}/input/recovered_mix.flac``.
- the instrumental (``existing_instrumental_gcs_path``): a persisted copy, or the
  copy staged at the job root / in stems (``existing_instrumental.*`` /
  ``custom_instrumental.*``), which storage retention keeps.

``recover_missing_inputs`` is idempotent and cheap when nothing is missing (a few
existence checks), so workers call it on entry right after
``ensure_job_inputs_persisted``.
"""
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.services.input_persistence import persisted_path

logger = logging.getLogger(__name__)

RECOVERED_MIX_NAME = "recovered_mix.flac"
FFMPEG_TIMEOUT_S = 300
_AUDIO_EXTS = (".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus")


class InputMissingError(RuntimeError):
    """The job's input audio is gone and couldn't be recovered."""


def _blob_path(path: Optional[str]) -> Optional[str]:
    if not isinstance(path, str) or not path:
        return None
    if path.startswith("gs://"):
        return path.split("/", 3)[-1] if path.count("/") >= 3 else None
    return path


def _with_vocals_candidates(job) -> List[str]:
    job_id = job.job_id
    file_urls = getattr(job, "file_urls", None) or {}
    candidates = [
        (file_urls.get("finals") or {}).get("with_vocals_mp4"),
        (file_urls.get("videos") or {}).get("with_vocals"),
        f"jobs/{job_id}/finals/with_vocals_mp4.mp4",
        f"jobs/{job_id}/videos/with_vocals.mkv",
        f"jobs/{job_id}/videos/with_vocals.mov",
    ]
    out: List[str] = []
    for c in candidates:
        p = _blob_path(c)
        if p and p not in out:
            out.append(p)
    return out


def _countdown_padding_seconds(job) -> float:
    state = getattr(job, "state_data", None) or {}
    if not (state.get("countdown_padding_added") or state.get("has_countdown_padding")):
        return 0.0
    try:
        return max(0.0, float(state.get("countdown_padding_seconds") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _extract_mix(storage, video_path: str, padding_s: float, workdir: str) -> str:
    local_video = os.path.join(workdir, "with_vocals" + (os.path.splitext(video_path)[1] or ".mp4"))
    storage.download_file(video_path, local_video)
    out = os.path.join(workdir, RECOVERED_MIX_NAME)
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", local_video]
    if padding_s > 0:
        # Output-side seek: sample-accurate trim of the countdown silence.
        cmd += ["-ss", f"{padding_s:.3f}"]
    cmd += ["-map", "0:a:0", "-vn", "-c:a", "flac", out]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S)
    if result.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        raise InputMissingError(f"Couldn't extract audio from {video_path}: {result.stderr.strip()[:300]}")
    return out


def _recover_mix(job, storage) -> Optional[Dict[str, str]]:
    """Return {'path': new_blob, 'source': ...} or None if nothing to recover from."""
    job_id = job.job_id
    current = _blob_path(job.input_media_gcs_path)
    persisted = persisted_path(job_id, current) if current else None
    if persisted and storage.file_exists(persisted):
        return {"path": persisted, "source": "persisted_upload"}

    recovered = f"jobs/{job_id}/input/{RECOVERED_MIX_NAME}"
    if storage.file_exists(recovered):
        return {"path": recovered, "source": "previous_recovery"}

    for video in _with_vocals_candidates(job):
        if not storage.file_exists(video):
            continue
        padding = _countdown_padding_seconds(job)
        with tempfile.TemporaryDirectory(prefix=f"recover-{job_id}-") as workdir:
            local = _extract_mix(storage, video, padding, workdir)
            storage.upload_file(local, recovered)
        return {"path": recovered, "source": video, "trimmed_padding_s": str(padding)}
    return None


def _recover_instrumental(job, storage) -> Optional[str]:
    job_id = job.job_id
    current = _blob_path(getattr(job, "existing_instrumental_gcs_path", None))
    persisted = persisted_path(job_id, current) if current else None
    if persisted and storage.file_exists(persisted):
        return persisted
    for prefix in (f"jobs/{job_id}/", f"jobs/{job_id}/stems/"):
        for stem in ("existing_instrumental", "custom_instrumental"):
            for ext in _AUDIO_EXTS:
                candidate = f"{prefix}{stem}{ext}"
                if storage.file_exists(candidate):
                    return candidate
    return None


def recover_missing_inputs(job, storage, job_manager) -> Dict[str, str]:
    """Repoint/rebuild missing input audio. Returns the field updates made.

    Raises InputMissingError when the mix is gone and nothing survives to rebuild
    it from, so the job fails with an actionable message instead of a raw 404.
    """
    job_id = job.job_id
    updates: Dict[str, object] = {}
    notes: Dict[str, str] = {}

    mix = _blob_path(getattr(job, "input_media_gcs_path", None))
    if mix and not storage.file_exists(mix):
        found = _recover_mix(job, storage)
        if not found:
            raise InputMissingError(
                "This track's original audio is no longer stored, so it can't be re-rendered. "
                "Please upload the audio again as a new track."
            )
        updates["input_media_gcs_path"] = found["path"]
        notes["input_media_from"] = found["source"]
        logger.warning(f"Job {job_id}: input mix {mix} missing; recovered → {found['path']} (from {found['source']})")

    instrumental = _blob_path(getattr(job, "existing_instrumental_gcs_path", None))
    if instrumental and not storage.file_exists(instrumental):
        found_inst = _recover_instrumental(job, storage)
        if found_inst:
            updates["existing_instrumental_gcs_path"] = found_inst
            notes["existing_instrumental_from"] = found_inst
            logger.warning(f"Job {job_id}: instrumental {instrumental} missing; repointed → {found_inst}")
        else:
            logger.error(f"Job {job_id}: instrumental {instrumental} missing and no surviving copy found")

    if updates:
        notes["recovered_at"] = datetime.now(timezone.utc).isoformat()
        job_manager.update_job(job_id, {**updates, "state_data.input_recovery": notes})
        for field, value in updates.items():
            setattr(job, field, value)
    return {k: str(v) for k, v in updates.items()}
