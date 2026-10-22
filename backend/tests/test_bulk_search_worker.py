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


@pytest.mark.asyncio
@pytest.mark.parametrize("state_extra", [
    {"credits_charged": 0, "payment_bypassed": True},
    {"credits_charged": None},  # legacy, field wiped — resolved below via admin owner
])
async def test_auto_select_never_charges_bypassed_job(monkeypatch, patched, state_extra):
    """Bypassed (admin/tenant) jobs: no deduction and credits_charged stays 0, so a
    later cancel can't refund credits that were never debited."""
    jm, ass, transitions = patched
    job = _make_job(auto=True, state_extra=state_extra)
    if state_extra.get("credits_charged") is None:
        del job.state_data["credits_charged"]
        job.user_email = "admin@nomadkaraoke.com"
    us = MagicMock()
    monkeypatch.setattr(worker, "get_user_service", lambda: us, raising=False)
    monkeypatch.setattr("backend.api.routes.audio_search._validate_and_prepare_selection",
                        lambda job_id, idx: {"ok": True})

    ok = await worker._auto_select(job, 0, {"duration": 1500}, jm)

    assert ok is True
    us.deduct_credits.assert_not_called()
    update = jm.update_job.call_args_list[-1][0][1]
    assert update["state_data.credits_charged"] == 0
    assert update["state_data.payment_bypassed"] is True


# ---------------------------------------------------------------------------
# Album path: one flacfetch album search for an album-mode batch
# ---------------------------------------------------------------------------

ALBUM = {"artist": "Daft Punk", "title": "Discovery", "release_group_mbid": "rg-1"}


def _album_job(job_id, title, position):
    job = _make_job(job_id, state_extra={"bulk_album": ALBUM, "bulk_track_position": position})
    job.title = job.audio_search_title = title
    return job


def _torrent(source_id, track_files, seeders=31, media="CD", release_type="Album"):
    return {
        "title": "Discovery", "artist": "Daft Punk", "provider": "RED", "source_id": source_id,
        "quality": "FLAC 16bit CD", "quality_data": {"format": "FLAC", "bit_depth": 16, "media": media},
        "is_lossless": True, "seeders": seeders, "release_type": release_type,
        "matched_count": len(track_files),
        "track_files": {k: {"target_file": f, "target_file_size": 1, "match_score": 1.0}
                        for k, f in track_files.items()},
    }


class FakeAlbumClient:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def search_album(self, artist, album, tracks):
        self.calls.append((artist, album, tracks))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def album_env(monkeypatch, patched):
    jm, _ass, transitions = patched
    monkeypatch.setattr("backend.api.routes.audio_search._validate_and_prepare_selection",
                        lambda job_id, idx: {"ok": True})
    stored = {}
    jm.update_job.side_effect = lambda job_id, updates: stored.setdefault(job_id, {}).update(updates)

    def use(client):
        monkeypatch.setattr(worker, "_album_search_client", lambda: client)
        return client
    return jm, transitions, stored, use


@pytest.mark.asyncio
async def test_album_batch_makes_one_search_and_auto_selects_every_track(album_env):
    jm, transitions, stored, use = album_env
    jobs = [_album_job("j1", "One More Time", 1), _album_job("j2", "Aerodynamic", 2)]
    client = use(FakeAlbumClient({"results": [
        _torrent("cd-31", {"j1": "01 One More Time.flac", "j2": "02 Aerodynamic.flac"}, seeders=31),
        _torrent("web-15", {"j1": "1-01 One More Time.flac", "j2": "1-02 Aerodynamic.flac"},
                 seeders=15, media="WEB"),
    ]}))

    fallback = await worker.process_album_jobs(ALBUM, jobs, jm)

    assert fallback == []
    assert len(client.calls) == 1
    artist, album, tracks = client.calls[0]
    assert (artist, album) == ("Daft Punk", "Discovery")
    assert tracks == [{"key": "j1", "title": "One More Time", "position": 1},
                      {"key": "j2", "title": "Aerodynamic", "position": 2}]
    for job_id, expected in (("j1", "01 One More Time.flac"), ("j2", "02 Aerodynamic.flac")):
        results = stored[job_id]["state_data.audio_search_results"]
        # Batch torrent first, the other edition kept as an alternative.
        assert [r["source_id"] for r in results] == ["cd-31", "web-15"]
        assert results[0]["target_file"] == expected
        assert stored[job_id]["state_data.bulk_auto_selected"] is True
    assert JobStatus.AWAITING_AUDIO_SELECTION not in transitions


