"""Tests for the Phase-2B 3-stem backing decider (scorer branch + executor gating).

Comparison fixtures mirror the real corpus signal profiles (private
validate_backing_decider.py table, 2026-08-28): the catastrophic
backing-stem-is-the-lead job (1d45b286), the noise-floor grungegaze job
(95d8e844), the quiet-genuine-harmonies job (d508adb6), and the high-corr
genuine keep that must NOT trip the lead gate (33453fa0).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.services.auto_approval.models import BackingVerdict
from backend.services.auto_approval.scorer import extract_backing_signals, score_backing


def _analysis(
    audible_percentage: float = 25.0,
    loud: int = 2,
    comparison: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    segments = [
        {
            "start_seconds": 10.0 * i,
            "end_seconds": 10.0 * i + 5.0,
            "duration_seconds": 5.0,
            "avg_amplitude_db": -15.0 if i < loud else -35.0,
            "peak_amplitude_db": -10.0,
        }
        for i in range(4)
    ]
    data: Dict[str, Any] = {
        "has_audible_content": True,
        "audible_percentage": audible_percentage,
        "audible_segments": segments,
        "recommended_selection": "with_backing",
    }
    if comparison is not None:
        data["stem_comparison"] = comparison
    return data


def _comparison(**overrides: Any) -> Dict[str, Any]:
    base = {
        "window_ms": 100,
        "silence_threshold_db": -40.0,
        "duration_seconds": 200.0,
        "backing_audible_fraction": 0.30,
        "lead_audible_fraction": 0.65,
        "vocals_audible_fraction": 0.70,
        "coverage_ratio": 0.43,
        "corr_backing_vocals": 0.40,
        "corr_backing_lead": 0.20,
        "backing_median_db": -26.0,
        "lead_median_db": -18.0,
        "vocals_median_db": -17.0,
        "lead_overlap_fraction": 0.80,
        "backing_db_std": 4.5,
        "flat_fraction": 0.05,
        "error": None,
    }
    base.update(overrides)
    return base


def test_genuine_backing_is_confident_keep() -> None:
    result = score_backing(_analysis(comparison=_comparison()))
    assert result.verdict == BackingVerdict.WITH_BACKING
    assert result.non_subjective is True


def test_no_comparison_stays_review() -> None:
    result = score_backing(_analysis())
    assert result.verdict == BackingVerdict.REVIEW
    assert result.non_subjective is False


def test_errored_comparison_reads_as_absent() -> None:
    result = score_backing(
        _analysis(comparison=_comparison(error="boom"))
    )
    assert result.verdict == BackingVerdict.REVIEW
    assert result.signals.comparison_present is False


def test_backing_stem_is_lead_never_keeps() -> None:
    # 1d45b286 profile: covR 0.94, corr 0.95, backing 60% vs lead 18%.
    result = score_backing(
        _analysis(
            audible_percentage=60.0,
            comparison=_comparison(
                coverage_ratio=0.94,
                corr_backing_vocals=0.95,
                backing_audible_fraction=0.60,
                lead_audible_fraction=0.18,
            ),
        )
    )
    assert result.verdict == BackingVerdict.REVIEW
    assert "BE the lead" in " ".join(result.reasons)


def test_high_corr_keep_with_stronger_lead_does_not_trip_lead_gate() -> None:
    # 33453fa0 profile: covR 0.77 / corr 0.86 but backing (28%) < lead (34%).
    result = score_backing(
        _analysis(
            audible_percentage=28.0,
            comparison=_comparison(
                coverage_ratio=0.77,
                corr_backing_vocals=0.86,
                backing_audible_fraction=0.28,
                lead_audible_fraction=0.34,
                backing_median_db=-33.0,
            ),
        )
    )
    assert result.verdict == BackingVerdict.WITH_BACKING


def test_noise_floor_signature_is_review() -> None:
    # 95d8e844 profile: median -38.6 dB over 30% of the track, corr -0.23.
    result = score_backing(
        _analysis(
            audible_percentage=30.0,
            comparison=_comparison(
                backing_median_db=-38.6,
                backing_audible_fraction=0.30,
                corr_backing_vocals=-0.23,
                coverage_ratio=0.42,
            ),
        )
    )
    assert result.verdict == BackingVerdict.REVIEW
    assert "noise-floor" in " ".join(result.reasons)


def test_quiet_sparse_harmonies_still_keep() -> None:
    # d508adb6 profile: 1% audible at -38 dB, overlap 0.89 — genuine harmonies.
    result = score_backing(
        _analysis(
            audible_percentage=1.0,
            loud=1,
            comparison=_comparison(
                backing_median_db=-38.1,
                backing_audible_fraction=0.01,
                corr_backing_vocals=0.19,
                coverage_ratio=0.01,
                lead_overlap_fraction=0.89,
            ),
        )
    )
    assert result.verdict == BackingVerdict.WITH_BACKING


def test_sparse_full_lead_overlap_is_bleed_review() -> None:
    result = score_backing(
        _analysis(
            audible_percentage=2.0,
            comparison=_comparison(
                backing_audible_fraction=0.02,
                coverage_ratio=0.03,
                lead_overlap_fraction=0.99,
            ),
        )
    )
    assert result.verdict == BackingVerdict.REVIEW
    assert "bleed" in " ".join(result.reasons)


def test_near_silent_rule_still_wins_over_comparison() -> None:
    analysis = _analysis(audible_percentage=0.1, comparison=_comparison())
    analysis["audible_segments"] = []
    result = score_backing(analysis)
    assert result.verdict == BackingVerdict.CLEAN


def test_comparison_signals_extracted() -> None:
    s = extract_backing_signals(_analysis(comparison=_comparison()))
    assert s.comparison_present is True
    assert s.coverage_ratio == 0.43
    assert s.corr_backing_vocals == 0.40
    assert s.lead_overlap_fraction == 0.80
    assert s.backing_median_db == -26.0
    assert s.flat_fraction == 0.05


# ---- executor gating for WITH_BACKING enforcement ----

from backend.models.job import JobStatus  # noqa: E402
from backend.tests.services.auto_approval.test_executor import (  # noqa: E402
    _confident_corrections,
    _job,
    _run,
)


def _keep_backing_analysis() -> Dict[str, Any]:
    return _analysis(comparison=_comparison())


def _keep_job(with_backing_stem: bool = True) -> SimpleNamespace:
    job = _job(backing=_keep_backing_analysis())
    if with_backing_stem:
        job.file_urls["stems"]["instrumental_with_backing"] = (
            "jobs/j1/stems/instrumental_with_backing.flac"
        )
    return job


def _settings(keep_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        auto_approval_enforce_enabled=True,
        auto_approval_backing_keep_enabled=keep_enabled,
    )


@pytest.mark.asyncio
async def test_confident_keep_blocked_while_flag_off() -> None:
    result, job_manager, *_ = await _run(_keep_job(), settings=_settings(False))
    assert result["outcome"] == "review"
    assert "backing_keep_disabled" in result["blockers"]
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["backing"]["verdict"] == "with_backing"


@pytest.mark.asyncio
async def test_confident_keep_enforced_when_flag_on() -> None:
    result, job_manager, _storage, worker = await _run(
        _keep_job(), settings=_settings(True)
    )
    assert result["outcome"] == "auto_completed"
    job_manager.update_state_data.assert_any_call(
        "j1", "instrumental_selection", "with_backing"
    )
    worker.trigger_render_video_worker.assert_awaited_once()


@pytest.mark.asyncio
async def test_confident_keep_needs_with_backing_stem() -> None:
    result, *_ = await _run(
        _keep_job(with_backing_stem=False), settings=_settings(True)
    )
    assert result["outcome"] == "review"
    assert "no_with_backing_stem" in result["blockers"]
