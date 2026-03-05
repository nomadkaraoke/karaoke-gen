"""
Tests for the audio download worker.

Tests the standalone worker that downloads audio from various sources
(YouTube, Spotify, RED/OPS) and triggers processing workers.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, UTC

from backend.models.job import Job, JobStatus
from backend.workers.audio_download_worker import (
    process_audio_download,
    _extract_gcs_path,
    _download_audio,
)
from backend.services.audio_search_service import DownloadError


def _make_job(
    job_id: str = "test-job-123",
    status=JobStatus.DOWNLOADING_AUDIO,
    source_name: str = "YouTube",
    source_id: str = "dQw4w9WgXcQ",
    state_data: dict = None,
    **kwargs,
) -> Job:
    """Helper to create test Job objects."""
    return Job(
        job_id=job_id,
        artist="Test Artist",
        title="Test Song",
        status=status,
        source_name=source_name,
        source_id=source_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        state_data=state_data or {},
        **kwargs,
    )


class TestExtractGcsPath:
    """Tests for _extract_gcs_path helper."""

    def test_strips_primary_bucket_prefix(self):
        path = "gs://karaoke-gen-storage-nomadkaraoke/uploads/123/audio/song.flac"
        assert _extract_gcs_path(path) == "uploads/123/audio/song.flac"

    def test_strips_alternate_bucket_prefix(self):
        path = "gs://karaoke-gen-storage/uploads/123/audio/song.flac"
        assert _extract_gcs_path(path) == "uploads/123/audio/song.flac"

    def test_unknown_gs_bucket_returned_as_is(self):
        """Unknown bucket gs:// paths are returned unchanged (only known buckets stripped)."""
        path = "gs://other-bucket/uploads/123/audio/song.flac"
        # The function only strips known bucket prefixes
        assert _extract_gcs_path(path) == path

    def test_returns_plain_path_unchanged(self):
        path = "uploads/123/audio/song.flac"
        assert _extract_gcs_path(path) == "uploads/123/audio/song.flac"


class TestProcessAudioDownload:
    """Tests for the main process_audio_download function."""

    @pytest.mark.asyncio
    async def test_returns_false_when_job_not_found(self):
        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"):
            mock_jm = MagicMock()
            mock_jm.get_job.return_value = None
            mock_jm_cls.return_value = mock_jm

            result = await process_audio_download("nonexistent-job")
            assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_download_params(self):
        """Job in wrong state with no download params should abort."""
        job = _make_job(
            status=JobStatus.PENDING,
            source_name=None,
            source_id=None,
        )

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"):
            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm_cls.return_value = mock_jm

            result = await process_audio_download("test-job-123")
            assert result is False

    @pytest.mark.asyncio
    async def test_youtube_download_success(self):
        """Successful YouTube download triggers workers."""
        job = _make_job(source_name="YouTube", source_id="dQw4w9WgXcQ")

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory:

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.mp3", "song.mp3")

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            result = await process_audio_download("test-job-123")

            assert result is True
            mock_jm.update_job.assert_called_once_with("test-job-123", {
                'input_media_gcs_path': "uploads/test-job-123/audio/song.mp3",
                'filename': "song.mp3",
            })
            mock_jm.transition_to_state.assert_called_once()
            mock_ws.trigger_audio_worker.assert_awaited_once_with("test-job-123")
            mock_ws.trigger_lyrics_worker.assert_awaited_once_with("test-job-123")

    @pytest.mark.asyncio
    async def test_download_error_fails_job(self):
        """DownloadError should fail the job and return False."""
        job = _make_job()

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl:

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm_cls.return_value = mock_jm

            mock_dl.side_effect = DownloadError("Download timed out")

            result = await process_audio_download("test-job-123")

            assert result is False
            mock_jm.fail_job.assert_called_once()
            assert "Download timed out" in mock_jm.fail_job.call_args[0][1]

    @pytest.mark.asyncio
    async def test_uses_state_data_selection_when_available(self):
        """Should prefer state_data search results over job-level params."""
        job = _make_job(
            source_name="YouTube",
            source_id="fallback-id",
            state_data={
                'audio_search_results': [
                    {'provider': 'Spotify', 'source_id': 'spotify123', 'target_file': None, 'url': None},
                ],
                'selected_audio_index': 0,
            },
        )

        with patch("backend.workers.audio_download_worker.JobManager") as mock_jm_cls, \
             patch("backend.workers.audio_download_worker.StorageService"), \
             patch("backend.workers.audio_download_worker._download_audio", new_callable=AsyncMock) as mock_dl, \
             patch("backend.workers.audio_download_worker.get_worker_service") as mock_ws_factory:

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = job
            mock_jm.transition_to_state.return_value = True
            mock_jm_cls.return_value = mock_jm

            mock_dl.return_value = ("uploads/test-job-123/audio/song.flac", "song.flac")

            mock_ws = AsyncMock()
            mock_ws_factory.return_value = mock_ws

            await process_audio_download("test-job-123")

            # Should have called with Spotify params from state_data, not YouTube fallback
            call_kwargs = mock_dl.call_args[1]
            assert call_kwargs['source_name'] == 'Spotify'
            assert call_kwargs['source_id'] == 'spotify123'


class TestDownloadAudio:
    """Tests for _download_audio routing."""

    @pytest.mark.asyncio
    async def test_routes_youtube_download(self):
        mock_storage = MagicMock()

        with patch("backend.workers.audio_download_worker._download_youtube", new_callable=AsyncMock) as mock_yt:
            mock_yt.return_value = ("uploads/j/audio/song.mp3", "song.mp3")

            result = await _download_audio(
                job_id="j", source_name="YouTube", source_id="vid123",
                target_file=None, download_url=None, remote_search_id=None,
                selection_index=None, selected={}, storage_service=mock_storage,
            )

            assert result == ("uploads/j/audio/song.mp3", "song.mp3")
            mock_yt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_red_to_torrent(self):
        mock_storage = MagicMock()

        with patch("backend.workers.audio_download_worker._download_torrent", new_callable=AsyncMock) as mock_torrent:
            mock_torrent.return_value = ("uploads/j/audio/song.flac", "song.flac")

            await _download_audio(
                job_id="j", source_name="RED", source_id="123",
                target_file="track.flac", download_url=None, remote_search_id=None,
                selection_index=None, selected={}, storage_service=mock_storage,
            )

            mock_torrent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_spotify_download(self):
        mock_storage = MagicMock()

        with patch("backend.workers.audio_download_worker._download_spotify", new_callable=AsyncMock) as mock_sp:
            mock_sp.return_value = ("uploads/j/audio/song.ogg", "song.ogg")

            await _download_audio(
                job_id="j", source_name="Spotify", source_id="track123",
                target_file=None, download_url=None, remote_search_id=None,
                selection_index=None, selected={}, storage_service=mock_storage,
            )

            mock_sp.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_for_unsupported_source(self):
        mock_storage = MagicMock()

        with pytest.raises(DownloadError, match="Unsupported audio source"):
            await _download_audio(
                job_id="j", source_name="SoundCloud", source_id="123",
                target_file=None, download_url=None, remote_search_id=None,
                selection_index=None, selected={}, storage_service=mock_storage,
            )
