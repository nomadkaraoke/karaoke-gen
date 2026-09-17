"""
Tests for pipeline stages.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestSeparationStage:
    """Tests for SeparationStage."""

    def test_stage_imports(self):
        """Test that the stage can be imported."""
        from karaoke_gen.pipeline.stages.separation import SeparationStage
        assert SeparationStage is not None

    def test_stage_name(self):
        """Test the stage name."""
        from karaoke_gen.pipeline.stages.separation import SeparationStage
        
        stage = SeparationStage()
        assert stage.name == "separation"

    def test_stage_required_inputs(self):
        """Test that separation has no required inputs."""
        from karaoke_gen.pipeline.stages.separation import SeparationStage
        
        stage = SeparationStage()
        assert stage.required_inputs == []

    def test_stage_output_keys(self):
        """Test the declared output keys."""
        from karaoke_gen.pipeline.stages.separation import SeparationStage
        
        stage = SeparationStage()
        assert "clean_instrumental" in stage.output_keys
        assert "other_stems" in stage.output_keys
        assert "backing_vocals" in stage.output_keys
        assert "combined_instrumentals" in stage.output_keys

    def test_stage_init_with_custom_params(self):
        """Test initializing with custom parameters."""
        from karaoke_gen.pipeline.stages.separation import SeparationStage
        
        stage = SeparationStage(
            model_file_dir="/custom/models",
            lossless_output_format="wav",
            skip_separation=True,
        )
        
        assert stage.model_file_dir == "/custom/models"
        assert stage.lossless_output_format == "wav"
        assert stage.skip_separation is True

    @pytest.mark.asyncio
    async def test_execute_skip_separation(self):
        """Test execute with skip_separation=True."""
        from karaoke_gen.pipeline.stages.separation import SeparationStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = SeparationStage(skip_separation=True)
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
        )
        
        result = await stage.execute(context)
        assert result.status == StageStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_execute_calls_audio_processor(self):
        """Test execute calls AudioProcessor."""
        from karaoke_gen.pipeline.stages.separation import SeparationStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = SeparationStage(skip_separation=False)
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
        )
        
        mock_processor = MagicMock()
        mock_processor.process_audio_separation.return_value = {
            "clean_instrumental": {"instrumental": "/path.flac"},
        }
        
        with patch('karaoke_gen.audio_processor.AudioProcessor', return_value=mock_processor):
            result = await stage.execute(context)
        
        assert result.status == StageStatus.COMPLETED
        mock_processor.process_audio_separation.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_handles_exception(self):
        """Test execute handles exceptions gracefully."""
        from karaoke_gen.pipeline.stages.separation import SeparationStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = SeparationStage(skip_separation=False)
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
        )
        
        with patch('karaoke_gen.audio_processor.AudioProcessor', side_effect=Exception("Test error")):
            result = await stage.execute(context)
        
        assert result.status == StageStatus.FAILED
        assert "Test error" in result.error_message


class TestTranscriptionStage:
    """Tests for TranscriptionStage."""

    def test_stage_imports(self):
        """Test that the stage can be imported."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        assert TranscriptionStage is not None

    def test_stage_name(self):
        """Test the stage name."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        
        stage = TranscriptionStage()
        assert stage.name == "transcription"

    def test_stage_required_inputs(self):
        """Test that transcription has no required inputs from other stages."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        
        stage = TranscriptionStage()
        assert stage.required_inputs == []

    def test_stage_optional_inputs(self):
        """Test optional inputs."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        
        stage = TranscriptionStage()
        assert "separation" in stage.optional_inputs

    def test_stage_output_keys(self):
        """Test the declared output keys."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        
        stage = TranscriptionStage()
        assert "lrc_filepath" in stage.output_keys
        assert "ass_filepath" in stage.output_keys

    def test_stage_init_with_params(self):
        """Test initializing with custom parameters."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        
        stage = TranscriptionStage(
            lyrics_file="/path/to/lyrics.txt",
            skip_transcription=True,
            skip_transcription_review=True,
            subtitle_offset_ms=100,
        )
        
        assert stage.lyrics_file == "/path/to/lyrics.txt"
        assert stage.skip_transcription is True
        assert stage.skip_transcription_review is True
        assert stage.subtitle_offset_ms == 100

    @pytest.mark.asyncio
    async def test_execute_skip_transcription(self):
        """Test execute with skip_transcription=True."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = TranscriptionStage(skip_transcription=True)
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
        )
        
        result = await stage.execute(context)
        assert result.status == StageStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_execute_handles_exception(self):
        """Test execute handles exceptions gracefully."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = TranscriptionStage(skip_transcription=False)
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
        )
        
        with patch('karaoke_gen.lyrics_processor.LyricsProcessor', side_effect=Exception("Test error")):
            result = await stage.execute(context)

        assert result.status == StageStatus.FAILED
        assert "Test error" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_happy_path_builds_outputs(self, tmp_path):
        """Full execute: transcribe, collect file outputs, parse countdown padding."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus

        output_dir = tmp_path / "out"
        lyrics_dir = output_dir / "lyrics"
        lyrics_dir.mkdir(parents=True)
        # A countdown padding file the stage should read back as a float.
        (lyrics_dir / "countdown_padding_seconds.txt").write_text("3.0\n")

        stage = TranscriptionStage(skip_transcription=False)
        # style_params (no style_params_json) -> exercises the temp-file write branch.
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir=str(output_dir),
            style_params={"foo": "bar"},
        )

        mock_processor = MagicMock()
        mock_processor.transcribe_lyrics.return_value = {
            "lrc_filepath": "/out/lyrics.lrc",
            "ass_filepath": "/out/lyrics.ass",
            "corrected_txt_path": "/out/corrected.txt",
        }
        mock_processor.corrections_result = {"corrections": []}

        with patch('karaoke_gen.lyrics_processor.LyricsProcessor', return_value=mock_processor):
            result = await stage.execute(context)

        assert result.status == StageStatus.COMPLETED
        assert result.outputs["lrc_filepath"] == "/out/lyrics.lrc"
        assert result.outputs["ass_filepath"] == "/out/lyrics.ass"
        assert result.outputs["corrected_txt_path"] == "/out/corrected.txt"
        assert result.outputs["corrections_result"] == {"corrections": []}
        assert result.outputs["countdown_padding_seconds"] == 3.0
        # transcribe_lyrics received the context's audio path and artist/title.
        _, kwargs = mock_processor.transcribe_lyrics.call_args
        assert kwargs["input_audio_wav"] == "/audio.flac"
        assert kwargs["artist"] == "Artist"

        # style_params (no style_params_json) makes the stage write a temp file and
        # register it on the context; clean it up so the test leaves no /tmp files.
        context.cleanup_temp_paths()

    @pytest.mark.asyncio
    async def test_execute_ignores_malformed_countdown_file(self, tmp_path):
        """A non-numeric countdown file is ignored, not fatal."""
        from karaoke_gen.pipeline.stages.transcription import TranscriptionStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus

        output_dir = tmp_path / "out"
        lyrics_dir = output_dir / "lyrics"
        lyrics_dir.mkdir(parents=True)
        (lyrics_dir / "countdown_padding_seconds.txt").write_text("not-a-number")

        stage = TranscriptionStage(skip_transcription=False)
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir=str(output_dir),
        )

        mock_processor = MagicMock()
        mock_processor.transcribe_lyrics.return_value = {}

        with patch('karaoke_gen.lyrics_processor.LyricsProcessor', return_value=mock_processor):
            result = await stage.execute(context)

        assert result.status == StageStatus.COMPLETED
        assert "countdown_padding_seconds" not in result.outputs


