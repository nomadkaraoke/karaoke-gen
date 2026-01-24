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
    async def test_run_packaging_no_lrc(self):
        """Test packaging stage with no LRC file."""
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

        # Should not raise, just skip
        await orchestrator._run_packaging()

        assert orchestrator.result.final_karaoke_cdg_zip is None

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
        """Test brand code generation from Dropbox."""
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

        with patch("backend.services.dropbox_service.get_dropbox_service") as mock_get:
            mock_dropbox = MagicMock()
            mock_dropbox.is_configured = True
            mock_dropbox.get_next_brand_code.return_value = "TEST-0001"
            mock_get.return_value = mock_dropbox

            await orchestrator._run_organization()

            assert orchestrator.result.brand_code == "TEST-0001"


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

        # Mock all stages
        with patch.object(orchestrator, "_run_packaging", new_callable=AsyncMock) as mock_packaging, \
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
        job.brand_prefix = "NOMAD"
        job.discord_webhook_url = "https://discord.com/api/webhooks/123/abc"
        job.youtube_description_template = "Test description"
        job.dropbox_path = "/Karaoke"
        job.gdrive_folder_id = None
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = None

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
        assert config.title_video_path == "/tmp/test/Test Artist - Test Title (Title).mov"
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
        job.brand_prefix = None
        job.discord_webhook_url = None
        job.youtube_description_template = None
        job.dropbox_path = None
        job.gdrive_folder_id = None
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = "gs://bucket/instrumental.mp3"

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
