"""Proactive auto-correct generation, run on the API service.

The lyrics worker (a Cloud Run Job) triggers this via the internal endpoint
once transcription + references are ready. It runs HERE — on the API service —
rather than in the lyrics job, because the service already has a working
ANTHROPIC_API_KEY + AUTO_CORRECT_COMPARE_MODELS configuration (the lyrics job's
secrets use Cloud Run secret aliases that `gcloud run jobs update
--update-secrets` can't extend; see docs/archive/2026-06-11-proactive-autocorrect-plan.md).

Pre-generates + caches the multi-model suggestion run so the review UI's
on-load request is an instant cache hit. Best-effort: it must never raise — a
failure just means the UI does the call on demand instead.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def process_proactive_auto_correct(job_id: str) -> dict:
    """Generate + cache auto-correct suggestions for a job. Never raises.

    Returns a small status dict for the endpoint to echo. Gated by
    settings.auto_correct_proactive_enabled; reads the corrections.json the
    lyrics worker just wrote (the same data the review UI loads on first open)
    and runs the multi-model path with default settings so the cache key lines
    up with the UI request.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.auto_correct_proactive_enabled:
        return {"status": "disabled"}

    try:
        from backend.services.auto_correct import get_auto_correct_service
        from backend.services.auto_correct.settings import AutoCorrectSettings
        from backend.services.job_manager import JobManager
        from backend.services.storage_service import StorageService

        storage = StorageService()
        corrections_path = f"jobs/{job_id}/lyrics/corrections.json"
        if not storage.file_exists(corrections_path):
            logger.info("[job:%s] proactive auto-correct skipped: no corrections.json", job_id)
            return {"status": "skipped", "reason": "no_corrections"}
        data = storage.download_json(corrections_path)
        segments = data.get("corrected_segments") or data.get("segments") or []
        reference_lyrics = data.get("reference_lyrics") or {}
        if not segments or not reference_lyrics:
            # No references → nothing to compare against. The reviewer can paste
            # references in the UI and trigger auto-correct manually later.
            logger.info(
                "[job:%s] proactive auto-correct skipped: %d segments, %d ref sources",
                job_id, len(segments), len(reference_lyrics),
            )
            return {"status": "skipped", "reason": "no_references"}

        job = JobManager().get_job(job_id)
        service = get_auto_correct_service()
        result = await asyncio.wait_for(
            asyncio.to_thread(
                service.suggest,
                job_id=job_id,
                segments=segments,
                reference_lyrics=reference_lyrics,
                artist=getattr(job, "artist", None) if job else None,
                title=getattr(job, "title", None) if job else None,
                # Must match what the review UI sends (default settings,
                # multi-model) or the cache key won't line up.
                settings=AutoCorrectSettings(compare_models=True),
            ),
            timeout=180,
        )
        logger.info(
            "[job:%s] proactive auto-correct done: %d suggestions cached (models=%s, %.1fs)",
            job_id, len(result.suggestions), result.model, result.elapsed_seconds,
        )
        return {"status": "generated", "suggestions": len(result.suggestions)}
    except Exception as e:  # noqa: BLE001 — proactive is best-effort, never fatal
        logger.warning("[job:%s] proactive auto-correct failed (non-fatal): %s", job_id, e, exc_info=True)
        return {"status": "error", "message": str(e)}
