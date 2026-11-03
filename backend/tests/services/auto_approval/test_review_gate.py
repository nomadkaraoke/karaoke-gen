"""Tests for the review gate: holding lyrics-confident jobs out of review until
stems land, and releasing them exactly once (audio worker / screens worker race /
stuck-job sweep)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import JobStatus
from backend.services.auto_approval import review_gate

HOLD_KEY = "auto_review_waiting_for_stems"


def _job(status=JobStatus.GENERATING_SCREENS, **state):
    return SimpleNamespace(status=status, state_data=dict(state))


def _jm(*jobs, claim=True):
    jm = MagicMock()
    jm.get_job.side_effect = list(jobs)
    jm.claim_auto_review_hold.return_value = claim
    return jm


def _patches(jm, auto_result=None, pre_apply=None):
    return (
        patch("backend.services.job_manager.JobManager", return_value=jm),
        patch("backend.services.auto_approval.executor.maybe_auto_complete_review",
              AsyncMock(return_value=auto_result or {"outcome": "review"})),
        patch("backend.services.auto_approval.pre_apply.ensure_and_pre_apply",
              AsyncMock(return_value=pre_apply or {"outcome": "skipped"})),
    )


async def _call(fn, jm, *args, auto_result=None, **kwargs):
    p1, p2, p3 = _patches(jm, auto_result)
    with p1, p2 as auto, p3 as pre:
        out = await fn(*args, **kwargs)
    return out, auto, pre


@pytest.mark.asyncio
async def test_no_hold_opens_review_with_pre_apply() -> None:
    jm = _jm()
    out, auto, pre = await _call(review_gate.route_after_scoring, jm, "j1", {"outcome": "review"})
    assert out == "review"
    pre.assert_awaited_once_with("j1")
    assert jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.AWAITING_REVIEW
    jm.park_review_for_auto_check.assert_not_called()
    auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_hold_parks_job_without_opening_review() -> None:
    jm = _jm(_job(audio_complete=False))
    out, auto, pre = await _call(
        review_gate.route_after_scoring, jm, "j1", {"outcome": "review", "hold_for_audio": True})
    assert out == "held"
    jm.park_review_for_auto_check.assert_called_once_with("j1")
    jm.transition_to_state.assert_not_called()
    pre.assert_not_awaited()
    auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_stems_landing_while_parking_rescores_immediately() -> None:
    # Audio finished between scoring and parking: the screens worker claims the
    # hold itself (the audio worker already ran its release and found no flag).
    jm = _jm(_job(audio_complete=True), _job(audio_complete=True))
    out, auto, _ = await _call(
        review_gate.route_after_scoring, jm, "j1", {"outcome": "review", "hold_for_audio": True},
        auto_result={"outcome": "auto_completed"})
    assert out == "auto_completed"
    auto.assert_awaited_once_with("j1", trigger="audio_hold")


@pytest.mark.asyncio
async def test_stems_landing_while_parking_but_audio_worker_claimed() -> None:
    jm = _jm(_job(audio_complete=True), claim=False)
    out, auto, _ = await _call(
        review_gate.route_after_scoring, jm, "j1", {"outcome": "review", "hold_for_audio": True})
    assert out == "released_elsewhere"
    auto.assert_not_awaited()
    jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_release_without_hold_is_noop() -> None:
    jm = _jm(claim=False)
    out, auto, _ = await _call(review_gate.release_audio_hold, jm, "j1", reason="audio_worker")
    assert out is None
    auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_auto_completes() -> None:
    jm = _jm(_job())
    out, auto, pre = await _call(review_gate.release_audio_hold, jm, "j1", reason="audio_worker",
                                 auto_result={"outcome": "auto_completed"})
    assert out == "auto_completed"
    pre.assert_not_awaited()
    jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_release_falls_back_to_human_review() -> None:
    # Re-score says review (e.g. timing gate fired / backing needs a human).
    jm = _jm(_job(), _job())
    out, _, pre = await _call(review_gate.release_audio_hold, jm, "j1", reason="audio_worker",
                              auto_result={"outcome": "review"})
    assert out == "review"
    pre.assert_awaited_once()
    assert jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.AWAITING_REVIEW


@pytest.mark.asyncio
async def test_release_after_job_moved_on_does_nothing() -> None:
    jm = _jm(_job(status=JobStatus.FAILED))
    out, auto, _ = await _call(review_gate.release_audio_hold, jm, "j1", reason="hold_timeout")
    assert out == "wrong_status"
    auto.assert_not_awaited()
    jm.transition_to_state.assert_not_called()


def test_audio_hold_expiry() -> None:
    now = datetime(2026, 10, 29, 12, 0, 0)
    fresh = _job(**{HOLD_KEY: (now - timedelta(minutes=5)).isoformat()})
    old = _job(**{HOLD_KEY: (now - timedelta(minutes=25)).isoformat()})
    assert review_gate.audio_hold_expired(fresh, now) is False
    assert review_gate.audio_hold_expired(old, now) is True
    assert review_gate.audio_hold_expired(_job(), now) is False
    assert review_gate.audio_hold_expired(_job(**{HOLD_KEY: False}), now) is False
    assert review_gate.audio_hold_expired(_job(**{HOLD_KEY: "garbage"}), now) is True


@pytest.mark.asyncio
async def test_rescore_crash_after_claim_falls_back_to_review() -> None:
    jm = _jm(_job(), _job())
    p1, p2, p3 = _patches(jm)
    with p1, patch("backend.services.auto_approval.executor.maybe_auto_complete_review",
                   AsyncMock(side_effect=RuntimeError("boom"))), p3:
        out = await review_gate.release_audio_hold("j1", reason="audio_worker")
    assert out == "review"
    assert jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.AWAITING_REVIEW


@pytest.mark.asyncio
async def test_review_fallback_crash_restores_hold_for_sweep() -> None:
    jm = _jm(_job(), _job())
    jm.transition_to_state.side_effect = RuntimeError("firestore down")
    out, _, _ = await _call(review_gate.release_audio_hold, jm, "j1", reason="audio_worker",
                            auto_result={"outcome": "review"})
    assert out == "error"
    jm.park_review_for_auto_check.assert_called_once_with("j1")


def _suggestion_patches(wait=None):
    return (
        patch("backend.workers.auto_correct_worker.process_proactive_auto_correct",
              wait or AsyncMock()),
        patch("backend.config.get_settings",
              return_value=SimpleNamespace(auto_correct_proactive_enabled=True)),
    )


async def _route_without_suggestions(jm, rescored, wait=None):
    first = {"outcome": "review", "ai_suggestions_available": False}
    p_wait, p_settings = _suggestion_patches(wait)
    with p_wait, p_settings:
        return await _call(review_gate.route_after_scoring, jm, "j1", first, auto_result=rescored)


@pytest.mark.asyncio
async def test_missing_suggestions_rescore_holds_instead_of_notifying() -> None:
    """Job 7f89f884: the "review your lyrics" push went out minutes before auto-approval."""
    jm = _jm(_job(audio_complete=False))
    out, auto, pre = await _route_without_suggestions(
        jm, {"outcome": "review", "hold_for_audio": True, "ai_suggestions_available": True})
    assert out == "held"
    auto.assert_awaited_once_with("j1", trigger="screens_worker")
    jm.park_review_for_auto_check.assert_called_once_with("j1")
    jm.transition_to_state.assert_not_called()
    pre.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_suggestions_rescore_can_auto_complete() -> None:
    jm = _jm()
    out, _, pre = await _route_without_suggestions(jm, {"outcome": "auto_completed"})
    assert out == "auto_completed"
    jm.transition_to_state.assert_not_called()
    pre.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_suggestions_rescore_still_review_opens_review() -> None:
    jm = _jm()
    out, auto, pre = await _route_without_suggestions(
        jm, {"outcome": "review", "hold_for_audio": False, "ai_suggestions_available": True})
    assert out == "review"
    auto.assert_awaited_once()
    pre.assert_awaited_once_with("j1")
    assert jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.AWAITING_REVIEW


@pytest.mark.asyncio
async def test_suggestions_wait_failure_still_opens_review() -> None:
    jm = _jm()
    out, auto, _ = await _route_without_suggestions(
        jm, {"outcome": "auto_completed"}, wait=AsyncMock(side_effect=RuntimeError("llm down")))
    assert out == "review"
    auto.assert_not_awaited()
    assert jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.AWAITING_REVIEW


@pytest.mark.asyncio
async def test_rescore_with_no_verdict_keeps_first_verdict() -> None:
    jm = _jm()
    out, _, _ = await _route_without_suggestions(jm, {"outcome": "error"})
    assert out == "review"
    assert jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.AWAITING_REVIEW


@pytest.mark.asyncio
async def test_suggestions_present_scores_only_once() -> None:
    jm = _jm()
    out, auto, _ = await _call(
        review_gate.route_after_scoring, jm, "j1",
        {"outcome": "review", "ai_suggestions_available": True})
    assert out == "review"
    auto.assert_not_awaited()
