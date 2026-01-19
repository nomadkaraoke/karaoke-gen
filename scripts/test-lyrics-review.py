#!/usr/bin/env python
"""
Test script for the Lyrics Review UI with sample correction data.

Usage:
    python scripts/test-lyrics-review.py [--audio /path/to/audio.mp3]

This will:
1. Create mock correction data (or load from file)
2. Start the lyrics review server
3. Open your browser to the review UI

Note: Without an audio file, playback won't work but you can still test the UI modals.
"""

import argparse
import logging
import os
import sys
import tempfile

# Add the project root and lyrics_transcriber_temp to the path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "lyrics_transcriber_temp"))

from karaoke_gen.lyrics_transcriber.types import (
    CorrectionResult,
    LyricsData,
    LyricsMetadata,
    LyricsSegment,
    Word,
    WordCorrection,
)
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
from karaoke_gen.lyrics_transcriber.review.server import ReviewServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def create_sample_correction_result() -> CorrectionResult:
    """Create sample correction data for testing the UI."""

    # Sample segments with words
    segments = [
        LyricsSegment(
            id="seg-1",
            text="Hello, is it me you're looking for?",
            words=[
                Word(id="w1", text="Hello,", start_time=0.0, end_time=0.5),
                Word(id="w2", text="is", start_time=0.5, end_time=0.7),
                Word(id="w3", text="it", start_time=0.7, end_time=0.9),
                Word(id="w4", text="me", start_time=0.9, end_time=1.1),
                Word(id="w5", text="you're", start_time=1.1, end_time=1.3),
                Word(id="w6", text="looking", start_time=1.3, end_time=1.6),
                Word(id="w7", text="for?", start_time=1.6, end_time=1.9),
            ],
            start_time=0.0,
            end_time=1.9,
        ),
        LyricsSegment(
            id="seg-2",
            text="I can see it in your eyes",
            words=[
                Word(id="w8", text="I", start_time=2.0, end_time=2.2),
                Word(id="w9", text="can", start_time=2.2, end_time=2.4),
                Word(id="w10", text="see", start_time=2.4, end_time=2.6),
                Word(id="w11", text="it", start_time=2.6, end_time=2.8),
                Word(id="w12", text="in", start_time=2.8, end_time=3.0),
                Word(id="w13", text="your", start_time=3.0, end_time=3.2),
                Word(id="w14", text="eyes", start_time=3.2, end_time=3.5),
            ],
            start_time=2.0,
            end_time=3.5,
        ),
        LyricsSegment(
            id="seg-3",
            text="I can see it in your smile",
            words=[
                Word(id="w15", text="I", start_time=4.0, end_time=4.2),
                Word(id="w16", text="can", start_time=4.2, end_time=4.4),
                Word(id="w17", text="see", start_time=4.4, end_time=4.6),
                Word(id="w18", text="it", start_time=4.6, end_time=4.8),
                Word(id="w19", text="in", start_time=4.8, end_time=5.0),
                Word(id="w20", text="your", start_time=5.0, end_time=5.2),
                Word(id="w21", text="smile", start_time=5.2, end_time=5.5),
            ],
            start_time=4.0,
            end_time=5.5,
        ),
        LyricsSegment(
            id="seg-4",
            text="You're all I've ever wanted",
            words=[
                Word(id="w22", text="You're", start_time=6.0, end_time=6.3),
                Word(id="w23", text="all", start_time=6.3, end_time=6.5),
                Word(id="w24", text="I've", start_time=6.5, end_time=6.7),
                Word(id="w25", text="ever", start_time=6.7, end_time=6.9),
                Word(id="w26", text="wanted", start_time=6.9, end_time=7.3),
            ],
            start_time=6.0,
            end_time=7.3,
        ),
        LyricsSegment(
            id="seg-5",
            text="And my arms are open wide",
            words=[
                Word(id="w27", text="And", start_time=8.0, end_time=8.2),
                Word(id="w28", text="my", start_time=8.2, end_time=8.4),
                Word(id="w29", text="arms", start_time=8.4, end_time=8.6),
                Word(id="w30", text="are", start_time=8.6, end_time=8.8),
                Word(id="w31", text="open", start_time=8.8, end_time=9.1),
                Word(id="w32", text="wide", start_time=9.1, end_time=9.5),
            ],
            start_time=8.0,
            end_time=9.5,
        ),
    ]

    # Sample corrections (simulating AI corrections)
    # WordCorrection fields: original_word, corrected_word, source, reason,
    #   original_position (int), segment_index (int), confidence (float),
    #   word_id (str), handler (str)
    corrections = [
        WordCorrection(
            original_word="your",
            corrected_word="you're",
            source="agentic",
            reason="Corrected contraction",
            original_position=5,  # position in segment
            segment_index=1,
            confidence=0.95,
            word_id="w13",
            handler="SpellingHandler",
            reference_positions={"genius": 5, "musixmatch": 5},
        ),
        WordCorrection(
            original_word="smile",
            corrected_word="smile,",
            source="agentic",
            reason="Added punctuation",
            original_position=6,
            segment_index=2,
            confidence=0.85,
            word_id="w21",
            handler="PunctuationHandler",
            reference_positions={"genius": 6},
        ),
    ]

    # Reference lyrics from different sources (must be LyricsData objects)
    genius_metadata = LyricsMetadata(
        source="genius",
        track_name="Hello",
        artist_names="Lionel Richie",
        language="en",
        lyrics_provider="genius",
        lyrics_provider_id="123456",
    )
    musixmatch_metadata = LyricsMetadata(
        source="musixmatch",
        track_name="Hello",
        artist_names="Lionel Richie",
        language="en",
        lyrics_provider="musixmatch",
        lyrics_provider_id="789012",
    )

    reference_lyrics = {
        "genius": LyricsData(
            segments=[
                LyricsSegment(
                    id="ref-g-1",
                    text="Hello, is it me you're looking for?",
                    words=[],
                    start_time=0.0,
                    end_time=1.9,
                ),
                LyricsSegment(
                    id="ref-g-2",
                    text="I can see it in your eyes",
                    words=[],
                    start_time=2.0,
                    end_time=3.5,
                ),
                LyricsSegment(
                    id="ref-g-3",
                    text="I can see it in your smile",
                    words=[],
                    start_time=4.0,
                    end_time=5.5,
                ),
                LyricsSegment(
                    id="ref-g-4",
                    text="You're all I've ever wanted",
                    words=[],
                    start_time=6.0,
                    end_time=7.3,
                ),
                LyricsSegment(
                    id="ref-g-5",
                    text="And my arms are open wide",
                    words=[],
                    start_time=8.0,
                    end_time=9.5,
                ),
            ],
            metadata=genius_metadata,
            source="genius",
        ),
        "musixmatch": LyricsData(
            segments=[
                LyricsSegment(
                    id="ref-m-1",
                    text="Hello, is it me you're looking for",
                    words=[],
                    start_time=0.0,
                    end_time=1.9,
                ),
                LyricsSegment(
                    id="ref-m-2",
                    text="I can see it in your eyes",
                    words=[],
                    start_time=2.0,
                    end_time=3.5,
                ),
            ],
            metadata=musixmatch_metadata,
            source="musixmatch",
        ),
    }

    # Create the CorrectionResult with all required fields
    return CorrectionResult(
        original_segments=segments,
        corrected_segments=segments.copy(),  # Would have corrections applied
        corrections=corrections,
        corrections_made=len(corrections),
        confidence=0.88,
        reference_lyrics=reference_lyrics,
        anchor_sequences=[],  # Empty for simple test
        gap_sequences=[],  # Empty for simple test
        resized_segments=segments.copy(),
        metadata={
            "title": "Hello",
            "artist": "Lionel Richie",
            "language": "en",
            "source": "whisper",
            "anchor_sequences_count": 0,
            "gap_sequences_count": 0,
            "total_words": 32,
            "correction_ratio": len(corrections) / 32,  # 2 corrections out of 32 words
        },
        correction_steps=[],  # Empty for simple test
        word_id_map={},  # Empty for simple test
        segment_id_map={},  # Empty for simple test
    )


