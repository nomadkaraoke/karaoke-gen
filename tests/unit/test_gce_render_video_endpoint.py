"""Tests for GCE encoding worker render-video endpoint and caching."""
import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock google.cloud.storage.Client before importing main.py to avoid
# OSError in CI where GCP project is not configured
patch("google.cloud.storage.Client", MagicMock()).start()


class TestDownloadWithCache:
    """Tests for the download_with_cache helper."""

    def test_cache_miss_downloads_and_caches(self, tmp_path):
        """On cache miss, downloads file and stores in cache."""
        from backend.services.gce_encoding.main import download_with_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        # Mock download to write a file
        def fake_download(uri, path):
            Path(path).write_bytes(b"font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs", side_effect=fake_download):
            download_with_cache(gcs_uri, dest, cache_dir)

        assert dest.read_bytes() == b"font-data"
        # Verify cached copy exists
        cache_key = hashlib.sha256(gcs_uri.encode()).hexdigest()
        cached = cache_dir / cache_key
        assert cached.exists()
        assert cached.read_bytes() == b"font-data"

    def test_cache_hit_skips_download(self, tmp_path):
        """On cache hit, copies from cache without downloading."""
        from backend.services.gce_encoding.main import download_with_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        # Pre-populate cache
        cache_key = hashlib.sha256(gcs_uri.encode()).hexdigest()
        cached = cache_dir / cache_key
        cached.write_bytes(b"cached-font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs") as mock_dl:
            download_with_cache(gcs_uri, dest, cache_dir)
            mock_dl.assert_not_called()

        assert dest.read_bytes() == b"cached-font-data"

    def test_cache_dir_none_downloads_directly(self, tmp_path):
        """When cache_dir is None, downloads without caching."""
        from backend.services.gce_encoding.main import download_with_cache

        dest = tmp_path / "output.ttf"
        gcs_uri = "gs://bucket/themes/nomad/font.ttf"

        def fake_download(uri, path):
            Path(path).write_bytes(b"font-data")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs", side_effect=fake_download):
            download_with_cache(gcs_uri, dest, cache_dir=None)

        assert dest.read_bytes() == b"font-data"


