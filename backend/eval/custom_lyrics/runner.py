"""Eval runner: load fixture → call service (cached) → score → return result."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.eval.custom_lyrics.cache import LlmCallCache
from backend.eval.custom_lyrics.scorer import FixtureScore, aggregate_fixture
from backend.services.custom_lyrics.service import CustomLyricsService
from backend.services.custom_lyrics.settings import (
    GenerationSettings,
    settings_from_dict,
)


@dataclass
class FixtureRunResult:
    fixture_id: str
    settings_name: str
    score: FixtureScore
    output_lines: list[str]
    line_metadata: list[dict[str, Any]]
    fixture_dir: Path


def load_fixture(fixture_dir: Path) -> dict:
    metadata = json.loads((fixture_dir / "metadata.json").read_text())
    metadata["original_lyrics"] = (fixture_dir / "original_lyrics.txt").read_text().strip().splitlines()
    metadata["original_segments"] = json.loads((fixture_dir / "original_segments.json").read_text())
    metadata["client_input"] = (fixture_dir / "client_input.txt").read_text()
    notes_path = fixture_dir / "notes.txt"
    metadata["notes"] = notes_path.read_text() if notes_path.exists() else None
    return metadata


def run_fixture(
    fixture_dir: Path,
    settings_name: str,
    settings_dict: dict[str, Any],
    *,
    cache: LlmCallCache,
    service: CustomLyricsService,
) -> FixtureRunResult:
    fixture = load_fixture(fixture_dir)
    settings = settings_from_dict(settings_dict)
    gemini_call_count = 0

    real_call = service._call_gemini

    def cached_call(*, system_prompt: str, user_prompt: str, pdf_bytes, settings: GenerationSettings):
        nonlocal gemini_call_count
        cached = cache.get(
            system_prompt, user_prompt,
            model=service.settings.custom_lyrics_model,
            settings_dict=settings.to_dict(),
        )
        if cached is not None:
            return cached
        gemini_call_count += 1
        result = real_call(
            system_prompt=system_prompt, user_prompt=user_prompt,
            pdf_bytes=pdf_bytes, settings=settings,
        )
        cache.set(
            system_prompt, user_prompt,
            model=service.settings.custom_lyrics_model,
            settings_dict=settings.to_dict(),
            response_lines=result,
        )
        return result

    with patch.object(service, "_call_gemini", side_effect=cached_call):
        result = service.generate(
            job_id=fixture["id"],
            target_lines=fixture["original_lyrics"],
            target_segments=fixture["original_segments"],
            artist=fixture.get("artist"),
            title=fixture.get("title"),
            custom_text=fixture["client_input"],
            file_bytes=None, file_mime=None, file_name=None,
            notes=fixture.get("notes"),
            settings=settings,
        )

    score = aggregate_fixture(
        fixture_id=fixture["id"],
        settings_name=settings_name,
        metadata=result.line_metadata,
        iterations_used=result.iterations_used,
        stop_reason=result.stop_reason.value,
        duration_ms=result.duration_ms,
        gemini_calls=gemini_call_count,
        line_count_match=not result.line_count_mismatch,
    )

    return FixtureRunResult(
        fixture_id=fixture["id"],
        settings_name=settings_name,
        score=score,
        output_lines=result.lines,
        line_metadata=[{
            "line_index": v.line_index,
            "target_text": v.target_text,
            "candidate_text": v.candidate_text,
            "min_delta": v.min_delta,
            "severity": v.severity.value,
        } for v in result.line_metadata],
        fixture_dir=fixture_dir,
    )
