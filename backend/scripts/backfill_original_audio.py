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
      - ``"skip-folder-missing"``    — the track's Dropbox folder doesn't exist (detail=folder)
      - ``"skip-already-present"``   — file already in the Dropbox folder (detail=remote_path)
      - ``"upload"``                 — detail={gcs_path, remote_path, filename}
    """
    from karaoke_gen.utils import sanitize_filename
    from backend.services.job_defaults_service import get_effective_distribution_for_job

    state = getattr(job, "state_data", None) or {}
    brand_code = state.get("brand_code")
    # Use the EFFECTIVE dropbox path — private (NOMADNP) tracks upload to the
    # NonPublished path, not the job's stored (public) dropbox_path. This mirrors
    # what the orchestrator does at distribution time.
    dropbox_path = get_effective_distribution_for_job(job).dropbox_path
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

    # Only backfill into a track folder that already exists — never create a new
    # folder (guards against drifted brand/artist/title naming producing an orphan).
    if not dropbox.file_exists(folder):
        return ("skip-folder-missing", folder)

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

    # NOTE: created_at is stored as an ISO string in Firestore, so server-side
    # created_after/created_before datetime filters match nothing. We fetch the most
    # recent completed jobs (ordered by created_at desc) and filter dates in Python.
    # `limit` therefore bounds how far back we look — set it high enough to cover the
    # window since `since`.
    jobs = jm.list_jobs(status=JobStatus.COMPLETE, limit=limit)

    def _created(j):
        c = getattr(j, "created_at", None)
        if c is None:
            return None
        return c.replace(tzinfo=None) if getattr(c, "tzinfo", None) else c

    since_naive = since.replace(tzinfo=None)
    until_naive = until.replace(tzinfo=None) if until else None

    def _in_window(j):
        c = _created(j)
        if c is None:
            return False
        if c < since_naive:
            return False
        if until_naive and c >= until_naive:
            return False
        return True

    jobs = [j for j in jobs if _in_window(j)]

    if environment:
        def _env(j):
            rm = getattr(j, "request_metadata", None)
            if rm is None:
                return None
            return rm.get("environment") if isinstance(rm, dict) else getattr(rm, "environment", None)
        jobs = [j for j in jobs if _env(j) == environment]

    logger.info(
        "Considering %d completed jobs since %s (env=%s, fetch limit=%d)",
        len(jobs), since.date(), environment or "all", limit,
    )

    tally = {}
    for job in jobs:
        try:
            action, detail = plan_job_backfill(job, storage=storage, dropbox=dropbox)
        except Exception as e:
            # A transient GCS/Dropbox error while planning shouldn't abort the whole run.
            logger.error("  planning failed for %s: %s", getattr(job, "job_id", "?"), e)
            tally["error"] = tally.get("error", 0) + 1
            continue
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
    parser.add_argument("--limit", type=int, default=5000,
                        help="Max completed jobs to fetch (most-recent first). Bounds how far "
                             "back the date window can reach.")
    parser.add_argument("--env", default=None, dest="environment",
                        help="Filter to a request environment (e.g. production). Default: all "
                             "(test jobs are skipped anyway — no brand code / Dropbox folder).")
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
