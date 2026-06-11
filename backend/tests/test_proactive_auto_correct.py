"""Tests for proactive auto-correct generation in the lyrics worker.

The helper must be best-effort: gated by a flag, never raise, never block, and
use the multi-model default settings that line up with the review UI's request.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.workers.lyrics_worker import _run_proactive_auto_correct


CORRECTIONS = {
    "corrected_segments": [
        {"id": "seg-1", "words": [{"id": "w0", "text": "glory"}]},
    ],
    "reference_lyrics": {"genius": {"segments": [{"text": "chlorine"}]}},
}


def _storage(corrections=CORRECTIONS, exists=True):
    storage = MagicMock()
    storage.file_exists.return_value = exists
    storage.download_json.return_value = corrections
    return storage


def _settings(enabled: bool):
    return SimpleNamespace(auto_correct_proactive_enabled=enabled)


@pytest.mark.asyncio
async def test_skips_when_flag_disabled() -> None:
    storage = _storage()
    job = SimpleNamespace(artist="A", title="T")
    with patch("backend.config.get_settings", return_value=_settings(False)), \
         patch("backend.services.auto_correct.get_auto_correct_service") as svc:
        await _run_proactive_auto_correct("job-1", job, storage, MagicMock())
    svc.assert_not_called()
    storage.file_exists.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_no_references() -> None:
    storage = _storage(corrections={"corrected_segments": [{"id": "s", "words": []}],
                                     "reference_lyrics": {}})
    job = SimpleNamespace(artist="A", title="T")
    with patch("backend.config.get_settings", return_value=_settings(True)), \
         patch("backend.services.auto_correct.get_auto_correct_service") as svc:
        await _run_proactive_auto_correct("job-1", job, storage, MagicMock())
    svc.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_calls_suggest_multi_model() -> None:
    storage = _storage()
    job = SimpleNamespace(artist="A", title="T")
    service = MagicMock()
    service.suggest.return_value = SimpleNamespace(
        suggestions=[1, 2], model="claude-fable-5, gemini-3.1-pro-preview",
        elapsed_seconds=12.3,
    )
    with patch("backend.config.get_settings", return_value=_settings(True)), \
         patch("backend.services.auto_correct.get_auto_correct_service", return_value=service):
        await _run_proactive_auto_correct("job-1", job, storage, MagicMock())
    service.suggest.assert_called_once()
    kwargs = service.suggest.call_args.kwargs
    assert kwargs["job_id"] == "job-1"
    assert kwargs["settings"].compare_models is True
    assert kwargs["artist"] == "A"
    assert kwargs["segments"] == CORRECTIONS["corrected_segments"]


@pytest.mark.asyncio
async def test_swallows_service_errors() -> None:
    storage = _storage()
    job = SimpleNamespace(artist="A", title="T")
    service = MagicMock()
    service.suggest.side_effect = RuntimeError("anthropic down / out of credits")
    job_log = MagicMock()
    with patch("backend.config.get_settings", return_value=_settings(True)), \
         patch("backend.services.auto_correct.get_auto_correct_service", return_value=service):
        # Must NOT raise — the karaoke job proceeds regardless.
        await _run_proactive_auto_correct("job-1", job, storage, job_log)
    job_log.warning.assert_called()  # logged, not raised


@pytest.mark.asyncio
async def test_swallows_storage_errors() -> None:
    storage = MagicMock()
    storage.file_exists.side_effect = RuntimeError("gcs blip")
    job = SimpleNamespace(artist="A", title="T")
    with patch("backend.config.get_settings", return_value=_settings(True)):
        await _run_proactive_auto_correct("job-1", job, storage, MagicMock())  # no raise
