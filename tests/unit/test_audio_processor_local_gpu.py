import os
import logging
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from backend.workers.audio_worker import create_audio_processor, process_audio_separation
from karaoke_gen.audio_processor import AudioProcessor


class TestCreateAudioProcessorLocalGPU:
    """Tests for create_audio_processor with local GPU (model_file_dir set)."""

    def test_model_file_dir_passed_through(self):
        """When model_file_dir is provided, AudioProcessor gets it for local separation."""
        processor = create_audio_processor(
            temp_dir="/tmp/test",
            model_file_dir="/models",
        )
        assert processor.model_file_dir == "/models"

    def test_model_file_dir_none_by_default(self):
        """When model_file_dir is not provided, AudioProcessor gets None (remote mode)."""
        processor = create_audio_processor(
            temp_dir="/tmp/test",
        )
        assert processor.model_file_dir is None

    def test_presets_set_by_default(self):
        """Ensemble presets are always set on the processor."""
        processor = create_audio_processor(
            temp_dir="/tmp/test",
            model_file_dir="/models",
        )
        assert processor.instrumental_preset == "instrumental_clean"
        assert processor.karaoke_preset == "karaoke"

    def test_custom_presets_override_defaults(self):
        """Custom presets override the defaults."""
        processor = create_audio_processor(
            temp_dir="/tmp/test",
            model_file_dir="/models",
            instrumental_preset="instrumental_full",
            karaoke_preset="vocal_clean",
        )
        assert processor.instrumental_preset == "instrumental_full"
        assert processor.karaoke_preset == "vocal_clean"


class TestProcessAudioSeparationMode:
    """Tests for separation mode selection (remote API vs local GPU)."""

    @pytest.mark.asyncio
    @patch("backend.workers.audio_worker.worker_registry")
    @patch("backend.workers.audio_worker.setup_job_logging")
    @patch("backend.workers.audio_worker.create_job_logger")
    @patch("backend.workers.audio_worker.JobManager")
    @patch("backend.workers.audio_worker.StorageService")
    @patch("backend.workers.audio_worker.get_settings")
    @patch("backend.workers.audio_worker.validate_worker_can_run", return_value=None)
    @patch("backend.workers.audio_worker.download_audio")
    @patch("backend.workers.audio_worker._store_audio_source_metadata")
    @patch("backend.workers.audio_worker.create_audio_processor")
    @patch("backend.workers.audio_worker.upload_separation_results", new_callable=AsyncMock)
    async def test_model_dir_enables_local_gpu(
        self,
        mock_upload,
        mock_create_processor,
        mock_store_metadata,
        mock_download,
        mock_validate,
        mock_settings,
        mock_storage,
        mock_job_manager_cls,
        mock_create_logger,
        mock_setup_logging,
        mock_registry,
    ):
        """When MODEL_DIR is set and AUDIO_SEPARATOR_API_URL is not, uses local GPU."""
        # Make async methods on the registry work properly
        mock_registry.register = AsyncMock()
        mock_registry.unregister = AsyncMock()

        # Set up environment
        env = {k: v for k, v in os.environ.items()}
        env.pop("AUDIO_SEPARATOR_API_URL", None)
        env["MODEL_DIR"] = "/models"

        with patch.dict(os.environ, env, clear=True):
            # Set up mocks
            mock_job = MagicMock()
            mock_job.artist = "Test"
            mock_job.title = "Song"
            mock_job.url = None
            mock_job.input_media_gcs_path = "audio/test.wav"
            mock_job.clean_instrumental_model = None
            mock_job.backing_vocals_models = None
            mock_job.other_stems_models = None

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = mock_job
            mock_job_manager_cls.return_value = mock_jm

            mock_settings_obj = MagicMock()
            mock_settings_obj.gcs_bucket_name = "test-bucket"
            mock_settings.return_value = mock_settings_obj

            mock_download.return_value = "/tmp/test.wav"
            mock_create_logger.return_value = MagicMock()

            mock_processor = MagicMock()
            mock_processor.process_audio_separation.return_value = {
                "clean_instrumental": {"vocals": "/tmp/v.flac", "instrumental": "/tmp/i.flac"},
                "other_stems": {},
                "backing_vocals": {},
                "combined_instrumentals": {},
            }
            mock_create_processor.return_value = mock_processor

            result = await process_audio_separation("test-job-id")

            assert result is True
            # Verify model_file_dir="/models" was passed
            mock_create_processor.assert_called_once()
            call_kwargs = mock_create_processor.call_args[1]
            assert call_kwargs.get("model_file_dir") == "/models"

    @pytest.mark.asyncio
    @patch("backend.workers.audio_worker.worker_registry")
    @patch("backend.workers.audio_worker.setup_job_logging")
    @patch("backend.workers.audio_worker.create_job_logger")
    @patch("backend.workers.audio_worker.JobManager")
    @patch("backend.workers.audio_worker.StorageService")
    @patch("backend.workers.audio_worker.get_settings")
    @patch("backend.workers.audio_worker.validate_worker_can_run", return_value=None)
    async def test_no_api_url_no_model_dir_raises(
        self,
        mock_validate,
        mock_settings,
        mock_storage,
        mock_job_manager_cls,
        mock_create_logger,
        mock_setup_logging,
        mock_registry,
    ):
        """When neither AUDIO_SEPARATOR_API_URL nor MODEL_DIR is set, raises error."""
        # Make async methods on the registry work properly
        mock_registry.register = AsyncMock()
        mock_registry.unregister = AsyncMock()

        env = {k: v for k, v in os.environ.items()}
        env.pop("AUDIO_SEPARATOR_API_URL", None)
        env.pop("MODEL_DIR", None)

        with patch.dict(os.environ, env, clear=True):
            mock_job = MagicMock()
            mock_job.artist = "Test"
            mock_job.title = "Song"
            mock_job.url = None
            mock_job.input_media_gcs_path = "audio/test.wav"
            mock_job.clean_instrumental_model = None
            mock_job.backing_vocals_models = None
            mock_job.other_stems_models = None

            mock_jm = MagicMock()
            mock_jm.get_job.return_value = mock_job
            mock_job_manager_cls.return_value = mock_jm

            mock_settings_obj = MagicMock()
            mock_settings_obj.gcs_bucket_name = "test-bucket"
            mock_settings.return_value = mock_settings_obj
            mock_create_logger.return_value = MagicMock()

            # Should fail because no separation mode is configured
            result = await process_audio_separation("test-job-id")
            assert result is False