class TestScreensStage:
    """Tests for ScreensStage."""

    def test_stage_imports(self):
        """Test that the stage can be imported."""
        from karaoke_gen.pipeline.stages.screens import ScreensStage
        assert ScreensStage is not None

    def test_stage_name(self):
        """Test the stage name."""
        from karaoke_gen.pipeline.stages.screens import ScreensStage
        
        stage = ScreensStage()
        assert stage.name == "screens"

    def test_stage_output_keys(self):
        """Test the declared output keys."""
        from karaoke_gen.pipeline.stages.screens import ScreensStage
        
        stage = ScreensStage()
        assert "title_video_path" in stage.output_keys
        assert "end_video_path" in stage.output_keys

    def test_stage_init_options(self):
        """Test initialization options."""
        from karaoke_gen.pipeline.stages.screens import ScreensStage
        
        stage = ScreensStage(output_png=False, output_jpg=False)
        
        assert stage.output_png is False
        assert stage.output_jpg is False

    def test_stage_required_inputs(self):
        """Test that screens has no required inputs."""
        from karaoke_gen.pipeline.stages.screens import ScreensStage
        
        stage = ScreensStage()
        assert stage.required_inputs == []

    @pytest.mark.asyncio
    async def test_execute_calls_video_generator(self):
        """Test execute calls VideoGenerator."""
        from karaoke_gen.pipeline.stages.screens import ScreensStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = ScreensStage()
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
        )
        
        mock_generator = MagicMock()
        mock_generator.generate_title_screen.return_value = {"video_path": "/title.mov"}
        mock_generator.generate_end_screen.return_value = {"video_path": "/end.mov"}
        
        with patch('karaoke_gen.video_generator.VideoGenerator', return_value=mock_generator):
            result = await stage.execute(context)
        
        assert result.status == StageStatus.COMPLETED
        mock_generator.generate_title_screen.assert_called_once()
        mock_generator.generate_end_screen.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_handles_exception(self):
        """Test execute handles exceptions gracefully."""
        from karaoke_gen.pipeline.stages.screens import ScreensStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = ScreensStage()
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
        )
        
        with patch('karaoke_gen.video_generator.VideoGenerator', side_effect=Exception("Test error")):
            result = await stage.execute(context)
        
        assert result.status == StageStatus.FAILED
        assert "Test error" in result.error_message


