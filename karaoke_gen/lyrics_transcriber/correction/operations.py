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
import os
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
from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import LyricsProviderConfig, BaseLyricsProvider
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig


def create_lyrics_providers(
    cache_dir: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> List[BaseLyricsProvider]:
    """
    Create lyrics providers based on available environment variables.

    Reads API keys from environment and returns a list of providers that are
    configured and ready to use. Providers with missing required credentials
    are silently skipped.

    Args:
        cache_dir: Optional cache directory for provider caching.
        logger: Optional logger instance.

    Returns:
        List of configured BaseLyricsProvider instances.
    """
    if not logger:
        logger = logging.getLogger(__name__)

    # Import provider classes here to keep them mockable in tests
    from karaoke_gen.lyrics_transcriber.lyrics.genius import GeniusProvider
    from karaoke_gen.lyrics_transcriber.lyrics.spotify import SpotifyProvider
    from karaoke_gen.lyrics_transcriber.lyrics.musixmatch import MusixmatchProvider
    from karaoke_gen.lyrics_transcriber.lyrics.lrclib import LRCLIBProvider

    genius_api_token = os.environ.get("GENIUS_API_KEY")
    rapidapi_key = os.environ.get("RAPIDAPI_KEY")
    spotify_cookie = os.environ.get("SPOTIFY_COOKIE_SP_DC")

    config = LyricsProviderConfig(
        genius_api_token=genius_api_token,
        rapidapi_key=rapidapi_key,
        spotify_cookie=spotify_cookie,
        cache_dir=cache_dir,
    )

    providers: List[BaseLyricsProvider] = []

    if genius_api_token or rapidapi_key:
        providers.append(GeniusProvider(config=config, logger=logger))
        logger.info("GeniusProvider configured")
    else:
        logger.info("Skipping GeniusProvider: no GENIUS_API_KEY or RAPIDAPI_KEY")

    if spotify_cookie or rapidapi_key:
        providers.append(SpotifyProvider(config=config, logger=logger))
        logger.info("SpotifyProvider configured")
    else:
        logger.info("Skipping SpotifyProvider: no SPOTIFY_COOKIE_SP_DC or RAPIDAPI_KEY")

    if rapidapi_key:
        providers.append(MusixmatchProvider(config=config, logger=logger))
        logger.info("MusixmatchProvider configured")
    else:
        logger.info("Skipping MusixmatchProvider: no RAPIDAPI_KEY")

    # LRCLIB needs no API key
    providers.append(LRCLIBProvider(config=config, logger=logger))
    logger.info("LRCLIBProvider configured")

    return providers


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
                            text=w["text"],
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
        logger: Optional[logging.Logger] = None,
        force: bool = False,
    ) -> CorrectionResult:
        """
        Add a new lyrics source and rerun correction.

        Args:
            correction_result: Current correction result
            source: Name of the new lyrics source
            lyrics_text: The lyrics text content
            cache_dir: Cache directory for correction operations
            logger: Optional logger instance
            force: If True and the source is rejected by the relevance filter, re-add
                   it to reference_lyrics anyway so it is preserved in the result.

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

        # If force=True and the source was rejected by the relevance filter, put it back
        # so the frontend can see it and the user's intent is respected.
        if force:
            rejected_sources = updated_result.metadata.get("rejected_sources", {})
            if source in rejected_sources:
                logger.info(
                    f"force=True: re-adding rejected source '{source}' to reference_lyrics"
                )
                updated_result.reference_lyrics[source] = lyrics_data

        logger.info(f"Successfully added lyrics source '{source}' and updated corrections")
        return updated_result

    @staticmethod
    def search_lyrics_sources(
        correction_result: CorrectionResult,
        artist: str,
        title: str,
        cache_dir: str,
        force_sources: Optional[List[str]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> Dict[str, Any]:
        """
        Search for lyrics from all configured providers and run correction.

        Fetches lyrics from Genius, Spotify, Musixmatch, and LRCLIB using the
        supplied artist/title, then runs them through the correction pipeline
        (which includes the relevance filter from Task 2).

        Args:
            correction_result: Current correction result (provides transcription data).
            artist: Artist name to search for.
            title: Song title to search for.
            cache_dir: Cache directory for providers and correction operations.
            force_sources: Optional list of provider names whose results should bypass
                           the relevance filter and be included regardless of score.
            logger: Optional logger instance.

        Returns:
            Dict with keys:
                - ``updated_result``: Updated CorrectionResult (or None if nothing passed).
                - ``sources_added``: List of source names that passed the filter.
                - ``sources_rejected``: Dict mapping rejected source names to relevance info.
                - ``sources_not_found``: List of provider names that returned no lyrics.
        """
        if not logger:
            logger = logging.getLogger(__name__)

        force_sources = force_sources or []

        logger.info(f"Searching lyrics for artist='{artist}' title='{title}'")

        # Store existing audio hash
        audio_hash = correction_result.metadata.get("audio_hash") if correction_result.metadata else None

        # Create and query all configured providers
        providers = create_lyrics_providers(cache_dir=cache_dir, logger=logger)

        new_lyrics: Dict[str, LyricsData] = {}
        sources_not_found: List[str] = []

        for provider in providers:
            provider_name = provider.get_name()
            # Skip providers whose source name is already in the existing reference_lyrics
            if provider_name in correction_result.reference_lyrics:
                logger.info(f"Skipping provider '{provider_name}': already present in reference_lyrics")
                continue
            try:
                lyrics_data = provider.fetch_lyrics(artist, title)
                if lyrics_data:
                    new_lyrics[provider_name] = lyrics_data
                    logger.info(f"Provider '{provider_name}' returned lyrics")
                else:
                    sources_not_found.append(provider_name)
                    logger.info(f"Provider '{provider_name}' found no lyrics")
            except Exception as e:
                logger.warning(f"Provider '{provider_name}' raised an error: {e}")
                sources_not_found.append(provider_name)

        if not new_lyrics:
            logger.info("No new lyrics found from any provider")
            return {
                "updated_result": None,
                "sources_added": [],
                "sources_rejected": {},
                "sources_not_found": sources_not_found,
            }

        # Combine new lyrics with existing reference_lyrics
        combined_lyrics = correction_result.reference_lyrics.copy()
        combined_lyrics.update(new_lyrics)

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

        # Run correction (relevance filter is automatic inside corrector.run)
        logger.info(f"Running correction with {len(combined_lyrics)} combined reference sources")
        corrector = LyricsCorrector(
            cache_dir=cache_dir,
            enabled_handlers=enabled_handlers,
            logger=logger,
        )

        updated_result = corrector.run(
            transcription_results=[TranscriptionResult(name="original", priority=1, result=transcription_data)],
            lyrics_results=combined_lyrics,
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

        # Determine which of the *new* sources were accepted vs rejected
        rejected_sources_meta = updated_result.metadata.get("rejected_sources", {})

        sources_added = [name for name in new_lyrics if name not in rejected_sources_meta]
        sources_rejected = {name: info for name, info in rejected_sources_meta.items() if name in new_lyrics}

        # Re-add force_sources that were rejected
        for source_name in force_sources:
            if source_name in sources_rejected and source_name in new_lyrics:
                logger.info(
                    f"force_sources: re-adding rejected source '{source_name}' to reference_lyrics"
                )
                updated_result.reference_lyrics[source_name] = new_lyrics[source_name]
                sources_added.append(source_name)
                del sources_rejected[source_name]

        logger.info(
            f"Search complete: added={sources_added}, "
            f"rejected={list(sources_rejected.keys())}, "
            f"not_found={sources_not_found}"
        )

        return {
            "updated_result": updated_result,
            "sources_added": sources_added,
            "sources_rejected": sources_rejected,
            "sources_not_found": sources_not_found,
        }

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