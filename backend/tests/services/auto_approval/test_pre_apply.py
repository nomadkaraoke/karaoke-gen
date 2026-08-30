"""Tests for the server-side pre-apply (workstream C, C2).

``ensure_and_pre_apply`` applies the cached AI suggestions BEFORE the review-ready
notification and writes ``corrections_updated.json`` with a ``pre_applied`` marker.
It is best-effort: any miss leaves corrections_updated absent so the review UI falls
back to its client-side on-load auto-apply.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from backend.services.auto_approval.pre_apply import ensure_and_pre_apply


def _corrections() -> Dict[str, Any]:
    words = [
        {"id": f"w{i}", "text": t, "start_time": i * 0.5, "end_time": i * 0.5 + 0.4}
        for i, t in enumerate(["an", "amateur", "here"])
    ]
    return {
        "corrected_segments": [{
            "id": "s0",
            "text": "an amateur here",
            "words": words,
            "start_time": 0.0,
            "end_time": 2.0,
        }],
        "reference_lyrics": {"spotify": {"segments": []}},
        "metadata": {"total_words": 3},
    }


def _replace_suggestion() -> Dict[str, Any]:
    # Replace "an amateur" -> "not much" (op=replace on the first two words).
    return {
        "id": "sug1",
        "op": "replace",
        "segment_ids": ["s0"],
        "word_ids": ["w0", "w1"],
        "original_text": "an amateur",
        "new_text": "not much",
        "category": "mishearing",
        "confidence": 0.95,
        "models": ["m1"],
        "consensus": 1,
        "total_models": 1,
    }


class _FakeStorage:
    def __init__(self, files: Dict[str, Any]):
        self._files = dict(files)
        self.uploads: Dict[str, Any] = {}

    def file_exists(self, path: str) -> bool:
        return path in self._files

    def download_json(self, path: str):
        return self._files[path]

    def list_files(self, prefix: str) -> List[str]:
        return [p for p in self._files if p.startswith(prefix)]

    def upload_json(self, path: str, data: Any):
        self.uploads[path] = data
        self._files[path] = data


async def _run(storage, *, proactive=True, generate_on_miss=True):
    settings = MagicMock(auto_correct_proactive_enabled=proactive)
    jm = MagicMock()
    generated = {"called": False}

    async def _fake_generate(job_id):
        generated["called"] = True
        return {"status": "generated"}

    with patch("backend.config.get_settings", return_value=settings), \
         patch("backend.services.storage_service.StorageService", return_value=storage), \
         patch("backend.services.job_manager.JobManager", return_value=jm), \
         patch("backend.workers.auto_correct_worker.process_proactive_auto_correct", _fake_generate):
        result = await ensure_and_pre_apply("j1", generate_on_miss=generate_on_miss)
        return result, jm, generated


CORR = "jobs/j1/lyrics/corrections.json"
UPD = "jobs/j1/lyrics/corrections_updated.json"
CACHE = "jobs/j1/lyrics/auto_correct_cache/x.json"


@pytest.mark.asyncio
async def test_pre_apply_writes_marker_and_applies():
    storage = _FakeStorage({
        CORR: _corrections(),
        CACHE: {"suggestions": [_replace_suggestion()]},
    })
    result, jm, _gen = await _run(storage)
    assert result["outcome"] == "pre_applied"
    assert result["applied"] == 1
    written = storage.uploads[UPD]
    aa = written["metadata"]["auto_approval"]
    assert aa["pre_applied"] is True
    assert aa["applied_suggestion_ids"] == ["sug1"]
    assert aa["suggestions"]  # carried for the UI panel
    # The correction was actually applied to the segments.
    assert "not much" in written["corrected_segments"][0]["text"]
    jm.update_file_url.assert_called_once()


@pytest.mark.asyncio
async def test_pre_apply_empty_suggestions_still_marks():
    # Known-empty cache: mark pre_applied so the UI doesn't run client-side.
    storage = _FakeStorage({CORR: _corrections(), CACHE: {"suggestions": []}})
    result, _, _gen = await _run(storage)
    assert result["outcome"] == "pre_applied"
    assert result["applied"] == 0
    assert storage.uploads[UPD]["metadata"]["auto_approval"]["pre_applied"] is True


@pytest.mark.asyncio
async def test_pre_apply_no_cache_and_proactive_disabled_is_noop():
    # No cache + proactive off -> unknown suggestion set -> do NOT mark (fall back).
    storage = _FakeStorage({CORR: _corrections()})
    result, _, _gen = await _run(storage, proactive=False)
    assert result["outcome"] == "skipped"
    assert result["reason"] == "no_suggestions_cache"
    assert UPD not in storage.uploads


@pytest.mark.asyncio
async def test_pre_apply_skips_when_already_applied():
    storage = _FakeStorage({
        CORR: _corrections(),
        UPD: {"metadata": {"auto_approval": {"auto_approved": True}}},
        CACHE: {"suggestions": [_replace_suggestion()]},
    })
    result, _, _gen = await _run(storage)
    assert result["outcome"] == "skipped"
    assert result["reason"] == "already_applied"


@pytest.mark.asyncio
async def test_pre_apply_no_corrections_is_noop():
    storage = _FakeStorage({})
    result, _, _gen = await _run(storage)
    assert result["outcome"] == "skipped"
    assert result["reason"] == "no_corrections"


@pytest.mark.asyncio
async def test_load_path_applies_existing_cache_without_generating():
    # generate_on_miss=False (the /correction-data load path): an existing cache is
    # applied, but generation is never triggered (page must not block on an LLM call).
    storage = _FakeStorage({
        CORR: _corrections(),
        CACHE: {"suggestions": [_replace_suggestion()]},
    })
    result, jm, gen = await _run(storage, generate_on_miss=False)
    assert result["outcome"] == "pre_applied"
    assert gen["called"] is False
    assert storage.uploads[UPD]["metadata"]["auto_approval"]["pre_applied"] is True


@pytest.mark.asyncio
async def test_load_path_cache_miss_does_not_generate_or_mark():
    # No cache + proactive ON but generate_on_miss=False -> no generation, no marker;
    # the UI falls back to its client-side apply (no page hang).
    storage = _FakeStorage({CORR: _corrections()})
    result, _, gen = await _run(storage, proactive=True, generate_on_miss=False)
    assert gen["called"] is False
    assert result["outcome"] == "skipped"
    assert result["reason"] == "no_suggestions_cache"
    assert UPD not in storage.uploads


@pytest.mark.asyncio
async def test_screens_path_generates_on_cache_miss():
    # Default generate_on_miss=True (screens_worker path): generation IS triggered
    # on a cache miss.
    storage = _FakeStorage({CORR: _corrections()})
    result, _, gen = await _run(storage, proactive=True, generate_on_miss=True)
    assert gen["called"] is True