class TestRunRenderVideo:
    """Tests for the run_render_video processing function."""

    def _make_request(self):
        from backend.services.gce_encoding.main import RenderVideoRequest
        return RenderVideoRequest(
            job_id="rv-test-001",
            original_corrections_gcs_path="gs://bucket/jobs/rv-test-001/corrections.json",
            updated_corrections_gcs_path=None,
            audio_gcs_path="gs://bucket/jobs/rv-test-001/audio.flac",
            style_params_gcs_path=None,
            style_assets=None,
            output_gcs_prefix="gs://bucket/jobs/rv-test-001/output",
            artist="Test Artist",
            title="Test Song",
            subtitle_offset_ms=0,
            video_resolution="4k",
        )

    @patch("backend.services.gce_encoding.main.upload_single_file_to_gcs")
    @patch("backend.services.gce_encoding.main.download_with_cache")
    @patch("backend.services.gce_encoding.main.download_single_file_from_gcs")
    @patch("backend.services.gce_encoding.main.subprocess")
    def test_sets_job_status_to_running(
        self, mock_subprocess, mock_dl, mock_cache_dl, mock_upload, tmp_path
    ):
        """Verifies run_render_video sets job status to running and populates output_files."""
        from backend.services.gce_encoding.main import run_render_video, jobs

        mock_subprocess.run.return_value = MagicMock(returncode=0)

        job_id = "rv-test-001"
        jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "progress": 0,
            "error": None,
            "output_files": None,
            "metadata": None,
        }

        request = self._make_request()
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        # Mock downloads to create dummy files
        def fake_download(uri, path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("{}")

        mock_dl.side_effect = fake_download

        # Mock karaoke-gen wheel imports
        mock_correction_result = MagicMock()
        mock_correction_result.events = []
        mock_countdown_processor = MagicMock()
        mock_countdown_processor.return_value.process.return_value = (mock_correction_result, False, 0)
        mock_output_generator_cls = MagicMock()
        mock_output_generator = MagicMock()
        mock_output_generator_cls.return_value = mock_output_generator
        mock_output_config = MagicMock()

        def fake_generate(*args, **kwargs):
            videos_dir = work_dir / "videos"
            lyrics_dir = work_dir / "lyrics"
            videos_dir.mkdir(parents=True, exist_ok=True)
            lyrics_dir.mkdir(parents=True, exist_ok=True)
            (videos_dir / "with_vocals.mkv").write_bytes(b"video")
            (lyrics_dir / "karaoke.ass").write_text("ass content")
            (lyrics_dir / "karaoke.lrc").write_text("lrc content")
            (lyrics_dir / "corrected.txt").write_text("text content")

        mock_output_generator.generate_outputs.side_effect = fake_generate

        import sys as _sys
        fake_modules = {
            "backend.output_generator": MagicMock(
                OutputGenerator=mock_output_generator_cls,
                OutputConfig=mock_output_config,
            ),
            "backend.services.countdown_processor": MagicMock(
                CountdownProcessor=mock_countdown_processor,
            ),
            "backend.models.correction_result": MagicMock(
                CorrectionResult=MagicMock(from_json=MagicMock(return_value=mock_correction_result)),
            ),
            "backend.services.correction_operations": MagicMock(
                CorrectionOperations=MagicMock(),
            ),
            "backend.utils.style_utils": MagicMock(
                load_styles_from_gcs=MagicMock(return_value=({}, [])),
            ),
            "backend.utils.filename_utils": MagicMock(
                sanitize_filename=MagicMock(side_effect=lambda x: x.replace(" ", "_")),
            ),
        }
        with patch.dict(_sys.modules, fake_modules):
            run_render_video(job_id, work_dir, request)

        # Verify status was set to running (then completed by caller)
        # and output_files were populated
        assert jobs[job_id]["output_files"] is not None
        assert len(jobs[job_id]["output_files"]) == 4

    @patch("backend.services.gce_encoding.main.upload_single_file_to_gcs")
    @patch("backend.services.gce_encoding.main.download_with_cache")
    @patch("backend.services.gce_encoding.main.download_single_file_from_gcs")
    @patch("backend.services.gce_encoding.main.subprocess")
    def test_uploads_all_output_files(
        self, mock_subprocess, mock_dl, mock_cache_dl, mock_upload, tmp_path
    ):
        """Verifies all 4 output files are uploaded to correct GCS paths."""
        from backend.services.gce_encoding.main import run_render_video, jobs

        mock_subprocess.run.return_value = MagicMock(returncode=0)

        job_id = "rv-test-002"
        jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "progress": 0,
            "error": None,
            "output_files": None,
            "metadata": None,
        }

        request = self._make_request()
        request.job_id = job_id
        request.output_gcs_prefix = "gs://bucket/jobs/rv-test-002/output"
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        def fake_download(uri, path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("{}")

        mock_dl.side_effect = fake_download

        mock_correction_result = MagicMock()
        mock_correction_result.events = []
        mock_countdown_processor = MagicMock()
        mock_countdown_processor.return_value.process.return_value = (mock_correction_result, False, 0)
        mock_output_generator_cls = MagicMock()
        mock_output_generator = MagicMock()
        mock_output_generator_cls.return_value = mock_output_generator
        mock_output_config = MagicMock()

        def fake_generate(*args, **kwargs):
            videos_dir = work_dir / "videos"
            lyrics_dir = work_dir / "lyrics"
            videos_dir.mkdir(parents=True, exist_ok=True)
            lyrics_dir.mkdir(parents=True, exist_ok=True)
            (videos_dir / "with_vocals.mkv").write_bytes(b"video")
            (lyrics_dir / "karaoke.ass").write_text("ass content")
            (lyrics_dir / "karaoke.lrc").write_text("lrc content")
            (lyrics_dir / "corrected.txt").write_text("text content")

        mock_output_generator.generate_outputs.side_effect = fake_generate

        import sys as _sys
        fake_modules = {
            "backend.output_generator": MagicMock(
                OutputGenerator=mock_output_generator_cls,
                OutputConfig=mock_output_config,
            ),
            "backend.services.countdown_processor": MagicMock(
                CountdownProcessor=mock_countdown_processor,
            ),
            "backend.models.correction_result": MagicMock(
                CorrectionResult=MagicMock(from_json=MagicMock(return_value=mock_correction_result)),
            ),
            "backend.services.correction_operations": MagicMock(
                CorrectionOperations=MagicMock(),
            ),
            "backend.utils.style_utils": MagicMock(
                load_styles_from_gcs=MagicMock(return_value=({}, [])),
            ),
            "backend.utils.filename_utils": MagicMock(
                sanitize_filename=MagicMock(side_effect=lambda x: x.replace(" ", "_")),
            ),
        }
        with patch.dict(_sys.modules, fake_modules):
            run_render_video(job_id, work_dir, request)

        # Check all 4 uploads happened with correct GCS paths
        upload_calls = mock_upload.call_args_list
        uploaded_gcs_paths = [call[0][1] for call in upload_calls]

        assert "gs://bucket/jobs/rv-test-002/output/videos/with_vocals.mkv" in uploaded_gcs_paths
        assert "gs://bucket/jobs/rv-test-002/output/lyrics/karaoke.ass" in uploaded_gcs_paths
        assert "gs://bucket/jobs/rv-test-002/output/lyrics/karaoke.lrc" in uploaded_gcs_paths
        assert "gs://bucket/jobs/rv-test-002/output/lyrics/corrected.txt" in uploaded_gcs_paths