class TestRenderStage:
    """Tests for RenderStage."""

    def test_stage_imports(self):
        """Test that the stage can be imported."""
        from karaoke_gen.pipeline.stages.render import RenderStage
        assert RenderStage is not None

    def test_stage_name(self):
        """Test the stage name."""
        from karaoke_gen.pipeline.stages.render import RenderStage
        
        stage = RenderStage()
        assert stage.name == "render"

    def test_stage_required_inputs(self):
        """Test that render requires transcription."""
        from karaoke_gen.pipeline.stages.render import RenderStage
        
        stage = RenderStage()
        assert "transcription" in stage.required_inputs

    def test_stage_output_keys(self):
        """Test the declared output keys."""
        from karaoke_gen.pipeline.stages.render import RenderStage
        
        stage = RenderStage()
        assert "with_vocals_video_path" in stage.output_keys

    def test_stage_optional_inputs(self):
        """Test optional inputs include separation."""
        from karaoke_gen.pipeline.stages.render import RenderStage
        
        stage = RenderStage()
        assert "separation" in stage.optional_inputs

    def test_stage_init_with_params(self):
        """Test initializing with custom parameters."""
        from karaoke_gen.pipeline.stages.render import RenderStage
        
        stage = RenderStage(render_bounding_boxes=True)
        assert stage.render_bounding_boxes is True

    @pytest.mark.asyncio
    async def test_execute_skips_without_corrections(self):
        """Test execute skips when no corrections result available."""
        from karaoke_gen.pipeline.stages.render import RenderStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = RenderStage()
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
            stage_outputs={"transcription": {}},  # No corrections_result
        )
        
        result = await stage.execute(context)
        assert result.status == StageStatus.SKIPPED

