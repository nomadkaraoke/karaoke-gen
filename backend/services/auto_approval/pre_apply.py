"""Server-side pre-apply of AI corrections BEFORE the review-ready notification.

Andrew's directive (2026-08-29): *"apply the ai-generated lyrics corrections and any
post-ai heuristic fixes before the user ever sees the notification/button saying the
lyrics review is ready ... it should all be done server-side and loaded as a single
load event when the lyrics review page launches."* Reaffirmed 2026-11-19: *"i want
the ai corrections to always be processed before i open the review page"*, with no
second, in-browser code path.

Called from ``review_gate.enter_human_review`` just before the AWAITING_REVIEW
transition, and from ``/correction-data`` for jobs that reached review before this
existed. It applies the cached suggestions with the same machinery the executor uses
(``apply.build_applied_segments``, non-strict: what "Accept All" would produce) and
writes ``corrections_updated.json`` with a ``pre_applied`` marker, so the review page
loads the corrected lyrics in one load. The review UI never applies suggestions by
itself any more.

BEST-EFFORT: never raises, bounded. If no suggestions can be had (proactive
disabled, LLM outage) it does nothing, and the review opens with the uncorrected
lyrics; the reviewer can still run the AI panel by hand. Auto-approval must never
strand a job.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CORRECTIONS_PATH = "jobs/{job_id}/lyrics/corrections.json"
CORRECTIONS_UPDATED_PATH = "jobs/{job_id}/lyrics/corrections_updated.json"


async def ensure_and_pre_apply(job_id: str, *, generate_on_miss: bool = True) -> Dict[str, Any]:
    """Ensure the suggestion cache exists, apply it, and persist the pre-applied
    corrections with a ``pre_applied`` marker. Returns a small status dict; never
    raises.

    ``generate_on_miss`` (default True, the screens_worker path): when the proactive
    cache is absent, generate it synchronously (bounded) before applying. The
    ``/correction-data`` load path passes False — it only applies an EXISTING cache
    so a review page never blocks on a multi-model LLM call.
    """
    try:
        from backend.config import get_settings
        from backend.services.auto_approval.apply import build_applied_segments
        from backend.services.auto_approval.executor import _load_ai_suggestions
        from backend.services.job_manager import JobManager
        from backend.services.storage_service import StorageService

        settings = get_settings()
        storage = StorageService()

        corrections_path = CORRECTIONS_PATH.format(job_id=job_id)
        # All GCS/Firestore calls below are sync — run them off the event loop.
        if not await asyncio.to_thread(storage.file_exists, corrections_path):
            return {"outcome": "skipped", "reason": "no_corrections"}

        updated_path = CORRECTIONS_UPDATED_PATH.format(job_id=job_id)
        if await asyncio.to_thread(storage.file_exists, updated_path):
            # Already applied once (executor auto-complete, a prior pre-apply run,
            # or a human edit). Never clobber existing corrections_updated.json.
            try:
                existing = await asyncio.to_thread(storage.download_json, updated_path)
                if ((existing.get("metadata") or {}).get("auto_approval")):
                    return {"outcome": "skipped", "reason": "already_applied"}
            except Exception:
                pass
            return {"outcome": "skipped", "reason": "corrections_updated_exists"}

        corrections = await asyncio.to_thread(storage.download_json, corrections_path)

        # 1) Ensure the proactive suggestion cache exists (generate on miss, unless
        #    the caller — e.g. the load-time path — asked to only apply an existing
        #    cache so the request doesn't block on an LLM call).
        ai_suggestions = await asyncio.to_thread(_load_ai_suggestions, storage, job_id)
        if ai_suggestions is None and generate_on_miss and settings.auto_correct_proactive_enabled:
            logger.info(f"[job:{job_id}] pre-apply: cache miss, generating suggestions synchronously")
            from backend.workers.auto_correct_worker import process_proactive_auto_correct
            await process_proactive_auto_correct(job_id)  # bounded (180s) + best-effort
            ai_suggestions = await asyncio.to_thread(_load_ai_suggestions, storage, job_id)

        if ai_suggestions is None:
            # Unknown suggestion set (proactive disabled or generation failed):
            # do NOT mark pre_applied; review opens with the uncorrected lyrics.
            return {"outcome": "skipped", "reason": "no_suggestions_cache"}

        # 2) Apply exactly what "Accept All" would. Non-strict: a human reviews
        #    the result, so stale/duplicate signals are warnings, not aborts.
        result = await asyncio.to_thread(
            lambda: build_applied_segments(corrections, ai_suggestions, strict=False)
        )
        if result.get("aborted"):
            logger.warning(f"[job:{job_id}] pre-apply could not apply ({result['aborted']})")
            return {"outcome": "skipped", "reason": result["aborted"]}
        if result.get("warnings"):
            logger.info(f"[job:{job_id}] pre-apply warnings: {result['warnings']}")

        # 3) Persist corrections_updated.json with the pre_applied marker + the
        #    suggestions themselves (so the review panel renders applied state).
        updated = dict(corrections)
        updated["corrected_segments"] = result["segments"]
        metadata = dict(updated.get("metadata") or {})
        metadata["auto_approval"] = {
            "pre_applied": True,
            "applied_suggestion_ids": result["applied_ids"],
            "rejected_suggestion_ids": result["rejected_ids"],
            "suggestions": ai_suggestions,
            # Per-suggestion undo info (SuggestionUndoInfo shape) so the review
            # UI can still revert an individual pre-applied correction even
            # though it never applied it client-side itself.
            "undo_info": result["undo_info"],
            "warnings": result.get("warnings") or [],
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
        updated["metadata"] = metadata
        # Create-only write: we only reach here when corrections_updated.json was
        # absent at the check above, so if_generation_match=0 makes the write atomic —
        # a concurrent writer (executor auto-complete, or an edit submission) that
        # created it in the meantime wins and we don't clobber their (newer) state.
        try:
            from google.api_core.exceptions import PreconditionFailed
        except Exception:  # pragma: no cover - google libs always present in prod
            PreconditionFailed = ()  # type: ignore[assignment]
        try:
            await asyncio.to_thread(storage.upload_json, updated_path, updated, if_generation_match=0)
        except PreconditionFailed:
            logger.info(f"[job:{job_id}] pre-apply raced a concurrent write — theirs wins")
            return {"outcome": "skipped", "reason": "raced_concurrent_write"}
        await asyncio.to_thread(
            JobManager().update_file_url, job_id, "lyrics", "corrections_updated", updated_path
        )

        logger.info(
            f"[job:{job_id}] pre-apply saved corrections_updated.json "
            f"({len(result['applied_ids'])} applied, {len(result['rejected_ids'])} rejected) "
            "before review notification"
        )
        return {"outcome": "pre_applied", "applied": len(result["applied_ids"])}

    except Exception as e:  # noqa: BLE001 — best-effort, never fatal
        logger.warning(f"[job:{job_id}] pre-apply failed non-fatally: {e}", exc_info=True)
        return {"outcome": "error", "error": str(e)}
