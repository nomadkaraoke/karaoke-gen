"""The fake workers: walk a job through its real states with canned media.

Each coroutine mirrors what the real worker writes (status transitions through
``JobManager``, ``file_urls``, ``state_data``, ``processing_metadata``) and
triggers the next stage through ``WorkerService`` exactly like the real one, so
everything outside the workers (API routes, review UI, downloads, emails) runs
for real. Where a real helper works offline it is reused rather than copied:
the audio download runs the real ``process_audio_download`` (only the fetch
from the provider is faked), and the fake audio worker calls the real stem
upload, review-stem transcode and backing-vocals analysis.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time

from backend.models.job import JobStatus
from backend.services.fake_workers.config import FakeConfig, get_fake_config
from backend.services.fake_workers.lyrics import build_corrections, build_lrc
from backend.services.fake_workers.media import MediaSet, get_media
from backend.services.job_manager import JobManager
from backend.services.quick_version import service as quick
from backend.services.storage_service import StorageService

logger = logging.getLogger(__name__)

FAKE_MODEL_NAMES = {
    "clean_instrumental_model": "fake_clean_model.ckpt",
    "backing_vocals_models": ["fake_backing_model.ckpt"],
    "other_stems_models": [],
}


# Set as state_data.youtube_url on public jobs when FakeConfig.youtube_url_enabled.
FAKE_YOUTUBE_URL = "https://youtu.be/dQw4w9WgXcQ"


class FakeWorkerFailure(Exception):
    """Raised inside a fake worker when FakeConfig.fail_at targets it."""


def _job_or_none(job_manager: JobManager, job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        logger.warning(f"[job:{job_id}] fake worker: job not found")
    return job


def _config_for_job(job_id: str) -> FakeConfig:
    job = JobManager().get_job(job_id)
    return get_fake_config(email=getattr(job, "user_email", None) if job else None)


async def _begin(kind: str, job_id: str) -> FakeConfig:
    config = await asyncio.to_thread(_config_for_job, job_id)
    logger.info(f"[job:{job_id}] WORKER_START worker={kind} fake=true")
    if config.step_delay_seconds:
        await asyncio.sleep(config.step_delay_seconds)
    if config.fail_at == kind:
        raise FakeWorkerFailure(f"Fake {kind} worker failed (fail_at={kind})")
    return config


def _media_for(config: FakeConfig) -> MediaSet:
    return get_media(int(config.audio_duration_seconds), config.backing_vocals)


# Stored processing_metadata.auto_approval verdicts per FakeConfig.auto_approval value:
# (lyrics verdict, backing verdict, backing non_subjective). The shapes
# auto_approval_summary() reads, so the review UI sees confident / unsure halves.
_AUTO_APPROVAL_VERDICTS = {
    "lyrics_confident": ("auto", "review", False),
    "backing_confident": ("review", "clean", True),
    "both_unsure": ("review", "review", False),
}


def auto_approval_payload(kind: str):
    """The ``processing_metadata.auto_approval`` verdict for ``kind``, or None for "none"."""
    verdicts = _AUTO_APPROVAL_VERDICTS.get(kind)
    if not verdicts:
        return None
    lyrics, backing, non_subjective = verdicts
    return {
        "outcome": "review",
        "source": "fake_workers",
        "lyrics": {"verdict": lyrics},
        "backing": {"verdict": backing, "non_subjective": non_subjective},
        "backing_analysis_available": True,
    }


# ---------------------------------------------------------------- audio download

async def fake_download_audio(job_id: str, selected: dict, storage: StorageService) -> tuple[str, str]:
    """Stand-in for ``audio_download_worker._download_audio``: "fetch" the canned song."""
    config = await asyncio.to_thread(_config_for_job, job_id)
    media = await asyncio.to_thread(_media_for, config)
    artist = (selected or {}).get("artist") or "Fake Artist"
    title = (selected or {}).get("title") or "Fake Title"
    filename = f"{artist} - {title}.flac".replace("/", "_")
    gcs_path = f"uploads/{job_id}/audio/{filename}"
    await asyncio.to_thread(storage.upload_file, media.input_audio, gcs_path)
    return gcs_path, filename


async def run_audio_download(job_id: str) -> bool:
    await _begin("audio_download", job_id)
    from backend.workers.audio_download_worker import process_audio_download

    return await process_audio_download(job_id)


# ---------------------------------------------------------------- audio separation

async def _emit_quick_version(job, job_manager: JobManager, storage: StorageService, media: MediaSet) -> None:
    """kjbox make-it jobs: write the same ``state_data.quick_version`` +
    ``file_urls.quick.video_mp4`` the real audio worker's quick-version thread does
    (``services/quick_version``), using the tiny fixture mp4. Same gate as prod
    (``should_build_quick_version``), so non-kjbox jobs are untouched."""
    if not quick.should_build_quick_version(job):
        return
    job_id = job.job_id
    quick.set_quick_state(job_manager, job_id, quick.STATUS_SEPARATING, started_at=quick._now_iso())
    quick.set_quick_state(job_manager, job_id, quick.STATUS_RENDERING)
    gcs_path = f"jobs/{job_id}/quick/quick.mp4"
    await asyncio.to_thread(storage.upload_file, media.final_mp4, gcs_path)
    job_manager.update_file_url(job_id, quick.FILE_CATEGORY, quick.FILE_KEY, gcs_path)
    quick.set_quick_state(
        job_manager, job_id, quick.STATUS_READY,
        lyrics_tier="fake_workers", line_count=0,
        duration_seconds=float(media.duration),
        size_bytes=os.path.getsize(media.final_mp4), ready_at=quick._now_iso(),
    )


async def run_audio(job_id: str) -> bool:
    config = await _begin("audio", job_id)
    from backend.workers.audio_worker import _transcode_review_stems, upload_separation_results
    from backend.workers.screens_worker import _analyze_backing_vocals
    from backend.workers.worker_logging import create_job_logger

    job_manager, storage = JobManager(), StorageService()
    job = _job_or_none(job_manager, job_id)
    if not job:
        return False
    job_log = create_job_logger(job_id, "audio")
    media = await asyncio.to_thread(_media_for, config)

    try:
        await _emit_quick_version(job, job_manager, storage, media)
    except Exception as e:  # non-fatal, same as the real worker
        logger.warning(f"[job:{job_id}] fake audio: quick version failed: {e}")
        quick.set_quick_state(job_manager, job_id, quick.STATUS_FAILED, error=str(e)[:500])

    job_manager.update_state_data(job_id, "audio_progress", {
        "stage": "separating", "progress": 30, "message": "Separating audio (fake)",
    })
    separation_result = {
        "clean_instrumental": {"instrumental": media.instrumental_clean, "vocals": media.vocals},
        "backing_vocals": {
            "fake_backing_model.ckpt": {
                "lead_vocals": media.lead_vocals,
                "backing_vocals": media.backing_vocals,
            }
        },
        "combined_instrumentals": {"fake_backing_model.ckpt": media.instrumental_with_backing},
    }
    job_manager.update_state_data(job_id, "model_names", FAKE_MODEL_NAMES)
    if config.stems_delay_seconds:
        # Late stems: this worker is its own thread/loop, so lyrics -> screens -> awaiting_review
        # proceed meanwhile and the review opens with audio_complete still false.
        logger.info(f"[job:{job_id}] fake audio: holding stems for {config.stems_delay_seconds}s")
        await asyncio.sleep(config.stems_delay_seconds)
    await upload_separation_results(job_id, separation_result, storage, job_manager)
    await _transcode_review_stems(job_id, job_manager, storage, job_log)
    job_manager.update_processing_metadata(job_id, "separation", {
        "provider": "fake_workers",
        "clean_model": FAKE_MODEL_NAMES["clean_instrumental_model"],
        "backing_models": FAKE_MODEL_NAMES["backing_vocals_models"],
        "other_models": [],
        "stems_generated": list(separation_result.keys()),
        "duration_seconds": 0.0,
    })
    try:
        await _analyze_backing_vocals(job_id, job_manager, storage, job_log)
    except Exception as e:  # non-fatal, same as the real worker
        logger.warning(f"[job:{job_id}] fake audio: backing vocals analysis failed: {e}")

    job_manager.mark_audio_complete(job_id)
    await job_manager.advance_to_screens_if_ready(job_id)
    # Same as the real audio worker: resume a final encode parked on these stems.
    await job_manager.resume_video_if_waiting_for_stems(job_id)
    return True


# ---------------------------------------------------------------- lyrics

async def run_lyrics(job_id: str) -> bool:
    config = await _begin("lyrics", job_id)
    job_manager, storage = JobManager(), StorageService()
    job = _job_or_none(job_manager, job_id)
    if not job:
        return False

    corrections = build_corrections(job.artist or "Fake Artist", job.title or "Fake Title",
                                    float(config.audio_duration_seconds), config.review_fixture)
    # Not part of corrections.json: gen keeps the vocal-gap analysis in state_data.
    vocal_gaps = corrections.pop("vocal_gaps", None)
    with tempfile.TemporaryDirectory(prefix=f"fake_lyrics_{job_id}_") as tmp:
        corrections_path = os.path.join(tmp, "corrections.json")
        with open(corrections_path, "w") as f:
            json.dump(corrections, f)
        lrc_path = os.path.join(tmp, "karaoke.lrc")
        with open(lrc_path, "w") as f:
            f.write(build_lrc(corrections))
        url = await asyncio.to_thread(storage.upload_file, corrections_path, f"jobs/{job_id}/lyrics/corrections.json")
        job_manager.update_file_url(job_id, "lyrics", "corrections", url)
        url = await asyncio.to_thread(storage.upload_file, lrc_path, f"jobs/{job_id}/lyrics/karaoke.lrc")
        job_manager.update_file_url(job_id, "lyrics", "lrc", url)

    if vocal_gaps:
        job_manager.update_state_data(job_id, "vocal_gaps", vocal_gaps)
    job_manager.update_state_data(job_id, "lyrics_metadata", {
        "segment_count": len(corrections["corrected_segments"]),
        "has_corrections": True,
        "ready_for_review": True,
        "has_countdown_padding": False,
        "countdown_padding_seconds": 0.0,
    })
    job_manager.update_processing_metadata(job_id, "transcription", {
        "provider": "fake_workers", "reference_sources": ["lrclib"],
    })
    job_manager.mark_lyrics_complete(job_id)
    await job_manager.advance_to_screens_if_ready(job_id)
    return True


# ---------------------------------------------------------------- screens

async def run_screens(job_id: str) -> bool:
    config = await _begin("screens", job_id)
    from backend.workers.screens_worker import (
        _SCREENS_ENTRY_STATUSES,
        _transcode_review_audio,
        _upload_screens,
    )
    from backend.workers.worker_logging import create_job_logger

    job_manager, storage = JobManager(), StorageService()
    job = _job_or_none(job_manager, job_id)
    if not job:
        return False
    if job.status not in _SCREENS_ENTRY_STATUSES:
        logger.info(f"[job:{job_id}] fake screens: duplicate dispatch (status={job.status}), skipping")
        return True
    if not job_manager.transition_to_state(
        job_id=job_id, new_status=JobStatus.GENERATING_SCREENS, progress=50,
        message="Generating title and end screens", raise_on_invalid=False,
    ):
        return True

    media = await asyncio.to_thread(_media_for, config)
    with tempfile.TemporaryDirectory(prefix=f"fake_screens_{job_id}_") as tmp:
        paths = {}
        for screen in ("title", "end"):
            for ext in ("png", "jpg"):
                src = getattr(media, f"{screen}_{ext}")
                dst = os.path.join(tmp, f"{screen}.{ext}")
                with open(src, "rb") as r, open(dst, "wb") as w:
                    w.write(r.read())
            paths[screen] = os.path.join(tmp, f"{screen}.png")
        await _upload_screens(job_id, job_manager, storage, paths["title"], paths["end"])
    job_manager.update_state_data(job_id, "screens_included", {"title": True, "end": True})
    await _transcode_review_audio(job_id, job_manager, create_job_logger(job_id, "screens"))

    if config.auto_approve and getattr(job, "review_mode", None) != "always_review":
        job_manager.update_state_data(job_id, "instrumental_selection", "clean")
        job_manager.update_processing_metadata(job_id, "auto_approval", {
            "outcome": "auto_completed", "source": "fake_workers",
        })
        job_manager.transition_to_state(
            job_id=job_id, new_status=JobStatus.REVIEW_COMPLETE, progress=60,
            message="Review auto-approved (fake workers)",
        )
        from backend.services.worker_service import get_worker_service

        await get_worker_service().trigger_render_video_worker(job_id)
    else:
        verdict = auto_approval_payload(config.auto_approval)
        if verdict:
            job_manager.update_processing_metadata(job_id, "auto_approval", verdict)
        job_manager.transition_to_state(
            job_id=job_id, new_status=JobStatus.AWAITING_REVIEW, progress=55,
            message="Ready for review. Please review lyrics and select your instrumental.",
        )
    job_manager.update_state_data(job_id, "screens_progress", {"stage": "complete"})
    return True


# ---------------------------------------------------------------- render (post-review)

async def run_render_video(job_id: str) -> bool:
    config = await _begin("render_video", job_id)
    job_manager, storage = JobManager(), StorageService()
    job = _job_or_none(job_manager, job_id)
    if not job:
        return False
    if job.status != JobStatus.REVIEW_COMPLETE:
        logger.info(f"[job:{job_id}] fake render: status={job.status}, not REVIEW_COMPLETE; skipping")
        return True
    job_manager.transition_to_state(
        job_id=job_id, new_status=JobStatus.RENDERING_VIDEO, progress=65,
        message="Rendering karaoke video with your corrections",
    )
    media = await asyncio.to_thread(_media_for, config)
    url = await asyncio.to_thread(storage.upload_file, media.with_vocals_mkv, f"jobs/{job_id}/videos/with_vocals.mkv")
    job_manager.update_file_url(job_id, "videos", "with_vocals", url)
    with tempfile.TemporaryDirectory(prefix=f"fake_render_{job_id}_") as tmp:
        for key, name, body in (
            ("ass", "karaoke.ass", "[Script Info]\nTitle: fake\n"),
            ("corrected_txt", "karaoke.txt", "fake lyrics\n"),
        ):
            path = os.path.join(tmp, name)
            with open(path, "w") as f:
                f.write(body)
            url = await asyncio.to_thread(storage.upload_file, path, f"jobs/{job_id}/lyrics/{name}")
            job_manager.update_file_url(job_id, "lyrics", key, url)
    job_manager.update_state_data(job_id, "render_progress", {"stage": "complete"})

    if getattr(job, "prep_only", False):
        job_manager.transition_to_state(
            job_id=job_id, new_status=JobStatus.PREP_COMPLETE, progress=100,
            message="Prep complete (fake workers)",
        )
        return True
    job_manager.transition_to_state(
        job_id=job_id, new_status=JobStatus.INSTRUMENTAL_SELECTED, progress=68,
        message="Render complete, starting final encoding",
    )
    from backend.services.worker_service import get_worker_service

    return await get_worker_service().trigger_video_worker(job_id)


# ---------------------------------------------------------------- final video

async def run_video(job_id: str) -> bool:
    config = await _begin("video", job_id)
    job_manager, storage = JobManager(), StorageService()
    job = _job_or_none(job_manager, job_id)
    if not job:
        return False
    started = time.time()
    media = await asyncio.to_thread(_media_for, config)

    for status, progress, message in (
        (JobStatus.GENERATING_VIDEO, 70, "Preparing files for video generation"),
        (JobStatus.ENCODING, 80, "Encoding videos (fake)"),
        (JobStatus.PACKAGING, 92, "Packaging outputs (fake)"),
    ):
        job_manager.transition_to_state(job_id=job_id, new_status=status, progress=progress, message=message)
        if config.step_delay_seconds:
            await asyncio.sleep(config.step_delay_seconds / 2)

    uploads = (
        (media.final_mp4, "finals", "lossless_4k_mp4"),
        (media.final_mkv, "finals", "lossless_4k_mkv"),
        (media.final_mp4, "finals", "lossy_4k_mp4"),
        (media.final_mp4, "finals", "lossy_720p_mp4"),
        (media.final_mp4, "finals", "with_vocals_mp4"),
        (media.cdg_zip, "packages", "cdg_zip"),
        (media.txt_zip, "packages", "txt_zip"),
    )
    for local, category, key in uploads:
        ext = os.path.splitext(local)[1]
        url = await asyncio.to_thread(storage.upload_file, local, f"jobs/{job_id}/{category}/{key}{ext}")
        job_manager.update_file_url(job_id, category, key, url)

    if config.youtube_url_enabled and not getattr(job, "is_private", False):
        # Real-looking link so the app's "ready" screen can show "Watch on YouTube".
        job_manager.update_state_data(job_id, "youtube_url", FAKE_YOUTUBE_URL)
    job_manager.update_state_data(job_id, "video_progress", {"stage": "complete"})
    job_manager.update_processing_metadata(job_id, "timing.video_worker_seconds", round(time.time() - started, 1))
    job_manager.transition_to_state(
        job_id=job_id, new_status=JobStatus.COMPLETE, progress=100,
        message="Karaoke generation complete!",
        timeline_metadata={"action": "completed", "brand_code": None, "fake_workers": True},
    )
    return True


RUNNERS = {
    "audio_download": run_audio_download,
    "audio": run_audio,
    "lyrics": run_lyrics,
    "screens": run_screens,
    "render_video": run_render_video,
    "video": run_video,
}


async def run(kind: str, job_id: str) -> bool:
    """Run one fake worker, failing the job (like a real worker crash) on error."""
    started = time.time()
    try:
        ok = await RUNNERS[kind](job_id)
        logger.info(
            f"[job:{job_id}] WORKER_END worker={kind} fake=true status={'success' if ok else 'noop'} "
            f"duration={time.time() - started:.1f}s"
        )
        return ok
    except Exception as e:
        logger.error(f"[job:{job_id}] WORKER_END worker={kind} fake=true status=error error={e}", exc_info=True)
        try:
            JobManager().fail_job(job_id, f"{kind} failed: {e}", error_details={"stage": kind, "fake_workers": True})
        except Exception:
            logger.exception(f"[job:{job_id}] fake worker: could not mark job failed")
        return False
