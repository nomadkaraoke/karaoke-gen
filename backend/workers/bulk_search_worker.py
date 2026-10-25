"""Bulk Mode search worker.

Processes every job in a batch (state_data.batch_id == batch_id): runs the audio
search, auto-selects a confident lossless ("tier 1") match when the batch enabled
it, and otherwise parks the job in AWAITING_AUDIO_SELECTION for the review queue.

Album-mode batches (picked in Bulk "By album") make ONE flacfetch album search:
each track's file is resolved inside the album's torrents and the whole album is
taken from one torrent. Only tracks no torrent covers are searched per track.

Runs as a Cloud Run Job (one execution per batch) so it survives API instance
scale-down and the user can close the tab. Mirrors the single-job search flow in
backend/api/routes/audio_search.py:search_audio.

Usage:
    python -m backend.workers.bulk_search_worker --batch-id <batch_id>
"""

import asyncio
import logging
import time

from backend.models.job import JobStatus
from backend.services.audio_search_service import (
    AudioSearchError,
    AudioSearchService,
    NoResultsError,
    pick_auto_selection,
)
from backend.services.job_manager import JobManager, with_notification_drain
from backend.services.pricing import duration_to_credits, is_blocked
from backend.services.user_service import get_user_service
from backend.services.worker_service import get_worker_service

logger = logging.getLogger(__name__)

# Per-track searches in flight at once within a batch. flacfetch runs searches on
# a single worker, so more than one just queues there and the queued ones run out
# their 95s budget and fail (4/12 of a 12-track album did, 2026-10-22).
SEARCH_CONCURRENCY = 1

# Searching one track at a time, a big batch can outlast the Cloud Run Job's
# 3600s task timeout, which would strand the rest with bulk_pending_search set.
# Past this many seconds, remaining jobs are parked for a manual pick instead.
BATCH_SEARCH_DEADLINE_SECONDS = 3000


def _prepare_theme(job, job_manager: JobManager) -> None:
    """Best-effort: copy the job's theme style into its folder (mirrors search_audio)."""
    theme_id = getattr(job, "theme_id", None)
    if not theme_id:
        return
    try:
        from backend.api.routes.file_upload import _prepare_theme_for_job

        color_overrides = getattr(job, "color_overrides", None) or {}
        style_params_path, theme_style_assets, youtube_desc = _prepare_theme_for_job(
            job.job_id, theme_id, color_overrides
        )
        updates = {
            "style_params_gcs_path": style_params_path,
            "style_assets": theme_style_assets,
        }
        if youtube_desc:
            updates["youtube_description_template"] = youtube_desc
        job_manager.update_job(job.job_id, updates)
    except Exception as e:  # noqa: BLE001
        logger.warning("[bulk] theme prep failed for job %s: %s", job.job_id, e)


