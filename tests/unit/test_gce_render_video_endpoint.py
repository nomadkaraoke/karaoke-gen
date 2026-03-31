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

    def _make_request(self, job_id="rv-test-001", output_prefix="gs://bucket/jobs/rv-test-001/output"):
        from backend.services.gce_encoding.main import RenderVideoRequest
        return RenderVideoRequest(
            job_id=job_id,
            original_corrections_gcs_path="gs://bucket/jobs/test/lyrics/corrections.json",
            audio_gcs_path="gs://bucket/jobs/test/audio.flac",
            output_gcs_prefix=output_prefix,
            artist="Test Artist",
            title="Test Song",
        )

    def _make_mock_outputs(self, tmp_path):
        """Create mock OutputGenerator outputs with real files."""
        mock_outputs = MagicMock()
        mock_outputs.video = str(tmp_path / "with_vocals.mkv")
        mock_outputs.ass = str(tmp_path / "karaoke.ass")
        mock_outputs.lrc = str(tmp_path / "karaoke.lrc")
        mock_outputs.corrected_txt = str(tmp_path / "corrected.txt")
        for attr in ["video", "ass", "lrc", "corrected_txt"]:
            Path(getattr(mock_outputs, attr)).write_bytes(b"fake")
        return mock_outputs

    def _run_with_mocks(self, job_id, work_dir, request, mock_outputs, tmp_path):
        """Run run_render_video with all karaoke-gen wheel imports mocked."""
        from backend.services.gce_encoding.main import run_render_video, jobs

        jobs[job_id] = {
            "job_id": job_id, "status": "pending", "progress": 0,
            "error": None, "output_files": None, "metadata": None,
        }

        mock_correction_result = MagicMock(corrected_segments=[])
        mock_gen = MagicMock()
        mock_gen.generate_outputs.return_value = mock_outputs

        import sys as _sys
        fake_modules = {
            "karaoke_gen.lyrics_transcriber.output.generator": MagicMock(
                OutputGenerator=MagicMock(return_value=mock_gen),
            ),
            "karaoke_gen.lyrics_transcriber.output.countdown_processor": MagicMock(
                CountdownProcessor=MagicMock(return_value=MagicMock(
                    process=MagicMock(return_value=(mock_correction_result, str(work_dir / "audio.flac"), False, 0))
                )),
            ),
            "karaoke_gen.lyrics_transcriber.types": MagicMock(
                CorrectionResult=MagicMock(from_dict=MagicMock(return_value=mock_correction_result)),
            ),
            "karaoke_gen.lyrics_transcriber.correction.operations": MagicMock(),
            "karaoke_gen.lyrics_transcriber.core.config": MagicMock(),
            "karaoke_gen.style_loader": MagicMock(
                load_styles_from_gcs=MagicMock(return_value=(str(tmp_path / "styles.json"), {})),
            ),
            "karaoke_gen.utils": MagicMock(
                sanitize_filename=MagicMock(side_effect=lambda x: x.replace(" ", "_")),
            ),
        }

        def fake_download(uri, path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("{}")

        with patch("backend.services.gce_encoding.main.download_single_file_from_gcs", side_effect=fake_download):
            with patch("backend.services.gce_encoding.main.download_with_cache"):
                with patch("backend.services.gce_encoding.main.upload_single_file_to_gcs") as mock_upload:
                    with patch("backend.services.gce_encoding.main.subprocess"):
                        with patch.dict(_sys.modules, fake_modules):
                            run_render_video(job_id, work_dir, request)

        return mock_upload

    def test_sets_job_status_and_populates_output_files(self, tmp_path):
        """run_render_video sets output_files and metadata."""
        from backend.services.gce_encoding.main import jobs

        job_id = "rv-test-001"
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        request = self._make_request(job_id)
        mock_outputs = self._make_mock_outputs(tmp_path)

        self._run_with_mocks(job_id, work_dir, request, mock_outputs, tmp_path)

        assert jobs[job_id]["output_files"] is not None
        assert len(jobs[job_id]["output_files"]) == 4
        assert jobs[job_id]["metadata"]["countdown_padding_added"] is False

    def test_uploads_all_output_files(self, tmp_path):
        """run_render_video uploads video, ass, lrc, and txt to correct GCS paths."""
        job_id = "rv-test-002"
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        request = self._make_request(job_id, "gs://bucket/jobs/rv-test-002/output")
        mock_outputs = self._make_mock_outputs(tmp_path)

        mock_upload = self._run_with_mocks(job_id, work_dir, request, mock_outputs, tmp_path)

        uploaded_gcs_paths = [call[0][1] for call in mock_upload.call_args_list]
        assert "gs://bucket/jobs/rv-test-002/output/videos/with_vocals.mkv" in uploaded_gcs_paths
        assert "gs://bucket/jobs/rv-test-002/output/lyrics/karaoke.ass" in uploaded_gcs_paths
        assert "gs://bucket/jobs/rv-test-002/output/lyrics/karaoke.lrc" in uploaded_gcs_paths
        assert "gs://bucket/jobs/rv-test-002/output/lyrics/corrected.txt" in uploaded_gcs_paths
