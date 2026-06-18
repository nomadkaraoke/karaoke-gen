"""Bulk Mode search worker.

Processes every job in a batch (state_data.batch_id == batch_id): runs the audio
search, auto-selects a confident lossless ("tier 1") match when the batch enabled
it, and otherwise parks the job in AWAITING_AUDIO_SELECTION for the review queue.

Runs as a Cloud Run Job (one execution per batch) so it survives API instance
scale-down and the user can close the tab. Mirrors the single-job search flow in
backend/api/routes/audio_search.py:search_audio.

Usage:
    python -m backend.workers.bulk_search_worker --batch-id <batch_id>
"""

import asyncio
import logging

from backend.models.job import JobStatus
from backend.services.audio_search_service import (
    AudioSearchError,
    AudioSearchService,
    NoResultsError,
    pick_auto_selection,
)
from backend.services.job_manager import JobManager
from backend.services.pricing import duration_to_credits, is_blocked
from backend.services.user_service import get_user_service
from backend.services.worker_service import get_worker_service

logger = logging.getLogger(__name__)

# How many per-song searches to run concurrently within a batch.
SEARCH_CONCURRENCY = 4


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
        credits_charged = int(sd.get("credits_charged", 1))
        target_total = duration_to_credits(duration) if duration is not None else credits_charged
        owed = max(0, target_total - credits_charged)
        bypass = bool(sd.get("payment_bypassed", False))
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
            "state_data.credits_charged": target_total,
            "state_data.select_charge_applied": True,
            "state_data.bulk_charge_pending": None,
        }
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


async def process_job(job_id: str, job_manager: JobManager, audio_search_service) -> None:
    """Search one batch job and either auto-select or park it."""
    job = job_manager.get_job(job_id)
    if not job:
        logger.warning("[bulk] job %s not found", job_id)
        return

    artist = job.audio_search_artist or job.artist
    title = job.audio_search_title or job.title
    sd = job.state_data or {}
    bulk_settings = sd.get("bulk_settings", {}) or {}
    auto_enabled = bulk_settings.get("auto_select_if_lossless", True)

    _prepare_theme(job, job_manager)

    job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.SEARCHING_AUDIO,
        progress=5,
        message=f"Searching for: {artist} - {title}",
        raise_on_invalid=False,
    )

    try:
        results = await audio_search_service.search_async(artist, title)
    except (NoResultsError, AudioSearchError) as e:
        logger.warning("[bulk] search failed for job %s (%s); parking", job_id, e)
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.AWAITING_AUDIO_SELECTION,
            progress=10,
            message="No automatic audio sources found. Please choose a source.",
            raise_on_invalid=False,
        )
        job_manager.update_job(job_id, {"state_data.bulk_pending_search": False})
        return

    results_dicts = [r.to_dict() for r in results]
    store = {"state_data.audio_search_results": results_dicts}
    remote_id = getattr(audio_search_service, "last_remote_search_id", None)
    if remote_id:
        store["state_data.remote_search_id"] = remote_id
    job_manager.update_job(job_id, store)

    selection_index = pick_auto_selection(results_dicts, title) if auto_enabled else None

    if selection_index is None:
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.AWAITING_AUDIO_SELECTION,
            progress=10,
            message=f"Found {len(results_dicts)} sources. Please choose one.",
            raise_on_invalid=False,
        )
        job_manager.update_job(job_id, {"state_data.bulk_pending_search": False})
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


async def process_batch(batch_id: str) -> int:
    """Process all pending-search jobs in a batch. Returns the count processed."""
    job_manager = JobManager()

    jobs = job_manager.list_jobs_by_batch_id(batch_id)
    pending = [j for j in jobs if (j.state_data or {}).get("bulk_pending_search")]
    logger.info("[batch:%s] processing %d/%d jobs needing search", batch_id, len(pending), len(jobs))

    sem = asyncio.Semaphore(SEARCH_CONCURRENCY)

    async def _guarded(job_id: str) -> None:
        async with sem:
            # Fresh service per task: last_remote_search_id is instance state and
            # would be cross-contaminated if shared across concurrent searches.
            audio_search_service = AudioSearchService()
            try:
                await process_job(job_id, job_manager, audio_search_service)
            except Exception as e:  # noqa: BLE001  (one job must not kill the batch)
                logger.error("[batch:%s] job %s crashed: %s", batch_id, job_id, e, exc_info=True)
                try:
                    job_manager.transition_to_state(
                        job_id=job_id,
                        new_status=JobStatus.AWAITING_AUDIO_SELECTION,
                        progress=10,
                        message="Search error. Please choose a source.",
                        raise_on_invalid=False,
                    )
                    job_manager.update_job(job_id, {"state_data.bulk_pending_search": False})
                except Exception:  # noqa: BLE001
                    pass

    await asyncio.gather(*[_guarded(j.job_id) for j in pending])
    return len(pending)


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
        count = asyncio.run(process_batch(args.batch_id))
        logger.info("Bulk search worker finished batch %s (%d jobs)", args.batch_id, count)
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        logger.error("Bulk search worker crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
