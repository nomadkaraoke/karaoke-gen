#!/usr/bin/env python3
"""Backfill original input audio into past jobs' Dropbox folders.

Restores files like ``<artist> - <title> (flacfetch).flac`` / ``(uploaded).mp3`` to
the organised Dropbox track folders for completed jobs that predate the pipeline fix
(the distributed pipeline stopped copying the source audio into the output folder).

Dry-run by default; pass ``--live`` to actually upload. NOTE: this cannot backfill the
screen ``.mov`` files — those require re-encoding the job.

Usage:
    python -m backend.scripts.backfill_original_audio                 # dry-run, since 2026-03-01
    python -m backend.scripts.backfill_original_audio --live          # actually upload
    python -m backend.scripts.backfill_original_audio --since 2026-02-01 --limit 50 -v
"""
import argparse
import logging
import os
import tempfile
from datetime import datetime, timezone

from backend.models.job import JobStatus
from backend.services.original_audio import (
    original_audio_gcs_path,
    original_audio_output_filename,
)

logger = logging.getLogger("backfill_original_audio")

# The regression window: screen/audio files stopped reaching Dropbox once the
# distributed pipeline took over (last known-good folder ~Feb; PR #647/#650 Mar 31).
DEFAULT_SINCE = "2026-03-01"


def plan_job_backfill(job, *, storage, dropbox):
    """Decide what to do for one job (existence checks only — no uploads).

    Returns ``(action, detail)`` where action is one of:
      - ``"skip-no-dropbox"``        — job was never uploaded to Dropbox
      - ``"skip-no-audio-record"``   — no original-audio reference on the job
      - ``"skip-audio-missing-gcs"`` — original audio no longer in GCS (detail=gcs_path)
      - ``"skip-already-present"``   — file already in the Dropbox folder (detail=remote_path)
      - ``"upload"``                 — detail={gcs_path, remote_path, filename}
    """
    from karaoke_gen.utils import sanitize_filename

    state = getattr(job, "state_data", None) or {}
    brand_code = state.get("brand_code")
    dropbox_path = getattr(job, "dropbox_path", None)
    if not brand_code or not dropbox_path:
        return ("skip-no-dropbox", None)

    gcs_path = original_audio_gcs_path(job)
    if not gcs_path:
        return ("skip-no-audio-record", None)
    if not storage.file_exists(gcs_path):
        return ("skip-audio-missing-gcs", gcs_path)

    safe_artist = sanitize_filename(job.artist) if job.artist else "Unknown"
    safe_title = sanitize_filename(job.title) if job.title else "Unknown"
    base_name = f"{safe_artist} - {safe_title}"
    filename = original_audio_output_filename(job, base_name)
    folder = f"{dropbox_path.rstrip('/')}/{brand_code} - {base_name}"
    remote_path = f"{folder}/{filename}"

    if dropbox.file_exists(remote_path):
        return ("skip-already-present", remote_path)

    return ("upload", {"gcs_path": gcs_path, "remote_path": remote_path, "filename": filename})


def _execute_upload(detail, *, storage, dropbox):
    """Download the original audio from GCS and upload it to the Dropbox folder."""
    with tempfile.TemporaryDirectory() as td:
        local = os.path.join(td, detail["filename"])
        storage.download_file(detail["gcs_path"], local)
        dropbox.upload_file(local, detail["remote_path"])


def run_backfill(*, since, until=None, limit, environment, live):
    from backend.services.job_manager import JobManager
    from backend.services.storage_service import StorageService
    from backend.services.dropbox_service import get_dropbox_service

    jm = JobManager()
    storage = StorageService()
    dropbox = get_dropbox_service()
    if not dropbox.is_configured:
        logger.error("Dropbox not configured; aborting.")
        return

    jobs = jm.list_jobs(
        status=JobStatus.COMPLETE,
        environment=environment,
        created_after=since,
        created_before=until,
        limit=limit,
    )
    logger.info(
        "Considering %d completed jobs since %s (env=%s)",
        len(jobs), since.date(), environment,
    )

    tally = {}
    for job in jobs:
        action, detail = plan_job_backfill(job, storage=storage, dropbox=dropbox)
        tally[action] = tally.get(action, 0) + 1
        if action == "upload":
            verb = "uploading" if live else "[DRY-RUN] would upload"
            logger.info("%s: %s -> %s", verb, job.job_id, detail["remote_path"])
            if live:
                try:
                    _execute_upload(detail, storage=storage, dropbox=dropbox)
                except Exception as e:
                    logger.error("  upload failed for %s: %s", job.job_id, e)
                    tally["upload"] -= 1
                    tally["error"] = tally.get("error", 0) + 1
        else:
            logger.debug("%s: %s (%s)", action, job.job_id, detail)

    logger.info("=== Backfill summary (%s) ===", "LIVE" if live else "DRY-RUN")
    for k in sorted(tally):
        logger.info("  %-24s %d", k, tally[k])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=DEFAULT_SINCE, help="Only jobs created on/after YYYY-MM-DD")
    parser.add_argument("--until", default=None, help="Only jobs created before YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--env", default="production", dest="environment")
    parser.add_argument("--live", action="store_true", help="Actually upload (default: dry-run)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    until = (
        datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.until else None
    )
    run_backfill(since=since, until=until, limit=args.limit, environment=args.environment, live=args.live)


if __name__ == "__main__":
    main()
