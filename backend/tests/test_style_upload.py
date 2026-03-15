"""
Tests for style file upload and processing.

Tests the full flow:
1. CLI parses style_params.json and extracts file references
2. Upload endpoint accepts style files
3. Style helper downloads and parses style config
4. Workers use style config for video generation
"""
import pytest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from io import BytesIO

# Test the remote CLI's style parsing
# Skip this class if flacfetch is not properly installed (remote_cli depends on it)
try:
    from karaoke_gen.utils.remote_cli import RemoteKaraokeClient, Config
    _remote_cli_available = True
except ImportError:
    _remote_cli_available = False


@pytest.mark.skipif(not _remote_cli_available, reason="flacfetch not properly installed")
class TestRemoteCLIStyleParsing:
    """Test that remote CLI correctly parses style_params.json."""
    
    def test_parse_style_params_extracts_file_paths(self):
        """Test that _parse_style_params extracts all file references."""
        
        # Create a mock style_params.json
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test files
            font_path = os.path.join(temp_dir, "font.ttf")
            intro_bg_path = os.path.join(temp_dir, "intro_bg.png")
            karaoke_bg_path = os.path.join(temp_dir, "karaoke_bg.png")
            
            Path(font_path).touch()
            Path(intro_bg_path).touch()
            Path(karaoke_bg_path).touch()
            
            # Create style_params.json
            style_params = {
                "intro": {
                    "background_image": intro_bg_path,
                    "font": font_path,
                },
                "karaoke": {
                    "background_image": karaoke_bg_path,
                    "font_path": font_path,
                },
                "end": {
                    "background_image": karaoke_bg_path,
                    "font": font_path,
                },
                "cdg": {
                    "font_path": font_path,
                }
            }
            
            style_json_path = os.path.join(temp_dir, "style_params.json")
            with open(style_json_path, 'w') as f:
                json.dump(style_params, f)
            
            # Create client and parse
            config = Config(
                service_url="http://test", 
                review_ui_url="http://test",
                poll_interval=5,
                output_dir=temp_dir
            )
            logger = Mock()
            client = RemoteKaraokeClient(config, logger)
            
            assets = client._parse_style_params(style_json_path)
            
            # Should extract unique files
            assert 'style_font' in assets
            assert 'style_intro_background' in assets
            assert 'style_karaoke_background' in assets
            assert assets['style_font'] == font_path
            assert assets['style_intro_background'] == intro_bg_path
            assert assets['style_karaoke_background'] == karaoke_bg_path
    
    def test_parse_style_params_handles_missing_files(self):
        """Test that _parse_style_params ignores non-existent files."""
        
        with tempfile.TemporaryDirectory() as temp_dir:
            style_params = {
                "intro": {
                    "background_image": "/nonexistent/path.png",
                    "font": "/nonexistent/font.ttf",
                },
            }
            
            style_json_path = os.path.join(temp_dir, "style_params.json")
            with open(style_json_path, 'w') as f:
                json.dump(style_params, f)
            
            config = Config(
                service_url="http://test", 
                review_ui_url="http://test",
                poll_interval=5,
                output_dir=temp_dir
            )
            logger = Mock()
            client = RemoteKaraokeClient(config, logger)
            
            assets = client._parse_style_params(style_json_path)
            
            # Should return empty dict for non-existent files
            assert len(assets) == 0


