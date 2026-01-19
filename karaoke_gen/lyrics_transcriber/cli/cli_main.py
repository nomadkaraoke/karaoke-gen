#!/usr/bin/env python3
import warnings
# Suppress SyntaxWarnings from third-party packages that haven't updated for Python 3.13
warnings.filterwarnings("ignore", category=SyntaxWarning)

import argparse
import logging
import os
from pathlib import Path
from typing import Dict
from importlib.metadata import version
from dotenv import load_dotenv

from karaoke_gen.lyrics_transcriber import LyricsTranscriber
from karaoke_gen.lyrics_transcriber.core.controller import TranscriberConfig, LyricsConfig, OutputConfig


def create_arg_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="lyrics-transcriber",
        description="Create synchronised lyrics files in ASS and MidiCo LRC formats with word-level timestamps",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=52),
    )

    # Required arguments
    parser.add_argument(
        "audio_filepath",
        nargs="?",
        help="The audio file path to transcribe lyrics for.",
        default=argparse.SUPPRESS,
    )

    # Version
    package_version = version("lyrics-transcriber")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {package_version}")

    # Optional arguments
    parser.add_argument(
        "--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level. Default: INFO"
    )

    # Song identification
    song_group = parser.add_argument_group("Song Identification")
    song_group.add_argument("--artist", help="Song artist for lyrics lookup and auto-correction")
    song_group.add_argument("--title", help="Song title for lyrics lookup and auto-correction")
    song_group.add_argument("--lyrics_file", help="Path to file containing lyrics (txt, docx, or rtf format)")

    # API Credentials
    api_group = parser.add_argument_group("API Credentials")
    api_group.add_argument(
        "--audioshake_api_token", help="AudioShake API token for lyrics transcription. Can also use AUDIOSHAKE_API_TOKEN env var."
    )
    api_group.add_argument("--genius_api_token", help="Genius API token for lyrics fetching. Can also use GENIUS_API_TOKEN env var.")
    api_group.add_argument(
        "--spotify_cookie", help="Spotify sp_dc cookie value for lyrics fetching. Can also use SPOTIFY_COOKIE_SP_DC env var."
    )
    api_group.add_argument("--runpod_api_key", help="RunPod API key for Whisper transcription. Can also use RUNPOD_API_KEY env var.")
    api_group.add_argument(
        "--whisper_runpod_id", help="RunPod endpoint ID for Whisper transcription. Can also use WHISPER_RUNPOD_ID env var."
    )

    # Output options
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument("--output_dir", type=Path, help="Directory where output files will be saved. Default: current directory")
    output_group.add_argument(
        "--cache_dir",
        type=Path,
        help="Directory to cache downloaded/generated files. Default: ~/lyrics-transcriber-cache/",
    )
    output_group.add_argument(
        "--output_styles_json",
        type=Path,
        help="JSON file containing output style configurations for CDG and video generation",
    )
    output_group.add_argument(
        "--subtitle_offset",
        type=int,
        default=0,
        help="Offset subtitle timing by N milliseconds (positive or negative). Default: 0",
    )

    # Feature control group
    feature_group = parser.add_argument_group("Feature Control")
    feature_group.add_argument("--skip_lyrics_fetch", action="store_true", help="Skip fetching lyrics from online sources")
    feature_group.add_argument("--skip_transcription", action="store_true", help="Skip audio transcription process")
    feature_group.add_argument("--skip_correction", action="store_true", help="Skip lyrics correction process")
    feature_group.add_argument("--skip_plain_text", action="store_true", help="Skip generating plain text output files")
    feature_group.add_argument("--skip_lrc", action="store_true", help="Skip generating LRC file")
    feature_group.add_argument("--skip_cdg", action="store_true", help="Skip generating CDG karaoke files")
    feature_group.add_argument("--skip_video", action="store_true", help="Skip rendering karaoke video")
    feature_group.add_argument(
        "--video_resolution", choices=["4k", "1080p", "720p", "360p"], default="360p", help="Resolution of the karaoke video. Default: 360p"
    )
    feature_group.add_argument(
        "--skip_countdown", action="store_true", help="Skip adding countdown intro for songs that start within 3 seconds"
    )

    # Agentic AI flags
    feature_group.add_argument(
        "--use-agentic-ai",
        action="store_true",
        help="Enable experimental agentic AI correction (sets USE_AGENTIC_AI=1)",
    )
    feature_group.add_argument(
        "--ai-model",
        type=str,
        help="Preferred AI model identifier (e.g., 'anthropic/claude-4-sonnet', 'gpt-5', 'gemini-2.5-pro')",
    )

    return parser


def parse_args(parser: argparse.ArgumentParser, args_list: list[str] | None = None) -> argparse.Namespace:
    """Parse and process command line arguments."""
    # Use provided args_list for testing, otherwise use sys.argv
    args = parser.parse_args(args_list)

    # Set default cache_dir if not provided
    if not hasattr(args, "cache_dir") or args.cache_dir is None:
        args.cache_dir = Path(os.getenv("LYRICS_TRANSCRIBER_CACHE_DIR", os.path.join(os.path.expanduser("~"), "lyrics-transcriber-cache")))

    # Export agentic flags to environment for downstream usage
    if getattr(args, "use_agentic_ai", False):
        os.environ["USE_AGENTIC_AI"] = "1"
    if getattr(args, "ai_model", None):
        os.environ["AGENTIC_AI_MODEL"] = args.ai_model

    return args