class TestFinalizeStage:
    """Tests for FinalizeStage."""

    def test_stage_imports(self):
        """Test that the stage can be imported."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        assert FinalizeStage is not None

    def test_stage_name(self):
        """Test the stage name."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        
        stage = FinalizeStage()
        assert stage.name == "finalize"

    def test_stage_required_inputs(self):
        """Test required inputs."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        
        stage = FinalizeStage()
        assert "render" in stage.required_inputs
        assert "screens" in stage.required_inputs

    def test_stage_output_keys(self):
        """Test the declared output keys."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        
        stage = FinalizeStage()
        assert "brand_code" in stage.output_keys
        assert "youtube_url" in stage.output_keys
        assert "cdg_zip_path" in stage.output_keys

    def test_stage_init_options(self):
        """Test initialization options."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        
        stage = FinalizeStage(
            enable_cdg=False,
            enable_txt=False,
            non_interactive=True,
            server_side_mode=True,
        )
        
        assert stage.enable_cdg is False
        assert stage.enable_txt is False
        assert stage.non_interactive is True
        assert stage.server_side_mode is True

    def test_stage_optional_inputs(self):
        """Test optional inputs include separation."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        
        stage = FinalizeStage()
        assert "separation" in stage.optional_inputs

    def test_stage_init_with_youtube_params(self):
        """Test initializing with YouTube parameters."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        
        youtube_creds = {"token": "test", "refresh_token": "test"}
        stage = FinalizeStage(
            youtube_credentials=youtube_creds,
            youtube_description_template="Test description",
        )
        
        assert stage.youtube_credentials == youtube_creds
        assert stage.youtube_description_template == "Test description"

    @pytest.mark.asyncio
    async def test_execute_fails_without_instrumental(self):
        """Test execute fails when no instrumental file found."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        
        stage = FinalizeStage()
        context = PipelineContext(
            job_id="test",
            artist="Artist",
            title="Title",
            input_audio_path="/audio.flac",
            output_dir="/output",
            stage_outputs={
                "render": {"with_vocals_video_path": "/video.mov"},
                "screens": {"title_video_path": "/title.mov"},
                "separation": {},  # No instrumental
            },
        )
        
        result = await stage.execute(context)
        assert result.status == StageStatus.FAILED
        assert "instrumental" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_handles_exception(self):
        """Test execute handles exceptions gracefully."""
        from karaoke_gen.pipeline.stages.finalize import FinalizeStage
        from karaoke_gen.pipeline.context import PipelineContext
        from karaoke_gen.pipeline.base import StageStatus
        import tempfile
        import os
        
        # Create a temp file to use as instrumental
        with tempfile.NamedTemporaryFile(delete=False, suffix='.flac') as f:
            f.write(b"fake audio")
            instrumental_path = f.name
        
        try:
            stage = FinalizeStage()
            context = PipelineContext(
                job_id="test",
                artist="Artist",
                title="Title",
                input_audio_path="/audio.flac",
                output_dir="/output",
                stage_outputs={
                    "render": {"with_vocals_video_path": "/video.mov"},
                    "screens": {"title_video_path": "/title.mov"},
                    "separation": {"combined_instrumentals": {"model": instrumental_path}},
                },
            )
            
            with patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise', side_effect=Exception("Test error")):
                result = await stage.execute(context)
            
            assert result.status == StageStatus.FAILED
            assert "Test error" in result.error_message
        finally:
            os.unlink(instrumental_path)


class TestStageModuleExports:
    """Tests for module exports."""

    def test_stages_init_exports(self):
        """Test that stages __init__ exports all stages."""
        from karaoke_gen.pipeline.stages import (
            SeparationStage,
            TranscriptionStage,
            ScreensStage,
            RenderStage,
            FinalizeStage,
        )
        
        assert SeparationStage is not None
        assert TranscriptionStage is not None
        assert ScreensStage is not None
        assert RenderStage is not None
        assert FinalizeStage is not None
