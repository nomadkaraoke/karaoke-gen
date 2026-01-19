"""
Video rendering pipeline stage.

This stage handles:
- Rendering karaoke video with synchronized lyrics
- Using the OutputGenerator from lyrics_transcriber
- Combining audio, video, and synchronized lyrics

This stage runs after transcription is complete and corrections
have been applied.
"""
import logging
import os
from typing import Any, Dict, List, Optional

from karaoke_gen.pipeline.base import PipelineStage, StageResult, StageStatus
from karaoke_gen.pipeline.context import PipelineContext


class RenderStage(PipelineStage):
    """
    Video rendering stage.
    
    Renders the karaoke video with synchronized lyrics overlay.
    Uses OutputGenerator from karaoke_gen.lyrics_transcriber.
    """
    
    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        render_bounding_boxes: bool = False,
    ):
        """
        Initialize the render stage.
        
        Args:
            logger: Logger instance
            render_bounding_boxes: If True, render debug bounding boxes
        """
        self.logger = logger or logging.getLogger(__name__)
        self.render_bounding_boxes = render_bounding_boxes
    
    @property
    def name(self) -> str:
        return "render"
    
    @property
    def required_inputs(self) -> List[str]:
        # Requires transcription output
        return ["transcription"]
    
    @property
    def optional_inputs(self) -> List[str]:
        return ["separation"]
    
    @property
    def output_keys(self) -> List[str]:
        return [
            "with_vocals_video_path",  # Path to rendered video with vocals
            "lrc_path",                 # Path to LRC file
            "ass_path",                 # Path to ASS subtitle file
        ]
    
    async def execute(self, context: PipelineContext) -> StageResult:
        """
        Execute video rendering.
        
        Args:
            context: Pipeline context with transcription outputs
            
        Returns:
            StageResult with rendered video path
        """
        import time
        start_time = time.time()
        
        try:
            context.update_progress(self.name, 0, "Starting video rendering")
            context.log("INFO", f"Rendering video for: {context.artist} - {context.title}")
            
            # Get transcription outputs
            transcription = context.stage_outputs.get("transcription", {})
            corrections_result = transcription.get("corrections_result")
            
            if not corrections_result:
                context.log("WARNING", "No corrections result available for rendering")
                return StageResult(
                    status=StageStatus.SKIPPED,
                    outputs={},
                    error_message="No corrections result available",
                )
            
            # Import OutputGenerator
            from karaoke_gen.lyrics_transcriber import OutputGenerator, OutputConfig
            
            # Build output config
            output_config = OutputConfig(
                output_dir=context.output_dir,
                cache_dir=os.path.join(context.output_dir, "cache"),
                video_resolution="4k",  # Default to 4K
            )
            
            # Apply style params if available
            if context.style_params:
                output_config = self._apply_style_params(output_config, context.style_params)
            
            context.update_progress(self.name, 20, "Initializing video generator")
            
            # Create OutputGenerator
            generator = OutputGenerator(
                config=output_config,
                logger=self.logger,
            )
            
            context.update_progress(self.name, 40, "Rendering video with lyrics")
            
            # Generate video
            result = generator.generate_video(
                result=corrections_result,
                output_prefix=context.base_name,
                audio_file=context.input_audio_path,
            )
            
            outputs = {}
            
            if result:
                outputs["with_vocals_video_path"] = result.get("video_path")
                outputs["lrc_path"] = result.get("lrc_path")
                outputs["ass_path"] = result.get("ass_path")
            
            context.update_progress(self.name, 100, "Video rendering complete")
            
            duration = time.time() - start_time
            context.log("INFO", f"Video rendering completed in {duration:.1f}s")
            
            return StageResult(
                status=StageStatus.COMPLETED,
                outputs=outputs,
                duration_seconds=duration,
            )
            
        except Exception as e:
            duration = time.time() - start_time
            context.log("ERROR", f"Video rendering failed: {str(e)}")
            return StageResult(
                status=StageStatus.FAILED,
                error_message=str(e),
                error_details={"exception_type": type(e).__name__},
                duration_seconds=duration,
            )
    
    def _apply_style_params(self, config, style_params: Dict[str, Any]):
        """Apply style parameters to output config."""
        # Apply karaoke style settings if present
        karaoke_params = style_params.get("karaoke", {})
        
        if karaoke_params.get("background_image"):
            config.background_image = karaoke_params["background_image"]
        
        if karaoke_params.get("font_path"):
            config.font_path = karaoke_params["font_path"]
        
        # Add more style mappings as needed
        
        return config
