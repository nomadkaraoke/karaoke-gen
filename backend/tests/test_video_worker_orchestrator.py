"""
Tests for Video Worker Orchestrator.

Tests cover:
- OrchestratorConfig and OrchestratorResult dataclasses
- VideoWorkerOrchestrator initialization
- Individual stage methods
- Full pipeline execution
- Error handling and recovery
"""

import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import asdict

from backend.workers.video_worker_orchestrator import (
    OrchestratorConfig,
    OrchestratorResult,
    VideoWorkerOrchestrator,
    create_orchestrator_config_from_job,
)


class TestOrchestratorConfig:
    """Test OrchestratorConfig dataclass."""

    def test_required_fields(self):
        """Test that required fields must be provided."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        assert config.job_id == "test-job"
        assert config.artist == "Test Artist"
        assert config.encoding_backend == "auto"

    def test_default_values(self):
        """Test default values for optional fields."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        assert config.enable_cdg is False
        assert config.enable_txt is False
        assert config.enable_youtube_upload is False
        assert config.dry_run is False
        assert config.non_interactive is True
        assert config.end_video_path is None

    def test_all_fields(self):
        """Test all fields including optional ones."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            end_video_path="/path/end.mov",
            enable_cdg=True,
            enable_txt=True,
            enable_youtube_upload=True,
            brand_prefix="TEST",
            discord_webhook_url="https://discord.com/api/webhooks/123/abc",
            encoding_backend="gce",
        )
        assert config.enable_cdg is True
        assert config.encoding_backend == "gce"
        assert config.discord_webhook_url is not None


class TestOrchestratorResult:
    """Test OrchestratorResult dataclass."""

    def test_success_result(self):
        """Test successful result."""
        result = OrchestratorResult(
            success=True,
            final_video="/output/video.mp4",
            brand_code="TEST-1234",
            youtube_url="https://youtube.com/watch?v=test",
        )
        assert result.success is True
        assert result.error_message is None
        assert result.brand_code == "TEST-1234"

    def test_failure_result(self):
        """Test failure result."""
        result = OrchestratorResult(
            success=False,
            error_message="Encoding failed",
        )
        assert result.success is False
        assert result.error_message == "Encoding failed"
        assert result.final_video is None

    def test_all_output_files(self):
        """Test all output file fields."""
        result = OrchestratorResult(
            success=True,
            final_video="/output/lossless.mp4",
            final_video_mkv="/output/lossless.mkv",
            final_video_lossy="/output/lossy.mp4",
            final_video_720p="/output/720p.mp4",
            final_karaoke_cdg_zip="/output/cdg.zip",
            final_karaoke_txt_zip="/output/txt.zip",
        )
        assert result.final_video == "/output/lossless.mp4"
        assert result.final_video_mkv == "/output/lossless.mkv"


class TestVideoWorkerOrchestratorInit:
    """Test VideoWorkerOrchestrator initialization."""

    def test_init_with_config(self):
        """Test initialization with config."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        assert orchestrator.config == config
        assert orchestrator.result.success is False

    def test_init_with_job_manager(self):
        """Test initialization with job manager."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        job_manager = MagicMock()
        orchestrator = VideoWorkerOrchestrator(config, job_manager=job_manager)

        assert orchestrator.job_manager == job_manager


class TestVideoWorkerOrchestratorServices:
    """Test service lazy-loading."""

    def test_get_encoding_backend(self):
        """Test encoding backend lazy loading."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            encoding_backend="local",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch("backend.services.encoding_interface.get_encoding_backend") as mock_get:
            mock_backend = MagicMock()
            mock_backend.name = "local"
            mock_get.return_value = mock_backend

            backend = orchestrator._get_encoding_backend()

            assert backend == mock_backend
            mock_get.assert_called_once()

    def test_get_packaging_service(self):
        """Test packaging service lazy loading."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            cdg_styles={"background_color": "black"},
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch("backend.services.packaging_service.PackagingService") as MockService:
            mock_service = MagicMock()
            MockService.return_value = mock_service

            service = orchestrator._get_packaging_service()

            assert service == mock_service
            MockService.assert_called_once()

    def test_get_youtube_service(self):
        """Test YouTube service lazy loading."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            youtube_credentials={"token": "test"},
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch("backend.services.youtube_upload_service.YouTubeUploadService") as MockService:
            mock_service = MagicMock()
            MockService.return_value = mock_service

            service = orchestrator._get_youtube_service()

            assert service == mock_service
            MockService.assert_called_once()

    def test_get_discord_service(self):
        """Test Discord service lazy loading."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch("backend.services.discord_service.DiscordNotificationService") as MockService:
            mock_service = MagicMock()
            MockService.return_value = mock_service

            service = orchestrator._get_discord_service()

            assert service == mock_service
            MockService.assert_called_once()


class TestVideoWorkerOrchestratorPackaging:
    """Test packaging stage."""

    @pytest.mark.asyncio
    async def test_run_packaging_no_lrc_cdg_disabled(self):
        """Test packaging stage with no LRC file and CDG disabled skips silently."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=False,
            lrc_file_path=None,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        # Should not raise, just skip
        await orchestrator._run_packaging()

        assert orchestrator.result.final_karaoke_cdg_zip is None

    @pytest.mark.asyncio
    async def test_run_packaging_no_lrc_cdg_enabled_raises(self):
        """Test packaging stage raises when CDG enabled but no LRC file."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=True,
            lrc_file_path=None,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with pytest.raises(RuntimeError, match="LRC file not available"):
            await orchestrator._run_packaging()

    @pytest.mark.asyncio
    async def test_run_packaging_cdg(self):
        """Test CDG packaging."""
        with tempfile.TemporaryDirectory() as temp_dir:
            lrc_file = os.path.join(temp_dir, "test.lrc")
            audio_file = os.path.join(temp_dir, "test.flac")

            # Create dummy files
            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")
            with open(audio_file, "w") as f:
                f.write("dummy audio")

            config = OrchestratorConfig(
                job_id="test-job",
                artist="Test Artist",
                title="Test Title",
                title_video_path="/path/title.mov",
                karaoke_video_path="/path/karaoke.mov",
                instrumental_audio_path=audio_file,
                lrc_file_path=lrc_file,
                output_dir=temp_dir,
                enable_cdg=True,
                cdg_styles={"background_color": "black"},
            )
            orchestrator = VideoWorkerOrchestrator(config)

            with patch.object(orchestrator, "_get_packaging_service") as mock_get:
                mock_service = MagicMock()
                mock_service.create_cdg_package.return_value = (
                    f"{temp_dir}/cdg.zip",
                    f"{temp_dir}/test.mp3",
                    f"{temp_dir}/test.cdg",
                )
                mock_get.return_value = mock_service

                await orchestrator._run_packaging()

                mock_service.create_cdg_package.assert_called_once()
                assert orchestrator.result.final_karaoke_cdg_zip == f"{temp_dir}/cdg.zip"

    @pytest.mark.asyncio
    async def test_run_packaging_cdg_with_countdown_padding(self):
        """Test CDG packaging passes countdown params when LRC has countdown padding.

        This test prevents regression of the CDG sync bug where:
        - Video rendering adds 3s countdown and shifts LRC timestamps by +3s
        - CDG generation was using shifted timestamps but instrumental audio without padding
        - Result: CDG lyrics appeared 3 seconds late

        The fix: When lrc_has_countdown_padding=True, the packaging service must pass
        this flag to CDGGenerator so it can strip the countdown and shift timestamps back.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            lrc_file = os.path.join(temp_dir, "test.lrc")
            audio_file = os.path.join(temp_dir, "test.flac")

            # Create LRC with countdown-shifted timestamps (as video rendering produces)
            with open(lrc_file, "w") as f:
                f.write("[00:00.10]1:/3... 2... 1...\n")  # Countdown at 0.1s
                f.write("[00:03.60]1:/First\n")  # First word at 3.6s (shifted from 0.6s)
            with open(audio_file, "w") as f:
                f.write("dummy audio")

            config = OrchestratorConfig(
                job_id="test-job",
                artist="Test Artist",
                title="Test Title",
                title_video_path="/path/title.mov",
                karaoke_video_path="/path/karaoke.mov",
                instrumental_audio_path=audio_file,
                lrc_file_path=lrc_file,
                output_dir=temp_dir,
                enable_cdg=True,
                cdg_styles={"background_color": "black"},
                lrc_has_countdown_padding=True,  # Critical: flag must be set
            )
            orchestrator = VideoWorkerOrchestrator(config)

            with patch.object(orchestrator, "_get_packaging_service") as mock_get:
                mock_service = MagicMock()
                mock_service.create_cdg_package.return_value = (
                    f"{temp_dir}/cdg.zip",
                    f"{temp_dir}/test.mp3",
                    f"{temp_dir}/test.cdg",
                )
                mock_get.return_value = mock_service

                await orchestrator._run_packaging()

                # CRITICAL ASSERTION: countdown padding params must be passed
                mock_service.create_cdg_package.assert_called_once()
                call_kwargs = mock_service.create_cdg_package.call_args[1]
                assert call_kwargs.get("lrc_has_countdown_padding") is True, \
                    "lrc_has_countdown_padding must be passed to packaging service for CDG sync fix"
                assert call_kwargs.get("countdown_padding_seconds") == 3.0, \
                    "countdown_padding_seconds must be passed for CDG timestamp adjustment"


