"""Server-side pre-apply of AI corrections BEFORE the review-ready notification.

Andrew's directive (2026-08-29): *"apply the ai-generated lyrics corrections and any
post-ai heuristic fixes before the user ever sees the notification/button saying the
lyrics review is ready ... it should all be done server-side and loaded as a single
load event when the lyrics review page launches."*

Called from ``screens_worker`` for review-bound jobs (i.e. when the executor did NOT
auto-complete the whole job), just before the AWAITING_REVIEW transition. It applies
the same cached suggestions the review UI would auto-apply on load, using the same
machinery the executor uses (``apply.build_applied_segments``), and writes
``corrections_updated.json`` with a ``pre_applied`` marker so the ``/correction-data``
endpoint serves the already-corrected state in a single load — eliminating the
in-browser auto-apply race.

BEST-EFFORT: never raises, bounded. On any miss (proactive disabled, cache absent,
LLM outage, sanity-check abort) it simply does nothing and the review UI falls back to
its existing client-side on-load auto-apply. Auto-approval must never strand a job.
"""
from __future__ import annotations

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
    so a review page never blocks on a multi-model LLM call; a cache-miss there
    falls through to the UI's own client-side apply.
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
        if not storage.file_exists(corrections_path):
            return {"outcome": "skipped", "reason": "no_corrections"}

        updated_path = CORRECTIONS_UPDATED_PATH.format(job_id=job_id)
        if storage.file_exists(updated_path):
            # Already applied once (executor auto-complete, a prior pre-apply run,
            # or a human edit). Never clobber existing corrections_updated.json.
            try:
                existing = storage.download_json(updated_path)
                if ((existing.get("metadata") or {}).get("auto_approval")):
                    return {"outcome": "skipped", "reason": "already_applied"}
            except Exception:
                pass
            return {"outcome": "skipped", "reason": "corrections_updated_exists"}

        corrections = storage.download_json(corrections_path)

        # 1) Ensure the proactive suggestion cache exists (generate on miss, unless
        #    the caller — e.g. the load-time path — asked to only apply an existing
        #    cache so the request doesn't block on an LLM call).
        ai_suggestions = _load_ai_suggestions(storage, job_id)
        if ai_suggestions is None and generate_on_miss and settings.auto_correct_proactive_enabled:
            logger.info(f"[job:{job_id}] pre-apply: cache miss, generating suggestions synchronously")
            from backend.workers.auto_correct_worker import process_proactive_auto_correct
            await process_proactive_auto_correct(job_id)  # bounded (180s) + best-effort
            ai_suggestions = _load_ai_suggestions(storage, job_id)

        if ai_suggestions is None:
            # Unknown suggestion set (proactive disabled or generation failed) —
            # do NOT mark pre_applied; let the UI run its client-side apply.
            return {"outcome": "skipped", "reason": "no_suggestions_cache"}

        # 2) Apply with the executor's sanity gates.
        result = build_applied_segments(corrections, ai_suggestions)
        if result.get("aborted"):
            logger.info(f"[job:{job_id}] pre-apply aborted ({result['aborted']}) — UI will apply client-side")
            return {"outcome": "skipped", "reason": result["aborted"]}

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
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
        updated["metadata"] = metadata
        storage.upload_json(updated_path, updated)
        JobManager().update_file_url(job_id, "lyrics", "corrections_updated", updated_path)

        logger.info(
            f"[job:{job_id}] pre-apply saved corrections_updated.json "
            f"({len(result['applied_ids'])} applied, {len(result['rejected_ids'])} rejected) "
            "before review notification"
        )
        return {"outcome": "pre_applied", "applied": len(result["applied_ids"])}

    except Exception as e:  # noqa: BLE001 — best-effort, never fatal
        logger.warning(f"[job:{job_id}] pre-apply failed non-fatally: {e}", exc_info=True)
        return {"outcome": "error", "error": str(e)}
