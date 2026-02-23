"""
Tests for audio_transcoding_service.py - Review audio transcoding to OGG Opus.

These tests mock StorageService and subprocess to verify:
- Cache path derivation for various GCS paths
- Cache hit path (no ffmpeg call)
- Cache miss with successful transcode
- Fallback to FLAC on transcoding failure
- Bulk prepare_review_audio_for_job transcodes all expected files
"""

import pytest
from unittest.mock import Mock, patch, call, ANY


class TestGetCachePath:
    """Test _get_cache_path() derivation logic."""

    def _make_service(self):
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        return AudioTranscodingService(storage_service=mock_storage)

    def test_input_audio_path(self):
        service = self._make_service()
        result = service._get_cache_path("jobs/abc123/input/song.flac")
        assert result == "jobs/abc123/review-audio/song.ogg"

    def test_stem_path(self):
        service = self._make_service()
        result = service._get_cache_path("jobs/abc123/stems/instrumental_clean.flac")
        assert result == "jobs/abc123/review-audio/instrumental_clean.ogg"

    def test_backing_vocals_path(self):
        service = self._make_service()
        result = service._get_cache_path("jobs/abc123/stems/backing_vocals.flac")
        assert result == "jobs/abc123/review-audio/backing_vocals.ogg"

    def test_nested_job_path(self):
        service = self._make_service()
        result = service._get_cache_path("jobs/4bed6f80/stems/instrumental_with_backing.flac")
        assert result == "jobs/4bed6f80/review-audio/instrumental_with_backing.ogg"

    def test_wav_extension(self):
        service = self._make_service()
        result = service._get_cache_path("jobs/abc123/input/song.wav")
        assert result == "jobs/abc123/review-audio/song.ogg"

    def test_no_jobs_prefix_fallback_uses_hash(self):
        service = self._make_service()
        result = service._get_cache_path("other/path/audio.flac")
        # Should use a hash-based fallback, not a literal "_unknown"
        assert result.startswith("jobs/_")
        assert result.endswith("/review-audio/audio.ogg")
        assert len(result.split("/")[1]) == 13  # "_" + 12-char hash

    def test_no_jobs_prefix_different_paths_different_hashes(self):
        service = self._make_service()
        result1 = service._get_cache_path("other/path/audio.flac")
        result2 = service._get_cache_path("another/path/audio.flac")
        # Different source paths should produce different cache paths
        assert result1 != result2


class TestTranscodeIfNeeded:
    """Test transcode_if_needed() cache hit/miss logic."""

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_cache_hit_skips_transcode(self, mock_subprocess):
        """When cached file exists, return it without transcoding."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        service = AudioTranscodingService(storage_service=mock_storage)

        result = service.transcode_if_needed("jobs/abc/input/song.flac")

        assert result == "jobs/abc/review-audio/song.ogg"
        mock_storage.file_exists.assert_called_once_with("jobs/abc/review-audio/song.ogg")
        mock_subprocess.run.assert_not_called()
        mock_storage.download_file.assert_not_called()

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_cache_miss_transcodes_and_uploads(self, mock_subprocess):
        """When cached file is missing, download + transcode + upload."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = False
        mock_subprocess.run.return_value = Mock(returncode=0, stderr="")
        service = AudioTranscodingService(storage_service=mock_storage)

        result = service.transcode_if_needed("jobs/abc/input/song.flac")

        assert result == "jobs/abc/review-audio/song.ogg"

        # Verify download from correct source path
        download_args = mock_storage.download_file.call_args[0]
        assert download_args[0] == "jobs/abc/input/song.flac"

        # Verify ffmpeg command
        mock_subprocess.run.assert_called_once()
        ffmpeg_call = mock_subprocess.run.call_args
        ffmpeg_cmd = ffmpeg_call[0][0]
        assert "ffmpeg" in ffmpeg_cmd[0]
        assert "libopus" in ffmpeg_cmd
        assert "128k" in ffmpeg_cmd
        assert "-vn" in ffmpeg_cmd
        # Verify timeout is set
        assert ffmpeg_call[1]["timeout"] == 120

        # Verify upload to correct cache path
        upload_args = mock_storage.upload_file.call_args[0]
        assert upload_args[1] == "jobs/abc/review-audio/song.ogg"