def main():
    parser = argparse.ArgumentParser(
        description="Test the Lyrics Review UI locally"
    )
    parser.add_argument(
        "--audio",
        default=None,
        help="Optional: Path to audio file for playback testing"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8766,
        help="Port to run the server on (default: 8766)"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser automatically"
    )

    args = parser.parse_args()

    # Create temp directory for outputs
    cache_dir = tempfile.mkdtemp(prefix="lyrics_review_test_")

    # Handle audio file
    if args.audio:
        if not os.path.exists(args.audio):
            logger.error(f"Audio file not found: {args.audio}")
            sys.exit(1)
        audio_path = args.audio
    else:
        # Create a placeholder - UI will work but playback won't
        audio_path = os.path.join(cache_dir, "placeholder.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"")  # Empty file
        logger.warning("No audio file provided - playback will not work")

    logger.info("=" * 60)
    logger.info("LYRICS REVIEW UI TEST")
    logger.info("=" * 60)
    logger.info(f"Cache dir: {cache_dir}")
    if args.audio:
        logger.info(f"Audio file: {args.audio}")
    logger.info("")

    # Create correction result
    logger.info("[1/2] Creating sample correction data...")
    correction_result = create_sample_correction_result()
    logger.info(f"  Segments: {len(correction_result.original_segments)}")
    logger.info(f"  Corrections: {len(correction_result.corrections)}")
    logger.info(f"  Reference sources: {list(correction_result.reference_lyrics.keys())}")
    logger.info("")

    # Create output config
    output_config = OutputConfig(
        output_dir=cache_dir,
        cache_dir=cache_dir,
        output_styles_json="{}",
        styles={},
    )

    # Start review server
    logger.info("[2/2] Starting review server...")
    logger.info(f"  URL: http://localhost:{args.port}/")
    logger.info("")
    logger.info("=" * 60)
    logger.info("REVIEW UI READY")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Test the following features:")
    logger.info("  - Word editing (click on words)")
    logger.info("  - Segment editing (edit mode)")
    logger.info("  - Reference lyrics comparison")
    logger.info("  - AI correction review")
    logger.info("  - Find/replace")
    logger.info("")
    logger.info("Press Ctrl+C to stop the server.")
    logger.info("")

    server = ReviewServer(
        correction_result=correction_result,
        output_config=output_config,
        audio_filepath=audio_path,
        logger=logger,
    )

    try:
        # Start server with proper browser URL (includes apiUrl param)
        import webbrowser
        import threading
        import urllib.parse

        # Build the correct URL with apiUrl query parameter
        # Note: Backend routes are at /api/ (not /api/v1/)
        api_url = f"http://localhost:{args.port}/api"
        encoded_api_url = urllib.parse.quote(api_url, safe='')
        browser_url = f"http://localhost:{args.port}?baseApiUrl={encoded_api_url}"

        if not args.no_browser:
            def open_browser():
                import time
                time.sleep(1)  # Wait for server to start
                logger.info(f"Opening browser: {browser_url}")
                webbrowser.open(browser_url)

            threading.Thread(target=open_browser, daemon=True).start()
        else:
            logger.info(f"Open in browser: {browser_url}")

        import uvicorn
        uvicorn.run(server.app, host="0.0.0.0", port=args.port, log_level="warning")

    except KeyboardInterrupt:
        logger.info("\nServer stopped by user.")


if __name__ == "__main__":
    main()