@pytest.mark.asyncio
async def test_uncovered_tracks_fall_back_to_per_track_search(album_env):
    jm, _t, stored, use = album_env
    jobs = [_album_job("j1", "One More Time", 1), _album_job("j2", "Bonus Thing", 15)]
    use(FakeAlbumClient({"results": [_torrent("cd", {"j1": "01 One More Time.flac"})]}))

    fallback = await worker.process_album_jobs(ALBUM, jobs, jm)

    assert [j.job_id for j in fallback] == ["j2"]
    assert "j2" not in stored


@pytest.mark.asyncio
async def test_album_search_failure_falls_back_for_all(album_env):
    from backend.services.flacfetch_client import FlacfetchServiceError
    jm, _t, stored, use = album_env
    jobs = [_album_job("j1", "One More Time", 1)]
    use(FakeAlbumClient(error=FlacfetchServiceError("timed out")))
    assert await worker.process_album_jobs(ALBUM, jobs, jm) == jobs
    assert stored == {}


@pytest.mark.asyncio
async def test_no_album_client_falls_back(album_env):
    jm, _t, _s, use = album_env
    use(None)
    jobs = [_album_job("j1", "One More Time", 1)]
    assert await worker.process_album_jobs(ALBUM, jobs, jm) == jobs


@pytest.mark.asyncio
async def test_poorly_seeded_album_parks_with_album_options(album_env):
    jm, transitions, stored, use = album_env
    use(FakeAlbumClient({"results": [_torrent("cd-5", {"j1": "01 One More Time.flac"}, seeders=5)]}))
    await worker.process_album_jobs(ALBUM, [_album_job("j1", "One More Time", 1)], jm)
    assert JobStatus.AWAITING_AUDIO_SELECTION in transitions
    assert stored["j1"]["state_data.audio_search_results"][0]["source_id"] == "cd-5"
    assert stored["j1"]["state_data.bulk_pending_search"] is False


@pytest.mark.asyncio
async def test_auto_select_disabled_parks_album_jobs(album_env):
    jm, transitions, _s, use = album_env
    job = _album_job("j1", "One More Time", 1)
    job.state_data["bulk_settings"]["auto_select_if_lossless"] = False
    use(FakeAlbumClient({"results": [_torrent("cd", {"j1": "01 One More Time.flac"})]}))
    await worker.process_album_jobs(ALBUM, [job], jm)
    assert JobStatus.AWAITING_AUDIO_SELECTION in transitions


@pytest.mark.asyncio
async def test_process_batch_routes_album_jobs_then_searches_rest_one_at_a_time(monkeypatch):
    album_jobs = [_album_job("a1", "One More Time", 1), _album_job("a2", "Bonus Thing", 15)]
    text_job = _make_job("t1")
    jm = MagicMock()
    jm.list_jobs_by_batch_id.return_value = album_jobs + [text_job]
    monkeypatch.setattr(worker, "JobManager", lambda: jm)

    seen_album = []

    async def fake_album(album, jobs, job_manager):
        seen_album.append([j.job_id for j in jobs])
        return [j for j in jobs if j.job_id == "a2"]  # uncovered track

    in_flight = max_in_flight = 0
    searched = []

    async def fake_process_job(job_id, job_manager, ass):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        import asyncio
        await asyncio.sleep(0.01)
        searched.append(job_id)
        in_flight -= 1

    monkeypatch.setattr(worker, "process_album_jobs", fake_album)
    monkeypatch.setattr(worker, "process_job", fake_process_job)
    monkeypatch.setattr(worker, "AudioSearchService", MagicMock)

    count = await worker.process_batch("b1")

    assert count == 3
    assert seen_album == [["a1", "a2"]]
    assert sorted(searched) == ["a2", "t1"]
    assert max_in_flight == 1  # never more than one flacfetch search at a time


def test_search_concurrency_is_one():
    assert worker.SEARCH_CONCURRENCY == 1


@pytest.mark.asyncio
async def test_jobs_past_the_batch_deadline_are_parked_not_searched(monkeypatch):
    jobs = [_make_job("t1"), _make_job("t2")]
    jm = MagicMock()
    jm.list_jobs_by_batch_id.return_value = jobs
    parked = []
    jm.transition_to_state.side_effect = lambda **kw: parked.append((kw["job_id"], kw["new_status"]))
    monkeypatch.setattr(worker, "JobManager", lambda: jm)
    monkeypatch.setattr(worker, "BATCH_SEARCH_DEADLINE_SECONDS", -1)  # already past
    searched = []

    async def fake_process_job(job_id, job_manager, ass):
        searched.append(job_id)

    monkeypatch.setattr(worker, "process_job", fake_process_job)
    monkeypatch.setattr(worker, "AudioSearchService", MagicMock)

    await worker.process_batch("b1")

    assert searched == []
    assert sorted(parked) == [("t1", JobStatus.AWAITING_AUDIO_SELECTION), ("t2", JobStatus.AWAITING_AUDIO_SELECTION)]