class TestGetReviewAudioUrl:
    """Test get_review_audio_url() with fallback behavior."""

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_returns_transcoded_url_on_success(self, mock_subprocess):
        """On success, return signed URL for transcoded file."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        mock_storage.generate_signed_url.return_value = "https://signed-url/song.ogg"
        service = AudioTranscodingService(storage_service=mock_storage)

        result = service.get_review_audio_url("jobs/abc/input/song.flac")

        assert result == "https://signed-url/song.ogg"
        mock_storage.generate_signed_url.assert_called_once_with(
            "jobs/abc/review-audio/song.ogg", 120
        )

    def test_falls_back_to_flac_on_error(self):
        """On transcoding error with no cache, fall back to original FLAC signed URL."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = False
        mock_storage.download_file.side_effect = Exception("GCS download failed")
        mock_storage.generate_signed_url.return_value = "https://signed-url/song.flac"
        service = AudioTranscodingService(storage_service=mock_storage)

        result = service.get_review_audio_url("jobs/abc/input/song.flac")

        assert result == "https://signed-url/song.flac"
        mock_storage.generate_signed_url.assert_called_once_with(
            "jobs/abc/input/song.flac", 120
        )
        # file_exists called twice: once in transcode_if_needed (cache miss),
        # once in fallback (cache still missing → fall through to FLAC)
        assert mock_storage.file_exists.call_count == 2

    def test_serves_cache_when_source_deleted(self):
        """When source is deleted but OGG cache exists, serve the cache."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        # First file_exists (in transcode_if_needed): cache miss
        # download_file fails: source deleted
        # Second file_exists (in fallback): cache found
        mock_storage.file_exists.side_effect = [False, True]
        mock_storage.download_file.side_effect = Exception("blob not found")
        mock_storage.generate_signed_url.return_value = "https://signed-url/song.ogg"
        service = AudioTranscodingService(storage_service=mock_storage)

        result = service.get_review_audio_url("jobs/abc/input/song.flac")

        assert result == "https://signed-url/song.ogg"
        mock_storage.generate_signed_url.assert_called_once_with(
            "jobs/abc/review-audio/song.ogg", 120
        )

    def test_falls_back_to_flac_when_cache_check_also_fails(self):
        """When source is deleted and file_exists() also throws, fall back to FLAC."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        # First file_exists (in transcode_if_needed): cache miss
        # download_file fails: source deleted
        # Second file_exists (in fallback): also throws
        mock_storage.file_exists.side_effect = [False, Exception("GCS unavailable")]
        mock_storage.download_file.side_effect = Exception("blob not found")
        mock_storage.generate_signed_url.return_value = "https://signed-url/song.flac"
        service = AudioTranscodingService(storage_service=mock_storage)

        result = service.get_review_audio_url("jobs/abc/input/song.flac")

        assert result == "https://signed-url/song.flac"
        mock_storage.generate_signed_url.assert_called_once_with(
            "jobs/abc/input/song.flac", 120
        )

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_custom_expiration_minutes(self, mock_subprocess):
        """Custom expiration_minutes is passed through to generate_signed_url."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        mock_storage.generate_signed_url.return_value = "https://signed-url/song.ogg"
        service = AudioTranscodingService(storage_service=mock_storage)

        service.get_review_audio_url("jobs/abc/input/song.flac", expiration_minutes=60)

        mock_storage.generate_signed_url.assert_called_once_with(
            "jobs/abc/review-audio/song.ogg", 60
        )

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_falls_back_on_ffmpeg_failure(self, mock_subprocess):
        """On ffmpeg failure, fall back to original FLAC signed URL."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = False
        mock_subprocess.run.return_value = Mock(returncode=1, stderr="codec error")
        service = AudioTranscodingService(storage_service=mock_storage)

        result = service.get_review_audio_url("jobs/abc/input/song.flac")

        # Should fall back to FLAC URL
        mock_storage.generate_signed_url.assert_called_once_with(
            "jobs/abc/input/song.flac", 120
        )