async def _auto_select(job, selection_index: int, selected: dict,
                       job_manager: JobManager) -> bool:
    """Charge the duration delta and start the download for an auto-selected job.

    Returns True if the job was successfully auto-selected and the download
    triggered; False if it should instead be parked for human selection (e.g.
    track too long, or insufficient credits for the duration delta).

    NOTE (money path): this replicates the duration-delta charge from
    audio_search.select_audio_source using the SAME primitives and state keys
    (credits_charged / select_charge_applied) rather than refactoring the proven
    route. Keep the two in sync; covered by test_bulk_search_worker.
    """
    job_id = job.job_id
    sd = job.state_data or {}
    duration = selected.get("duration")

    # Track too long → defer to human (they may pick a different source).
    if duration is not None and is_blocked(duration):
        logger.info("[bulk] job %s auto-pick is over 60min; parking for selection", job_id)
        return False

    if not sd.get("select_charge_applied"):
        from backend.services.job_credits import get_credits_charged, is_payment_bypassed
        credits_charged = get_credits_charged(job)
        target_total = duration_to_credits(duration) if duration is not None else credits_charged
        owed = max(0, target_total - credits_charged)
        bypass = is_payment_bypassed(job)
        if owed > 0 and not bypass:
            user_email = job.user_email
            if not user_email:
                return False
            # Crash-safety: if a previous attempt already marked the charge as
            # pending, the deduction may have gone through — do NOT deduct again
            # (bias toward under-charge over double-charge on worker retry).
            if sd.get("bulk_charge_pending"):
                logger.warning(
                    "[bulk] job %s had a pending charge from a prior attempt; "
                    "skipping re-deduction to avoid double-charge", job_id
                )
            else:
                job_manager.update_job(job_id, {"state_data.bulk_charge_pending": owed})
                ok, _, _ = get_user_service().deduct_credits(
                    user_email, job_id, amount=owed, reason="job_creation",
                    count_as_job_creation=False,
                )
                if not ok:
                    job_manager.update_job(job_id, {"state_data.bulk_charge_pending": None})
                    logger.info("[bulk] job %s lacks credits for duration delta; parking", job_id)
                    return False
        update = {
            # Bypassed jobs were never billed: keep 0 so no refund pays out.
            "state_data.credits_charged": 0 if bypass else target_total,
            "state_data.select_charge_applied": True,
            "state_data.bulk_charge_pending": None,
        }
        if bypass:
            update["state_data.payment_bypassed"] = True
        if duration is not None:
            update["state_data.duration_estimate_seconds"] = duration
            update["state_data.duration_estimate_source"] = "search_metadata"
        job_manager.update_job(job_id, update)

    # Reuse the proven route helper to validate + transition to DOWNLOADING_AUDIO.
    from backend.api.routes.audio_search import _validate_and_prepare_selection

    try:
        _validate_and_prepare_selection(job_id, selection_index)
    except Exception as e:  # noqa: BLE001  (route helper raises HTTPException)
        logger.warning("[bulk] job %s validate/prepare failed: %s", job_id, e)
        return False

    return await get_worker_service().trigger_audio_download_worker(job_id)


def _begin_search(job, job_manager: JobManager, message: str) -> None:
    """Theme prep + move the job to SEARCHING_AUDIO."""
    _prepare_theme(job, job_manager)
    job_manager.transition_to_state(
        job_id=job.job_id,
        new_status=JobStatus.SEARCHING_AUDIO,
        progress=5,
        message=message,
        raise_on_invalid=False,
    )


def _park(job_id: str, job_manager: JobManager, message: str) -> None:
    """Leave the job for human selection (the batch review queue)."""
    job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.AWAITING_AUDIO_SELECTION,
        progress=10,
        message=message,
        raise_on_invalid=False,
    )
    job_manager.update_job(job_id, {"state_data.bulk_pending_search": False})


async def _finish_with_results(
    job, results_dicts: list, selection_index, job_manager: JobManager, extra_state: dict | None = None
) -> None:
    """Store a job's search results, then auto-select ``selection_index`` or park it."""
    job_id = job.job_id
    store = {"state_data.audio_search_results": results_dicts, **(extra_state or {})}
    job_manager.update_job(job_id, store)

    if selection_index is None:
        _park(job_id, job_manager, f"Found {len(results_dicts)} sources. Please choose one.")
        return

    auto_ok = await _auto_select(job, selection_index, results_dicts[selection_index], job_manager)
    if not auto_ok:
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.AWAITING_AUDIO_SELECTION,
            progress=10,
            message=f"Found {len(results_dicts)} sources. Please choose one.",
            raise_on_invalid=False,
        )
    job_manager.update_job(
        job_id,
        {"state_data.bulk_pending_search": False, "state_data.bulk_auto_selected": bool(auto_ok)},
    )


def _search_terms(job) -> tuple[str, str]:
    return (job.audio_search_artist or job.artist, job.audio_search_title or job.title)


def _auto_enabled(job) -> bool:
    bulk_settings = (job.state_data or {}).get("bulk_settings", {}) or {}
    return bulk_settings.get("auto_select_if_lossless", True)


