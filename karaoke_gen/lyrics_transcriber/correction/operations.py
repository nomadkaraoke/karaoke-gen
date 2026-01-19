"""
Correction Operations Module

This module contains reusable correction operations that can be shared between
the local ReviewServer and remote Modal serverless implementations.

These operations handle dynamic updates to correction results including:
- Adding new lyrics sources
- Updating correction handlers  
- Generating preview videos
- Updating correction data
"""

import json
import hashlib
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

from karaoke_gen.lyrics_transcriber.types import (
    CorrectionResult, 
    WordCorrection, 
    LyricsSegment, 
    Word,
    TranscriptionResult,
    TranscriptionData,
    LyricsData
)
from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector
from karaoke_gen.lyrics_transcriber.lyrics.user_input_provider import UserInputProvider
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig


class CorrectionOperations:
    """Static methods for common correction operations."""
    
    @staticmethod
    def update_correction_result_with_data(
        base_result: CorrectionResult, 
        updated_data: Dict[str, Any]
    ) -> CorrectionResult:
        """Update a CorrectionResult with new correction data from dict."""
        return CorrectionResult(
            corrections=[
                WordCorrection(
                    original_word=c.get("original_word", "").strip(),
                    corrected_word=c.get("corrected_word", "").strip(),
                    original_position=c.get("original_position", 0),
                    source=c.get("source", "review"),
                    reason=c.get("reason", "manual_review"),
                    segment_index=c.get("segment_index", 0),
                    confidence=c.get("confidence"),
                    alternatives=c.get("alternatives", {}),
                    is_deletion=c.get("is_deletion", False),
                    split_index=c.get("split_index"),
                    split_total=c.get("split_total"),
                    corrected_position=c.get("corrected_position"),
                    reference_positions=c.get("reference_positions"),
                    length=c.get("length", 1),
                    handler=c.get("handler"),
                    word_id=c.get("word_id"),
                    corrected_word_id=c.get("corrected_word_id"),
                )
                for c in updated_data["corrections"]
            ],
            corrected_segments=[
                LyricsSegment(
                    id=s["id"],
                    text=s["text"].strip(),
                    words=[
                        Word(
                            id=w["id"],
                            text=w["text"].strip(),
                            start_time=w["start_time"],
                            end_time=w["end_time"],
                            confidence=w.get("confidence"),
                            created_during_correction=w.get("created_during_correction", False),
                        )
                        for w in s["words"]
                    ],
                    start_time=s["start_time"],
                    end_time=s["end_time"],
                )
                for s in updated_data["corrected_segments"]
            ],
            # Copy existing fields from the base result
            original_segments=base_result.original_segments,
            corrections_made=len(updated_data["corrections"]),
            confidence=base_result.confidence,
            reference_lyrics=base_result.reference_lyrics,
            anchor_sequences=base_result.anchor_sequences,
            gap_sequences=base_result.gap_sequences,
            resized_segments=None,  # Will be generated if needed
            metadata=base_result.metadata,
            correction_steps=base_result.correction_steps,
            word_id_map=base_result.word_id_map,
            segment_id_map=base_result.segment_id_map,
        )
    
    @staticmethod
    def add_lyrics_source(
        correction_result: CorrectionResult,
        source: str,
        lyrics_text: str,
        cache_dir: str,
        logger: Optional[logging.Logger] = None
    ) -> CorrectionResult:
        """
        Add a new lyrics source and rerun correction.
        
        Args:
            correction_result: Current correction result
            source: Name of the new lyrics source
            lyrics_text: The lyrics text content
            cache_dir: Cache directory for correction operations
            logger: Optional logger instance
            
        Returns:
            Updated CorrectionResult with new lyrics source and corrections
            
        Raises:
            ValueError: If source name is already in use or inputs are invalid
        """
        if not logger:
            logger = logging.getLogger(__name__)
            
        logger.info(f"Adding lyrics source '{source}' with {len(lyrics_text)} characters")
        
        # Validate inputs
        if not source or not lyrics_text:
            raise ValueError("Source name and lyrics text are required")
            
        if source in correction_result.reference_lyrics:
            raise ValueError(f"Source name '{source}' is already in use")
        
        # Store existing audio hash
        audio_hash = correction_result.metadata.get("audio_hash") if correction_result.metadata else None
        
        # Create lyrics data using the provider
        logger.info("Creating LyricsData using UserInputProvider")
        provider = UserInputProvider(
            lyrics_text=lyrics_text,
            source_name=source,
            metadata=correction_result.metadata or {},
            logger=logger
        )
        lyrics_data = provider._convert_result_format({
            "text": lyrics_text, 
            "metadata": correction_result.metadata or {}
        })
        logger.info(f"Created LyricsData with {len(lyrics_data.segments)} segments")
        
        # Add to reference lyrics (create a copy to avoid modifying original)
        updated_reference_lyrics = correction_result.reference_lyrics.copy()
        updated_reference_lyrics[source] = lyrics_data
        logger.info(f"Added source '{source}' to reference lyrics")
        
        # Create TranscriptionData from original segments
        transcription_data = TranscriptionData(
            segments=correction_result.original_segments,
            words=[word for segment in correction_result.original_segments for word in segment.words],
            text="\n".join(segment.text for segment in correction_result.original_segments),
            source="original",
        )
        
        # Get currently enabled handlers from metadata
        enabled_handlers = None
        if correction_result.metadata:
            enabled_handlers = correction_result.metadata.get("enabled_handlers")
        
        # Rerun correction with updated reference lyrics
        logger.info("Running correction with updated reference lyrics")
        corrector = LyricsCorrector(
            cache_dir=cache_dir,
            enabled_handlers=enabled_handlers,
            logger=logger,
        )
        
        updated_result = corrector.run(
            transcription_results=[TranscriptionResult(name="original", priority=1, result=transcription_data)],
            lyrics_results=updated_reference_lyrics,
            metadata=correction_result.metadata,
        )
        
        # Update metadata with handler state
        if not updated_result.metadata:
            updated_result.metadata = {}
        updated_result.metadata.update({
            "available_handlers": corrector.all_handlers,
            "enabled_handlers": [getattr(handler, "name", handler.__class__.__name__) for handler in corrector.handlers],
        })
        
        # Restore audio hash
        if audio_hash:
            updated_result.metadata["audio_hash"] = audio_hash
            
        logger.info(f"Successfully added lyrics source '{source}' and updated corrections")
        return updated_result
    
    @staticmethod
    def update_correction_handlers(
        correction_result: CorrectionResult,
        enabled_handlers: List[str],
        cache_dir: str,
        logger: Optional[logging.Logger] = None
    ) -> CorrectionResult:
        """
        Update enabled correction handlers and rerun correction.
        
        Args:
            correction_result: Current correction result
            enabled_handlers: List of handler names to enable
            cache_dir: Cache directory for correction operations
            logger: Optional logger instance
            
        Returns:
            Updated CorrectionResult with new handler configuration
        """
        if not logger:
            logger = logging.getLogger(__name__)
            
        logger.info(f"Updating correction handlers: {enabled_handlers}")
        
        # Store existing audio hash
        audio_hash = correction_result.metadata.get("audio_hash") if correction_result.metadata else None
        
        # Update metadata with new handler configuration
        updated_metadata = (correction_result.metadata or {}).copy()
        updated_metadata["enabled_handlers"] = enabled_handlers
        
        # Create TranscriptionData from original segments
        transcription_data = TranscriptionData(
            segments=correction_result.original_segments,
            words=[word for segment in correction_result.original_segments for word in segment.words],
            text="\n".join(segment.text for segment in correction_result.original_segments),
            source="original",
        )
        
        # Rerun correction with updated handlers
        logger.info("Running correction with updated handlers")
        corrector = LyricsCorrector(
            cache_dir=cache_dir,
            enabled_handlers=enabled_handlers,
            logger=logger,
        )
        
        updated_result = corrector.run(
            transcription_results=[TranscriptionResult(name="original", priority=1, result=transcription_data)],
            lyrics_results=correction_result.reference_lyrics,
            metadata=updated_metadata,
        )
        
        # Update metadata with handler state
        if not updated_result.metadata:
            updated_result.metadata = {}
        updated_result.metadata.update({
            "available_handlers": corrector.all_handlers,
            "enabled_handlers": [getattr(handler, "name", handler.__class__.__name__) for handler in corrector.handlers],
        })
        
        # Restore audio hash
        if audio_hash:
            updated_result.metadata["audio_hash"] = audio_hash
            
        logger.info(f"Successfully updated handlers: {enabled_handlers}")
        return updated_result
    
    @staticmethod
    def generate_preview_video(
        correction_result: CorrectionResult,
        updated_data: Dict[str, Any],
        output_config: OutputConfig,
        audio_filepath: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        ass_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Generate a preview video with current corrections.

        Args:
            correction_result: Current correction result
            updated_data: Updated correction data for preview
            output_config: Output configuration
            audio_filepath: Path to audio file
            artist: Optional artist name
            title: Optional title
            logger: Optional logger instance
            ass_only: If True, generate only ASS subtitles without video encoding.
                      Useful when video encoding is offloaded to external service.

        Returns:
            Dict with status, preview_hash, and video_path (or ass_path if ass_only)

        Raises:
            ValueError: If preview video generation fails
        """
        if not logger:
            logger = logging.getLogger(__name__)
            
        logger.info("Generating preview video with corrected data")
        
        # Create temporary correction result with updated data
        temp_correction = CorrectionOperations.update_correction_result_with_data(
            correction_result, updated_data
        )
        
        # Generate a unique hash for this preview
        preview_data = json.dumps(updated_data, sort_keys=True).encode("utf-8")
        preview_hash = hashlib.md5(preview_data).hexdigest()[:12]
        
        # Set up preview config
        preview_config = OutputConfig(
            output_dir=str(Path(output_config.output_dir) / "previews"),
            cache_dir=output_config.cache_dir,
            output_styles_json=output_config.output_styles_json,
            video_resolution="360p",  # Force 360p for preview
            render_video=True,
            generate_cdg=False,
            generate_plain_text=False,
            generate_lrc=False,
            fetch_lyrics=False,
            run_transcription=False,
            run_correction=False,
        )
        
        # Create previews directory
        preview_dir = Path(output_config.output_dir) / "previews"
        preview_dir.mkdir(exist_ok=True)
        
        # Initialize output generator
        output_generator = OutputGenerator(config=preview_config, logger=logger, preview_mode=True)
        
        # Generate preview outputs
        preview_outputs = output_generator.generate_outputs(
            transcription_corrected=temp_correction,
            lyrics_results={},  # Empty dict since we don't need lyrics results for preview
            output_prefix=f"preview_{preview_hash}",
            audio_filepath=audio_filepath,
            artist=artist,
            title=title,
            ass_only=ass_only,
        )

        # When ass_only, we only need the ASS file (video encoding done externally)
        if ass_only:
            if not preview_outputs.ass:
                raise ValueError("Preview ASS generation failed")
            logger.info(f"Generated preview ASS: {preview_outputs.ass}")
            return {
                "status": "success",
                "preview_hash": preview_hash,
                "ass_path": preview_outputs.ass,
            }

        if not preview_outputs.video:
            raise ValueError("Preview video generation failed")

        logger.info(f"Generated preview video: {preview_outputs.video}")

        return {
            "status": "success",
            "preview_hash": preview_hash,
            "video_path": preview_outputs.video,
        } 