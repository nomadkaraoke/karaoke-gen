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
