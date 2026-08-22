"""Tests for the Bulk Mode search worker: auto-select vs park branching."""

from unittest.mock import MagicMock

import pytest

import backend.workers.bulk_search_worker as worker
from backend.models.job import JobStatus
from backend.services.audio_search_service import NoResultsError


class FakeResult:
    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return self._d


def _lossless_best(index=0, seeders=80, duration=240):
    return {
        "index": index, "title": "One More Time", "artist": "Daft Punk",
        "provider": "RED", "is_lossless": True, "seeders": seeders,
        "release_type": "album", "target_file": "01 - One More Time.flac",
        "duration": duration, "source_id": "abc",
        "quality_data": {"format": "FLAC", "bit_depth": 16, "media": "CD"},
    }


def _lossy(index=0):
    return {
        "index": index, "title": "One More Time", "artist": "Daft Punk",
        "provider": "YouTube", "is_lossless": False, "seeders": None,
        "release_type": "", "duration": 240, "quality_data": {"format": "OPUS"},
    }


def _make_job(job_id="job1", auto=True, state_extra=None):
    job = MagicMock()
    job.job_id = job_id
    job.artist = "Daft Punk"
    job.title = "One More Time"
    job.audio_search_artist = "Daft Punk"
    job.audio_search_title = "One More Time"
    job.theme_id = None  # skip theme prep
    job.user_email = "t@example.com"
    job.state_data = {"batch_id": "b1", "bulk_settings": {"auto_select_if_lossless": auto},
                      "bulk_pending_search": True, "credits_charged": 1}
    if state_extra:
        job.state_data.update(state_extra)
    return job


@pytest.fixture
def patched(monkeypatch):
    jm = MagicMock()
    transitions = []
    jm.transition_to_state.side_effect = lambda **kw: transitions.append(kw["new_status"]) or True
    ass = MagicMock()
    ass.last_remote_search_id = "remote-1"
    # default no theme prep import path needed (theme_id None)
    monkeypatch.setattr(worker, "get_worker_service", lambda: _async_worker_service())
    return jm, ass, transitions


def _async_worker_service():
    ws = MagicMock()
    async def _dl(job_id):
        return True
    ws.trigger_audio_download_worker = _dl
    return ws


@pytest.mark.asyncio
async def test_auto_selects_confident_lossless(monkeypatch, patched):
    jm, ass, transitions = patched
    job = _make_job(auto=True)
    jm.get_job.return_value = job

    async def _search(artist, title):
        return [FakeResult(_lossy(0)), FakeResult(_lossless_best(1))]
    ass.search_async = _search

    # No duration delta owed (240s -> 1 credit, already charged 1)
    monkeypatch.setattr(worker, "_validate_and_prepare_selection_imported", None, raising=False)
    monkeypatch.setattr("backend.api.routes.audio_search._validate_and_prepare_selection",
                        lambda job_id, idx: {"ok": True})

    await worker.process_job("job1", jm, ass)

    # Auto path: should NOT end in AWAITING_AUDIO_SELECTION; SEARCHING_AUDIO only
    assert JobStatus.AWAITING_AUDIO_SELECTION not in transitions
    # bulk_auto_selected True recorded
    final = jm.update_job.call_args[0][1]
    assert final["state_data.bulk_auto_selected"] is True
    assert final["state_data.bulk_pending_search"] is False


@pytest.mark.asyncio
async def test_parks_when_only_lossy(monkeypatch, patched):
    jm, ass, transitions = patched
    jm.get_job.return_value = _make_job(auto=True)

    async def _search(artist, title):
        return [FakeResult(_lossy(0))]
    ass.search_async = _search

    await worker.process_job("job1", jm, ass)
    assert JobStatus.AWAITING_AUDIO_SELECTION in transitions
    final = jm.update_job.call_args[0][1]
    assert final["state_data.bulk_pending_search"] is False


@pytest.mark.asyncio
async def test_parks_when_auto_disabled_even_if_lossless(monkeypatch, patched):
    jm, ass, transitions = patched
    jm.get_job.return_value = _make_job(auto=False)

    async def _search(artist, title):
        return [FakeResult(_lossless_best(0))]
    ass.search_async = _search

    await worker.process_job("job1", jm, ass)
    assert JobStatus.AWAITING_AUDIO_SELECTION in transitions


@pytest.mark.asyncio
async def test_no_results_parks(monkeypatch, patched):
    jm, ass, transitions = patched
    jm.get_job.return_value = _make_job(auto=True)

    async def _search(artist, title):
        raise NoResultsError("nope")
    ass.search_async = _search

    await worker.process_job("job1", jm, ass)
    assert JobStatus.AWAITING_AUDIO_SELECTION in transitions


@pytest.mark.asyncio
async def test_auto_select_charges_duration_delta(monkeypatch, patched):
    jm, ass, transitions = patched
    # 1300s -> ceil(1300/600)=3 credits; already charged 1 -> owed 2
    job = _make_job(auto=True)
    jm.get_job.return_value = job

    async def _search(artist, title):
        return [FakeResult(_lossless_best(0, duration=1300))]
    ass.search_async = _search

    us = MagicMock()
    us.deduct_credits.return_value = (True, 0, "ok")
    monkeypatch.setattr(worker, "get_user_service", lambda: us)
    monkeypatch.setattr("backend.api.routes.audio_search._validate_and_prepare_selection",
                        lambda job_id, idx: {"ok": True})

    await worker.process_job("job1", jm, ass)

    us.deduct_credits.assert_called_once()
    _, kwargs = us.deduct_credits.call_args
    assert kwargs["amount"] == 2  # the delta


@pytest.mark.asyncio
async def test_auto_select_parks_when_delta_unaffordable(monkeypatch, patched):
    jm, ass, transitions = patched
    jm.get_job.return_value = _make_job(auto=True)

    async def _search(artist, title):
        return [FakeResult(_lossless_best(0, duration=1300))]
    ass.search_async = _search

    us = MagicMock()
    us.deduct_credits.return_value = (False, 0, "insufficient")
    monkeypatch.setattr(worker, "get_user_service", lambda: us)

    await worker.process_job("job1", jm, ass)
    assert JobStatus.AWAITING_AUDIO_SELECTION in transitions