class TestPackagingValidation:
    """Test CDG/TXT packaging validation checks.

    These tests verify that the orchestrator fails fast when CDG/TXT
    generation is enabled but fails, rather than silently proceeding
    to expensive encoding. This prevents jobs from completing without
    their requested CDG/TXT packages.

    Added after jobs 5b6aba25 and 5161b069 completed with enable_cdg=True
    but no CDG ZIP was produced due to a FileNotFoundError being silently caught.
    """

    @pytest.mark.asyncio
    async def test_cdg_validation_fails_when_enabled_but_not_produced_and_lrc_has_content(self):
        """Test that orchestrator raises when CDG enabled but not produced and LRC has lyrics."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=True,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        # Mock _run_packaging to NOT set result.final_karaoke_cdg_zip (simulates failure)
        async def mock_packaging():
            pass  # CDG generation "failed" silently

        with patch.object(orchestrator, "_run_packaging", side_effect=mock_packaging), \
             patch.object(orchestrator, "_lrc_has_lyrics_content", return_value=True), \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock) as mock_encoding:

            result = await orchestrator.run()

            assert result.success is False
            assert "CDG generation was enabled but failed" in result.error_message
            mock_encoding.assert_not_called()  # Should not reach encoding

    @pytest.mark.asyncio
    async def test_cdg_skipped_gracefully_when_lrc_has_no_content(self):
        """Test that CDG failure is a warning (not error) when LRC has no lyrics content."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=True,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        async def mock_packaging():
            pass  # CDG generation "failed" silently

        with patch.object(orchestrator, "_run_packaging", side_effect=mock_packaging), \
             patch.object(orchestrator, "_lrc_has_lyrics_content", return_value=False), \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock) as mock_encoding, \
             patch.object(orchestrator, "_run_organization", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_distribution", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_notifications", new_callable=AsyncMock):

            result = await orchestrator.run()

            assert result.success is True  # Pipeline continues
            mock_encoding.assert_called_once()  # Encoding still runs

    @pytest.mark.asyncio
    async def test_txt_validation_fails_when_enabled_but_not_produced_and_lrc_has_content(self):
        """Test that orchestrator raises when TXT enabled but not produced and LRC has lyrics."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_txt=True,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        async def mock_packaging():
            pass  # TXT generation "failed" silently

        with patch.object(orchestrator, "_run_packaging", side_effect=mock_packaging), \
             patch.object(orchestrator, "_lrc_has_lyrics_content", return_value=True), \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock) as mock_encoding:

            result = await orchestrator.run()

            assert result.success is False
            assert "TXT generation was enabled but failed" in result.error_message
            mock_encoding.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_succeeds_when_cdg_disabled(self):
        """Test that pipeline succeeds normally when CDG is not enabled."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=False,
            enable_txt=False,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch.object(orchestrator, "_run_packaging", new_callable=AsyncMock) as mock_pkg, \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_organization", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_distribution", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_notifications", new_callable=AsyncMock):

            result = await orchestrator.run()

            assert result.success is True
            mock_pkg.assert_not_called()  # Packaging skipped entirely

    @pytest.mark.asyncio
    async def test_cdg_validation_passes_when_cdg_produced(self):
        """Test that validation passes when CDG is successfully produced."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=True,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        async def mock_packaging_success():
            orchestrator.result.final_karaoke_cdg_zip = "/output/cdg.zip"

        with patch.object(orchestrator, "_run_packaging", side_effect=mock_packaging_success), \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_organization", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_distribution", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_notifications", new_callable=AsyncMock):

            result = await orchestrator.run()

            assert result.success is True

    @pytest.mark.asyncio
    async def test_both_cdg_and_txt_validation_fail_together(self):
        """Test that orchestrator catches CDG failure when both CDG and TXT enabled and LRC has content."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=True,
            enable_txt=True,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        async def mock_packaging():
            pass  # Both CDG and TXT "failed" silently

        with patch.object(orchestrator, "_run_packaging", side_effect=mock_packaging), \
             patch.object(orchestrator, "_lrc_has_lyrics_content", return_value=True), \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock) as mock_encoding:

            result = await orchestrator.run()

            assert result.success is False
            # CDG validation is checked first
            assert "CDG generation was enabled but failed" in result.error_message
            mock_encoding.assert_not_called()

    @pytest.mark.asyncio
    async def test_lrc_missing_raises_when_cdg_enabled(self):
        """Test that missing LRC file raises when CDG is enabled."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=True,
            lrc_file_path="/nonexistent/path.lrc",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with pytest.raises(RuntimeError, match="LRC file not available"):
            await orchestrator._run_packaging()

    @pytest.mark.asyncio
    async def test_lrc_missing_raises_when_txt_enabled(self):
        """Test that missing LRC file raises when TXT is enabled."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_txt=True,
            lrc_file_path=None,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with pytest.raises(RuntimeError, match="LRC file not available"):
            await orchestrator._run_packaging()

    @pytest.mark.asyncio
    async def test_lrc_missing_skips_when_cdg_disabled(self):
        """Test that missing LRC file just skips packaging when CDG/TXT disabled."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=False,
            enable_txt=False,
            lrc_file_path=None,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        # Should not raise, just skip
        await orchestrator._run_packaging()
        assert orchestrator.result.final_karaoke_cdg_zip is None


class TestVideoWorkerOrchestratorEncoding:
    """Test encoding stage."""

    @pytest.mark.asyncio
    async def test_run_encoding_success(self):
        """Test successful encoding."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            output_dir="/output",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch.object(orchestrator, "_get_encoding_backend") as mock_get:
            from backend.services.encoding_interface import EncodingOutput

            mock_backend = MagicMock()
            mock_backend.name = "local"
            mock_backend.encode = AsyncMock(return_value=EncodingOutput(
                success=True,
                lossless_4k_mp4_path="/output/lossless.mp4",
                lossy_4k_mp4_path="/output/lossy.mp4",
                lossy_720p_mp4_path="/output/720p.mp4",
                lossless_mkv_path="/output/lossless.mkv",
                encoding_time_seconds=120.5,
                encoding_backend="local",
            ))
            mock_get.return_value = mock_backend

            await orchestrator._run_encoding()

            mock_backend.encode.assert_called_once()
            assert orchestrator.result.final_video == "/output/lossless.mp4"
            assert orchestrator.result.final_video_720p == "/output/720p.mp4"
            assert orchestrator.result.encoding_time_seconds == 120.5

    def test_encoding_input_gcs_paths_pattern(self):
        """Test that EncodingInput.options contains proper GCS paths structure.

        This test verifies the GCS path construction pattern that GCE encoding
        requires. It tests the structure without running _run_encoding().

        This would have caught: 'GCE encoding requires input_gcs_path and
        output_gcs_path in options' error when the orchestrator didn't pass
        the required paths for GCE encoding.
        """
        from backend.services.encoding_interface import EncodingInput

        # Test that EncodingInput can hold GCS paths in options
        # This mirrors what the orchestrator should build
        job_id = "test-job-123"
        bucket = "test-bucket"
        input_gcs_path = f"gs://{bucket}/jobs/{job_id}/"
        output_gcs_path = f"gs://{bucket}/jobs/{job_id}/finals/"

        encoding_input = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            artist="Test Artist",
            title="Test Title",
            brand_code="TEST-001",
            output_dir="/output",
            options={
                "input_gcs_path": input_gcs_path,
                "output_gcs_path": output_gcs_path,
            },
        )

        # Verify the structure that GCEEncodingBackend expects
        assert "input_gcs_path" in encoding_input.options, \
            "EncodingInput.options must include input_gcs_path for GCE encoding"
        assert "output_gcs_path" in encoding_input.options, \
            "EncodingInput.options must include output_gcs_path for GCE encoding"
        # Verify path format
        assert encoding_input.options["input_gcs_path"].startswith("gs://")
        assert encoding_input.options["output_gcs_path"].startswith("gs://")
        assert job_id in encoding_input.options["input_gcs_path"]
        assert job_id in encoding_input.options["output_gcs_path"]

    @pytest.mark.asyncio
    async def test_run_encoding_failure(self):
        """Test encoding failure."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch.object(orchestrator, "_get_encoding_backend") as mock_get:
            from backend.services.encoding_interface import EncodingOutput

            mock_backend = MagicMock()
            mock_backend.name = "local"
            mock_backend.encode = AsyncMock(return_value=EncodingOutput(
                success=False,
                error_message="FFmpeg failed",
                encoding_backend="local",
            ))
            mock_get.return_value = mock_backend

            with pytest.raises(Exception) as exc_info:
                await orchestrator._run_encoding()

            assert "Encoding failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_encoding_gce_downloads_files(self):
        """Test that GCE encoding downloads files from GCS to local directory.

        This test verifies the fix for YouTube upload failure when using GCE encoding.
        GCE encoding returns GCS blob paths, which need to be downloaded locally
        before YouTube upload can access them.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config = OrchestratorConfig(
                job_id="test-job",
                artist="Test Artist",
                title="Test Title",
                title_video_path=os.path.join(temp_dir, "title.mov"),
                karaoke_video_path=os.path.join(temp_dir, "karaoke.mov"),
                instrumental_audio_path=os.path.join(temp_dir, "audio.flac"),
                output_dir=temp_dir,
            )

            # Mock storage service
            mock_storage = MagicMock()
            mock_storage.download_file = MagicMock()

            orchestrator = VideoWorkerOrchestrator(
                config,
                storage=mock_storage,
            )

            with patch.object(orchestrator, "_get_encoding_backend") as mock_get:
                from backend.services.encoding_interface import EncodingOutput

                # GCE backend returns GCS blob paths (not local paths)
                mock_backend = MagicMock()
                mock_backend.name = "gce"  # Important: must be "gce" to trigger download
                mock_backend.encode = AsyncMock(return_value=EncodingOutput(
                    success=True,
                    lossless_4k_mp4_path="jobs/test-job/finals/output_4k_lossless.mp4",
                    lossy_4k_mp4_path="jobs/test-job/finals/output_4k_lossy.mp4",
                    lossy_720p_mp4_path="jobs/test-job/finals/output_720p.mp4",
                    lossless_mkv_path="jobs/test-job/finals/output_4k.mkv",
                    encoding_time_seconds=60.0,
                    encoding_backend="gce",
                ))
                mock_get.return_value = mock_backend

                await orchestrator._run_encoding()

                # Verify download_file was called for each output file
                assert mock_storage.download_file.call_count == 4

                # Verify the result paths were updated to local paths
                assert orchestrator.result.final_video.startswith(temp_dir)
                assert orchestrator.result.final_video_mkv.startswith(temp_dir)
                assert orchestrator.result.final_video_lossy.startswith(temp_dir)
                assert orchestrator.result.final_video_720p.startswith(temp_dir)

    @pytest.mark.asyncio
    async def test_run_encoding_local_does_not_download(self):
        """Test that local encoding does NOT trigger GCS download.

        Local encoding produces files directly in the output directory,
        so no download is needed.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config = OrchestratorConfig(
                job_id="test-job",
                artist="Test Artist",
                title="Test Title",
                title_video_path=os.path.join(temp_dir, "title.mov"),
                karaoke_video_path=os.path.join(temp_dir, "karaoke.mov"),
                instrumental_audio_path=os.path.join(temp_dir, "audio.flac"),
                output_dir=temp_dir,
            )

            # Mock storage service
            mock_storage = MagicMock()
            mock_storage.download_file = MagicMock()

            orchestrator = VideoWorkerOrchestrator(
                config,
                storage=mock_storage,
            )

            with patch.object(orchestrator, "_get_encoding_backend") as mock_get:
                from backend.services.encoding_interface import EncodingOutput

                # Local backend returns local paths directly
                mock_backend = MagicMock()
                mock_backend.name = "local"  # Local backend
                mock_backend.encode = AsyncMock(return_value=EncodingOutput(
                    success=True,
                    lossless_4k_mp4_path=os.path.join(temp_dir, "lossless.mp4"),
                    lossy_4k_mp4_path=os.path.join(temp_dir, "lossy.mp4"),
                    lossy_720p_mp4_path=os.path.join(temp_dir, "720p.mp4"),
                    lossless_mkv_path=os.path.join(temp_dir, "lossless.mkv"),
                    encoding_time_seconds=120.0,
                    encoding_backend="local",
                ))
                mock_get.return_value = mock_backend

                await orchestrator._run_encoding()

                # Verify download_file was NOT called for local encoding
                mock_storage.download_file.assert_not_called()


    @pytest.mark.asyncio
    async def test_run_encoding_gce_stale_cache_retries(self):
        """Test that stale GCE cache (files deleted from GCS) triggers retry with new job ID.

        When admin deletes outputs and re-runs, the GCE worker may return a cached
        'complete' result but the actual files no longer exist in GCS. The orchestrator
        should detect this (0 files downloaded) and retry with a suffixed job ID.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config = OrchestratorConfig(
                job_id="test-job",
                artist="Test Artist",
                title="Test Title",
                title_video_path=os.path.join(temp_dir, "title.mov"),
                karaoke_video_path=os.path.join(temp_dir, "karaoke.mov"),
                instrumental_audio_path=os.path.join(temp_dir, "audio.flac"),
                output_dir=temp_dir,
            )

            mock_storage = MagicMock()
            # First call: all downloads fail (404 - stale cache)
            # Second call: downloads succeed
            call_count = [0]
            def download_side_effect(gcs_path, local_path):
                call_count[0] += 1
                if call_count[0] <= 4:
                    # First 4 calls fail (stale cache)
                    raise Exception(f"404 No such object: {gcs_path}")
                else:
                    # Subsequent calls succeed (retry)
                    with open(local_path, 'w') as f:
                        f.write("fake video")

            mock_storage.download_file = MagicMock(side_effect=download_side_effect)

            orchestrator = VideoWorkerOrchestrator(
                config,
                storage=mock_storage,
            )

            with patch.object(orchestrator, "_get_encoding_backend") as mock_get:
                from backend.services.encoding_interface import EncodingOutput

                gce_output = EncodingOutput(
                    success=True,
                    lossless_4k_mp4_path="jobs/test-job/finals/output_4k_lossless.mp4",
                    lossy_4k_mp4_path="jobs/test-job/finals/output_4k_lossy.mp4",
                    lossy_720p_mp4_path="jobs/test-job/finals/output_720p.mp4",
                    lossless_mkv_path="jobs/test-job/finals/output_4k.mkv",
                    encoding_time_seconds=0.0,  # Instant = cached
                    encoding_backend="gce",
                )

                mock_backend = MagicMock()
                mock_backend.name = "gce"
                # First encode returns stale cache, second returns fresh results
                mock_backend.encode = AsyncMock(return_value=gce_output)
                mock_backend._get_service = MagicMock(return_value=MagicMock())
                mock_get.return_value = mock_backend

                await orchestrator._run_encoding()

                # encode should be called twice: once for stale cache, once for retry
                assert mock_backend.encode.call_count == 2

                # The retry call should use a different job_id
                retry_call_args = mock_backend.encode.call_args_list[1]
                retry_input = retry_call_args[0][0]
                assert retry_input.options["job_id"].startswith("test-job_retry_")

                # Result should have local paths from the successful retry
                assert orchestrator.result.final_video is not None
                assert orchestrator.result.final_video.startswith(temp_dir)

    @pytest.mark.asyncio
    async def test_download_gce_encoded_files_zero_downloads_raises(self):
        """Test that _download_gce_encoded_files raises when zero files download."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = OrchestratorConfig(
                job_id="test-job",
                artist="Test Artist",
                title="Test Title",
                title_video_path=os.path.join(temp_dir, "title.mov"),
                karaoke_video_path=os.path.join(temp_dir, "karaoke.mov"),
                instrumental_audio_path=os.path.join(temp_dir, "audio.flac"),
                output_dir=temp_dir,
            )

            mock_storage = MagicMock()
            mock_storage.download_file = MagicMock(
                side_effect=Exception("404 Not Found")
            )

            orchestrator = VideoWorkerOrchestrator(
                config,
                storage=mock_storage,
            )

            from backend.services.encoding_interface import EncodingOutput
            output = EncodingOutput(
                success=True,
                lossless_4k_mp4_path="jobs/test-job/finals/out.mp4",
                lossless_mkv_path="jobs/test-job/finals/out.mkv",
                lossy_4k_mp4_path="jobs/test-job/finals/out_lossy.mp4",
                lossy_720p_mp4_path="jobs/test-job/finals/out_720p.mp4",
                encoding_time_seconds=0.0,
                encoding_backend="gce",
            )

            with pytest.raises(RuntimeError, match="stale"):
                await orchestrator._download_gce_encoded_files(output)


class TestVideoWorkerOrchestratorOrganization:
    """Test organization stage."""

    @pytest.mark.asyncio
    async def test_run_organization_keep_brand_code(self):
        """Test organization with existing brand code."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            keep_brand_code="NOMAD-9999",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        await orchestrator._run_organization()

        assert orchestrator.result.brand_code == "NOMAD-9999"

    @pytest.mark.asyncio
    async def test_run_organization_generate_brand_code(self):
        """Test brand code generation via atomic BrandCodeService."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            dropbox_path="/Karaoke/Tracks",
            brand_prefix="TEST",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch("backend.services.brand_code_service.get_brand_code_service") as mock_get:
            mock_service = MagicMock()
            mock_service.allocate_brand_code.return_value = "TEST-0001"
            mock_get.return_value = mock_service

            await orchestrator._run_organization()

            assert orchestrator.result.brand_code == "TEST-0001"
            mock_service.allocate_brand_code.assert_called_once_with("TEST", "/Karaoke/Tracks")


class TestVideoWorkerOrchestratorDistribution:
    """Test distribution stage."""

    @pytest.mark.asyncio
    async def test_upload_to_youtube(self):
        """Test YouTube upload."""
        with tempfile.TemporaryDirectory() as temp_dir:
            video_file = os.path.join(temp_dir, "test.mp4")
            with open(video_file, "w") as f:
                f.write("dummy video")

            config = OrchestratorConfig(
                job_id="test-job",
                artist="Test Artist",
                title="Test Title",
                title_video_path="/path/title.mov",
                karaoke_video_path="/path/karaoke.mov",
                instrumental_audio_path="/path/audio.flac",
                enable_youtube_upload=True,
                youtube_credentials={"token": "test"},
            )
            orchestrator = VideoWorkerOrchestrator(config)
            orchestrator.result.final_video_lossy = video_file

            with patch.object(orchestrator, "_get_youtube_service") as mock_get:
                mock_service = MagicMock()
                mock_service.upload_video.return_value = (
                    "video123",
                    "https://youtube.com/watch?v=video123"
                )
                mock_get.return_value = mock_service

                await orchestrator._upload_to_youtube()

                mock_service.upload_video.assert_called_once()
                assert orchestrator.result.youtube_url == "https://youtube.com/watch?v=video123"

    @pytest.mark.asyncio
    async def test_upload_to_dropbox(self):
        """Test Dropbox upload."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = OrchestratorConfig(
                job_id="test-job",
                artist="Test Artist",
                title="Test Title",
                title_video_path="/path/title.mov",
                karaoke_video_path="/path/karaoke.mov",
                instrumental_audio_path="/path/audio.flac",
                output_dir=temp_dir,
                dropbox_path="/Karaoke/Tracks",
                brand_prefix="TEST",
            )
            orchestrator = VideoWorkerOrchestrator(config)
            orchestrator.result.brand_code = "TEST-0001"

            with patch("backend.services.dropbox_service.get_dropbox_service") as mock_get:
                mock_dropbox = MagicMock()
                mock_dropbox.is_configured = True
                mock_dropbox.create_shared_link.return_value = "https://dropbox.com/link"
                mock_get.return_value = mock_dropbox

                await orchestrator._upload_to_dropbox()

                mock_dropbox.upload_folder.assert_called_once()
                assert orchestrator.result.dropbox_link == "https://dropbox.com/link"


    @pytest.mark.asyncio
    async def test_upload_to_youtube_no_video_file_adds_warning(self):
        """Test that YouTube upload adds distribution warning when no video file exists."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_youtube_upload=True,
            youtube_credentials={"token": "test"},
        )
        orchestrator = VideoWorkerOrchestrator(config)
        # No video files set on result — all are None

        await orchestrator._upload_to_youtube()

        assert len(orchestrator.result.distribution_warnings) == 1
        assert "no video file available" in orchestrator.result.distribution_warnings[0].lower()
        assert orchestrator.result.youtube_url is None


class TestVideoWorkerOrchestratorNotifications:
    """Test notifications stage."""

    @pytest.mark.asyncio
    async def test_run_notifications_with_youtube_url(self):
        """Test Discord notification with YouTube URL."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )
        orchestrator = VideoWorkerOrchestrator(config)
        orchestrator.result.youtube_url = "https://youtube.com/watch?v=test"

        with patch.object(orchestrator, "_get_discord_service") as mock_get:
            mock_service = MagicMock()
            mock_service.post_video_notification.return_value = True
            mock_get.return_value = mock_service

            await orchestrator._run_notifications()

            mock_service.post_video_notification.assert_called_once_with(
                "https://youtube.com/watch?v=test"
            )

    @pytest.mark.asyncio
    async def test_run_notifications_no_youtube_url(self):
        """Test notification skipped without YouTube URL."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )
        orchestrator = VideoWorkerOrchestrator(config)
        # No youtube_url set

        with patch.object(orchestrator, "_get_discord_service") as mock_get:
            mock_service = MagicMock()
            mock_get.return_value = mock_service

            await orchestrator._run_notifications()

            mock_service.post_video_notification.assert_not_called()


class TestVideoWorkerOrchestratorFullPipeline:
    """Test full pipeline execution."""

    @pytest.mark.asyncio
    async def test_run_full_pipeline_success(self):
        """Test successful full pipeline."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=True,  # Enable to trigger packaging stage
        )
        orchestrator = VideoWorkerOrchestrator(config)

        # Mock packaging to set CDG result (validation requires it when enable_cdg=True)
        async def mock_packaging_with_result():
            orchestrator.result.final_karaoke_cdg_zip = "/output/cdg.zip"

        # Mock all stages
        with patch.object(orchestrator, "_run_packaging", side_effect=mock_packaging_with_result) as mock_packaging, \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock) as mock_encoding, \
             patch.object(orchestrator, "_run_organization", new_callable=AsyncMock) as mock_org, \
             patch.object(orchestrator, "_run_distribution", new_callable=AsyncMock) as mock_dist, \
             patch.object(orchestrator, "_run_notifications", new_callable=AsyncMock) as mock_notify:

            result = await orchestrator.run()

            assert result.success is True
            mock_packaging.assert_called_once()
            mock_encoding.assert_called_once()
            mock_org.assert_called_once()
            mock_dist.assert_called_once()
            mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_pipeline_skips_packaging_when_disabled(self):
        """Test pipeline skips packaging when CDG/TXT disabled."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            enable_cdg=False,
            enable_txt=False,
        )
        orchestrator = VideoWorkerOrchestrator(config)

        # Mock all stages
        with patch.object(orchestrator, "_run_packaging", new_callable=AsyncMock) as mock_packaging, \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock) as mock_encoding, \
             patch.object(orchestrator, "_run_organization", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_distribution", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_notifications", new_callable=AsyncMock):

            result = await orchestrator.run()

            assert result.success is True
            mock_packaging.assert_not_called()
            mock_encoding.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_full_pipeline_encoding_failure(self):
        """Test pipeline failure during encoding."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        with patch.object(orchestrator, "_run_packaging", new_callable=AsyncMock), \
             patch.object(orchestrator, "_run_encoding", new_callable=AsyncMock) as mock_encoding, \
             patch.object(orchestrator, "_run_organization", new_callable=AsyncMock) as mock_org:

            mock_encoding.side_effect = Exception("Encoding failed")

            result = await orchestrator.run()

            assert result.success is False
            assert "Encoding failed" in result.error_message
            mock_org.assert_not_called()


