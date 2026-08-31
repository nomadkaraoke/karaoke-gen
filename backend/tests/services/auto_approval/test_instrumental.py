"""Tests for the shared instrumental-decision helper (workstream C).

``backing_decision`` is the single source of truth used by the executor, the
complete-review "auto" resolver, and the per-screen-skip UI summary — they must
all agree, so the matrix is pinned here.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.services.auto_approval.instrumental import (
    auto_approval_summary,
    backing_decision,
    has_custom_instrumental,
    resolve_auto_instrumental,
)


def _decide(**kw):
    base = dict(
        backing_verdict="clean",
        backing_non_subjective=True,
        backing_preference="auto",
        backing_keep_enabled=True,
        custom_instrumental=False,
    )
    base.update(kw)
    return backing_decision(**base)


# ---- backing_decision matrix ------------------------------------------------

def test_custom_instrumental_always_custom():
    d = _decide(custom_instrumental=True, backing_verdict="review", backing_non_subjective=False)
    assert d == {"ok": True, "selection": "custom", "keep_backing": False}


def test_clean_verdict_auto_resolves_clean():
    assert _decide(backing_verdict="clean")["selection"] == "clean"
    assert _decide(backing_verdict="clean")["ok"] is True


def test_with_backing_verdict_keep_enabled_resolves_with_backing():
    d = _decide(backing_verdict="with_backing", backing_keep_enabled=True)
    assert d == {"ok": True, "selection": "with_backing", "keep_backing": True}


def test_with_backing_verdict_keep_disabled_not_resolvable():
    d = _decide(backing_verdict="with_backing", backing_keep_enabled=False)
    assert d["ok"] is False and d["selection"] is None and d["keep_backing"] is True


def test_review_verdict_not_resolvable():
    d = _decide(backing_verdict="review", backing_non_subjective=False)
    assert d["ok"] is False and d["selection"] is None


def test_preference_clean_forces_clean_even_over_review_verdict():
    # User explicitly asked to strip backing vocals -> clean regardless of verdict.
    d = _decide(backing_verdict="review", backing_non_subjective=False, backing_preference="clean")
    assert d == {"ok": True, "selection": "clean", "keep_backing": False}


def test_preference_clean_overrides_with_backing_verdict():
    d = _decide(backing_verdict="with_backing", backing_preference="clean")
    assert d["selection"] == "clean" and d["keep_backing"] is False


def test_preference_review_suppresses_even_confident_clean():
    # User always wants to pick the instrumental themselves.
    d = _decide(backing_verdict="clean", backing_preference="review")
    assert d["ok"] is False and d["selection"] is None


def test_unknown_preference_falls_back_to_auto():
    assert _decide(backing_verdict="clean", backing_preference="garbage")["selection"] == "clean"


# ---- has_custom_instrumental ------------------------------------------------

def test_has_custom_instrumental_from_existing_path():
    job = SimpleNamespace(existing_instrumental_gcs_path="jobs/j/inst.flac", file_urls=None)
    assert has_custom_instrumental(job) is True


def test_has_custom_instrumental_from_stem():
    job = SimpleNamespace(
        existing_instrumental_gcs_path=None,
        file_urls={"stems": {"custom_instrumental": "jobs/j/custom.flac"}},
    )
    assert has_custom_instrumental(job) is True


def test_has_custom_instrumental_false():
    job = SimpleNamespace(
        existing_instrumental_gcs_path=None,
        file_urls={"stems": {"instrumental_clean": "jobs/j/clean.flac"}},
    )
    assert has_custom_instrumental(job) is False


# ---- summary + resolver over stored verdict ---------------------------------

def _job_with_verdict(backing_verdict, non_subjective, lyrics_verdict="auto", pref="auto",
                      custom=False, review_mode="auto", stems=None):
    if stems is None:
        stems = {
            "instrumental_clean": "jobs/j/clean.flac",
            "instrumental_with_backing": "jobs/j/backing.flac",
        }
    return SimpleNamespace(
        processing_metadata={
            "auto_approval": {
                "backing": {"verdict": backing_verdict, "non_subjective": non_subjective},
                "lyrics": {"verdict": lyrics_verdict},
            }
        },
        backing_preference=pref,
        review_mode=review_mode,
        existing_instrumental_gcs_path=("jobs/j/inst.flac" if custom else None),
        file_urls={"stems": stems},
    )


def _settings(keep=True):
    return SimpleNamespace(auto_approval_backing_keep_enabled=keep)


def test_summary_confident_clean():
    s = auto_approval_summary(_job_with_verdict("clean", True), _settings())
    assert s["backing"] == {"verdict": "clean", "confident": True, "resolved_selection": "clean"}
    assert s["lyrics"] == {"verdict": "auto", "confident": True}
    assert s["verdict_present"] is True


def test_summary_backing_needs_review_not_confident():
    s = auto_approval_summary(_job_with_verdict("review", False), _settings())
    assert s["backing"]["confident"] is False
    assert s["backing"]["resolved_selection"] is None


def test_summary_no_verdict_is_safe_default():
    job = SimpleNamespace(processing_metadata={}, backing_preference="auto",
                          existing_instrumental_gcs_path=None, file_urls={"stems": {}})
    s = auto_approval_summary(job, _settings())
    assert s["verdict_present"] is False
    assert s["backing"]["confident"] is False
    assert s["lyrics"]["confident"] is False


def test_resolve_auto_instrumental_with_backing_gated():
    # keep disabled -> not resolvable
    assert resolve_auto_instrumental(_job_with_verdict("with_backing", True), _settings(keep=False)) is None
    # keep enabled -> with_backing
    assert resolve_auto_instrumental(_job_with_verdict("with_backing", True), _settings(keep=True)) == "with_backing"


def test_resolve_auto_instrumental_custom():
    assert resolve_auto_instrumental(_job_with_verdict("review", False, custom=True), _settings()) == "custom"


def test_always_review_never_confident():
    # review_mode="always_review" -> the user wants every screen; nothing skips.
    s = auto_approval_summary(_job_with_verdict("clean", True, review_mode="always_review"), _settings())
    assert s["backing"]["confident"] is False
    assert s["backing"]["resolved_selection"] is None
    assert s["lyrics"]["confident"] is False
    assert resolve_auto_instrumental(
        _job_with_verdict("clean", True, review_mode="always_review"), _settings()) is None


def test_resolve_returns_none_when_clean_stem_absent():
    # Confident clean verdict but the clean stem isn't present -> a human must pick.
    job = _job_with_verdict("clean", True, stems={"instrumental_with_backing": "x"})
    assert resolve_auto_instrumental(job, _settings()) is None
    assert auto_approval_summary(job, _settings())["backing"]["confident"] is False


def test_resolve_returns_none_when_with_backing_stem_absent():
    job = _job_with_verdict("with_backing", True, stems={"instrumental_clean": "x"})
    assert resolve_auto_instrumental(job, _settings(keep=True)) is None


# ---- late backing analysis self-correction (race fix) -----------------------

def _job_scored_before_analysis(analysis, stems=None):
    """A job whose stored verdict was scored WITHOUT the backing analysis
    (screens_worker ran while audio was still separating), but the analysis has
    since landed in state_data. Mirrors the real race for job be8231fd."""
    if stems is None:
        stems = {
            "instrumental_clean": "jobs/j/clean.flac",
            "instrumental_with_backing": "jobs/j/backing.flac",
        }
    return SimpleNamespace(
        processing_metadata={
            "auto_approval": {
                # verdict recorded while audio was incomplete: no analysis -> review
                "backing": {"verdict": "review", "non_subjective": False},
                "lyrics": {"verdict": "review"},
                "backing_analysis_available": False,
            }
        },
        state_data={"backing_vocals_analysis": analysis},
        backing_preference="auto",
        review_mode="auto",
        existing_instrumental_gcs_path=None,
        file_urls={"stems": stems},
    )


# Real signals from be8231fd ("The Qemists - Take It Back"): genuine backing,
# not a misclassified lead (corr 0.75 < 0.80), above the noise floor (-28.7 dB),
# well over the bleed threshold (64% audible) -> confident WITH_BACKING keep.
_GENUINE_BACKING_ANALYSIS = {
    "has_audible_content": True,
    "audible_percentage": 63.99,
    "recommended_selection": "with_backing",
    "audible_segments": [],
    "stem_comparison": {
        "coverage_ratio": 0.9182,
        "corr_backing_vocals": 0.7478,
        "corr_backing_lead": 0.5107,
        "backing_median_db": -28.74,
        "backing_audible_fraction": 0.5396,
        "lead_audible_fraction": 0.4869,
        "lead_overlap_fraction": 0.8305,
        "backing_db_std": 5.213,
        "flat_fraction": 0.1403,
        "error": None,
    },
}


def test_summary_self_corrects_when_analysis_arrives_after_scoring():
    # Stored verdict is a stale "review" (no analysis); the analysis is now present
    # -> the summary re-derives a confident with_backing keep.
    job = _job_scored_before_analysis(_GENUINE_BACKING_ANALYSIS)
    s = auto_approval_summary(job, _settings(keep=True))
    assert s["backing"]["confident"] is True
    assert s["backing"]["resolved_selection"] == "with_backing"


def test_summary_self_correction_respects_keep_flag():
    # Same late analysis, but the keep flag is off -> still needs a human pick.
    job = _job_scored_before_analysis(_GENUINE_BACKING_ANALYSIS)
    s = auto_approval_summary(job, _settings(keep=False))
    assert s["backing"]["confident"] is False
    assert s["backing"]["resolved_selection"] is None


def test_summary_does_not_self_correct_when_analysis_was_present():
    # When the stored verdict already had the analysis, trust it verbatim (no
    # re-derivation) even if state_data carries an analysis.
    job = _job_scored_before_analysis(_GENUINE_BACKING_ANALYSIS)
    job.processing_metadata["auto_approval"]["backing_analysis_available"] = True
    s = auto_approval_summary(job, _settings(keep=True))
    assert s["backing"]["confident"] is False  # trusts the stored "review"


# ---- JobCreate coercion (typo must never silently flip intent) ---------------

def test_jobcreate_coerces_out_of_set_preferences():
    from backend.models.job import JobCreate
    assert JobCreate(backing_preference="cleen").backing_preference == "auto"
    assert JobCreate(backing_preference="clean").backing_preference == "clean"
    # review_mode fails safe toward MORE review, never toward auto-skip.
    assert JobCreate(review_mode="typo").review_mode == "always_review"
    assert JobCreate(review_mode="auto").review_mode == "auto"
    assert JobCreate().backing_preference == "auto" and JobCreate().review_mode == "auto"


# ---- timing gate interaction with the C1 lyrics-skip ------------------------

def test_summary_lyrics_not_confident_while_timing_pending_audio():
    # A verdict scored before audio separation finished could not run the
    # timing check; the lyrics screen must not be skipped until the
    # audio_worker re-score clears the pending marker.
    job = _job_with_verdict("clean", True)
    job.processing_metadata["auto_approval"]["timing"] = {"status": "pending_audio"}
    s = auto_approval_summary(job, _settings())
    assert s["lyrics"]["confident"] is False


def test_summary_lyrics_confident_when_timing_checked():
    job = _job_with_verdict("clean", True)
    job.processing_metadata["auto_approval"]["timing"] = {"status": "checked", "fired": []}
    s = auto_approval_summary(job, _settings())
    assert s["lyrics"]["confident"] is True


def test_summary_lyrics_confident_without_timing_block():
    # Jobs scored before the timing gate existed have no timing block at all —
    # their confidence semantics are unchanged.
    s = auto_approval_summary(_job_with_verdict("clean", True), _settings())
    assert s["lyrics"]["confident"] is True