class TestLocalSeparationWithPresets:
    """Tests for local audio separation using ensemble presets."""

    def setup_method(self):
        self.processor = AudioProcessor(
            logger=logging.getLogger("test"),
            log_level=logging.DEBUG,
            log_formatter=None,
            model_file_dir="/models",
            lossless_output_format="FLAC",
            clean_instrumental_model="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
            backing_vocals_models=["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"],
            other_stems_models=[],
            ffmpeg_base_command="ffmpeg",
        )
        self.processor.instrumental_preset = "instrumental_clean"
        self.processor.karaoke_preset = "karaoke"

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.shutil")
    @patch("karaoke_gen.audio_processor.Separator")
    def test_local_separation_uses_ensemble_preset_for_stage1(self, mock_separator_cls, mock_shutil):
        """Stage 1 uses instrumental_preset on the Separator constructor."""
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)

        # Disable karaoke_preset so stage 2 is skipped, isolating stage 1
        self.processor.karaoke_preset = None
        self.processor.backing_vocals_models = []

        # Use realistic ensemble preset filenames
        stems_dir = "/tmp/stems"
        mock_sep = MagicMock()
        mock_sep.separate.return_value = [
            f"{stems_dir}/Song_(Vocals)_preset_instrumental_clean.flac",
            f"{stems_dir}/Song_(Instrumental)_preset_instrumental_clean.flac",
        ]
        mock_separator_cls.return_value = mock_sep
        mock_shutil.move.side_effect = lambda src, dst: dst

        with patch.object(self.processor, '_create_stems_directory', return_value=stems_dir):
            with patch.object(self.processor, '_normalize_audio_files'):
                with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                    self.processor.process_audio_separation(
                        "/tmp/test.wav", "Artist - Title", "/tmp/output"
                    )

        # Verify Separator was constructed with ensemble_preset="instrumental_clean"
        mock_separator_cls.assert_called_once()
        constructor_kwargs = mock_separator_cls.call_args[1]
        assert constructor_kwargs.get("ensemble_preset") == "instrumental_clean"

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.shutil")
    @patch("karaoke_gen.audio_processor.Separator")
    def test_local_separation_uses_karaoke_preset_for_stage2(self, mock_separator_cls, mock_shutil):
        """Stage 2 uses karaoke_preset on a fresh Separator for BV separation."""
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)

        stems_dir = "/tmp/stems"

        # Stage 1 output
        s1_vocals = f"{stems_dir}/Song_(Vocals)_preset_instrumental_clean.flac"
        s1_instrumental = f"{stems_dir}/Song_(Instrumental)_preset_instrumental_clean.flac"
        # Stage 2 output (uses clean-named input)
        s2_lead = f"{stems_dir}/Artist - Title (Vocals)_(Vocals)_preset_karaoke.flac"
        s2_backing = f"{stems_dir}/Artist - Title (Vocals)_(No Vocal)_preset_karaoke.flac"

        mock_sep1 = MagicMock()
        mock_sep1.separate.return_value = [s1_vocals, s1_instrumental]
        mock_sep2 = MagicMock()
        mock_sep2.separate.return_value = [s2_lead, s2_backing]
        mock_separator_cls.side_effect = [mock_sep1, mock_sep2]
        mock_shutil.move.side_effect = lambda src, dst: dst
        mock_shutil.copy2 = MagicMock()

        with patch.object(self.processor, '_create_stems_directory', return_value=stems_dir):
            with patch.object(self.processor, '_normalize_audio_files'):
                with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                    self.processor.process_audio_separation(
                        "/tmp/test.wav", "Artist - Title", "/tmp/output"
                    )

        # Separator should be instantiated twice: once for stage 1, once for stage 2
        assert mock_separator_cls.call_count == 2
        stage2_kwargs = mock_separator_cls.call_args_list[1][1]
        assert stage2_kwargs.get("ensemble_preset") == "karaoke"


