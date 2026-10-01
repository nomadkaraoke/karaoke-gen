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
import time
from typing import Dict

logger = logging.getLogger(__name__)

# --- De-duplication -------------------------------------------------------
# Two callers race to generate the same suggestions: the lyrics worker's HTTP
# trigger (internal /workers/auto-correct) and the screens worker's pre-apply
# (ensure_and_pre_apply → generate on cache miss). Screens is triggered BEFORE
# the lyrics worker fires its proactive trigger, so the pre-apply routinely
# checks the cache while the proactive run is still in flight and starts a
# second identical multi-model run (prod 2026-09-17..10-01: 92/167 jobs ran it
# twice, ~$0.07 each). Two guards:
#   1. In-process single-flight: a concurrent caller on the same instance awaits
#      the in-flight run instead of starting another (~90% of duplicates).
#   2. Cross-instance GCS lease (create-only object): a caller on another
#      instance waits for the holder's cache instead of regenerating.
_INFLIGHT: Dict[str, "asyncio.Task[dict]"] = {}

LEASE_PATH = "jobs/{job_id}/lyrics/auto_correct_inflight.json"
# Generation is bounded at 180s; anything older is a crashed holder's leftover.
LEASE_TTL_SECONDS = 240
# How long a non-holder waits for the holder's cache (fits inside the lyrics
# trigger's 200s HTTP timeout).
PEER_WAIT_SECONDS = 180
PEER_POLL_SECONDS = 3


def _acquire_lease(storage, job_id: str) -> bool:
    """Create-only lease write. True if we hold it (or can't tell — fail open)."""
    path = LEASE_PATH.format(job_id=job_id)
    try:
        from google.api_core.exceptions import PreconditionFailed
    except Exception:  # pragma: no cover - google libs always present in prod
        PreconditionFailed = ()  # type: ignore[assignment]
    try:
        storage.upload_json(path, {"acquired_at": time.time()}, if_generation_match=0)
        return True
    except PreconditionFailed:
        pass
    except Exception as e:  # noqa: BLE001 — lease is an optimisation; fail open
        logger.info("[job:%s] auto-correct lease write failed, proceeding: %s", job_id, e)
        return True
    # Someone else holds it — unless it's stale (crashed holder), then take over.
    try:
        acquired_at = float((storage.download_json(path) or {}).get("acquired_at") or 0)
    except Exception:  # noqa: BLE001 — vanished/unreadable lease → treat as stale
        acquired_at = 0.0
    if time.time() - acquired_at > LEASE_TTL_SECONDS:
        try:
            storage.upload_json(path, {"acquired_at": time.time()})
        except Exception:  # noqa: BLE001
            pass
        return True
    return False


def _release_lease(storage, job_id: str) -> None:
    try:
        storage.delete_file(LEASE_PATH.format(job_id=job_id), ignore_missing=True)
    except Exception as e:  # noqa: BLE001 — a leftover lease just goes stale
        logger.info("[job:%s] auto-correct lease release failed: %s", job_id, e)


async def _wait_for_peer(storage, job_id: str) -> dict:
    """Another instance is generating: wait for its cache instead of re-running."""
    from backend.services.auto_approval.executor import _load_ai_suggestions

    lease_path = LEASE_PATH.format(job_id=job_id)
    deadline = time.monotonic() + PEER_WAIT_SECONDS
    while time.monotonic() < deadline:
        # Off-loop: these are blocking GCS calls, polled repeatedly.
        suggestions = await asyncio.to_thread(_load_ai_suggestions, storage, job_id)
        if suggestions is not None:
            logger.info("[job:%s] proactive auto-correct deduped: used peer's cache", job_id)
            return {"status": "deduped", "suggestions": len(suggestions)}
        if not await asyncio.to_thread(storage.file_exists, lease_path):
            # Holder finished without a cache (its run failed). Don't retry here:
            # the review UI requests suggestions on demand as the fallback.
            suggestions = await asyncio.to_thread(_load_ai_suggestions, storage, job_id)
            if suggestions is not None:
                return {"status": "deduped", "suggestions": len(suggestions)}
            return {"status": "skipped", "reason": "peer_failed"}
        await asyncio.sleep(PEER_POLL_SECONDS)
    logger.info("[job:%s] proactive auto-correct: peer still running after %ss", job_id, PEER_WAIT_SECONDS)
    return {"status": "skipped", "reason": "peer_timeout"}


async def process_proactive_auto_correct(job_id: str) -> dict:
    """Generate + cache auto-correct suggestions for a job. Never raises.

    Single-flight per job (see the de-duplication note above): concurrent
    callers share one generation run.
    """
    from backend.config import get_settings

    if not get_settings().auto_correct_proactive_enabled:
        return {"status": "disabled"}

    existing = _INFLIGHT.get(job_id)
    if existing is not None:
        logger.info("[job:%s] proactive auto-correct already in flight here; joining it", job_id)
        try:
            # shield: a cancelled joiner must not cancel the shared run
            return await asyncio.shield(existing)
        except Exception as e:  # noqa: BLE001 — never raise
            return {"status": "error", "message": str(e)}

    task = asyncio.ensure_future(_generate(job_id))
    _INFLIGHT[job_id] = task

    def _clear(t, _job_id=job_id):
        if _INFLIGHT.get(_job_id) is t:
            _INFLIGHT.pop(_job_id, None)

    task.add_done_callback(_clear)
    try:
        return await asyncio.shield(task)
    except Exception as e:  # noqa: BLE001 — never raise
        return {"status": "error", "message": str(e)}


async def _generate(job_id: str) -> dict:
    """Generate + cache auto-correct suggestions for a job. Never raises.

    Returns a small status dict for the endpoint to echo. Reads the corrections.json the
    lyrics worker just wrote (the same data the review UI loads on first open)
    and runs the multi-model path with default settings so the cache key lines
    up with the UI request.
    """
    try:
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

        if not _acquire_lease(storage, job_id):
            return await _wait_for_peer(storage, job_id)

        try:
            return await _run_suggest(job_id, data, segments, reference_lyrics)
        finally:
            _release_lease(storage, job_id)
    except Exception as e:  # noqa: BLE001 — proactive is best-effort, never fatal
        logger.warning("[job:%s] proactive auto-correct failed (non-fatal): %s", job_id, e, exc_info=True)
        return {"status": "error", "message": str(e)}


async def _run_suggest(job_id: str, data: dict, segments: list, reference_lyrics: dict) -> dict:
    from backend.services.auto_correct import get_auto_correct_service
    from backend.services.auto_correct.settings import AutoCorrectSettings
    from backend.services.job_manager import JobManager

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
            # Already loaded — saves the service re-fetching it for the
            # deterministic (P4/P5) generators.
            correction_data=data,
        ),
        timeout=180,
    )
    logger.info(
        "[job:%s] proactive auto-correct done: %d suggestions cached (models=%s, %.1fs)",
        job_id, len(result.suggestions), result.model, result.elapsed_seconds,
    )
    return {"status": "generated", "suggestions": len(result.suggestions)}