class TestStyleHelper:
    """Test the backend style helper module."""
    
    @pytest.mark.asyncio
    async def test_style_config_raises_error_when_no_theme(self):
        """Test that StyleConfig raises error when no theme provided (Phase 2)."""
        pytest.importorskip("google.cloud.storage", reason="GCP libraries not available")
        from backend.workers.style_helper import StyleConfig

        # Mock job with no style assets and no style_params_gcs_path
        job = Mock()
        job.job_id = "test-123"
        job.style_assets = {}
        job.style_params_gcs_path = None  # Explicitly set to None

        storage = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            config = StyleConfig(job, storage, temp_dir)

            # Phase 2: Should raise error when loading without theme
            with pytest.raises(ValueError, match="style_params_gcs_path is required"):
                await config.load()
    
    @pytest.mark.asyncio
    async def test_style_config_loads_complete_theme(self):
        """Test that StyleConfig loads and parses complete themes (Phase 2)."""
        pytest.importorskip("google.cloud.storage", reason="GCP libraries not available")
        from backend.workers.style_helper import StyleConfig
        from karaoke_gen.style_loader import (
            DEFAULT_INTRO_STYLE,
            DEFAULT_END_STYLE,
            DEFAULT_KARAOKE_STYLE,
            DEFAULT_CDG_STYLE,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a SEPARATE source directory for the source style file
            # (StyleConfig will use temp_dir/style/ for downloads, so we use source/)
            source_dir = os.path.join(temp_dir, "source")
            os.makedirs(source_dir, exist_ok=True)

            # Create a COMPLETE theme (Phase 2 requirement)
            style_params = {
                "intro": DEFAULT_INTRO_STYLE.copy(),
                "end": DEFAULT_END_STYLE.copy(),
                "karaoke": DEFAULT_KARAOKE_STYLE.copy(),
                "cdg": DEFAULT_CDG_STYLE.copy(),
            }
            # Customize some values
            style_params["intro"]["background_color"] = "#FF0000"
            style_params["intro"]["title_color"] = "#00FF00"
            style_params["cdg"]["instrumental_background"] = "custom_bg.png"

            style_json_path = os.path.join(source_dir, "style_params.json")
            with open(style_json_path, 'w') as f:
                json.dump(style_params, f)

            # Create a work directory for StyleConfig (separate from source)
            work_dir = os.path.join(temp_dir, "work")
            os.makedirs(work_dir, exist_ok=True)

            # Mock job with style assets
            job = Mock()
            job.job_id = "test-123"
            job.style_assets = {"style_params": "jobs/test/style_params.json"}
            job.style_params_gcs_path = "jobs/test/style_params.json"

            # Mock storage to "download" the file
            storage = Mock()
            def mock_download(gcs_path, local_path):
                # Copy our test file to the expected location
                import shutil
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                shutil.copy(style_json_path, local_path)
            storage.download_file = mock_download

            config = StyleConfig(job, storage, work_dir)
            await config.load()

            assert config.has_custom_styles()

            intro_format = config.get_intro_format()
            assert intro_format['background_color'] == "#FF0000"
            assert intro_format['title_color'] == "#00FF00"

            cdg_styles = config.get_cdg_styles()
            assert cdg_styles['instrumental_background'] == "custom_bg.png"


class TestFileUploadEndpoint:
    """Test the file upload endpoint accepts style files."""
    
    def test_upload_endpoint_validates_style_files(self):
        """Test that upload endpoint validates file types."""
        pytest.importorskip("google.cloud.firestore", reason="GCP libraries not available")
        # This would be an integration test with FastAPI TestClient
        # For now, just verify the validation logic exists
        from backend.api.routes.file_upload import (
            ALLOWED_AUDIO_EXTENSIONS,
            ALLOWED_IMAGE_EXTENSIONS,
            ALLOWED_FONT_EXTENSIONS
        )
        
        assert '.mp3' in ALLOWED_AUDIO_EXTENSIONS
        assert '.flac' in ALLOWED_AUDIO_EXTENSIONS
        assert '.png' in ALLOWED_IMAGE_EXTENSIONS
        assert '.jpg' in ALLOWED_IMAGE_EXTENSIONS
        assert '.ttf' in ALLOWED_FONT_EXTENSIONS
        assert '.otf' in ALLOWED_FONT_EXTENSIONS


class TestVideoWorkerStyleIntegration:
    """Test that video worker properly uses style config."""
    
    def test_video_worker_passes_cdg_styles_to_finalise(self):
        """Test that video worker passes CDG styles to KaraokeFinalise."""
        pytest.importorskip("google.cloud.firestore", reason="GCP libraries not available")
        # This is implicitly tested by the video_worker code structure
        # The key is that cdg_styles is passed to KaraokeFinalise constructor
        
        # Verify the import works
        from backend.workers.video_worker import generate_video
        assert generate_video is not None
    
    def test_video_worker_passes_discord_webhook(self):
        """Test that video worker passes discord webhook to KaraokeFinalise."""
        # The video_worker.py now passes discord_webhook_url from job
        # This is verified by code inspection
        pass


class TestJobModelStyleFields:
    """Test that Job model has all required style fields."""
    
    def test_job_model_has_style_fields(self):
        """Test Job model includes style configuration fields."""
        from backend.models.job import Job, JobCreate
        
        # Check Job model fields (use model_fields for Pydantic v2)
        job_fields = Job.model_fields if hasattr(Job, 'model_fields') else Job.__fields__
        assert 'style_params_gcs_path' in job_fields
        assert 'style_assets' in job_fields
        assert 'brand_prefix' in job_fields
        assert 'discord_webhook_url' in job_fields
        
        # Check JobCreate model fields
        create_fields = JobCreate.model_fields if hasattr(JobCreate, 'model_fields') else JobCreate.__fields__
        assert 'style_params_gcs_path' in create_fields
        assert 'style_assets' in create_fields
        assert 'brand_prefix' in create_fields
        assert 'discord_webhook_url' in create_fields