class TestPrepareReviewAudioForJob:
    """Test prepare_review_audio_for_job() bulk transcoding."""

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_transcodes_all_expected_files(self, mock_subprocess):
        """Should transcode input + all stems and return correct cache paths."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True  # All cache hits
        service = AudioTranscodingService(storage_service=mock_storage)

        mock_job = Mock()
        mock_job.job_id = "abc123"
        mock_job.input_media_gcs_path = "jobs/abc123/input/song.flac"
        mock_job.file_urls = {
            "stems": {
                "instrumental_clean": "jobs/abc123/stems/instrumental_clean.flac",
                "instrumental_with_backing": "jobs/abc123/stems/instrumental_with_backing.flac",
                "backing_vocals": "jobs/abc123/stems/backing_vocals.flac",
            }
        }

        result = service.prepare_review_audio_for_job(mock_job)

        assert len(result) == 4
        assert "jobs/abc123/review-audio/song.ogg" in result
        assert "jobs/abc123/review-audio/instrumental_clean.ogg" in result
        assert "jobs/abc123/review-audio/instrumental_with_backing.ogg" in result
        assert "jobs/abc123/review-audio/backing_vocals.ogg" in result
        assert mock_storage.file_exists.call_count == 4
        # No ffmpeg calls since all are cache hits
        mock_subprocess.run.assert_not_called()

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_continues_on_individual_failure(self, mock_subprocess):
        """If one file fails to transcode, others should still succeed."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        # First call (input): cache miss + download fails
        # Subsequent calls: cache hits
        mock_storage.file_exists.side_effect = [False, True, True, True]
        mock_storage.download_file.side_effect = Exception("download failed")
        service = AudioTranscodingService(storage_service=mock_storage)

        mock_job = Mock()
        mock_job.job_id = "abc123"
        mock_job.input_media_gcs_path = "jobs/abc123/input/song.flac"
        mock_job.file_urls = {
            "stems": {
                "instrumental_clean": "jobs/abc123/stems/instrumental_clean.flac",
                "instrumental_with_backing": "jobs/abc123/stems/instrumental_with_backing.flac",
                "backing_vocals": "jobs/abc123/stems/backing_vocals.flac",
            }
        }

        result = service.prepare_review_audio_for_job(mock_job)

        # 3 out of 4 succeeded (the 3 cache hits)
        assert len(result) == 3

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_handles_missing_stems(self, mock_subprocess):
        """Should handle jobs with missing stem paths gracefully."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        service = AudioTranscodingService(storage_service=mock_storage)

        mock_job = Mock()
        mock_job.job_id = "abc123"
        mock_job.input_media_gcs_path = "jobs/abc123/input/song.flac"
        mock_job.file_urls = {"stems": {}}  # No stems

        result = service.prepare_review_audio_for_job(mock_job)

        # Only the input audio
        assert len(result) == 1

    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_handles_no_input_audio(self, mock_subprocess):
        """Should handle jobs with no input_media_gcs_path."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        service = AudioTranscodingService(storage_service=mock_storage)

        mock_job = Mock()
        mock_job.job_id = "abc123"
        mock_job.input_media_gcs_path = None
        mock_job.file_urls = {
            "stems": {
                "instrumental_clean": "jobs/abc123/stems/instrumental_clean.flac",
            }
        }

        result = service.prepare_review_audio_for_job(mock_job)

        assert len(result) == 1


    @patch("backend.services.audio_transcoding_service.subprocess")
    def test_handles_no_stems_key(self, mock_subprocess):
        """Should handle jobs where file_urls has no stems key at all."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        service = AudioTranscodingService(storage_service=mock_storage)

        mock_job = Mock()
        mock_job.job_id = "abc123"
        mock_job.input_media_gcs_path = "jobs/abc123/input/song.flac"
        mock_job.file_urls = {}  # No stems key

        result = service.prepare_review_audio_for_job(mock_job)

        assert len(result) == 1
        assert result[0] == "jobs/abc123/review-audio/song.ogg"


class TestInit:
    """Test AudioTranscodingService initialization."""

    @patch("backend.services.audio_transcoding_service.StorageService")
    def test_creates_default_storage_service(self, mock_storage_class):
        """Creates a StorageService when none is provided."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        service = AudioTranscodingService()

        mock_storage_class.assert_called_once()
        assert service.storage is mock_storage_class.return_value

    def test_uses_provided_storage_service(self):
        """Uses the provided storage service instead of creating one."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        service = AudioTranscodingService(storage_service=mock_storage)

        assert service.storage is mock_storage


class TestGetReviewAudioUrlAsync:
    """Test async wrapper."""

    @pytest.mark.asyncio
    @patch("backend.services.audio_transcoding_service.subprocess")
    async def test_async_wrapper_delegates(self, mock_subprocess):
        """Async wrapper should call sync method via to_thread."""
        from backend.services.audio_transcoding_service import AudioTranscodingService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        mock_storage.generate_signed_url.return_value = "https://signed/url"
        service = AudioTranscodingService(storage_service=mock_storage)

        result = await service.get_review_audio_url_async("jobs/abc/input/song.flac")

        assert result == "https://signed/url"
