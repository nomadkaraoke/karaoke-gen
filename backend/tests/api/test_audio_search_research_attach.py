"""
Tests for the parked-job recovery endpoints on audio-search:

- POST /api/audio-search/{job_id}/research      — retry / edit-and-re-search
- POST /api/audio-search/{job_id}/provide-url   — attach a YouTube/yt-dlp URL
- POST /api/audio-search/{job_id}/attach-upload-url      — signed URL for upload
- POST /api/audio-search/{job_id}/attach-upload-complete — finalise an upload

These cover the "0 results / dead-end" bulk-mode scenario where a job is parked
in AWAITING_AUDIO_SELECTION with no usable sources and the user needs a way out.
"""
from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import Job, JobStatus
from backend.services.audio_search_service import AudioSearchResult, NoResultsError, AudioSearchError
from backend.services.auth_service import UserType, AuthResult
from backend.services.youtube_download_service import YouTubeDownloadError

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _auth_overrides():
    from backend.api.dependencies import require_auth, require_admin
    from backend.main import app

    async def mock_require_auth():
        return AuthResult(
            is_valid=True, user_type=UserType.STRIPE, remaining_uses=5,
            message="test", is_admin=False, user_email="buyer@example.com",
        )

    app.dependency_overrides[require_auth] = mock_require_auth
    app.dependency_overrides[require_admin] = mock_require_auth
    yield
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_admin, None)


def _parked_job(job_id="park-1", artist="Arctic Monkeys", title="The View From the Afternoon",
                status=JobStatus.AWAITING_AUDIO_SELECTION) -> Job:
    return Job(
        job_id=job_id, status=status, created_at=NOW, updated_at=NOW,
        user_email="buyer@example.com", artist=artist, title=title,
        audio_search_artist=artist, audio_search_title=title,
        state_data={"credits_charged": 1, "audio_search_results": []},
    )


def _result(idx=0, title="The View From the Afternoon", artist="Arctic Monkeys"):
    r = AudioSearchResult(
        index=idx, title=title, artist=artist, provider="RED",
        url="", duration=180, quality="FLAC", source_id=f"id{idx}",
    )
    r.raw_result = {"target_file": f"{title}.flac", "is_lossless": True, "seeders": 20}
    return r


@pytest.fixture
def mock_job_manager():
    jm = MagicMock()
    jm.get_job.return_value = _parked_job()
    jm.update_job.return_value = None
    jm.transition_to_state.return_value = True
    jm.start_job_processing = AsyncMock(return_value=None)
    return jm


# ---------------------------------------------------------------------------
# research (re-search)
# ---------------------------------------------------------------------------


