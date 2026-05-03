"""Tests for the validate-and-repair loop orchestrator."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.custom_lyrics.result import StopReason
from backend.services.custom_lyrics.service import CustomLyricsService
from backend.services.custom_lyrics.settings import GenerationSettings, StrictnessLevel


def _segment(start: float, end: float, text: str = "x"):
    """Minimal LyricsSegment-shaped dict for tests."""
    return {
        "id": f"seg-{start}",
        "text": text,
        "start_time": start,
        "end_time": end,
        "words": [],
    }


@pytest.fixture
def stub_counter() -> MagicMock:
    c = MagicMock()
    c.count_per_line = MagicMock(
        side_effect=lambda line: [len([w for w in line.split() if w])] * 4
    )
    c.any_method_within = MagicMock(
        side_effect=lambda cand, tgt, tol: min(abs(a - b) for a in cand for b in tgt) <= tol
    )
    c.min_delta = MagicMock(
        side_effect=lambda cand, tgt: min(abs(a - b) for a in cand for b in tgt)
    )
    return c


@pytest.fixture
def service(stub_counter: MagicMock) -> CustomLyricsService:
    s = CustomLyricsService(counter=stub_counter)
    return s


def _patch_gemini(returns: list[list[str]]):
    """Helper: patch service._call_gemini to return the next list each call."""
    iterator = iter(returns)
    return patch.object(
        CustomLyricsService,
        "_call_gemini",
        side_effect=lambda *args, **kwargs: next(iterator),
    )


def test_success_first_iteration(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb")]
    target_lines = ["aa", "bb"]
    with _patch_gemini([["xx", "yy"]]):
        result = service.generate(
            job_id="j1",
            target_lines=target_lines,
            target_segments=target_segments,
            artist=None,
            title=None,
            custom_text="custom",
            file_bytes=None,
            file_mime=None,
            file_name=None,
            notes=None,
            settings=GenerationSettings(),
        )
    assert result.lines == ["xx", "yy"]
    assert result.iterations_used == 0
    assert result.stop_reason is StopReason.SUCCESS
    assert all(v.passes for v in result.line_metadata)


def test_repairs_one_line(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb")]
    target_lines = ["aa", "bb"]
    # iteration 0: line 1 has too many words; iteration 1: fixed
    with _patch_gemini([
        ["xx yy zz qq", "yy"],  # 4 vs 1 → fail at tol=2
        ["xx", "yy"],
    ]):
        result = service.generate(
            job_id="j1",
            target_lines=target_lines,
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.BALANCED),
        )
    assert result.lines == ["xx", "yy"]
    assert result.iterations_used == 1
    assert result.stop_reason is StopReason.SUCCESS


def test_plateau_detection(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa")]
    target_lines = ["aa"]
    # Same bad output every iteration → plateau
    with _patch_gemini([
        ["xx yy zz qq pp"],
        ["xx yy zz qq pp"],
        ["xx yy zz qq pp"],
    ]):
        result = service.generate(
            job_id="j1",
            target_lines=target_lines,
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.BALANCED),
        )
    assert result.stop_reason is StopReason.PLATEAU
    assert result.iterations_used == 1  # one repair attempt before plateau


def test_max_iters_reached(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa")]
    target_lines = ["aa"]
    # Improving by 1 each iteration but never passing
    with _patch_gemini([
        ["a b c d e f g h"],   # 8 → delta 7
        ["a b c d e f g"],     # 7 → delta 6
        ["a b c d e f"],       # 6 → delta 5
        ["a b c d e"],         # 5 → delta 4
        ["a b c d"],           # 4 → delta 3
    ]):
        result = service.generate(
            job_id="j1",
            target_lines=target_lines,
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.STRICT),  # max_iter=4
        )
    assert result.stop_reason is StopReason.MAX_ITERS_REACHED
    assert result.iterations_used == 4


def test_verbatim_skips_loop(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb")]
    with _patch_gemini([["wildly wrong syllables here", "second"]]):
        result = service.generate(
            job_id="j1",
            target_lines=["aa", "bb"],
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.VERBATIM),
        )
    assert result.iterations_used == 0
    assert result.stop_reason is StopReason.VERBATIM_SKIP
    # metadata still populated
    assert len(result.line_metadata) == 2


def test_best_iteration_tracking(service: CustomLyricsService) -> None:
    """If iteration 2 regresses, return iteration 1's result."""
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb")]
    with _patch_gemini([
        ["a b c d", "yy"],     # iter 0: 1 violation, delta=3
        ["a", "yy"],           # iter 1: 0 violations
        ["a b c d e f", "yy"], # iter 2: 1 violation worse than before
    ]):
        result = service.generate(
            job_id="j1",
            target_lines=["aa", "bb"],
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.BALANCED),
        )
    assert result.lines == ["a", "yy"]


def test_variable_line_count_returns_new_timing(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb"), _segment(2.0, 3.0, "cc")]
    with _patch_gemini([["alpha beta gamma", "delta"]]):
        result = service.generate(
            job_id="j1",
            target_lines=["aa", "bb", "cc"],
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(fixed_line_count=False),
        )
    assert len(result.lines) == 2
    assert result.new_segment_timing is not None
    assert len(result.new_segment_timing) == 2
    assert result.line_count_mismatch is True


def test_existing_lines_empty_raises(service: CustomLyricsService) -> None:
    from backend.services.custom_lyrics.service import CustomLyricsServiceError
    with pytest.raises(CustomLyricsServiceError, match="must not be empty"):
        service.generate(
            job_id="j1",
            target_lines=[],
            target_segments=[],
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(),
        )