async def process_job(job_id: str, job_manager: JobManager, audio_search_service) -> None:
    """Search one batch job on its own (per-track) and either auto-select or park it."""
    job = job_manager.get_job(job_id)
    if not job:
        logger.warning("[bulk] job %s not found", job_id)
        return

    artist, title = _search_terms(job)
    _begin_search(job, job_manager, f"Searching for: {artist} - {title}")

    try:
        results = await audio_search_service.search_async(artist, title)
    except (NoResultsError, AudioSearchError) as e:
        logger.warning("[bulk] search failed for job %s (%s); parking", job_id, e)
        _park(job_id, job_manager, "No automatic audio sources found. Please choose a source.")
        return

    results_dicts = [r.to_dict() for r in results]
    extra = {}
    remote_id = getattr(audio_search_service, "last_remote_search_id", None)
    if remote_id:
        extra["state_data.remote_search_id"] = remote_id

    selection_index = pick_auto_selection(results_dicts, title, artist) if _auto_enabled(job) else None
    await _finish_with_results(job, results_dicts, selection_index, job_manager, extra)


def _album_search_client():
    """flacfetch client for album search (the fake one on the local stack), or None."""
    from backend.config import get_settings
    from backend.services.fake_workers import fake_workers_enabled

    if fake_workers_enabled(get_settings()):
        from backend.services.fake_workers.search import FakeAlbumSearchClient

        return FakeAlbumSearchClient()
    from backend.services.flacfetch_client import get_flacfetch_client

    return get_flacfetch_client()


async def process_album_jobs(album: dict, jobs: list, job_manager: JobManager) -> list:
    """Resolve an album batch with ONE flacfetch album search.

    Every job whose track the album's torrents cover gets those torrents as its
    search results, with the batch torrent (one torrent for the whole album) first
    and auto-selected when it's a confident pick. Returns the jobs that still need
    a per-track search: tracks no torrent covers, or every job when album search
    is unavailable or fails.
    """
    from backend.services.bulk_album_search import (
        build_track_results,
        choose_batch_torrent,
        pick_album_auto_selection,
    )
    from backend.services.flacfetch_client import FlacfetchServiceError

    client = _album_search_client()
    if client is None:
        return jobs

    tracks = []
    for job in jobs:
        _artist, title = _search_terms(job)
        tracks.append({
            "key": job.job_id,
            "title": title,
            "position": (job.state_data or {}).get("bulk_track_position"),
        })

    try:
        response = await client.search_album(album["artist"], album["title"], tracks)
    except FlacfetchServiceError as e:
        logger.warning("[bulk] album search failed for %s - %s (%s); searching per track",
                       album["artist"], album["title"], e)
        return jobs

    torrents = response.get("results") or []
    batch_torrent = choose_batch_torrent(torrents)
    logger.info(
        "[bulk] album search %s - %s: %d torrents; batch torrent %s",
        album["artist"], album["title"], len(torrents),
        f"{batch_torrent.get('provider')} {batch_torrent.get('source_id')} "
        f"({batch_torrent.get('matched_count')}/{len(tracks)} tracks)" if batch_torrent else "none",
    )

    fallback = []
    for job in jobs:
        results = build_track_results(torrents, job.job_id, batch_torrent)
        if not results:
            fallback.append(job)
            continue
        try:
            artist, title = _search_terms(job)
            _begin_search(job, job_manager, f"Matching {title} on {album['title']}")
            selection_index = (
                pick_album_auto_selection(results, batch_torrent, title, artist)
                if _auto_enabled(job) else None
            )
            await _finish_with_results(job, results, selection_index, job_manager)
        except Exception as e:  # noqa: BLE001  (one job must not kill the batch)
            logger.error("[bulk] album job %s crashed: %s", job.job_id, e, exc_info=True)
            try:
                _park_after_crash(job.job_id, job_manager)
            except BatchParkingError:
                fallback.append(job)  # still pending: try it per track
    return fallback


class BatchParkingError(RuntimeError):
    """A job couldn't be parked after a crash: it's still pending, so the batch
    task must fail and let Cloud Run retry it (the retry only sees pending jobs)."""