class TestEnsembleStemTagParsing:
    """Tests for stem tag extraction from ensemble preset output filenames.

    Ensemble presets produce files named: {base}_(StemTag)_preset_{name}.flac
    The parser must identify the StemTag (between _( and )_) and NOT be confused
    by the preset name (e.g., "instrumental_clean" contains "instrumental").
    """

    def setup_method(self):
        self.processor = AudioProcessor(
            logger=logging.getLogger("test"),
            log_level=logging.DEBUG,
            log_formatter=None,
            model_file_dir="/models",
            lossless_output_format="FLAC",
            clean_instrumental_model="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
            backing_vocals_models=["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"],
            other_stems_models=[],
            ffmpeg_base_command="ffmpeg",
        )
        self.processor.instrumental_preset = "instrumental_clean"
        self.processor.karaoke_preset = "karaoke"

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.shutil")
    @patch("karaoke_gen.audio_processor.Separator")
    def test_stage1_instrumental_clean_preset_classifies_vocals_correctly(
        self, mock_separator_cls, mock_shutil
    ):
        """Regression: preset name 'instrumental_clean' must not cause Vocals file
        to be classified as instrumental (the old substring match bug)."""
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)

        # Disable stage 2 to isolate stage 1
        self.processor.karaoke_preset = None
        self.processor.backing_vocals_models = []

        # Realistic filenames from instrumental_clean ensemble preset
        stems_dir = "/tmp/stems"
        vocals_file = f"{stems_dir}/Song_(Vocals)_preset_instrumental_clean.flac"
        instrumental_file = f"{stems_dir}/Song_(Instrumental)_preset_instrumental_clean.flac"

        mock_sep = MagicMock()
        mock_sep.separate.return_value = [vocals_file, instrumental_file]
        mock_separator_cls.return_value = mock_sep

        # shutil.move returns the destination path
        mock_shutil.move.side_effect = lambda src, dst: dst

        with patch.object(self.processor, '_create_stems_directory', return_value=stems_dir):
            with patch.object(self.processor, '_normalize_audio_files'):
                with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                    result = self.processor.process_audio_separation(
                        "/tmp/test.wav", "Artist - Song", "/tmp/output"
                    )

        # Vocals must be set (this is what the bug prevented)
        assert result["clean_instrumental"]["vocals"] == vocals_file
        # Instrumental must also be set
        assert result["clean_instrumental"]["instrumental"] is not None

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.shutil")
    @patch("karaoke_gen.audio_processor.Separator")
    def test_stage1_vocals_path_enables_stage2(self, mock_separator_cls, mock_shutil):
        """When Stage 1 correctly identifies vocals, Stage 2 runs and produces
        backing vocals + lead vocals stems."""
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)

        stems_dir = "/tmp/stems"

        # Stage 1 output (instrumental_clean preset)
        s1_vocals = f"{stems_dir}/Song_(Vocals)_preset_instrumental_clean.flac"
        s1_instrumental = f"{stems_dir}/Song_(Instrumental)_preset_instrumental_clean.flac"

        # Stage 2 output uses the RENAMED clean input, so filenames don't have
        # nested stem tags (this is the fix for the regex collision bug)
        s2_lead = f"{stems_dir}/Artist - Song (Vocals)_(Vocals)_preset_karaoke.flac"
        s2_backing = f"{stems_dir}/Artist - Song (Vocals)_(No Vocal)_preset_karaoke.flac"

        mock_sep1 = MagicMock()
        mock_sep1.separate.return_value = [s1_vocals, s1_instrumental]
        mock_sep2 = MagicMock()
        mock_sep2.separate.return_value = [s2_lead, s2_backing]
        mock_separator_cls.side_effect = [mock_sep1, mock_sep2]

        mock_shutil.move.side_effect = lambda src, dst: dst
        mock_shutil.copy2 = MagicMock()  # For the vocals rename

        with patch.object(self.processor, '_create_stems_directory', return_value=stems_dir):
            with patch.object(self.processor, '_normalize_audio_files'):
                with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                    result = self.processor.process_audio_separation(
                        "/tmp/test.wav", "Artist - Song", "/tmp/output"
                    )

        # Stage 2 should have run (Separator instantiated twice)
        assert mock_separator_cls.call_count == 2

        # Vocals file should have been copied to a clean name for Stage 2
        mock_shutil.copy2.assert_called_once()
        copy_dest = mock_shutil.copy2.call_args[0][1]
        assert "_(Vocals)_preset_instrumental_clean" not in copy_dest
        assert "Artist - Song (Vocals).flac" in copy_dest

        # Stage 2 separator should receive the clean-named file
        stage2_input = mock_sep2.separate.call_args[0][0]
        assert "_(Vocals)_preset_instrumental_clean" not in stage2_input

        # Backing vocals correctly classified
        bv = result["backing_vocals"]["karaoke"]
        assert bv["lead_vocals"] == s2_lead
        assert bv["backing_vocals"] == s2_backing

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.shutil")
    @patch("karaoke_gen.audio_processor.Separator")
    def test_stage1_no_vocal_tag_recognized(self, mock_separator_cls, mock_shutil):
        """Files with '(No Vocal)' stem tag are correctly classified as instrumental."""
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)
        self.processor.karaoke_preset = None
        self.processor.backing_vocals_models = []

        stems_dir = "/tmp/stems"
        vocals_file = f"{stems_dir}/Song_(Vocals)_preset_instrumental_clean.flac"
        no_vocal_file = f"{stems_dir}/Song_(No Vocal)_preset_instrumental_clean.flac"

        mock_sep = MagicMock()
        mock_sep.separate.return_value = [vocals_file, no_vocal_file]
        mock_separator_cls.return_value = mock_sep
        mock_shutil.move.side_effect = lambda src, dst: dst

        with patch.object(self.processor, '_create_stems_directory', return_value=stems_dir):
            with patch.object(self.processor, '_normalize_audio_files'):
                with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                    result = self.processor.process_audio_separation(
                        "/tmp/test.wav", "Artist - Song", "/tmp/output"
                    )

        assert result["clean_instrumental"]["vocals"] == vocals_file
        assert result["clean_instrumental"]["instrumental"] is not None

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.shutil")
    @patch("karaoke_gen.audio_processor.Separator")
    def test_unrecognized_stem_tag_logs_warning(self, mock_separator_cls, mock_shutil):
        """Files with unrecognized stem tags produce a warning log."""
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)
        self.processor.karaoke_preset = None
        self.processor.backing_vocals_models = []

        stems_dir = "/tmp/stems"
        weird_file = f"{stems_dir}/Song_(Drums)_preset_instrumental_clean.flac"

        mock_sep = MagicMock()
        mock_sep.separate.return_value = [weird_file]
        mock_separator_cls.return_value = mock_sep

        with patch.object(self.processor, '_create_stems_directory', return_value=stems_dir):
            with patch.object(self.processor, '_normalize_audio_files'):
                with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                    with patch.object(self.processor.logger, 'warning') as mock_warn:
                        result = self.processor.process_audio_separation(
                            "/tmp/test.wav", "Artist - Song", "/tmp/output"
                        )

        mock_warn.assert_called_once()
        assert "drums" in mock_warn.call_args[0][0].lower()

    @patch.dict(os.environ, {}, clear=False)
    @patch("karaoke_gen.audio_processor.shutil")
    @patch("karaoke_gen.audio_processor.Separator")
    def test_filename_without_stem_tag_pattern_logs_warning(self, mock_separator_cls, mock_shutil):
        """Files missing the _(Tag)_ pattern produce a warning."""
        os.environ.pop("AUDIO_SEPARATOR_API_URL", None)
        self.processor.karaoke_preset = None
        self.processor.backing_vocals_models = []

        stems_dir = "/tmp/stems"
        plain_file = f"{stems_dir}/vocals.flac"

        mock_sep = MagicMock()
        mock_sep.separate.return_value = [plain_file]
        mock_separator_cls.return_value = mock_sep

        with patch.object(self.processor, '_create_stems_directory', return_value=stems_dir):
            with patch.object(self.processor, '_normalize_audio_files'):
                with patch.object(self.processor, '_generate_combined_instrumentals', return_value={}):
                    with patch.object(self.processor.logger, 'warning') as mock_warn:
                        result = self.processor.process_audio_separation(
                            "/tmp/test.wav", "Artist - Song", "/tmp/output"
                        )

        mock_warn.assert_called_once()
        # Should not have classified anything
        assert result["clean_instrumental"].get("vocals") is None
        assert result["clean_instrumental"].get("instrumental") is None