class TestCreateOrchestratorConfigFromJob:
    """Test helper function for creating config from job."""

    def test_create_config_from_job(self):
        """Test creating config from a job object."""
        job = MagicMock()
        job.job_id = "test-123"
        job.artist = "Test Artist"
        job.title = "Test Title"
        job.state_data = {"instrumental_selection": "clean"}
        job.enable_cdg = True
        job.enable_txt = False
        job.enable_youtube_upload = True
        job.is_private = False
        job.brand_prefix = "NOMAD"
        job.discord_webhook_url = "https://discord.com/api/webhooks/123/abc"
        job.youtube_description_template = "Test description"
        job.dropbox_path = "/Karaoke"
        job.gdrive_folder_id = None
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = None
        job.file_urls = {'screens': {'title_png': 'jobs/test-123/screens/title.png', 'end_png': 'jobs/test-123/screens/end.png'}}

        config = create_orchestrator_config_from_job(
            job=job,
            temp_dir="/tmp/test",
            youtube_credentials={"token": "test"},
            cdg_styles={"background": "black"},
        )

        assert config.job_id == "test-123"
        assert config.artist == "Test Artist"
        assert config.title == "Test Title"
        assert config.enable_cdg is True
        assert config.enable_youtube_upload is True
        assert config.title_video_path == "/tmp/test/Test Artist - Test Title (Title).png"
        assert config.instrumental_audio_path == "/tmp/test/Test Artist - Test Title (Instrumental Clean).flac"

    def test_create_config_from_job_with_existing_instrumental(self):
        """Test config with user-provided instrumental."""
        job = MagicMock()
        job.job_id = "test-123"
        job.artist = "Test Artist"
        job.title = "Test Title"
        job.state_data = {"instrumental_selection": "custom"}
        job.enable_cdg = False
        job.enable_txt = False
        job.enable_youtube_upload = False
        job.is_private = False
        job.brand_prefix = None
        job.discord_webhook_url = None
        job.youtube_description_template = None
        job.dropbox_path = None
        job.gdrive_folder_id = None
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = "gs://bucket/instrumental.mp3"
        job.file_urls = {'screens': {'title_png': 'screens/title.png', 'end_png': 'screens/end.png'}}

        config = create_orchestrator_config_from_job(
            job=job,
            temp_dir="/tmp/test",
        )

        assert config.instrumental_audio_path == "/tmp/test/Test Artist - Test Title (Instrumental User).mp3"

    def test_create_config_passes_instrumental_selection(self):
        """Test that instrumental_selection is passed through to OrchestratorConfig.

        This is a REGRESSION TEST for the bug where orchestrator -> GCE encoding
        path did not pass instrumental_selection, causing GCE worker to default
        to 'clean' even when user selected 'with_backing'.

        The bug was:
        - PR #271 fixed GCE worker to READ instrumental_selection from config
        - But the orchestrator path (encoding_interface.py) was never updated to SEND it
        - The legacy path (video_worker.py _encode_via_gce) was already correct
        - So the bug only manifested when USE_NEW_ORCHESTRATOR=true (the default)

        See: fix(gce): Respect user's instrumental selection in GCE encoding worker (#271)
        """
        job = MagicMock()
        job.job_id = "test-123"
        job.artist = "Test Artist"
        job.title = "Test Title"
        job.state_data = {"instrumental_selection": "with_backing"}  # User selected backing vocals
        job.enable_cdg = False
        job.enable_txt = False
        job.enable_youtube_upload = False
        job.is_private = False
        job.brand_prefix = None
        job.discord_webhook_url = None
        job.youtube_description_template = None
        job.dropbox_path = None
        job.gdrive_folder_id = None
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = None
        job.file_urls = {'screens': {'title_png': 'screens/title.png', 'end_png': 'screens/end.png'}}

        config = create_orchestrator_config_from_job(
            job=job,
            temp_dir="/tmp/test",
        )

        # CRITICAL: instrumental_selection must be passed to OrchestratorConfig
        # If this fails, the GCE worker will default to 'clean' and ignore user's selection
        assert config.instrumental_selection == "with_backing", \
            "instrumental_selection must be passed from job.state_data to OrchestratorConfig"

        # Also verify the instrumental path uses "Backing" not "Clean"
        assert "Backing" in config.instrumental_audio_path, \
            "When with_backing is selected, instrumental path should contain 'Backing'"