class TestResearch:
    def _patched(self, jm, search_mock):
        return patch.multiple(
            "backend.api.routes.audio_search",
            job_manager=jm,
        ), patch(
            "backend.api.routes.audio_search.get_audio_search_service",
            return_value=search_mock,
        )

    def test_research_returns_results(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        search = MagicMock()
        search.search_async = AsyncMock(return_value=[_result(0), _result(1)])
        search.last_remote_search_id = "remote-xyz"

        p1, p2 = self._patched(mock_job_manager, search)
        with p1, p2:
            client = TestClient(app)
            resp = client.post("/api/audio-search/park-1/research", json={})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["results_count"] == 2
        assert len(data["results"]) == 2
        # results were cached back onto the job
        cached = [c for c in mock_job_manager.update_job.call_args_list
                  if "state_data.audio_search_results" in c.args[1]]
        assert cached, "expected results to be cached on the job"

    def test_research_empty_is_200_not_error(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        search = MagicMock()
        search.search_async = AsyncMock(side_effect=NoResultsError("nothing"))
        search.last_remote_search_id = None

        p1, p2 = self._patched(mock_job_manager, search)
        with p1, p2:
            client = TestClient(app)
            resp = client.post("/api/audio-search/park-1/research", json={})

        assert resp.status_code == 200, resp.text
        assert resp.json()["results_count"] == 0

    def test_research_applies_edited_terms(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        search = MagicMock()
        search.search_async = AsyncMock(return_value=[_result(0)])
        search.last_remote_search_id = None

        p1, p2 = self._patched(mock_job_manager, search)
        with p1, p2:
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/research",
                json={"artist": "Arctic Monkeys", "title": "The View from the Afternoon"},
            )

        assert resp.status_code == 200, resp.text
        # search ran with the edited (normalized) title
        search.search_async.assert_awaited_once()
        args = search.search_async.await_args.args
        assert args[0] == "Arctic Monkeys"
        assert args[1] == "The View from the Afternoon"
        # edit applied to the job (search terms + display metadata)
        applied = [c for c in mock_job_manager.update_job.call_args_list
                   if c.args[1].get("title") == "The View from the Afternoon"]
        assert applied, "expected edited title applied to job"

    def test_research_502_on_search_error(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        search = MagicMock()
        search.search_async = AsyncMock(side_effect=AudioSearchError("boom"))

        p1, p2 = self._patched(mock_job_manager, search)
        with p1, p2:
            client = TestClient(app)
            resp = client.post("/api/audio-search/park-1/research", json={})

        assert resp.status_code == 502

    def test_research_rejects_wrong_status(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        mock_job_manager.get_job.return_value = _parked_job(status=JobStatus.DOWNLOADING)
        search = MagicMock()
        search.search_async = AsyncMock(return_value=[])

        p1, p2 = self._patched(mock_job_manager, search)
        with p1, p2:
            client = TestClient(app)
            resp = client.post("/api/audio-search/park-1/research", json={})

        assert resp.status_code == 400
        search.search_async.assert_not_awaited()

    def test_research_404_missing_job(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        mock_job_manager.get_job.return_value = None
        search = MagicMock()
        p1, p2 = self._patched(mock_job_manager, search)
        with p1, p2:
            client = TestClient(app)
            resp = client.post("/api/audio-search/nope/research", json={})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# provide-url
# ---------------------------------------------------------------------------


class TestProvideUrl:
    def test_youtube_url_uses_async_worker(self, mock_job_manager):
        """YouTube URLs go through the async download worker (no blocking request)."""
        from fastapi.testclient import TestClient
        from backend.main import app

        yt = MagicMock()
        yt.check_availability = AsyncMock(return_value={"available": True})
        yt.download = AsyncMock()
        ws = MagicMock()
        ws.trigger_audio_download_worker = AsyncMock(return_value=True)

        with patch.multiple("backend.api.routes.audio_search", job_manager=mock_job_manager), \
                patch("backend.api.routes.audio_search.get_youtube_download_service", return_value=yt), \
                patch("backend.api.routes.audio_search.get_worker_service", return_value=ws):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/provide-url",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk"},
            )

        assert resp.status_code == 200, resp.text
        ws.trigger_audio_download_worker.assert_awaited_once_with("park-1")
        yt.download.assert_not_awaited()  # async worker does the download, not inline
        # job pointed at the URL source for the worker's job-level fallback
        srcd = [c for c in mock_job_manager.update_job.call_args_list
                if c.args[1].get("source_name") == "YouTube" and c.args[1].get("download_url")]
        assert srcd

    def test_youtube_worker_trigger_failure_requeues(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        yt = MagicMock()
        yt.check_availability = AsyncMock(return_value={"available": True})
        ws = MagicMock()
        ws.trigger_audio_download_worker = AsyncMock(return_value=False)

        with patch.multiple("backend.api.routes.audio_search", job_manager=mock_job_manager), \
                patch("backend.api.routes.audio_search.get_youtube_download_service", return_value=yt), \
                patch("backend.api.routes.audio_search.get_worker_service", return_value=ws):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/provide-url",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk"},
            )
        assert resp.status_code == 502
        statuses = [c.kwargs.get("new_status") for c in mock_job_manager.transition_to_state.call_args_list]
        assert JobStatus.AWAITING_AUDIO_SELECTION in statuses

    def test_generic_url_downloads_inline_and_starts(self, mock_job_manager):
        """Non-YouTube (yt-dlp) URLs download inline, then start processing."""
        from fastapi.testclient import TestClient
        from backend.main import app

        yt = MagicMock()
        yt.download = AsyncMock(return_value="uploads/park-1/audio/song.opus")

        with patch.multiple("backend.api.routes.audio_search", job_manager=mock_job_manager), \
                patch("backend.api.routes.audio_search.get_youtube_download_service", return_value=yt):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/provide-url",
                json={"url": "https://soundcloud.com/artist/track"},
            )

        assert resp.status_code == 200, resp.text
        yt.download.assert_awaited_once()
        pathed = [c for c in mock_job_manager.update_job.call_args_list
                  if c.args[1].get("input_media_gcs_path") == "uploads/park-1/audio/song.opus"]
        assert pathed
        mock_job_manager.start_job_processing.assert_awaited()

    def test_drm_url_rejected(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        yt = MagicMock()
        with patch.multiple("backend.api.routes.audio_search", job_manager=mock_job_manager), \
                patch("backend.api.routes.audio_search.get_youtube_download_service", return_value=yt):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/provide-url",
                json={"url": "https://open.spotify.com/track/xyz"},
            )
        assert resp.status_code == 400

    def test_unavailable_youtube_rejected(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        yt = MagicMock()
        yt.check_availability = AsyncMock(return_value={"available": False, "is_private": True})
        ws = MagicMock()
        ws.trigger_audio_download_worker = AsyncMock(return_value=True)

        with patch.multiple("backend.api.routes.audio_search", job_manager=mock_job_manager), \
                patch("backend.api.routes.audio_search.get_youtube_download_service", return_value=yt), \
                patch("backend.api.routes.audio_search.get_worker_service", return_value=ws):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/provide-url",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk"},
            )
        assert resp.status_code == 400
        ws.trigger_audio_download_worker.assert_not_awaited()

    def test_generic_download_failure_requeues_and_502(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        yt = MagicMock()
        yt.download = AsyncMock(side_effect=YouTubeDownloadError("dead"))

        with patch.multiple("backend.api.routes.audio_search", job_manager=mock_job_manager), \
                patch("backend.api.routes.audio_search.get_youtube_download_service", return_value=yt):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/provide-url",
                json={"url": "https://soundcloud.com/artist/track"},
            )
        assert resp.status_code == 502
        # restored to awaiting so the user can retry
        statuses = [c.kwargs.get("new_status") for c in mock_job_manager.transition_to_state.call_args_list]
        assert JobStatus.AWAITING_AUDIO_SELECTION in statuses

    def test_provide_url_rejects_wrong_status(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        mock_job_manager.get_job.return_value = _parked_job(status=JobStatus.DOWNLOADING)
        yt = MagicMock()
        with patch.multiple("backend.api.routes.audio_search", job_manager=mock_job_manager), \
                patch("backend.api.routes.audio_search.get_youtube_download_service", return_value=yt):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/provide-url",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk"},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# attach upload