def get_config_from_env() -> Dict[str, str]:
    """Load configuration from environment variables."""
    load_dotenv()
    return {
        "audioshake_api_token": os.getenv("AUDIOSHAKE_API_TOKEN"),
        "genius_api_token": os.getenv("GENIUS_API_TOKEN"),
        "rapidapi_key": os.getenv("RAPIDAPI_KEY"),
        "spotify_cookie": os.getenv("SPOTIFY_COOKIE_SP_DC"),
        "runpod_api_key": os.getenv("RUNPOD_API_KEY"),
        "whisper_runpod_id": os.getenv("WHISPER_RUNPOD_ID"),
    }


def setup_logging(log_level: str) -> logging.Logger:
    """Configure logging with consistent format."""
    logger = logging.getLogger("lyrics_transcriber")
    log_level_enum = getattr(logging, log_level.upper())
    logger.setLevel(log_level_enum)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(fmt="%(asctime)s.%(msecs)03d - %(levelname)s - %(module)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def create_configs(args: argparse.Namespace, env_config: Dict[str, str]) -> tuple[TranscriberConfig, LyricsConfig, OutputConfig]:
    """Create configuration objects from arguments and environment variables."""
    transcriber_config = TranscriberConfig(
        audioshake_api_token=args.audioshake_api_token or env_config.get("audioshake_api_token"),
        runpod_api_key=args.runpod_api_key or env_config.get("runpod_api_key"),
        whisper_runpod_id=args.whisper_runpod_id or env_config.get("whisper_runpod_id"),
    )

    lyrics_config = LyricsConfig(
        genius_api_token=args.genius_api_token or env_config.get("genius_api_token"),
        rapidapi_key=env_config.get("rapidapi_key"),
        spotify_cookie=args.spotify_cookie or env_config.get("spotify_cookie"),
        lyrics_file=args.lyrics_file,
    )

    output_config = OutputConfig(
        output_styles_json=str(args.output_styles_json),
        output_dir=str(args.output_dir) if args.output_dir else os.getcwd(),
        cache_dir=str(args.cache_dir),
        video_resolution=args.video_resolution,
        subtitle_offset_ms=args.subtitle_offset,
        fetch_lyrics=not args.skip_lyrics_fetch,
        run_transcription=not args.skip_transcription,
        run_correction=not args.skip_correction,
        generate_plain_text=not args.skip_plain_text,
        generate_lrc=not args.skip_lrc,
        generate_cdg=not args.skip_cdg,
        render_video=not args.skip_video,
        add_countdown=not args.skip_countdown,
    )

    return transcriber_config, lyrics_config, output_config


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser, logger: logging.Logger) -> None:
    """Validate command line arguments."""
    if not hasattr(args, "audio_filepath"):
        parser.print_help()
        logger.error("No audio filepath provided")
        exit(1)

    if not os.path.exists(args.audio_filepath):
        logger.error(f"Audio file not found: {args.audio_filepath}")
        exit(1)

    if args.artist and not args.title or args.title and not args.artist:
        logger.error("Both artist and title must be provided together")
        exit(1)


def main() -> None:
    """Main entry point for the CLI."""
    parser = create_arg_parser()
    
    # Check if --help or -h is in sys.argv to handle direct module execution
    import sys
    if '--help' in sys.argv or '-h' in sys.argv:
        parser.print_help()
        return
        
    args = parse_args(parser)

    # Set up logging first
    logger = setup_logging(args.log_level)

    # Validate arguments
    validate_args(args, parser, logger)

    # Load environment variables
    env_config = get_config_from_env()

    # Create configuration objects
    transcriber_config, lyrics_config, output_config = create_configs(args, env_config)

    try:
        # Initialize and run transcriber
        transcriber = LyricsTranscriber(
            audio_filepath=args.audio_filepath,
            artist=args.artist,
            title=args.title,
            transcriber_config=transcriber_config,
            lyrics_config=lyrics_config,
            output_config=output_config,
            logger=logger,
        )

        results = transcriber.process()

        # Log results
        logger.info("*** Success! ***")

        # Log all generated output files
        if results.original_txt:
            logger.info(f"Generated original transcription: {results.original_txt}")
        if results.corrections_json:
            logger.info(f"Generated corrections data: {results.corrections_json}")

        if results.corrected_txt:
            logger.info(f"Generated corrected lyrics: {results.corrected_txt}")
        if results.lrc_filepath:
            logger.info(f"Generated LRC file: {results.lrc_filepath}")

        if results.cdg_filepath:
            logger.info(f"Generated CDG file: {results.cdg_filepath}")
        if results.mp3_filepath:
            logger.info(f"Generated MP3 file: {results.mp3_filepath}")
        if results.cdg_zip_filepath:
            logger.info(f"Generated CDG ZIP archive: {results.cdg_zip_filepath}")

        if results.ass_filepath:
            logger.info(f"Generated ASS subtitles: {results.ass_filepath}")
        if results.video_filepath:
            logger.info(f"Generated video: {results.video_filepath}")

    except Exception as e:
        # Get the full exception traceback
        import traceback

        error_details = traceback.format_exc()

        # Log both the error message and the full traceback
        logger.error(f"Processing failed: {str(e)}\n\nFull traceback:\n{error_details}")
        exit(1)

if __name__ == "__main__":
    main()
