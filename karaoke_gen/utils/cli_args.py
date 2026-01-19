#!/usr/bin/env python
"""
Shared CLI argument parser for karaoke-gen and karaoke-gen-remote.
This module provides a common argument parser to ensure feature parity.
"""
import argparse
import logging
import os
import tempfile
from importlib import metadata


def get_version() -> str:
    """Get package version."""
    try:
        return metadata.version("karaoke-gen")
    except metadata.PackageNotFoundError:
        return "unknown"


def create_parser(prog: str = "karaoke-gen") -> argparse.ArgumentParser:
    """
    Create the argument parser for karaoke-gen CLIs.
    
    Args:
        prog: Program name for the parser
    
    Returns:
        Configured ArgumentParser instance
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Generate karaoke videos with synchronized lyrics. Handles the entire process from downloading audio and lyrics to creating the final video.",
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(prog, max_help_position=54),
    )

    # Basic information
    parser.add_argument(
        "args",
        nargs="*",
        help="[File path] [Artist] [Title] of song to process. If a local audio file is provided, Artist and Title are optional but increase chance of fetching the correct lyrics. If only Artist and Title are provided (no file), audio will be searched and downloaded using flacfetch.",
    )

    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {get_version()}")

    # Workflow control
    workflow_group = parser.add_argument_group("Workflow Control")
    workflow_group.add_argument(
        "--prep-only",
        action="store_true",
        help="Only run the preparation phase (download audio, lyrics, separate stems, create title screens). Example: --prep-only",
    )
    workflow_group.add_argument(
        "--finalise-only",
        action="store_true",
        help="Only run the finalisation phase (remux, encode, organize). Must be run in a directory prepared by the prep phase. Example: --finalise-only",
    )
    workflow_group.add_argument(
        "--skip-transcription",
        action="store_true",
        help="Skip automatic lyrics transcription/synchronization. Use this to fall back to manual syncing. Example: --skip-transcription",
    )
    workflow_group.add_argument(
        "--skip-separation",
        action="store_true",
        help="Skip audio separation process. Example: --skip-separation",
    )
    workflow_group.add_argument(
        "--skip-lyrics",
        action="store_true",
        help="Skip fetching and processing lyrics. Example: --skip-lyrics",
    )
    workflow_group.add_argument(
        "--lyrics-only",
        action="store_true",
        help="Only process lyrics, skipping audio separation and title/end screen generation. Example: --lyrics-only",
    )
    workflow_group.add_argument(
        "--edit-lyrics",
        action="store_true",
        help="Edit lyrics of an existing track. This will backup existing outputs, re-run the lyrics transcription process, and update all outputs. Example: --edit-lyrics",
    )
    workflow_group.add_argument(
        "--resume", "-r",
        metavar="JOB_ID",
        help="Resume monitoring an existing job (remote mode only). Example: --resume abc12345",
    )
    workflow_group.add_argument(
        "--cancel",
        metavar="JOB_ID",
        help="Cancel a running job (remote mode only). Stops processing but keeps the job record. Example: --cancel abc12345",
    )
    workflow_group.add_argument(
        "--retry",
        metavar="JOB_ID",
        help="Retry a failed job from the last successful checkpoint (remote mode only). Example: --retry abc12345",
    )
    workflow_group.add_argument(
        "--delete",
        metavar="JOB_ID",
        help="Delete a job and all its files (remote mode only). Permanent removal. Example: --delete abc12345",
    )
    workflow_group.add_argument(
        "--list", "--list-jobs",
        action="store_true",
        dest="list_jobs",
        help="List all jobs (remote mode only). Shows job ID, status, artist, and title. Example: --list",
    )

    # Logging & Debugging
    debug_group = parser.add_argument_group("Logging & Debugging")
    debug_group.add_argument(
        "--log_level",
        default="info",
        help="Optional: logging level, e.g. info, debug, warning (default: %(default)s). Example: --log_level=debug",
    )
    debug_group.add_argument(
        "--dry_run",
        action="store_true",
        help="Optional: perform a dry run without making any changes. Example: --dry_run",
    )
    debug_group.add_argument(
        "--render_bounding_boxes",
        action="store_true",
        help="Optional: render bounding boxes around text regions for debugging. Example: --render_bounding_boxes",
    )

    # Input/Output Configuration
    io_group = parser.add_argument_group("Input/Output Configuration")
    io_group.add_argument(
        "--filename_pattern",
        help="Required if processing a folder: Python regex pattern to extract track names from filenames. Must contain a named group 'title'. Example: --filename_pattern='(?P<index>\\d+) - (?P<title>.+).mp3'",
    )
    io_group.add_argument(
        "--output_dir",
        default=".",
        help="Optional: directory to write output files (default: <current dir>). Example: --output_dir=/app/karaoke",
    )
    io_group.add_argument(
        "--no_track_subfolders",
        action="store_false",
        dest="no_track_subfolders",
        help="Optional: do NOT create a named subfolder for each track. Example: --no_track_subfolders",
    )
    io_group.add_argument(
        "--lossless_output_format",
        default="FLAC",
        help="Optional: lossless output format for separated audio (default: FLAC). Example: --lossless_output_format=WAV",
    )
    io_group.add_argument(
        "--output_png",
        type=lambda x: (str(x).lower() == "true"),
        default=True,
        help="Optional: output PNG format for title and end images (default: %(default)s). Example: --output_png=False",
    )
    io_group.add_argument(
        "--output_jpg",
        type=lambda x: (str(x).lower() == "true"),
        default=True,
        help="Optional: output JPG format for title and end images (default: %(default)s). Example: --output_jpg=False",
    )

    # Audio Fetching Configuration (flacfetch)
    fetch_group = parser.add_argument_group("Audio Fetching Configuration")
    fetch_group.add_argument(
        "--auto-download",
        action="store_true",
        help="Optional: Automatically select best audio source when searching by artist/title. When disabled (default), presents options for manual selection. Example: --auto-download",
    )

    # Audio Processing Configuration
    audio_group = parser.add_argument_group("Audio Processing Configuration")
    audio_group.add_argument(
        "--clean_instrumental_model",
        default="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        help="Optional: Model for clean instrumental separation (default: %(default)s).",
    )
    audio_group.add_argument(
        "--backing_vocals_models",
        nargs="+",
        default=["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"],
        help="Optional: List of models for backing vocals separation (default: %(default)s).",
    )
    audio_group.add_argument(
        "--other_stems_models",
        nargs="+",
        default=["htdemucs_6s.yaml"],
        help="Optional: List of models for other stems separation (default: %(default)s).",
    )

    default_model_dir_unix = "/tmp/audio-separator-models/"
    if os.name == "posix" and os.path.exists(default_model_dir_unix):
        default_model_dir = default_model_dir_unix
    else:
        default_model_dir = os.path.join(tempfile.gettempdir(), "audio-separator-models")

    audio_group.add_argument(
        "--model_file_dir",
        default=default_model_dir,
        help="Optional: model files directory (default: %(default)s). Example: --model_file_dir=/app/models",
    )
    audio_group.add_argument(
        "--existing_instrumental",
        help="Optional: Path to an existing instrumental audio file. If provided, audio separation will be skipped.",
    )
    audio_group.add_argument(
        "--instrumental_format",
        default="flac",
        help="Optional: format / file extension for instrumental track to use for remux (default: %(default)s). Example: --instrumental_format=mp3",
    )
    audio_group.add_argument(
        "--skip_instrumental_review",
        action="store_true",
        help="Optional: Skip the interactive instrumental review UI and use the old numeric selection. Example: --skip_instrumental_review",
    )

    # Lyrics Configuration
    lyrics_group = parser.add_argument_group("Lyrics Configuration")
    lyrics_group.add_argument(
        "--lyrics_artist",
        help="Optional: Override the artist name used for lyrics search. Example: --lyrics_artist='The Beatles'",
    )
    lyrics_group.add_argument(
        "--lyrics_title",
        help="Optional: Override the song title used for lyrics search. Example: --lyrics_title='Hey Jude'",
    )
    lyrics_group.add_argument(
        "--lyrics_file",
        help="Optional: Path to a file containing lyrics to use instead of fetching from online. Example: --lyrics_file='/path/to/lyrics.txt'",
    )
    lyrics_group.add_argument(
        "--subtitle_offset_ms",
        type=int,
        default=0,
        help="Optional: Adjust subtitle timing by N milliseconds (+ve delays, -ve advances). Example: --subtitle_offset_ms=500",
    )
    lyrics_group.add_argument(
        "--skip_transcription_review",
        action="store_true",
        help="Optional: Skip the review step after transcription. Example: --skip_transcription_review",
    )

    # Style Configuration
    style_group = parser.add_argument_group("Style Configuration")
    style_group.add_argument(
        "--theme",
        help="Optional: Theme ID for pre-made styles stored in GCS (e.g., 'nomad', 'default'). "
             "When using a theme, CDG/TXT are enabled by default. "
             "Example: --theme=nomad",
    )
    style_group.add_argument(
        "--style_params_json",
        help="Optional: Path to JSON file containing style configuration. "
             "Takes precedence over --theme if both are provided. "
             "Example: --style_params_json='/path/to/style_params.json'",
    )
    style_group.add_argument(
        "--style_override",
        action="append",
        help="Optional: Override a style parameter. Can be used multiple times. Example: --style_override 'intro.background_image=/path/to/new_image.png'",
    )
    style_group.add_argument(
        "--background_video",
        help="Optional: Path to video file to use as background instead of static image. Example: --background_video='/path/to/video.mp4'",
    )
    style_group.add_argument(
        "--background_video_darkness",
        type=int,
        default=50,
        help="Optional: Darkness overlay percentage (0-100) for video background (default: %(default)s). Example: --background_video_darkness=20",
    )

    # Finalisation Configuration
    finalise_group = parser.add_argument_group("Finalisation Configuration")
    finalise_group.add_argument(
        "--enable_cdg",
        action="store_true",
        help="Optional: Enable CDG ZIP generation during finalisation. Example: --enable_cdg",
    )
    finalise_group.add_argument(
        "--enable_txt",
        action="store_true",
        help="Optional: Enable TXT ZIP generation during finalisation. Example: --enable_txt",
    )
    finalise_group.add_argument(
        "--no-video",
        action="store_true",
        help="Optional: Skip video encoding and distribution, generate only CDG/TXT packages. Example: --no-video --enable_cdg",
    )
    finalise_group.add_argument(
        "--brand_prefix",
        help="Optional: Your brand prefix to calculate the next sequential number. Example: --brand_prefix=BRAND",
    )
    finalise_group.add_argument(
        "--organised_dir",
        help="Optional: Target directory where the processed folder will be moved. Example: --organised_dir='/path/to/Tracks-Organized'",
    )
    finalise_group.add_argument(
        "--organised_dir_rclone_root",
        help="Optional: Rclone path which maps to your organised_dir. Example: --organised_dir_rclone_root='dropbox:Media/Karaoke/Tracks-Organized'",
    )
    finalise_group.add_argument(
        "--public_share_dir",
        help="Optional: Public share directory for final files. Example: --public_share_dir='/path/to/Tracks-PublicShare'",
    )
    finalise_group.add_argument(
        "--enable_youtube_upload",
        action="store_true",
        help="Optional: Enable YouTube upload. For remote mode, uses server-side credentials. Example: --enable_youtube_upload",
    )
    finalise_group.add_argument(
        "--youtube_client_secrets_file",
        help="Optional: Path to youtube client secrets file (local mode only). Example: --youtube_client_secrets_file='/path/to/client_secret.json'",
    )
    finalise_group.add_argument(
        "--youtube_description_file",
        help="Optional: Path to youtube description template. Example: --youtube_description_file='/path/to/description.txt'",
    )
    finalise_group.add_argument(
        "--rclone_destination",
        help="Optional: Rclone destination for public_share_dir sync (local mode). Example: --rclone_destination='googledrive:KaraokeFolder'",
    )
    
    # Native API distribution (for remote CLI - uses server-side credentials)
    finalise_group.add_argument(
        "--dropbox_path",
        help="Optional: Dropbox folder path for organized output (remote mode). Example: --dropbox_path='/Karaoke/Tracks-Organized'",
    )
    finalise_group.add_argument(
        "--gdrive_folder_id",
        help="Optional: Google Drive folder ID for public share uploads (remote mode). Example: --gdrive_folder_id='1abc123xyz'",
    )
    
    finalise_group.add_argument(
        "--discord_webhook_url",
        help="Optional: Discord webhook URL for notifications. Example: --discord_webhook_url='https://discord.com/api/webhooks/...'",
    )
    finalise_group.add_argument(
        "--email_template_file",
        help="Optional: Path to email template file. Example: --email_template_file='/path/to/template.txt'",
    )
    finalise_group.add_argument(
        "--keep-brand-code",
        action="store_true",
        help="Optional: Use existing brand code from current directory instead of generating new one. Example: --keep-brand-code",
    )
    finalise_group.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Optional: Run in non-interactive mode, assuming yes to all prompts. Example: -y",
    )
    finalise_group.add_argument(
        "--test_email_template",
        action="store_true",
        help="Optional: Test the email template functionality with fake data. Example: --test_email_template",
    )
    
    # Remote CLI specific options
    remote_group = parser.add_argument_group("Remote Processing Options (karaoke-gen-remote only)")
    remote_group.add_argument(
        "--service-url",
        default=os.environ.get('KARAOKE_GEN_URL', ''),
        help="Backend service URL (or set KARAOKE_GEN_URL env var). Required for remote mode.",
    )
    remote_group.add_argument(
        "--review-ui-url",
        default=os.environ.get('REVIEW_UI_URL', os.environ.get('LYRICS_REVIEW_UI_URL', 'https://gen.nomadkaraoke.com')),
        help="Frontend base URL for remote mode. Defaults to 'https://gen.nomadkaraoke.com'. "
             "Review UIs are at /app/jobs/{id}/review and /app/jobs/{id}/instrumental. "
             "For local mode: defaults to bundled frontend (from lyrics_transcriber/frontend/). "
             "(env: REVIEW_UI_URL or LYRICS_REVIEW_UI_URL)",
    )
    remote_group.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.environ.get('POLL_INTERVAL', '5')),
        help="Seconds between status polls (default: 5)",
    )
    
    # Job tracking and filtering options (remote mode)
    tracking_group = parser.add_argument_group("Job Tracking Options (karaoke-gen-remote only)")
    tracking_group.add_argument(
        "--environment",
        default=os.environ.get('KARAOKE_GEN_ENVIRONMENT', ''),
        help="Tag jobs with environment (test/production/development). Sent as X-Environment header. Can also set via KARAOKE_GEN_ENVIRONMENT env var. Example: --environment=test",
    )
    tracking_group.add_argument(
        "--client-id",
        default=os.environ.get('KARAOKE_GEN_CLIENT_ID', ''),
        help="Tag jobs with client identifier for filtering. Sent as X-Client-ID header. Can also set via KARAOKE_GEN_CLIENT_ID env var. Example: --client-id=my-user-id",
    )
    tracking_group.add_argument(
        "--filter-environment",
        help="Filter jobs by environment when using --list. Example: --list --filter-environment=test",
    )
    tracking_group.add_argument(
        "--filter-client-id",
        help="Filter jobs by client ID when using --list. Example: --list --filter-client-id=my-user-id",
    )
    tracking_group.add_argument(
        "--bulk-delete",
        action="store_true",
        help="Delete all jobs matching filters. Requires --filter-environment or --filter-client-id. Example: --bulk-delete --filter-environment=test",
    )

    return parser


def process_style_overrides(style_override_list, logger=None):
    """
    Process style override arguments into a dictionary.
    
    Args:
        style_override_list: List of override strings in 'key=value' format
        logger: Optional logger instance
    
    Returns:
        Dictionary of style overrides
    
    Raises:
        ValueError: If override format is invalid
    """
    style_overrides = {}
    if style_override_list:
        for override in style_override_list:
            try:
                key, value = override.split("=", 1)
                style_overrides[key] = value
            except ValueError:
                error_msg = f"Invalid style override format: {override}. Must be in 'key=value' format."
                if logger:
                    logger.error(error_msg)
                raise ValueError(error_msg)
    return style_overrides


def is_url(string: str) -> bool:
    """Simple check to determine if a string is a URL."""
    return string.startswith("http://") or string.startswith("https://")


def is_file(string: str) -> bool:
    """Check if a string is a valid file."""
    return os.path.isfile(string)