def _park_after_crash(
    job_id: str, job_manager: JobManager, message: str = "Search error. Please choose a source."
) -> None:
    """Park a job that can't be searched. Raises BatchParkingError if even that
    fails, so the caller can fail the batch instead of stranding the job."""
    try:
        _park(job_id, job_manager, message)
    except Exception as e:  # noqa: BLE001
        logger.error("[bulk] could not park job %s: %s", job_id, e, exc_info=True)
        raise BatchParkingError(job_id) from e


async def process_batch(batch_id: str) -> int:
    """Process all pending-search jobs in a batch. Returns the count processed.

    Album-mode jobs (``state_data.bulk_album``) share one album search per album;
    everything else, and album tracks it didn't cover, is searched per track,
    one at a time (SEARCH_CONCURRENCY).
    """
    job_manager = JobManager()

    jobs = job_manager.list_jobs_by_batch_id(batch_id)
    pending = [j for j in jobs if (j.state_data or {}).get("bulk_pending_search")]
    logger.info("[batch:%s] processing %d/%d jobs needing search", batch_id, len(pending), len(jobs))

    per_track = []
    albums: dict = {}
    for job in pending:
        album = (job.state_data or {}).get("bulk_album")
        if album and album.get("artist") and album.get("title"):
            albums.setdefault((album["artist"], album["title"]), (album, []))[1].append(job)
        else:
            per_track.append(job)

    for album, album_jobs in albums.values():
        try:
            per_track.extend(await process_album_jobs(album, album_jobs, job_manager))
        except Exception as e:  # noqa: BLE001  (fall back rather than strand the album's jobs)
            logger.error("[batch:%s] album path crashed: %s; searching per track", batch_id, e, exc_info=True)
            per_track.extend(j for j in album_jobs if _still_pending(j.job_id, job_manager))

    sem = asyncio.Semaphore(SEARCH_CONCURRENCY)
    deadline = time.monotonic() + BATCH_SEARCH_DEADLINE_SECONDS

    async def _guarded(job_id: str) -> None:
        async with sem:
            if time.monotonic() > deadline:
                logger.warning("[batch:%s] search deadline passed; parking %s for selection", batch_id, job_id)
                _park_after_crash(job_id, job_manager, "Please choose an audio source.")
                return
            # Fresh service per task: last_remote_search_id is instance state and
            # would be cross-contaminated if shared across concurrent searches.
            audio_search_service = AudioSearchService()
            try:
                await process_job(job_id, job_manager, audio_search_service)
            except Exception as e:  # noqa: BLE001  (one job must not kill the batch)
                logger.error("[batch:%s] job %s crashed: %s", batch_id, job_id, e, exc_info=True)
                _park_after_crash(job_id, job_manager)

    # Every job gets its turn; a job that couldn't even be parked fails the batch
    # afterwards so Cloud Run retries it (max_retries) instead of stranding it.
    outcomes = await asyncio.gather(*[_guarded(j.job_id) for j in per_track], return_exceptions=True)
    stranded = [o for o in outcomes if isinstance(o, BaseException)]
    if stranded:
        raise BatchParkingError(f"{len(stranded)} job(s) could not be parked: {stranded}")
    return len(pending)


def _still_pending(job_id: str, job_manager: JobManager) -> bool:
    job = job_manager.get_job(job_id)
    return bool(job and (job.state_data or {}).get("bulk_pending_search"))


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Bulk Mode search worker")
    parser.add_argument("--batch-id", required=True, help="Batch ID to process")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.info("Starting bulk search worker for batch %s", args.batch_id)
    try:
        # Drain: push/email sends scheduled by the job's last state change must
        # finish before asyncio.run cancels pending tasks on exit.
        count = asyncio.run(with_notification_drain(process_batch(args.batch_id)))
        logger.info("Bulk search worker finished batch %s (%d jobs)", args.batch_id, count)
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        logger.error("Bulk search worker crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
