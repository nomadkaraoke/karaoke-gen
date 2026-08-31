"""Auto-approval executor: complete the combined review without a human when safe.

Called from two places (covering both orderings of audio vs lyrics completion):
- ``screens_worker`` just before it would transition to AWAITING_REVIEW
  (trigger="screens_worker", job still GENERATING_SCREENS), and
- ``audio_worker`` after stems + backing analysis land, when the job is already
  sitting in AWAITING_REVIEW (trigger="audio_worker").

Every call scores the job and records the verdict in
``processing_metadata.auto_approval`` (so shadow data accumulates on every job).
Enforcement — actually skipping the review screens — happens only when ALL of:
- the feature flag is on and the job's ``review_mode`` is "auto" (the default);
- the job is not a made-for-you order (the human QC pass is part of that
  product);
- the job is ELIGIBLE: with a user-supplied instrumental (tenant bulk uploads,
  the existing-instrumental upload option, mute-region edits) there is no
  instrumental decision to make, so confident lyrics alone qualify and the
  selection is "custom" — mirroring the human tenant complete-review flow.
  Otherwise the scorer's ``overall_auto`` must be True (confident lyrics AND a
  non-subjective backing decision);
- audio separation is complete and the required instrumental exists (clean or
  with-backing stem; the video worker validates custom sources itself).

The enforce path replicates exactly what a human clicking "Complete Review"
does after the UI's on-load auto-apply: apply the cached AI suggestions
(conflict groups resolved identically), save ``corrections_updated.json``,
store the instrumental selection, clear worker progress keys, transition to
REVIEW_COMPLETE and trigger the render worker.

FAIL-SAFE: any anomaly (stale suggestions, duplicate-word artifacts, missing
data, exceptions) aborts enforcement and the job proceeds to normal human
review. Auto-approval must never be the reason a job fails.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.models.job import JobStatus
from backend.services.auto_approval.instrumental import (
    backing_decision,
    has_custom_instrumental,
)
from backend.services.auto_approval.models import LyricsVerdict

logger = logging.getLogger(__name__)

CORRECTIONS_PATH = "jobs/{job_id}/lyrics/corrections.json"
CORRECTIONS_UPDATED_PATH = "jobs/{job_id}/lyrics/corrections_updated.json"
AUTO_CORRECT_CACHE_PREFIX = "jobs/{job_id}/lyrics/auto_correct_cache/"

REVIEW_MODE_AUTO = "auto"
REVIEW_MODE_ALWAYS_REVIEW = "always_review"


def _load_ai_suggestions(storage, job_id: str) -> Optional[List[Dict[str, Any]]]:
    """The proactive auto-correct cache the review UI would auto-apply on load."""
    try:
        prefix = AUTO_CORRECT_CACHE_PREFIX.format(job_id=job_id)
        for cache_path in sorted(storage.list_files(prefix)):
            cached = storage.download_json(cache_path)
            suggestions = cached.get("suggestions")
            if isinstance(suggestions, list):
                return suggestions
    except Exception as e:
        logger.info(f"[job:{job_id}] auto-approval: no auto-correct cache readable ({e})")
    return None


def _enforcement_blockers(job, settings) -> List[str]:
    """Why this job may not be ENFORCED (shadow scoring still happens)."""
    blockers: List[str] = []
    if not settings.auto_approval_enforce_enabled:
        blockers.append("flag_disabled")
    review_mode = getattr(job, "review_mode", REVIEW_MODE_AUTO) or REVIEW_MODE_AUTO
    if review_mode != REVIEW_MODE_AUTO:
        blockers.append(f"review_mode:{review_mode}")
    if getattr(job, "made_for_you", False):
        blockers.append("made_for_you")
    return blockers


async def maybe_auto_complete_review(job_id: str, trigger: str) -> Dict[str, Any]:
    """Score the job, record the verdict, and auto-complete the review if safe.

    Returns a small status dict; ``{"outcome": "auto_completed"}`` means the
    review screens were skipped and the render worker has been triggered.
    Never raises.
    """
    from backend.config import get_settings
    from backend.services.auto_approval.scorer import score_job
    from backend.services.job_manager import JobManager
    from backend.services.storage_service import StorageService

    outcome: Dict[str, Any] = {"outcome": "skipped"}
    try:
        settings = get_settings()
        job_manager = JobManager()
        storage = StorageService()

        job = job_manager.get_job(job_id)
        if not job:
            return {"outcome": "no_job"}

        # Only act on the two known launch points; a human already in the
        # editor (IN_REVIEW) or any other state is out of scope.
        if trigger == "screens_worker":
            expected_statuses = (JobStatus.GENERATING_SCREENS,)
        else:
            expected_statuses = (JobStatus.AWAITING_REVIEW,)
        if job.status not in expected_statuses:
            return {"outcome": "wrong_status", "status": str(job.status)}
        if job.state_data.get("instrumental_selection"):
            return {"outcome": "already_selected"}

        corrections_path = CORRECTIONS_PATH.format(job_id=job_id)
        if not storage.file_exists(corrections_path):
            return {"outcome": "no_corrections"}
        corrections = storage.download_json(corrections_path)

        ai_suggestions = _load_ai_suggestions(storage, job_id)
        state_data = job.state_data or {}
        backing_analysis = state_data.get("backing_vocals_analysis")
        audio_complete = bool(state_data.get("audio_complete", False))
        stems = (job.file_urls or {}).get("stems", {}) if job.file_urls else {}

        verdict = score_job(corrections, backing_analysis, ai_suggestions)

        # Timing-plausibility gate: word timing vs the lead-vocal stem. Audio
        # IO (~15MB stem + a few seconds of numpy), so it runs only when the
        # text signals would otherwise let the lyrics auto-ship — the only
        # case where it can change the outcome. Fail-OPEN on analysis errors
        # (recorded below; an environmental break must not demote the whole
        # auto class), fail-CLOSED on pending audio (the audio_worker
        # second-chance trigger re-scores once stems exist, and the C1 summary
        # keeps the lyrics screen until then).
        timing_info: Dict[str, Any] = {"status": "not_needed"}
        if verdict.lyrics.verdict == LyricsVerdict.AUTO:
            if not getattr(settings, "auto_approval_timing_gate_enabled", True):
                timing_info = {"status": "disabled"}
            elif not audio_complete:
                timing_info = {"status": "pending_audio"}
            else:
                signals = _compute_timing_signals(
                    job_id, corrections, ai_suggestions, stems, storage
                )
                if signals is None:
                    timing_info = {"status": "no_lead_stem"}
                elif signals.error:
                    timing_info = {"status": "error", "error": signals.error}
                else:
                    timing_info = {"status": "checked", **signals.to_dict()}
                    if signals.fired:
                        verdict = score_job(
                            corrections, backing_analysis, ai_suggestions,
                            timing_signals=signals,
                        )

        blockers = _enforcement_blockers(job, settings)
        custom_instrumental = has_custom_instrumental(job)
        backing_preference = getattr(job, "backing_preference", "auto") or "auto"
        backing_keep_enabled = getattr(settings, "auto_approval_backing_keep_enabled", False)
        # Single source of truth for the instrumental decision (shared with the
        # complete-review "auto" resolver and the per-screen-skip UI summary).
        decision = backing_decision(
            backing_verdict=verdict.backing.verdict.value,
            backing_non_subjective=verdict.backing.non_subjective,
            backing_preference=backing_preference,
            backing_keep_enabled=backing_keep_enabled,
            custom_instrumental=custom_instrumental,
        )
        keep_backing = decision["keep_backing"]

        # Eligibility: confident lyrics AND a resolvable instrumental decision.
        # With a user-supplied instrumental the backing decision is moot, so
        # confident lyrics alone qualify (selection="custom").
        lyrics_auto = verdict.lyrics.verdict == LyricsVerdict.AUTO
        if custom_instrumental:
            eligible = lyrics_auto
            enforcement_basis = "lyrics_only_custom_instrumental"
        else:
            eligible = lyrics_auto and decision["ok"]
            enforcement_basis = (
                "overall_auto"
                if backing_preference == "auto"
                else f"lyrics_auto_backing_pref:{backing_preference}"
            )

        # Stem / audio blockers (the instrumental the selection needs must exist).
        if not audio_complete:
            blockers.append("audio_incomplete")
        elif custom_instrumental:
            # User-supplied instrumental -> "custom"; the video worker validates
            # the custom source itself, no separated-stem requirements.
            pass
        elif keep_backing:
            # Confident-keep (3-stem decider) selects the with-backing
            # instrumental — gated shadow-first behind its own flag.
            if not backing_keep_enabled:
                blockers.append("backing_keep_disabled")
            if not stems.get("instrumental_with_backing"):
                blockers.append("no_with_backing_stem")
        elif not stems.get("instrumental_clean"):
            # Clean selection (auto-clean verdict OR backing_preference="clean").
            blockers.append("no_clean_stem")

        payload = verdict.to_dict()
        payload.update({
            "mode": "shadow",  # flipped to "enforce" only when we actually enforce
            "enforcement_eligible": eligible and not blockers,
            "enforcement_basis": enforcement_basis,
            "custom_instrumental": custom_instrumental,
            "backing_preference": backing_preference,
            "resolved_instrumental_selection": decision["selection"],
            "trigger": trigger,
            "enforcement_blockers": blockers,
            "timing": timing_info,
            "audio_complete_at_scoring": audio_complete,
            "backing_analysis_available": backing_analysis is not None,
            "ai_suggestions_available": ai_suggestions is not None,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        })

        summary = (
            f"auto-approval [{trigger}]: eligible={eligible} ({enforcement_basis}) "
            f"lyrics={verdict.lyrics.verdict.value}/{verdict.lyrics.tier} "
            f"backing={verdict.backing.verdict.value} blockers={blockers or 'none'} "
            f"(scorer v{verdict.scorer_version})"
        )
        logger.info(f"[job:{job_id}] {summary}")

        if not eligible or blockers:
            payload["outcome"] = "review"
            job_manager.update_processing_metadata(job_id, "auto_approval", payload)
            return {"outcome": "review", "overall_auto": verdict.overall_auto,
                    "blockers": blockers}

        # ---- ENFORCE: complete the review exactly like a human would ----
        # The status gate above used a snapshot read. Re-read now so a human who
        # opened the editor in the meantime (AWAITING_REVIEW -> IN_REVIEW) is not
        # overwritten — IN_REVIEW -> REVIEW_COMPLETE is a legal transition and
        # would otherwise pass validation mid-edit.
        fresh = job_manager.get_job(job_id)
        if (
            not fresh
            or fresh.status not in expected_statuses
            or (fresh.state_data or {}).get("instrumental_selection")
        ):
            payload["outcome"] = "aborted"
            payload["abort_reason"] = "state_changed_during_enforce"
            job_manager.update_processing_metadata(job_id, "auto_approval", payload)
            return {"outcome": "aborted", "reason": "state_changed_during_enforce"}

        applied_info = _build_auto_corrections(
            job_id, corrections, ai_suggestions or [], storage
        )
        if applied_info.get("aborted"):
            payload["outcome"] = "aborted"
            payload["abort_reason"] = applied_info["aborted"]
            job_manager.update_processing_metadata(job_id, "auto_approval", payload)
            logger.warning(
                f"[job:{job_id}] auto-approval aborted -> human review "
                f"({applied_info['aborted']})"
            )
            return {"outcome": "aborted", "reason": applied_info["aborted"]}

        # Instrumental selection resolved by the shared decision: user-supplied
        # instrumental -> "custom"; otherwise the non-subjective backing verdict
        # (or the user's backing_preference) picks the separated stem.
        selection = decision["selection"] or ("with_backing" if keep_backing else "clean")
        job_manager.update_state_data(job_id, "instrumental_selection", selection)

        # Clear worker progress keys so downstream workers run fresh (mirrors
        # complete_review — idempotency keys would otherwise skip re-runs).
        progress_keys = ["render_progress", "video_progress", "encoding_progress"]
        job_manager.delete_state_data_keys(job_id, progress_keys)

        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.REVIEW_COMPLETE,
            progress=70,
            message=(
                "Auto-approved: lyrics verified against synced references and "
                + (
                    "using your provided instrumental"
                    if custom_instrumental
                    else (
                        "clear backing vocals retained"
                        if keep_backing
                        else "no audible backing vocals"
                    )
                )
                + " — skipping review, rendering video"
            ),
        )

        # Record the verdict AFTER the transition so the metadata never claims
        # auto-completion for a job that in fact fell back to human review.
        payload["mode"] = "enforce"
        payload["outcome"] = "auto_completed"
        payload["applied_suggestions"] = len(applied_info.get("applied_ids") or [])
        job_manager.update_processing_metadata(job_id, "auto_approval", payload)

        from backend.services.worker_service import get_worker_service
        await get_worker_service().trigger_render_video_worker(job_id)

        logger.info(f"[job:{job_id}] auto-approval COMPLETED review "
                    f"(applied {payload['applied_suggestions']} AI suggestions)")
        return {"outcome": "auto_completed"}

    except Exception as e:
        logger.warning(f"[job:{job_id}] auto-approval failed non-fatally: {e}", exc_info=True)
        try:
            from backend.services.job_manager import JobManager
            JobManager().update_processing_metadata(job_id, "auto_approval", {
                "outcome": "error",
                "error": str(e),
                "trigger": trigger,
                "scored_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        return {"outcome": "error", "error": str(e)}


def _compute_timing_signals(job_id, corrections, ai_suggestions, stems, storage):
    """Download the lead-vocal stem and run the timing-plausibility analysis
    on the POST-AI word stream (the state auto-ship would publish).

    Returns ``None`` when no lead/vocals stem is registered, else a
    ``TimingSignals`` (whose ``.error`` is set on analysis failure — caller
    decides fail-open). Never raises.
    """
    import os
    import tempfile

    from backend.services.auto_approval.apply import build_applied_segments
    from backend.services.auto_approval.timing_check import (
        TimingSignals,
        compute_timing_signals,
    )

    stem_path = stems.get("lead_vocals") or stems.get("vocals_clean")
    if not stem_path:
        return None
    try:
        # The published state is raw + auto-applied suggestions; on an apply
        # abort the executor falls back to human review anyway, so analyzing
        # the raw segments there is only shadow data.
        segments = corrections.get("corrected_segments") or []
        if ai_suggestions:
            applied = build_applied_segments(corrections, ai_suggestions)
            if not applied.get("aborted"):
                segments = applied["segments"]
        with tempfile.TemporaryDirectory() as temp_dir:
            local = os.path.join(temp_dir, "lead_vocals.flac")
            storage.download_file(stem_path, local)
            signals = compute_timing_signals(segments, local)
        logger.info(
            f"[job:{job_id}] timing check: fired={signals.fired or 'no'} "
            f"shadow={signals.shadow_fired or 'no'} "
            f"(start_inactive={signals.pct_start_inactive}% "
            f"suspect_bad={signals.n_suspect_bad} "
            f"unclaimed_run={signals.max_unclaimed_run_s}s error={signals.error})"
        )
        return signals
    except Exception as e:  # noqa: BLE001 — gate is best-effort by design
        logger.warning(f"[job:{job_id}] timing check failed non-fatally: {e}")
        return TimingSignals(error=str(e))


def _build_auto_corrections(
    job_id: str,
    corrections: Dict[str, Any],
    ai_suggestions: List[Dict[str, Any]],
    storage,
) -> Dict[str, Any]:
    """Apply the AI suggestions and save corrections_updated.json.

    Returns ``{"aborted": <reason>}`` on any anomaly (caller falls back to
    human review), else ``{"applied_ids": [...]}``.
    """
    from backend.services.auto_approval.apply import build_applied_segments

    result = build_applied_segments(corrections, ai_suggestions)
    if result.get("aborted"):
        return {"aborted": result["aborted"]}

    updated = dict(corrections)
    updated["corrected_segments"] = result["segments"]
    metadata = dict(updated.get("metadata") or {})
    metadata["auto_approval"] = {
        "auto_approved": True,
        "applied_suggestion_ids": result["applied_ids"],
        "rejected_suggestion_ids": result["rejected_ids"],
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    updated["metadata"] = metadata

    updated_path = CORRECTIONS_UPDATED_PATH.format(job_id=job_id)
    storage.upload_json(updated_path, updated)

    from backend.services.job_manager import JobManager
    JobManager().update_file_url(job_id, "lyrics", "corrections_updated", updated_path)
    logger.info(
        f"[job:{job_id}] auto-approval saved corrections_updated.json "
        f"({len(result['applied_ids'])} suggestions applied, "
        f"{len(result['rejected_ids'])} conflict-losers rejected)"
    )
    return {"applied_ids": result["applied_ids"]}
