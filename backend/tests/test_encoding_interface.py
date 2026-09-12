"""
Tests for Encoding Interface.

Tests cover:
- EncodingInput and EncodingOutput dataclasses
- LocalEncodingBackend implementation
- Backend factory function
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from backend.services.encoding_interface import (
    EncodingInput,
    EncodingOutput,
    EncodingBackend,
    LocalEncodingBackend,
    GCEEncodingBackend,
    classify_encoded_output,
    get_encoding_backend,
)


class TestClassifyEncodedOutput:
    """Format classification must key off the trailing (format) parenthetical only,
    never the artist/title text — the NOMAD-1632 root cause."""

    def test_720p_of_portrait_titled_song_is_not_misfiled_as_portrait(self):
        """Regression for NOMAD-1632: 'Portrait of Jennie' 720p → mp4_720p, NOT portrait."""
        f = "Nat King Cole - Portrait of Jennie (Final Karaoke Lossy 720p).mp4"
        assert classify_encoded_output(f) == "mp4_720p"

    def test_real_portrait_video_classifies_as_portrait(self):
        f = "Nat King Cole - Portrait of Jennie (Final Karaoke Portrait 1080x1920).mp4"
        assert classify_encoded_output(f) == "portrait_mp4"

    def test_4k_variants_of_portrait_titled_song(self):
        base = "Nat King Cole - Portrait of Jennie"
        assert classify_encoded_output(f"{base} (Final Karaoke Lossless 4k).mp4") == "mp4_4k_lossless"
        assert classify_encoded_output(f"{base} (Final Karaoke Lossless 4k).mkv") == "mkv_4k"
        assert classify_encoded_output(f"{base} (Final Karaoke Lossy 4k).mp4") == "mp4_4k_lossy"

    def test_title_with_4k_or_720p_words_does_not_leak(self):
        """A title literally containing '720p' must not steal the 4k lossy classification."""
        assert classify_encoded_output("DJ 720p - 4k Dreams (Final Karaoke Lossy 4k).mp4") == "mp4_4k_lossy"
        assert classify_encoded_output("DJ 720p - 4k Dreams (Final Karaoke Lossy 720p).mp4") == "mp4_720p"

    def test_short_canonical_names(self):
        assert classify_encoded_output("jobs/x/finals/lossy_720p_mp4.mp4") == "mp4_720p"
        assert classify_encoded_output("jobs/x/finals/lossy_4k_mp4.mp4") == "mp4_4k_lossy"
        assert classify_encoded_output("jobs/x/finals/lossless_4k_mkv.mkv") == "mkv_4k"
        assert classify_encoded_output("jobs/x/finals/portrait_1080x1920.mp4") == "portrait_mp4"
        assert classify_encoded_output("jobs/x/packages/cdg_zip.zip") == "cdg_zip"
        assert classify_encoded_output("jobs/x/packages/txt_zip.zip") == "txt_zip"

    def test_screens_and_unknown(self):
        assert classify_encoded_output("Artist - Title (Title).mov") == "title_mov"
        assert classify_encoded_output("Artist - Title (End).mov") == "end_mov"
        assert classify_encoded_output("Artist - Title (Karaoke).mp4") is None


class TestEncodingInput:
    """Test EncodingInput dataclass."""

    def test_required_fields(self):
        """Test that required fields must be provided."""
        input_config = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        assert input_config.title_video_path == "/path/title.mov"
        assert input_config.end_video_path is None

    def test_all_fields(self):
        """Test all fields including optional ones."""
        input_config = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            end_video_path="/path/end.mov",
            artist="Test Artist",
            title="Test Title",
            brand_code="NOMAD-1234",
            output_dir="/output",
            options={"quality": "high"}
        )
        assert input_config.artist == "Test Artist"
        assert input_config.brand_code == "NOMAD-1234"
        assert input_config.options["quality"] == "high"


class TestEncodingOutput:
    """Test EncodingOutput dataclass."""

    def test_success_output(self):
        """Test successful output."""
        output = EncodingOutput(
            success=True,
            lossless_4k_mp4_path="/output/video.mp4",
            encoding_backend="local"
        )
        assert output.success is True
        assert output.error_message is None

    def test_failure_output(self):
        """Test failure output."""
        output = EncodingOutput(
            success=False,
            error_message="Encoding failed",
            encoding_backend="gce"
        )
        assert output.success is False
        assert output.error_message == "Encoding failed"

    def test_output_files_dict(self):
        """Test output_files dictionary."""
        output = EncodingOutput(
            success=True,
            output_files={
                "lossless_4k_mp4": "/output/lossless.mp4",
                "720p_mp4": "/output/720p.mp4"
            }
        )
        assert "lossless_4k_mp4" in output.output_files
        assert output.output_files["720p_mp4"] == "/output/720p.mp4"


class TestLocalEncodingBackend:
    """Test LocalEncodingBackend implementation."""

    def test_name(self):
        """Test backend name."""
        backend = LocalEncodingBackend()
        assert backend.name == "local"

    def test_init_with_dry_run(self):
        """Test initialization with dry run."""
        backend = LocalEncodingBackend(dry_run=True)
        assert backend.dry_run is True

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_is_available_success(self, mock_run):
        """Test availability check when FFmpeg is installed."""
        mock_run.return_value = MagicMock(returncode=0)

        backend = LocalEncodingBackend()
        available = await backend.is_available()

        assert available is True

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_is_available_not_found(self, mock_run):
        """Test availability check when FFmpeg is not installed."""
        mock_run.side_effect = FileNotFoundError()

        backend = LocalEncodingBackend()
        available = await backend.is_available()

        assert available is False

    @pytest.mark.asyncio
    @patch.object(LocalEncodingBackend, "is_available")
    @patch.object(LocalEncodingBackend, "_get_service")
    async def test_get_status(self, mock_get_service, mock_is_available):
        """Test status retrieval."""
        mock_is_available.return_value = True
        mock_service = MagicMock()
        mock_service.hwaccel_available = True
        mock_service.video_encoder = "h264_nvenc"
        mock_get_service.return_value = mock_service

        backend = LocalEncodingBackend()
        status = await backend.get_status()

        assert status["backend"] == "local"
        assert status["available"] is True

    @pytest.mark.asyncio
    @patch("asyncio.to_thread")
    @patch.object(LocalEncodingBackend, "_get_service")
    async def test_encode_success(self, mock_get_service, mock_to_thread):
        """Test successful encoding."""
        from backend.services.local_encoding_service import EncodingResult

        mock_result = EncodingResult(
            success=True,
            output_files={"key": "/path/file.mp4"}
        )
        mock_to_thread.return_value = mock_result

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        backend = LocalEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Test Artist",
            title="Test Title",
            output_dir="/output"
        )

        output = await backend.encode(input_config)

        assert output.success is True
        assert output.encoding_backend == "local"
        assert output.encoding_time_seconds is not None

    @pytest.mark.asyncio
    @patch("asyncio.to_thread")
    @patch.object(LocalEncodingBackend, "_get_service")
    async def test_encode_failure(self, mock_get_service, mock_to_thread):
        """Test encoding failure."""
        from backend.services.local_encoding_service import EncodingResult

        mock_result = EncodingResult(
            success=False,
            output_files={},
            error="FFmpeg failed"
        )
        mock_to_thread.return_value = mock_result

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        backend = LocalEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Test",
            title="Test",
        )

        output = await backend.encode(input_config)

        assert output.success is False
        assert output.error_message == "FFmpeg failed"


class TestGCEEncodingBackend:
    """Test GCEEncodingBackend implementation."""

    def test_name(self):
        """Test backend name."""
        backend = GCEEncodingBackend()
        assert backend.name == "gce"

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_is_available_enabled(self, mock_get_service):
        """Test availability when GCE is enabled."""
        mock_service = MagicMock()
        mock_service.is_enabled = True
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        available = await backend.is_available()

        assert available is True

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_is_available_disabled(self, mock_get_service):
        """Test availability when GCE is disabled."""
        mock_service = MagicMock()
        mock_service.is_enabled = False
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        available = await backend.is_available()

        assert available is False

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_missing_gcs_paths(self, mock_get_service):
        """Test encoding fails without GCS paths."""
        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            # Missing GCS paths in options
        )

        output = await backend.encode(input_config)

        assert output.success is False
        assert "gcs_path" in output.error_message.lower()

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_success(self, mock_get_service):
        """Test successful GCE encoding."""
        mock_service = MagicMock()
        mock_service.encode_videos = AsyncMock(return_value={
            "status": "complete",
            # A healthy worker returns every requested format (see the completeness
            # guard in GCEEncodingBackend.encode).
            "output_files": {
                "mp4_4k_lossless": "gs://bucket/output/lossless.mp4",
                "mp4_4k_lossy": "gs://bucket/output/lossy.mp4",
                "mkv_4k": "gs://bucket/output/lossless.mkv",
                "mp4_720p": "gs://bucket/output/720p.mp4",
            }
        })
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Test Artist",
            title="Test Title",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        output = await backend.encode(input_config)

        assert output.success is True
        assert output.encoding_backend == "gce"
        mock_service.encode_videos.assert_called_once()

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_portrait_titled_song_maps_720p(self, mock_get_service):
        """Regression for NOMAD-1632: a song titled 'Portrait of Jennie' must still
        map its 720p to lossy_720p_mp4_path (not silently drop it as the portrait
        video), so the completeness guard passes and the 720p publishes."""
        mock_service = MagicMock()
        mock_service.encode_videos = AsyncMock(return_value={
            "status": "complete",
            "output_files": [
                "jobs/j/finals/Nat King Cole - Portrait of Jennie (Final Karaoke Lossless 4k).mp4",
                "jobs/j/finals/Nat King Cole - Portrait of Jennie (Final Karaoke Lossless 4k).mkv",
                "jobs/j/finals/Nat King Cole - Portrait of Jennie (Final Karaoke Lossy 4k).mp4",
                "jobs/j/finals/Nat King Cole - Portrait of Jennie (Final Karaoke Lossy 720p).mp4",
                "jobs/j/finals/Nat King Cole - Portrait of Jennie (Final Karaoke Portrait 1080x1920).mp4",
            ],
        })
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Nat King Cole",
            title="Portrait of Jennie",
            options={
                "job_id": "j",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        output = await backend.encode(input_config)

        assert output.success is True
        assert output.lossy_720p_mp4_path.endswith("(Final Karaoke Lossy 720p).mp4")
        assert output.portrait_mp4_path.endswith("(Final Karaoke Portrait 1080x1920).mp4")

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_fails_on_incomplete_output(self, mock_get_service):
        """A worker that returns fewer formats than requested must fail loud.

        Regression for the NOMAD-1632 MP4-720p sequence gap (2026-09-11): a
        fallback encoding worker returned the 4K + MKV finals but silently omitted
        the 720p, and the pipeline published an incomplete public release. Only the
        daily GDrive validator caught the gap ~24h later. The completeness guard
        must refuse a partial result so the job errors and is retried instead of
        publishing a track with no 720p.
        """
        mock_service = MagicMock()
        # Worker returned everything EXCEPT the 720p — exactly the NOMAD-1632 case.
        mock_service.encode_videos = AsyncMock(return_value={
            "status": "complete",
            "output_files": [
                "jobs/test/finals/Artist - Title (Final Karaoke Lossless 4k).mp4",
                "jobs/test/finals/Artist - Title (Final Karaoke Lossless 4k).mkv",
                "jobs/test/finals/Artist - Title (Final Karaoke Lossy 4k).mp4",
            ],
        })
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Artist",
            title="Title",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        output = await backend.encode(input_config)

        assert output.success is False
        assert "mp4_720p" in output.error_message
        # The formats that DID come back are still reported for debugging.
        assert output.output_files.get("mp4_4k_lossy")

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_failure(self, mock_get_service):
        """Test GCE encoding failure handling."""
        mock_service = MagicMock()
        mock_service.encode_videos = AsyncMock(side_effect=Exception("GCE worker error"))
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        output = await backend.encode(input_config)

        assert output.success is False
        assert "GCE worker error" in output.error_message

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_reraises_lost_job_error(self, mock_get_service):
        """A lost-job error (worker restart) must propagate, NOT become success=False.

        The orchestrator's resubmit wrapper only fires on the typed exception; if
        the backend flattened it into success=False the render would just fail
        instead of being retried."""
        from backend.services.encoding_errors import EncodingJobLostError

        mock_service = MagicMock()
        mock_service.encode_videos = AsyncMock(
            side_effect=EncodingJobLostError("worker lost the job", job_id="test-job")
        )
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        with pytest.raises(EncodingJobLostError):
            await backend.encode(input_config)

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_handles_list_result(self, mock_get_service):
        """Test GCE encoding handles list response gracefully.

        This would have caught: 'list' object has no attribute 'get' error
        when GCE worker returns a list instead of a dict.
        """
        mock_service = MagicMock()
        # Simulate GCE worker returning a list instead of dict (with a complete
        # format set so the completeness guard passes and we isolate list handling).
        mock_service.encode_videos = AsyncMock(return_value=[
            {"output_files": {
                "mp4_4k_lossless": "gs://bucket/output/lossless.mp4",
                "mp4_4k_lossy": "gs://bucket/output/lossy.mp4",
                "mkv_4k": "gs://bucket/output/lossless.mkv",
                "mp4_720p": "gs://bucket/output/720p.mp4",
            }}
        ])
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        # This should not raise an error
        output = await backend.encode(input_config)

        # Should still succeed by extracting from the list
        assert output.success is True
        assert output.lossless_4k_mp4_path == "gs://bucket/output/lossless.mp4"


    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_maps_with_vocals_mp4_from_list(self, mock_get_service):
        """Test GCE encoding maps 'With Vocals' MP4 from output_files list.

        The GCE worker returns output_files as a list of blob paths.
        The mapper must recognize '(With Vocals).mp4' and set with_vocals_mp4_path.
        Regression test for: with_vocals_mp4 silently dropped from GCE results.
        """
        mock_service = MagicMock()
        mock_service.encode_videos = AsyncMock(return_value={
            "status": "complete",
            "output_files": [
                "jobs/test/finals/Artist - Title (Final Karaoke Lossless 4k).mp4",
                "jobs/test/finals/Artist - Title (Final Karaoke Lossy 4k).mp4",
                "jobs/test/finals/Artist - Title (Final Karaoke Lossless 4k).mkv",
                "jobs/test/finals/Artist - Title (Final Karaoke Lossy 720p).mp4",
                "jobs/test/finals/Artist - Title (With Vocals).mp4",
                "jobs/test/finals/Artist - Title (Karaoke).mp4",
            ]
        })
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.mov",
            karaoke_video_path="/input/karaoke.mov",
            instrumental_audio_path="/input/audio.flac",
            artist="Artist",
            title="Title",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        output = await backend.encode(input_config)

        assert output.success is True
        assert output.with_vocals_mp4_path == "jobs/test/finals/Artist - Title (With Vocals).mp4"
        assert output.lossless_4k_mp4_path == "jobs/test/finals/Artist - Title (Final Karaoke Lossless 4k).mp4"
        assert output.lossy_4k_mp4_path == "jobs/test/finals/Artist - Title (Final Karaoke Lossy 4k).mp4"
        assert output.lossless_mkv_path == "jobs/test/finals/Artist - Title (Final Karaoke Lossless 4k).mkv"
        assert output.lossy_720p_mp4_path == "jobs/test/finals/Artist - Title (Final Karaoke Lossy 720p).mp4"

    @patch.object(GCEEncodingBackend, "_get_service")
    @pytest.mark.asyncio
    async def test_encode_maps_screen_movs_from_list(self, mock_get_service):
        """Test GCE encoding maps the standalone (Title).mov / (End).mov from output_files list.

        The GCE worker now copies the 5-second screen videos into its outputs/ dir so they
        upload to GCS finals. The mapper must recognise them and populate
        title_mov_path / end_mov_path so the orchestrator downloads them into output_dir
        (and thus into the Dropbox folder). Regression for: screen MOVs lost from Dropbox
        after #647/#650 moved screen generation to the encoder.
        """
        mock_service = MagicMock()
        mock_service.encode_videos = AsyncMock(return_value={
            "status": "complete",
            "output_files": [
                "jobs/test/finals/Artist - Title (Final Karaoke Lossless 4k).mp4",
                "jobs/test/finals/Artist - Title (Final Karaoke Lossy 4k).mp4",
                "jobs/test/finals/Artist - Title (Final Karaoke Lossless 4k).mkv",
                "jobs/test/finals/Artist - Title (Final Karaoke Lossy 720p).mp4",
                "jobs/test/finals/Artist - Title (Title).mov",
                "jobs/test/finals/Artist - Title (End).mov",
            ]
        })
        mock_get_service.return_value = mock_service

        backend = GCEEncodingBackend()
        input_config = EncodingInput(
            title_video_path="/input/title.png",
            karaoke_video_path="/input/karaoke.mkv",
            instrumental_audio_path="/input/audio.flac",
            artist="Artist",
            title="Title",
            options={
                "job_id": "test-job",
                "input_gcs_path": "gs://bucket/input/",
                "output_gcs_path": "gs://bucket/output/",
            }
        )

        output = await backend.encode(input_config)

        assert output.success is True
        assert output.title_mov_path == "jobs/test/finals/Artist - Title (Title).mov"
        assert output.end_mov_path == "jobs/test/finals/Artist - Title (End).mov"


class TestGetEncodingBackend:
    """Test encoding backend factory function."""

    def test_get_local_backend(self):
        """Test getting local backend."""
        backend = get_encoding_backend("local")
        assert isinstance(backend, LocalEncodingBackend)
        assert backend.name == "local"

    @patch.object(GCEEncodingBackend, "_get_service")
    def test_get_auto_backend_gce_disabled(self, mock_get_service):
        """Test getting auto backend falls back to local when GCE disabled."""
        mock_service = MagicMock()
        mock_service.is_enabled = False
        mock_get_service.return_value = mock_service

        backend = get_encoding_backend("auto")
        assert isinstance(backend, LocalEncodingBackend)

    def test_get_local_backend_with_options(self):
        """Test getting local backend with options."""
        backend = get_encoding_backend("local", dry_run=True)
        assert backend.dry_run is True

    def test_get_gce_backend(self):
        """Test getting GCE backend."""
        backend = get_encoding_backend("gce")
        assert isinstance(backend, GCEEncodingBackend)
        assert backend.name == "gce"

    def test_get_unknown_backend_raises(self):
        """Test that unknown backend raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_encoding_backend("unknown")
        assert "Unknown encoding backend type" in str(exc_info.value)

    def test_get_gce_backend_with_options(self):
        """Test getting GCE backend with common options like dry_run.

        This ensures all backends accept the same kwargs, preventing
        errors when get_encoding_backend() passes **kwargs to different backends.
        """
        # This would have caught: GCEEncodingBackend.__init__() got an unexpected keyword argument 'dry_run'
        backend = get_encoding_backend("gce", dry_run=True)
        assert isinstance(backend, GCEEncodingBackend)
        assert backend.dry_run is True

    @patch.object(GCEEncodingBackend, "_get_service")
    def test_all_backends_accept_dry_run(self, mock_get_service):
        """Test that all backend types accept dry_run parameter.

        This is an integration test to ensure the factory function
        can pass dry_run to any backend without TypeError.
        """
        mock_service = MagicMock()
        mock_service.is_enabled = False
        mock_get_service.return_value = mock_service

        for backend_type in ["local", "gce", "auto"]:
            # This should not raise TypeError
            backend = get_encoding_backend(backend_type, dry_run=True)
            assert backend is not None