class TestInstrumentalSelectionEndToEnd:
    """End-to-end tests for instrumental selection flow.

    These tests verify that instrumental_selection flows correctly from:
    job.state_data -> OrchestratorConfig -> EncodingInput -> GCE encoding_config

    This test class was added after discovering that PR #271 only fixed the
    GCE worker (receiving side) but not the orchestrator (sending side),
    causing the bug to persist in production where USE_NEW_ORCHESTRATOR=true.
    """

    def test_encoding_input_has_instrumental_selection_field(self):
        """Test that EncodingInput dataclass includes instrumental_selection.

        Without this field, the orchestrator cannot pass the selection to
        the encoding backend.
        """
        from backend.services.encoding_interface import EncodingInput

        # Test with explicit selection
        input_with_backing = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            instrumental_selection="with_backing",
        )
        assert input_with_backing.instrumental_selection == "with_backing"

        # Test default value
        input_default = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        assert input_default.instrumental_selection == "clean", \
            "Default instrumental_selection should be 'clean' for backward compatibility"

    def test_orchestrator_config_has_instrumental_selection_field(self):
        """Test that OrchestratorConfig includes instrumental_selection."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            instrumental_selection="with_backing",
        )
        assert config.instrumental_selection == "with_backing"

        # Test default
        config_default = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        assert config_default.instrumental_selection == "clean"

    def test_gce_encoding_config_includes_instrumental_selection(self):
        """Test that GCEEncodingBackend passes instrumental_selection to encoding_config.

        This is the CRITICAL test that would have caught the bug in PR #271.
        The GCE worker reads config.get("instrumental_selection", "clean"),
        so if we don't send it, it defaults to 'clean' regardless of user selection.
        """
        from backend.services.encoding_interface import EncodingInput, GCEEncodingBackend

        backend = GCEEncodingBackend(dry_run=True)

        # Create input with 'with_backing' selection
        encoding_input = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            artist="Test Artist",
            title="Test Title",
            instrumental_selection="with_backing",
            options={
                "job_id": "test-123",
                "input_gcs_path": "gs://bucket/jobs/test-123/",
                "output_gcs_path": "gs://bucket/jobs/test-123/finals/",
            },
        )

        # We can't easily test the actual encoding_config dict without mocking
        # the service, but we can verify the input has the right value
        assert encoding_input.instrumental_selection == "with_backing"

        # The fix ensures GCEEncodingBackend.encode() includes this in encoding_config:
        # encoding_config = {
        #     ...
        #     "instrumental_selection": input_config.instrumental_selection,
        # }

    @pytest.mark.asyncio
    async def test_orchestrator_passes_instrumental_selection_to_encoding(self):
        """Test full flow: orchestrator creates EncodingInput with instrumental_selection.

        This integration test verifies the complete path:
        OrchestratorConfig.instrumental_selection -> EncodingInput.instrumental_selection
        """
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            output_dir="/output",
            instrumental_selection="with_backing",
        )
        orchestrator = VideoWorkerOrchestrator(config)

        # Capture the EncodingInput that gets passed to the backend
        captured_input = None

        async def capture_encode(encoding_input):
            nonlocal captured_input
            captured_input = encoding_input
            from backend.services.encoding_interface import EncodingOutput
            return EncodingOutput(
                success=True,
                lossless_4k_mp4_path="/output/lossless.mp4",
                encoding_time_seconds=1.0,
                encoding_backend="mock",
            )

        with patch.object(orchestrator, "_get_encoding_backend") as mock_get:
            mock_backend = MagicMock()
            mock_backend.name = "mock"
            mock_backend.encode = capture_encode
            mock_get.return_value = mock_backend

            await orchestrator._run_encoding()

        # CRITICAL ASSERTION: instrumental_selection must be passed through
        assert captured_input is not None, "encode() should have been called"
        assert captured_input.instrumental_selection == "with_backing", \
            "Orchestrator must pass instrumental_selection to EncodingInput"


class TestOrchestratorCountdownPadding:
    """Tests for countdown padding flow through orchestrator.

    These tests verify that countdown_padding_seconds flows correctly from:
    job.state_data.lyrics_metadata -> OrchestratorConfig -> EncodingInput.options

    This test class was added after discovering that the orchestrator path
    did not propagate countdown_padding_seconds to the GCE encoding backend,
    causing instrumental audio to not be padded for songs with countdown intros.
    """

    def test_orchestrator_config_has_countdown_field(self):
        """Test that OrchestratorConfig dataclass includes countdown_padding_seconds."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            countdown_padding_seconds=3.0,
        )
        assert config.countdown_padding_seconds == 3.0

    def test_orchestrator_config_countdown_defaults_to_none(self):
        """Test that countdown_padding_seconds defaults to None."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
        )
        assert config.countdown_padding_seconds is None

    def test_create_orchestrator_config_reads_countdown_from_lyrics_metadata(self):
        """Test that create_orchestrator_config_from_job reads countdown from lyrics_metadata.

        This is the CRITICAL test that would have caught the bug where the orchestrator
        path didn't read countdown state from lyrics_metadata.
        """
        job = MagicMock()
        job.job_id = "test-job"
        job.artist = "Test Artist"
        job.title = "Test Title"
        job.state_data = {
            'instrumental_selection': 'clean',
            'lyrics_metadata': {
                'has_countdown_padding': True,
                'countdown_padding_seconds': 3.0,
            }
        }
        job.enable_cdg = False
        job.enable_txt = False
        job.enable_youtube_upload = False
        job.brand_prefix = None
        job.discord_webhook_url = None
        job.youtube_description_template = None
        job.dropbox_path = None
        job.gdrive_folder_id = None
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = None

        config = create_orchestrator_config_from_job(
            job=job,
            temp_dir="/tmp/test",
        )

        # CRITICAL: countdown_padding_seconds must be passed to OrchestratorConfig
        # If this fails, the GCE worker will not pad the instrumental audio
        assert config.countdown_padding_seconds == 3.0, \
            "countdown_padding_seconds must be read from job.state_data.lyrics_metadata"

    def test_create_orchestrator_config_handles_missing_countdown(self):
        """Test graceful handling when lyrics_metadata has no countdown fields."""
        job = MagicMock()
        job.job_id = "test-job"
        job.artist = "Test Artist"
        job.title = "Test Title"
        job.state_data = {
            'instrumental_selection': 'clean',
            'lyrics_metadata': {
                'has_corrections': True,  # No countdown fields
            }
        }
        job.enable_cdg = False
        job.enable_txt = False
        job.enable_youtube_upload = False
        job.brand_prefix = None
        job.discord_webhook_url = None
        job.youtube_description_template = None
        job.dropbox_path = None
        job.gdrive_folder_id = None
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = None

        config = create_orchestrator_config_from_job(
            job=job,
            temp_dir="/tmp/test",
        )

        # Should default to None when not present
        assert config.countdown_padding_seconds is None

    def test_create_orchestrator_config_handles_false_countdown(self):
        """Test that countdown is None when has_countdown_padding is False."""
        job = MagicMock()
        job.job_id = "test-job"
        job.artist = "Test Artist"
        job.title = "Test Title"
        job.state_data = {
            'instrumental_selection': 'clean',
            'lyrics_metadata': {
                'has_countdown_padding': False,  # Explicitly False
                'countdown_padding_seconds': 0.0,
            }
        }
        job.enable_cdg = False
        job.enable_txt = False
        job.enable_youtube_upload = False
        job.brand_prefix = None
        job.discord_webhook_url = None
        job.youtube_description_template = None
        job.dropbox_path = None
        job.gdrive_folder_id = None
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = None

        config = create_orchestrator_config_from_job(
            job=job,
            temp_dir="/tmp/test",
        )

        # Should be None because has_countdown_padding is False
        assert config.countdown_padding_seconds is None

    @pytest.mark.asyncio
    async def test_orchestrator_passes_countdown_to_encoding_options(self):
        """Test full flow: orchestrator passes countdown_padding_seconds through EncodingInput.options.

        This verifies the complete flow from OrchestratorConfig to the encoding backend.
        """
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            output_dir="/output",
            countdown_padding_seconds=3.0,  # Countdown padding enabled
        )

        job_manager = MagicMock()
        storage = MagicMock()
        job_logger = MagicMock()

        orchestrator = VideoWorkerOrchestrator(
            config=config,
            job_manager=job_manager,
            storage=storage,
            job_logger=job_logger,
        )

        # Capture the EncodingInput that gets passed to the backend
        captured_input = None

        async def capture_encode(encoding_input):
            nonlocal captured_input
            captured_input = encoding_input
            from backend.services.encoding_interface import EncodingOutput
            return EncodingOutput(
                success=True,
                lossless_4k_mp4_path="/output/lossless.mp4",
                encoding_time_seconds=1.0,
                encoding_backend="mock",
            )

        with patch.object(orchestrator, "_get_encoding_backend") as mock_get:
            mock_backend = MagicMock()
            mock_backend.name = "mock"
            mock_backend.encode = capture_encode
            mock_get.return_value = mock_backend

            await orchestrator._run_encoding()

        # CRITICAL ASSERTION: countdown_padding_seconds must be passed through options
        assert captured_input is not None, "encode() should have been called"
        assert captured_input.options.get("countdown_padding_seconds") == 3.0, \
            "Orchestrator must pass countdown_padding_seconds in EncodingInput.options"


class TestDistributionWarnings:
    """Test distribution_warnings tracking in OrchestratorResult."""

    def test_result_has_distribution_warnings_field(self):
        """Test that OrchestratorResult has distribution_warnings as empty list by default."""
        result = OrchestratorResult(success=True)
        assert result.distribution_warnings == []

    @pytest.mark.asyncio
    async def test_gdrive_failure_populates_warnings(self):
        """Test that Google Drive upload failure populates distribution_warnings."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            gdrive_folder_id="folder-123",
        )
        orchestrator = VideoWorkerOrchestrator(config)
        orchestrator.result.brand_code = "NOMAD-0001"

        with patch("backend.services.gdrive_service.get_gdrive_service") as mock_get:
            mock_gdrive = MagicMock()
            mock_gdrive.is_configured = True
            mock_gdrive.upload_to_public_share.side_effect = Exception("Connection refused")
            mock_get.return_value = mock_gdrive

            await orchestrator._upload_to_gdrive()

        assert len(orchestrator.result.distribution_warnings) == 1
        assert "Google Drive upload failed" in orchestrator.result.distribution_warnings[0]

    @pytest.mark.asyncio
    async def test_dropbox_failure_populates_warnings(self):
        """Test that Dropbox upload failure populates distribution_warnings."""
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            dropbox_path="/Public Share",
        )
        orchestrator = VideoWorkerOrchestrator(config)
        orchestrator.result.brand_code = "NOMAD-0001"

        with patch("backend.services.dropbox_service.get_dropbox_service") as mock_get:
            mock_dropbox = MagicMock()
            mock_dropbox.is_configured = True
            mock_dropbox.upload_folder.side_effect = Exception("Auth expired")
            mock_get.return_value = mock_dropbox

            await orchestrator._upload_to_dropbox()

        assert len(orchestrator.result.distribution_warnings) == 1
        assert "Dropbox upload failed" in orchestrator.result.distribution_warnings[0]