# ---------------------------------------------------------------------------


class TestAttachUpload:
    def test_attach_upload_url_returns_signed_url(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        storage = MagicMock()
        storage.generate_signed_upload_url.return_value = "https://signed.example/put"

        with patch.multiple("backend.api.routes.audio_search",
                            job_manager=mock_job_manager, storage_service=storage):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/attach-upload-url",
                json={"filename": "my song.flac", "content_type": "audio/flac"},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["gcs_path"] == "uploads/park-1/audio/my song.flac"
        assert data["upload_url"] == "https://signed.example/put"

    def test_attach_upload_url_rejects_bad_extension(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        storage = MagicMock()
        storage.generate_signed_upload_url.return_value = "https://signed.example/put"

        with patch.multiple("backend.api.routes.audio_search",
                            job_manager=mock_job_manager, storage_service=storage):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/attach-upload-url",
                json={"filename": "..\\evil.exe", "content_type": "application/octet-stream"},
            )
        assert resp.status_code == 400
        storage.generate_signed_upload_url.assert_not_called()

    def test_attach_complete_starts_processing(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        storage = MagicMock()
        storage.list_files.return_value = ["uploads/park-1/audio/my_song.flac"]

        with patch.multiple("backend.api.routes.audio_search",
                            job_manager=mock_job_manager, storage_service=storage):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/attach-upload-complete",
                json={"gcs_path": "uploads/park-1/audio/my_song.flac"},
            )

        assert resp.status_code == 200, resp.text
        pathed = [c for c in mock_job_manager.update_job.call_args_list
                  if c.args[1].get("input_media_gcs_path") == "uploads/park-1/audio/my_song.flac"]
        assert pathed
        mock_job_manager.start_job_processing.assert_awaited()

    def test_attach_complete_rejects_gcs_path_outside_prefix(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        storage = MagicMock()
        storage.list_files.return_value = ["uploads/park-1/audio/my_song.flac"]

        with patch.multiple("backend.api.routes.audio_search",
                            job_manager=mock_job_manager, storage_service=storage):
            client = TestClient(app)
            resp = client.post(
                "/api/audio-search/park-1/attach-upload-complete",
                json={"gcs_path": "uploads/other-job/audio/steal.flac"},
            )
        assert resp.status_code == 400
        mock_job_manager.start_job_processing.assert_not_awaited()

    def test_attach_complete_400_when_no_file(self, mock_job_manager):
        from fastapi.testclient import TestClient
        from backend.main import app

        storage = MagicMock()
        storage.list_files.return_value = []

        with patch.multiple("backend.api.routes.audio_search",
                            job_manager=mock_job_manager, storage_service=storage):
            client = TestClient(app)
            resp = client.post("/api/audio-search/park-1/attach-upload-complete", json={})
        assert resp.status_code == 400
        mock_job_manager.start_job_processing.assert_not_awaited()