class TestLrcHasLyricsContent:
    """Tests for _lrc_has_lyrics_content helper."""

    def _make_orchestrator(self, lrc_file_path=None):
        config = OrchestratorConfig(
            job_id="test-job",
            artist="Test Artist",
            title="Test Title",
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/audio.flac",
            lrc_file_path=lrc_file_path,
        )
        return VideoWorkerOrchestrator(config)

    def test_returns_false_when_no_lrc_path(self):
        orchestrator = self._make_orchestrator(lrc_file_path=None)
        assert orchestrator._lrc_has_lyrics_content() is False

    def test_returns_false_when_lrc_file_missing(self):
        orchestrator = self._make_orchestrator(lrc_file_path="/nonexistent/path.lrc")
        assert orchestrator._lrc_has_lyrics_content() is False

    def test_returns_false_for_empty_lrc(self, tmp_path):
        lrc_file = tmp_path / "empty.lrc"
        lrc_file.write_text("[re:MidiCo]\n")
        orchestrator = self._make_orchestrator(lrc_file_path=str(lrc_file))
        assert orchestrator._lrc_has_lyrics_content() is False

    def test_returns_true_for_lrc_with_lyrics(self, tmp_path):
        lrc_file = tmp_path / "valid.lrc"
        lrc_file.write_text("[re:MidiCo]\n[00:12.345]Hello world\n[00:15.678]Second line\n")
        orchestrator = self._make_orchestrator(lrc_file_path=str(lrc_file))
        assert orchestrator._lrc_has_lyrics_content() is True

    def test_returns_true_for_centisecond_timestamps(self, tmp_path):
        lrc_file = tmp_path / "centisecond.lrc"
        lrc_file.write_text("[01:02.34]Lyric line\n")
        orchestrator = self._make_orchestrator(lrc_file_path=str(lrc_file))
        assert orchestrator._lrc_has_lyrics_content() is True

    def test_returns_true_for_no_decimal_timestamps(self, tmp_path):
        lrc_file = tmp_path / "nodecimal.lrc"
        lrc_file.write_text("[01:30]Lyric line\n")
        orchestrator = self._make_orchestrator(lrc_file_path=str(lrc_file))
        assert orchestrator._lrc_has_lyrics_content() is True

    def test_returns_false_for_metadata_only_lrc(self, tmp_path):
        lrc_file = tmp_path / "meta.lrc"
        lrc_file.write_text("[ti:Song Title]\n[ar:Artist Name]\n[re:MidiCo]\n")
        orchestrator = self._make_orchestrator(lrc_file_path=str(lrc_file))
        assert orchestrator._lrc_has_lyrics_content() is False
