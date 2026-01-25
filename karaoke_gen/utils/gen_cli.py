#!/usr/bin/env python
# Suppress SyntaxWarnings from third-party dependencies (pydub, syrics)
# that have invalid escape sequences in regex patterns (not yet fixed for Python 3.12+)
import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pydub")
warnings.filterwarnings("ignore", category=SyntaxWarning, module="syrics")

import argparse
import logging
from importlib import metadata
import tempfile
import os
import sys
import json
import asyncio
import time
import glob
import shutil
import pyperclip
from karaoke_gen import KaraokePrep
from karaoke_gen.karaoke_finalise import KaraokeFinalise
from karaoke_gen.audio_fetcher import UserCancelledError
from karaoke_gen.instrumental_review import AudioAnalyzer
from karaoke_gen.lyrics_transcriber.types import CorrectionResult
from karaoke_gen.lyrics_transcriber.review.server import ReviewServer
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.utils import sanitize_filename
from .cli_args import create_parser, process_style_overrides, is_url, is_file


def _resolve_path_for_cwd(path: str, track_dir: str) -> str:
    """
    Resolve a path that may have been created relative to the original working directory.
    
    After os.chdir(track_dir), paths like './TrackDir/stems/file.flac' become invalid.
    This function converts such paths to work from the new current directory.
    
    Args:
        path: The path to resolve (may be relative or absolute)
        track_dir: The track directory we've chdir'd into
        
    Returns:
        A path that's valid from the current working directory
    """
    if os.path.isabs(path):
        return path
    
    # Normalize both paths for comparison
    norm_path = os.path.normpath(path)
    norm_track_dir = os.path.normpath(track_dir)
    
    # If path starts with track_dir, strip it to get the relative path from within track_dir
    # e.g., './Four Lanes Male Choir - The White Rose/stems/file.flac' -> 'stems/file.flac'
    if norm_path.startswith(norm_track_dir + os.sep):
        return norm_path[len(norm_track_dir) + 1:]
    elif norm_path.startswith(norm_track_dir):
        return norm_path[len(norm_track_dir):].lstrip(os.sep) or '.'
    
    # If path doesn't start with track_dir, it might already be relative to track_dir
    # or it's a path that doesn't need transformation
    return path


def auto_select_instrumental(track: dict, track_dir: str, logger: logging.Logger) -> str:
    """
    Auto-select the best instrumental file when --skip_instrumental_review is used.
    
    Selection priority:
    1. Padded combined instrumental (+BV) - synchronized with vocals + backing vocals
    2. Non-padded combined instrumental (+BV) - has backing vocals
    3. Padded clean instrumental - synchronized with vocals
    4. Non-padded clean instrumental - basic instrumental
    
    Args:
        track: The track dictionary from KaraokePrep containing separated audio info
        track_dir: The track output directory (we're already chdir'd into it)
        logger: Logger instance
        
    Returns:
        Path to the selected instrumental file
        
    Raises:
        FileNotFoundError: If no suitable instrumental file can be found
    """
    separated = track.get("separated_audio", {})
    
    # Look for combined instrumentals first (they include backing vocals)
    combined = separated.get("combined_instrumentals", {})
    for model, path in combined.items():
        if path:
            resolved = _resolve_path_for_cwd(path, track_dir)
            # Prefer padded version if it exists
            base, ext = os.path.splitext(resolved)
            padded = f"{base} (Padded){ext}"
            if os.path.exists(padded):
                logger.info(f"Auto-selected padded combined instrumental: {padded}")
                return padded
            if os.path.exists(resolved):
                logger.info(f"Auto-selected combined instrumental: {resolved}")
                return resolved
    
    # Fall back to clean instrumental
    clean = separated.get("clean_instrumental", {})
    if clean.get("instrumental"):
        resolved = _resolve_path_for_cwd(clean["instrumental"], track_dir)
        # Prefer padded version if it exists
        base, ext = os.path.splitext(resolved)
        padded = f"{base} (Padded){ext}"
        if os.path.exists(padded):
            logger.info(f"Auto-selected padded clean instrumental: {padded}")
            return padded
        if os.path.exists(resolved):
            logger.info(f"Auto-selected clean instrumental: {resolved}")
            return resolved
    
    # If separated_audio doesn't have what we need, search the directory
    # This handles edge cases and custom instrumentals
    logger.info("No instrumental found in separated_audio, searching directory...")
    instrumental_files = glob.glob("*(Instrumental*.flac") + glob.glob("*(Instrumental*.wav")
    
    # Sort to prefer padded versions and combined instrumentals
    padded_combined = [f for f in instrumental_files if "(Padded)" in f and "+BV" in f]
    if padded_combined:
        logger.info(f"Auto-selected from directory: {padded_combined[0]}")
        return padded_combined[0]
    
    padded_files = [f for f in instrumental_files if "(Padded)" in f]
    if padded_files:
        logger.info(f"Auto-selected from directory: {padded_files[0]}")
        return padded_files[0]
    
    combined_files = [f for f in instrumental_files if "+BV" in f]
    if combined_files:
        logger.info(f"Auto-selected from directory: {combined_files[0]}")
        return combined_files[0]
    
    if instrumental_files:
        logger.info(f"Auto-selected from directory: {instrumental_files[0]}")
        return instrumental_files[0]
    
    raise FileNotFoundError(
        "No instrumental file found. Audio separation may have failed. "
        "Check the stems/ directory for separated audio files."
    )


def run_combined_review(
    track: dict,
    track_dir: str,
    corrections_json_path: str,
    audio_filepath: str,
    style_params_json: str,
    render_video: bool,
    logger: logging.Logger,
) -> tuple[str | None, CorrectionResult | None]:
    """
    Run the sequential review UI (lyrics review → instrumental review).

    This starts the ReviewServer which serves a two-step review flow:
    1. Lyrics Review: User edits lyrics and previews video with vocals
    2. Instrumental Review: User selects the best instrumental track

    The user proceeds from step 1 to step 2, then submits both corrections
    and instrumental selection together.

    Args:
        track: The track dictionary from KaraokePrep containing separated audio info
        track_dir: The track output directory (for resolving relative paths)
        corrections_json_path: Path to the lyrics corrections JSON file
        audio_filepath: Path to the main audio file (vocals)
        style_params_json: Path to style parameters JSON for output config
        render_video: Whether video rendering is enabled (affects preview video)
        logger: Logger instance

    Returns:
        Tuple of (instrumental_selection, reviewed_correction_result)
        - instrumental_selection: "clean", "with_backing", etc. or None if not selected
        - reviewed_correction_result: Updated CorrectionResult after review, or None if failed
    """
    import json as json_module

    # Load existing correction result from JSON
    if not corrections_json_path or not os.path.exists(corrections_json_path):
        logger.warning(f"Corrections JSON not found: {corrections_json_path}")
        return None, None

    try:
        with open(corrections_json_path, "r", encoding="utf-8") as f:
            corrections_data = json_module.load(f)
        correction_result = CorrectionResult.from_dict(corrections_data)
        logger.info(f"Loaded correction result from {corrections_json_path}")
    except Exception as e:
        logger.error(f"Failed to load corrections JSON: {e}")
        return None, None

    # Get separation results
    separated = track.get("separated_audio", {})
    if not separated:
        logger.info("No separated audio found, running lyrics-only review")
        # Still run review for lyrics editing, just without instrumental options

    # Find audio paths
    backing_vocals_path = None
    backing_vocals_result = separated.get("backing_vocals", {})
    for model, paths in backing_vocals_result.items():
        if isinstance(paths, dict) and paths.get("backing_vocals"):
            backing_vocals_path = _resolve_path_for_cwd(paths["backing_vocals"], track_dir)
            if os.path.exists(backing_vocals_path):
                break
            backing_vocals_path = None

    clean_instrumental_path = None
    clean_result = separated.get("clean_instrumental", {})
    if isinstance(clean_result, dict) and clean_result.get("instrumental"):
        clean_instrumental_path = _resolve_path_for_cwd(clean_result["instrumental"], track_dir)
        if not os.path.exists(clean_instrumental_path):
            clean_instrumental_path = None

    with_backing_path = None
    combined_result = separated.get("combined_instrumentals", {})
    for model, path in combined_result.items():
        if path:
            resolved = _resolve_path_for_cwd(path, track_dir)
            if os.path.exists(resolved):
                with_backing_path = resolved
                break

    # Build instrumental options
    instrumental_options = []
    if clean_instrumental_path:
        instrumental_options.append({
            "id": "clean",
            "label": "Clean Instrumental",
            "audio_path": clean_instrumental_path,
        })
    if with_backing_path:
        instrumental_options.append({
            "id": "with_backing",
            "label": "With Backing Vocals",
            "audio_path": with_backing_path,
        })

    # Run backing vocals analysis
    backing_vocals_analysis = None
    if backing_vocals_path and os.path.exists(backing_vocals_path):
        try:
            analyzer = AudioAnalyzer()
            analysis = analyzer.analyze(backing_vocals_path)
            backing_vocals_analysis = {
                "has_audible_content": analysis.has_audible_content,
                "total_duration_seconds": analysis.total_duration_seconds,
                "audible_segments": [
                    {
                        "start_seconds": seg.start_seconds,
                        "end_seconds": seg.end_seconds,
                        "duration_seconds": seg.duration_seconds,
                        "avg_amplitude_db": seg.avg_amplitude_db,
                        "peak_amplitude_db": seg.peak_amplitude_db,
                    }
                    for seg in analysis.audible_segments
                ],
                "recommended_selection": analysis.recommended_selection.value,
                "total_audible_duration_seconds": analysis.total_audible_duration_seconds,
                "audible_percentage": analysis.audible_percentage,
            }
            logger.info(
                f"Backing vocals analysis: has_audible_content={analysis.has_audible_content}, "
                f"recommendation={analysis.recommended_selection.value}"
            )
        except Exception as e:
            logger.warning(f"Failed to analyze backing vocals: {e}")

    # Create output config for ReviewServer
    output_config = OutputConfig(
        output_styles_json=style_params_json,
        output_dir=track_dir,
        render_video=False,
        allow_preview_video=render_video,
        cache_dir=os.path.join(track_dir, ".cache"),
    )

    # Resolve audio_filepath for review
    resolved_audio = _resolve_path_for_cwd(audio_filepath, track_dir) if audio_filepath else None
    if resolved_audio and not os.path.exists(resolved_audio):
        # Try without resolution
        resolved_audio = audio_filepath if os.path.exists(audio_filepath) else None

    if not resolved_audio:
        logger.warning("No audio file found for review")

    logger.info("=== Starting Interactive Review (Lyrics → Instrumental) ===")
    if instrumental_options:
        logger.info(f"Prepared {len(instrumental_options)} instrumental options for selection")
    else:
        logger.info("No instrumental options available (lyrics-only review)")

    try:
        # Create and start review server with combined data
        review_server = ReviewServer(
            correction_result=correction_result,
            output_config=output_config,
            audio_filepath=resolved_audio or "",
            logger=logger,
            # Instrumental review data
            instrumental_options=instrumental_options,
            backing_vocals_analysis=backing_vocals_analysis,
            clean_instrumental_path=clean_instrumental_path,
            with_backing_path=with_backing_path,
            backing_vocals_path=backing_vocals_path,
        )
        reviewed_result = review_server.start()

        logger.info("Interactive review completed")

        # Get instrumental selection
        instrumental_selection = review_server.instrumental_selection
        if instrumental_selection:
            logger.info(f"User selected instrumental: {instrumental_selection}")

        # Save reviewed result back to JSON
        try:
            with open(corrections_json_path, "w", encoding="utf-8") as f:
                json_module.dump(reviewed_result.to_dict(), f, indent=2)
            logger.info(f"Saved reviewed corrections to {corrections_json_path}")
        except Exception as e:
            logger.warning(f"Failed to save reviewed corrections: {e}")

        return instrumental_selection, reviewed_result

    except KeyboardInterrupt:
        logger.info("Combined review cancelled by user")
        return None, None
    except Exception as e:
        logger.error(f"Error during interactive review: {e}")
        return None, None



async def async_main():
    logger = logging.getLogger(__name__)
    # Prevent log propagation to root logger to avoid duplicate logs
    # when external packages (like lyrics_converter) configure root logger handlers
    logger.propagate = False
    log_handler = logging.StreamHandler()
    log_formatter = logging.Formatter(fmt="%(asctime)s.%(msecs)03d - %(levelname)s - %(module)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log_handler.setFormatter(log_formatter)
    logger.addHandler(log_handler)

    # Use shared CLI parser
    parser = create_parser(prog="karaoke-gen")
    args = parser.parse_args()

    # Set review UI URL environment variable for the lyrics transcriber review server
    # Only set this if the user explicitly wants to use a dev server (e.g., http://localhost:5173)
    # By default, let the ReviewServer use its bundled local frontend (served from lyrics_transcriber/frontend/)
    # This enables local iteration on the frontend without redeploying
    if hasattr(args, 'review_ui_url') and args.review_ui_url:
        # Check if user provided a custom value (not the default hosted URL)
        # For local karaoke-gen CLI, we want to use the bundled local frontend by default
        # Only set LYRICS_REVIEW_UI_URL if user explicitly provides a dev server URL
        default_hosted_urls = [
            'https://gen.nomadkaraoke.com',
            'https://gen.nomadkaraoke.com/',
            'https://gen.nomadkaraoke.com/lyrics',
            'https://gen.nomadkaraoke.com/lyrics/'
        ]
        if args.review_ui_url.rstrip('/') not in [url.rstrip('/') for url in default_hosted_urls]:
            # User explicitly wants a specific URL (e.g., Vite dev server)
            os.environ['LYRICS_REVIEW_UI_URL'] = args.review_ui_url
        
    # Process style overrides
    try:
        style_overrides = process_style_overrides(args.style_override, logger)
    except ValueError:
        sys.exit(1)

    # Handle --list-themes flag
    if args.list_themes:
        from backend.services.theme_service import ThemeService
        theme_service = ThemeService()
        themes = theme_service.list_themes()
        print("Available themes:")
        for theme in themes:
            marker = " (default)" if theme.is_default else ""
            print(f"  {theme.id}: {theme.name}{marker}")
            print(f"    {theme.description}")
        sys.exit(0)

    # Handle --validate-theme flag
    if args.validate_theme:
        from karaoke_gen.style_loader import validate_theme_completeness, load_style_params_from_file

        # Load style params from theme or file
        if args.theme:
            from backend.services.theme_service import ThemeService
            theme_service = ThemeService()
            theme = theme_service.get_theme(args.theme)
            if not theme:
                logger.error(f"Theme not found: {args.theme}")
                sys.exit(1)
            style_params = theme.style_params
            logger.info(f"Validating theme: {args.theme}")
        elif args.style_params_json:
            style_params = load_style_params_from_file(args.style_params_json, logger)
            logger.info(f"Validating style file: {args.style_params_json}")
        else:
            logger.error("--validate-theme requires either --theme or --style_params_json")
            sys.exit(1)

        # Validate completeness
        is_complete, missing = validate_theme_completeness(style_params, logger)
        if is_complete:
            logger.info("✓ Theme is complete with all required parameters")
            sys.exit(0)
        else:
            logger.error(f"✗ Theme is incomplete. Missing: {missing}")
            sys.exit(1)

    # Handle test email template case first
    if args.test_email_template:
        log_level = getattr(logging, args.log_level.upper())
        logger.setLevel(log_level)
        logger.info("Testing email template functionality...")
        kfinalise = KaraokeFinalise(
            log_formatter=log_formatter,
            log_level=log_level,
            email_template_file=args.email_template_file,
        )
        kfinalise.test_email_template()
        return

    # Handle edit-lyrics mode
    if args.edit_lyrics:
        log_level = getattr(logging, args.log_level.upper())
        logger.setLevel(log_level)
        logger.info("Running in edit-lyrics mode...")
        
        # Get the current directory name to extract artist and title
        current_dir = os.path.basename(os.getcwd())
        logger.info(f"Current directory: {current_dir}")
        
        # Extract artist and title from directory name
        # Format could be either "Artist - Title" or "BRAND-XXXX - Artist - Title"
        if " - " not in current_dir:
            logger.error("Current directory name does not contain ' - ' separator. Cannot extract artist and title.")
            sys.exit(1)
            return  # Explicit return for testing
            
        parts = current_dir.split(" - ")
        if len(parts) == 2:
            artist, title = parts
        elif len(parts) >= 3:
            # Handle brand code format: "BRAND-XXXX - Artist - Title"
            artist = parts[1]
            title = " - ".join(parts[2:])
        else:
            logger.error(f"Could not parse artist and title from directory name: {current_dir}")
            sys.exit(1)
            return  # Explicit return for testing
            
        logger.info(f"Extracted artist: {artist}, title: {title}")
        
        # Initialize KaraokePrep
        kprep_coroutine = KaraokePrep(
            artist=artist,
            title=title,
            input_media=None,  # Will be set by backup_existing_outputs
            dry_run=args.dry_run,
            log_formatter=log_formatter,
            log_level=log_level,
            render_bounding_boxes=args.render_bounding_boxes,
            output_dir=".",  # We're already in the track directory
            create_track_subfolders=False,  # Don't create subfolders, we're already in one
            lossless_output_format=args.lossless_output_format,
            output_png=args.output_png,
            output_jpg=args.output_jpg,
            clean_instrumental_model=args.clean_instrumental_model,
            backing_vocals_models=args.backing_vocals_models,
            other_stems_models=args.other_stems_models,
            model_file_dir=args.model_file_dir,
            skip_separation=True,  # Skip separation as we already have the audio files
            lyrics_artist=args.lyrics_artist or artist,
            lyrics_title=args.lyrics_title or title,
            lyrics_file=args.lyrics_file,
            skip_lyrics=False,  # We want to process lyrics
            skip_transcription=False,  # We want to transcribe
            skip_transcription_review=args.skip_transcription_review,
            subtitle_offset_ms=args.subtitle_offset_ms,
            style_params_json=args.style_params_json,
            style_overrides=style_overrides,
            background_video=args.background_video,
            background_video_darkness=args.background_video_darkness,
            auto_download=getattr(args, 'auto_download', False),
        )
        # No await needed for constructor
        kprep = kprep_coroutine
        
        # Backup existing outputs and get the input audio file
        track_output_dir = os.getcwd()
        input_audio_wav = kprep.file_handler.backup_existing_outputs(track_output_dir, artist, title)
        kprep.input_media = input_audio_wav
        
        # Run KaraokePrep
        try:
            tracks = await kprep.process()
        except UserCancelledError:
            logger.info("Operation cancelled by user")
            return
        except KeyboardInterrupt:
            logger.info("Operation cancelled by user (Ctrl+C)")
            return
        
        # Filter out None tracks (can happen if prep failed for some tracks)
        tracks = [t for t in tracks if t is not None] if tracks else []
        
        if not tracks:
            logger.warning("No tracks to process")
            return
        
        # Load CDG styles if CDG generation is enabled
        cdg_styles = None
        if args.enable_cdg:
            if not args.style_params_json:
                logger.error("CDG styles JSON file path (--style_params_json) is required when --enable_cdg is used")
                sys.exit(1)
                return  # Explicit return for testing
            try:
                with open(args.style_params_json, "r") as f:
                    style_params = json.loads(f.read())
                    cdg_styles = style_params["cdg"]
            except FileNotFoundError:
                logger.error(f"CDG styles configuration file not found: {args.style_params_json}")
                sys.exit(1)
                return  # Explicit return for testing
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in CDG styles configuration file: {e}")
                sys.exit(1)
                return  # Explicit return for testing
            except KeyError:
                logger.error(f"'cdg' key not found in style parameters file: {args.style_params_json}")
                sys.exit(1)
                return # Explicit return for testing
        
        # Run KaraokeFinalise with keep_brand_code=True and replace_existing=True
        kfinalise = KaraokeFinalise(
            log_formatter=log_formatter,
            log_level=log_level,
            dry_run=args.dry_run,
            instrumental_format=args.instrumental_format,
            enable_cdg=args.enable_cdg,
            enable_txt=args.enable_txt,
            brand_prefix=args.brand_prefix,
            organised_dir=args.organised_dir,
            organised_dir_rclone_root=args.organised_dir_rclone_root,
            public_share_dir=args.public_share_dir,
            youtube_client_secrets_file=args.youtube_client_secrets_file,
            youtube_description_file=args.youtube_description_file,
            rclone_destination=args.rclone_destination,
            discord_webhook_url=args.discord_webhook_url,
            email_template_file=args.email_template_file,
            cdg_styles=cdg_styles,
            keep_brand_code=True,  # Always keep brand code in edit mode
            non_interactive=args.yes,
        )
        
        try:
            final_track = kfinalise.process(replace_existing=True)  # Replace existing YouTube video
            logger.info(f"Successfully completed editing lyrics for: {artist} - {title}")
            
            # Display summary of outputs
            logger.info(f"Karaoke lyrics edit complete! Output files:")
            logger.info(f"")
            logger.info(f"Track: {final_track['artist']} - {final_track['title']}")
            logger.info(f"")
            logger.info(f"Working Files:")
            logger.info(f" Video With Vocals: {final_track['video_with_vocals']}")
            logger.info(f" Video With Instrumental: {final_track['video_with_instrumental']}")
            logger.info(f"")
            logger.info(f"Final Videos:")
            logger.info(f" Lossless 4K MP4 (PCM): {final_track['final_video']}")
            logger.info(f" Lossless 4K MKV (FLAC): {final_track['final_video_mkv']}")
            logger.info(f" Lossy 4K MP4 (AAC): {final_track['final_video_lossy']}")
            logger.info(f" Lossy 720p MP4 (AAC): {final_track['final_video_720p']}")

            if "final_karaoke_cdg_zip" in final_track or "final_karaoke_txt_zip" in final_track:
                logger.info(f"")
                logger.info(f"Karaoke Files:")

            if "final_karaoke_cdg_zip" in final_track:
                logger.info(f" CDG+MP3 ZIP: {final_track['final_karaoke_cdg_zip']}")

            if "final_karaoke_txt_zip" in final_track:
                logger.info(f" TXT+MP3 ZIP: {final_track['final_karaoke_txt_zip']}")

            if final_track["brand_code"]:
                logger.info(f"")
                logger.info(f"Organization:")
                logger.info(f" Brand Code: {final_track['brand_code']}")
                logger.info(f" Directory: {final_track['new_brand_code_dir_path']}")

            if final_track["youtube_url"] or final_track["brand_code_dir_sharing_link"]:
                logger.info(f"")
                logger.info(f"Sharing:")

            if final_track["brand_code_dir_sharing_link"]:
                logger.info(f" Folder Link: {final_track['brand_code_dir_sharing_link']}")
                try:
                    time.sleep(1)  # Brief pause between clipboard operations
                    pyperclip.copy(final_track["brand_code_dir_sharing_link"])
                    logger.info(f" (Folder link copied to clipboard)")
                except Exception as e:
                    logger.warning(f" Failed to copy folder link to clipboard: {str(e)}")

            if final_track["youtube_url"]:
                logger.info(f" YouTube URL: {final_track['youtube_url']}")
                try:
                    pyperclip.copy(final_track["youtube_url"])
                    logger.info(f" (YouTube URL copied to clipboard)")
                except Exception as e:
                    logger.warning(f" Failed to copy YouTube URL to clipboard: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Error during finalisation: {str(e)}")
            raise e
            
        return

    # Handle finalise-only mode
    if args.finalise_only:
        log_level = getattr(logging, args.log_level.upper())
        logger.setLevel(log_level)
        logger.info("Running in finalise-only mode...")
        
        # Load CDG styles if CDG generation is enabled
        cdg_styles = None
        if args.enable_cdg:
            if not args.style_params_json:
                logger.error("CDG styles JSON file path (--style_params_json) is required when --enable_cdg is used")
                sys.exit(1)
                return  # Explicit return for testing
            try:
                with open(args.style_params_json, "r") as f:
                    style_params = json.loads(f.read())
                    cdg_styles = style_params["cdg"]
            except FileNotFoundError:
                logger.error(f"CDG styles configuration file not found: {args.style_params_json}")
                sys.exit(1)
                return  # Explicit return for testing
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in CDG styles configuration file: {e}")
                sys.exit(1)
                return  # Explicit return for testing
            except KeyError:
                logger.error(f"'cdg' key not found in style parameters file: {args.style_params_json}")
                sys.exit(1)
                return # Explicit return for testing
        
        kfinalise = KaraokeFinalise(
            log_formatter=log_formatter,
            log_level=log_level,
            dry_run=args.dry_run,
            instrumental_format=args.instrumental_format,
            enable_cdg=args.enable_cdg,
            enable_txt=args.enable_txt,
            brand_prefix=args.brand_prefix,
            organised_dir=args.organised_dir,
            organised_dir_rclone_root=args.organised_dir_rclone_root,
            public_share_dir=args.public_share_dir,
            youtube_client_secrets_file=args.youtube_client_secrets_file,
            youtube_description_file=args.youtube_description_file,
            rclone_destination=args.rclone_destination,
            discord_webhook_url=args.discord_webhook_url,
            email_template_file=args.email_template_file,
            cdg_styles=cdg_styles,
            keep_brand_code=getattr(args, 'keep_brand_code', False),
            non_interactive=args.yes,
        )
        
        try:
            track = kfinalise.process()
            logger.info(f"Successfully completed finalisation for: {track['artist']} - {track['title']}")
            
            # Display summary of outputs
            logger.info(f"Karaoke finalisation complete! Output files:")
            logger.info(f"")
            logger.info(f"Track: {track['artist']} - {track['title']}")
            logger.info(f"")
            logger.info(f"Working Files:")
            logger.info(f" Video With Vocals: {track['video_with_vocals']}")
            logger.info(f" Video With Instrumental: {track['video_with_instrumental']}")
            logger.info(f"")
            logger.info(f"Final Videos:")
            logger.info(f" Lossless 4K MP4 (PCM): {track['final_video']}")
            logger.info(f" Lossless 4K MKV (FLAC): {track['final_video_mkv']}")
            logger.info(f" Lossy 4K MP4 (AAC): {track['final_video_lossy']}")
            logger.info(f" Lossy 720p MP4 (AAC): {track['final_video_720p']}")

            if "final_karaoke_cdg_zip" in track or "final_karaoke_txt_zip" in track:
                logger.info(f"")
                logger.info(f"Karaoke Files:")

            if "final_karaoke_cdg_zip" in track:
                logger.info(f" CDG+MP3 ZIP: {track['final_karaoke_cdg_zip']}")

            if "final_karaoke_txt_zip" in track:
                logger.info(f" TXT+MP3 ZIP: {track['final_karaoke_txt_zip']}")

            if track["brand_code"]:
                logger.info(f"")
                logger.info(f"Organization:")
                logger.info(f" Brand Code: {track['brand_code']}")
                logger.info(f" Directory: {track['new_brand_code_dir_path']}")

            if track["youtube_url"] or track["brand_code_dir_sharing_link"]:
                logger.info(f"")
                logger.info(f"Sharing:")

            if track["brand_code_dir_sharing_link"]:
                logger.info(f" Folder Link: {track['brand_code_dir_sharing_link']}")
                try:
                    time.sleep(1)  # Brief pause between clipboard operations
                    pyperclip.copy(track["brand_code_dir_sharing_link"])
                    logger.info(f" (Folder link copied to clipboard)")
                except Exception as e:
                    logger.warning(f" Failed to copy folder link to clipboard: {str(e)}")

            if track["youtube_url"]:
                logger.info(f" YouTube URL: {track['youtube_url']}")
                try:
                    pyperclip.copy(track["youtube_url"])
                    logger.info(f" (YouTube URL copied to clipboard)")
                except Exception as e:
                    logger.warning(f" Failed to copy YouTube URL to clipboard: {str(e)}")
        except Exception as e:
            logger.error(f"An error occurred during finalisation, see stack trace below: {str(e)}")
            raise e
        
        return

    # For prep or full workflow, parse input arguments
    input_media, artist, title, filename_pattern = None, None, None, None

    if not args.args:
        parser.print_help()
        sys.exit(1)
        return  # Explicit return for testing

    # Allow 3 forms of positional arguments:
    # 1. URL or Media File only (may be single track URL, playlist URL, or local file)
    # 2. Artist and Title only
    # 3. URL, Artist, and Title
    if args.args and (is_url(args.args[0]) or is_file(args.args[0])):
        input_media = args.args[0]
        if len(args.args) > 2:
            artist = args.args[1]
            title = args.args[2]
        elif len(args.args) > 1:
            artist = args.args[1]
        else:
            logger.warning("Input media provided without Artist and Title, both will be guessed from title")

    elif os.path.isdir(args.args[0]):
        if not args.filename_pattern:
            logger.error("Filename pattern is required when processing a folder.")
            sys.exit(1)
            return  # Explicit return for testing
        if len(args.args) <= 1:
            logger.error("Second parameter provided must be Artist name; Artist is required when processing a folder.")
            sys.exit(1)
            return  # Explicit return for testing

        input_media = args.args[0]
        artist = args.args[1]
        filename_pattern = args.filename_pattern

    elif len(args.args) > 1:
        artist = args.args[0]
        title = args.args[1]
        if getattr(args, 'auto_download', False):
            logger.info(f"No input media provided, flacfetch will automatically search and download: {artist} - {title}")
        else:
            logger.info(f"No input media provided, flacfetch will search for: {artist} - {title} (interactive selection)")

    else:
        parser.print_help()
        sys.exit(1)
        return  # Explicit return for testing

    log_level = getattr(logging, args.log_level.upper())
    logger.setLevel(log_level)

    # Set up environment variables for lyrics-only mode
    if args.lyrics_only:
        args.skip_separation = True
        os.environ["KARAOKE_GEN_SKIP_AUDIO_SEPARATION"] = "1"
        os.environ["KARAOKE_GEN_SKIP_TITLE_END_SCREENS"] = "1"
        logger.info("Lyrics-only mode enabled: skipping audio separation and title/end screen generation")

    # Step 1: Run KaraokePrep
    # NOTE: For interactive review flow, we ALWAYS skip transcription review during KaraokePrep.
    # This is because transcription and separation run in parallel, and we want the interactive
    # review UI to have access to both lyrics data AND instrumental options.
    # The interactive review runs AFTER KaraokePrep completes (in the finalisation loop).
    # If user explicitly wants no review (either flag), we respect that.
    skip_transcription_review = args.skip_transcription_review
    skip_instrumental_review = getattr(args, 'skip_instrumental_review', False)
    review_enabled = not skip_transcription_review and not skip_instrumental_review

    # When review is enabled, defer video rendering until AFTER review
    # (only render in KaraokePrep if review is skipped for non-interactive mode)
    render_video_in_prep = (not args.no_video) and not review_enabled

    kprep_coroutine = KaraokePrep(
        input_media=input_media,
        artist=artist,
        title=title,
        filename_pattern=filename_pattern,
        dry_run=args.dry_run,
        log_formatter=log_formatter,
        log_level=log_level,
        render_bounding_boxes=args.render_bounding_boxes,
        output_dir=args.output_dir,
        create_track_subfolders=args.no_track_subfolders,
        lossless_output_format=args.lossless_output_format,
        output_png=args.output_png,
        output_jpg=args.output_jpg,
        clean_instrumental_model=args.clean_instrumental_model,
        backing_vocals_models=args.backing_vocals_models,
        other_stems_models=args.other_stems_models,
        model_file_dir=args.model_file_dir,
        existing_instrumental=args.existing_instrumental,
        skip_separation=args.skip_separation,
        lyrics_artist=args.lyrics_artist,
        lyrics_title=args.lyrics_title,
        lyrics_file=args.lyrics_file,
        skip_lyrics=args.skip_lyrics,
        skip_transcription=args.skip_transcription,
        skip_transcription_review=True,  # Always defer review to interactive review flow
        subtitle_offset_ms=args.subtitle_offset_ms,
        style_params_json=args.style_params_json,
        style_overrides=style_overrides,
        background_video=args.background_video,
        background_video_darkness=args.background_video_darkness,
        auto_download=getattr(args, 'auto_download', False),
        render_video=render_video_in_prep,  # Only render if review is skipped
    )
    # No await needed for constructor
    kprep = kprep_coroutine

    # Create final tracks data structure
    try:
        tracks = await kprep.process()
    except UserCancelledError:
        logger.info("Operation cancelled by user")
        return
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Operation cancelled by user (Ctrl+C)")
        return

    # Filter out None tracks (can happen if prep failed for some tracks)
    tracks = [t for t in tracks if t is not None] if tracks else []
    
    if not tracks:
        logger.warning("No tracks to process")
        return

    # If prep-only mode, we're done
    if args.prep_only:
        logger.info("Prep-only mode: skipping finalisation phase")
        return

    # Step 2: For each track, run KaraokeFinalise
    for track in tracks:
        logger.info(f"Starting finalisation phase for {track['artist']} - {track['title']}...")

        # Use the track directory that was actually created by KaraokePrep
        track_dir = track["track_output_dir"]
        if not os.path.exists(track_dir):
            logger.error(f"Track directory not found: {track_dir}")
            continue

        logger.info(f"Changing to directory: {track_dir}")
        os.chdir(track_dir)

        # Select instrumental file - either via interactive review, auto-selection, or custom
        # Combined review shows both lyrics editor AND instrumental selector in same UI
        selected_instrumental_file = None
        skip_all_review = getattr(args, 'skip_instrumental_review', False)

        # Check if a custom instrumental was provided (via --existing_instrumental)
        # In this case, the instrumental is already chosen - skip review entirely
        separated_audio = track.get("separated_audio", {})
        custom_instrumental = separated_audio.get("Custom", {}).get("instrumental")

        if custom_instrumental:
            # Custom instrumental was provided - use it directly, no review needed
            resolved_path = _resolve_path_for_cwd(custom_instrumental, track_dir)
            if os.path.exists(resolved_path):
                logger.info(f"Using custom instrumental (--existing_instrumental): {resolved_path}")
                selected_instrumental_file = resolved_path
            else:
                logger.error(f"Custom instrumental file not found: {resolved_path}")
                logger.error("The file may have been moved or deleted after preparation.")
                sys.exit(1)
                return  # Explicit return for testing
        elif skip_all_review:
            # Auto-select instrumental when all review is skipped (non-interactive mode)
            logger.info("All review skipped (--skip_instrumental_review), auto-selecting instrumental file...")
            try:
                selected_instrumental_file = auto_select_instrumental(
                    track=track,
                    track_dir=track_dir,
                    logger=logger,
                )
            except FileNotFoundError as e:
                logger.error(f"Failed to auto-select instrumental: {e}")
                logger.error("Check that audio separation completed successfully.")
                sys.exit(1)
                return  # Explicit return for testing
        elif review_enabled:
            # Run interactive review UI (lyrics review → instrumental review)
            # Find the corrections JSON file
            artist_name = track.get("artist", "Unknown")
            title_name = track.get("title", "Unknown")
            # Sanitize for filename matching
            def sanitize(s):
                for char in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
                    s = s.replace(char, "_")
                return s.rstrip(" ")

            sanitized_artist = sanitize(artist_name)
            sanitized_title = sanitize(title_name)
            corrections_filename = f"{sanitized_artist} - {sanitized_title} (Lyrics Corrections).json"

            # After os.chdir(track_dir), use paths relative to current directory
            # Check lyrics directory first, then track directory root
            lyrics_dir = "lyrics"
            corrections_json_path = os.path.join(lyrics_dir, corrections_filename)
            if not os.path.exists(corrections_json_path):
                corrections_json_path = corrections_filename

            # Get audio file path
            audio_filepath = track.get("input_audio_wav", "")

            logger.info("Running interactive review (lyrics → instrumental)...")
            instrumental_selection, reviewed_result = run_combined_review(
                track=track,
                track_dir=track_dir,
                corrections_json_path=corrections_json_path,
                audio_filepath=audio_filepath,
                style_params_json=args.style_params_json,
                render_video=not args.no_video,
                logger=logger,
            )

            # After review, render the video (was deferred during KaraokePrep)
            if not args.no_video and reviewed_result:
                logger.info("=== Rendering Video After Review ===")

                # Set up paths (we're already in track_dir due to os.chdir above)
                sanitized_artist = sanitize_filename(artist_name)
                sanitized_title = sanitize_filename(title_name)
                lyrics_dir = "lyrics"  # Relative to current dir (track_dir)
                cache_dir = "cache"    # Relative to current dir (track_dir)
                os.makedirs(cache_dir, exist_ok=True)

                # Process countdown (pad audio, shift timestamps)
                logger.info("Processing countdown intro (if needed)...")
                countdown_processor = CountdownProcessor(
                    cache_dir=cache_dir,
                    logger=logger,
                )

                # Resolve audio path for current working directory (we've os.chdir'd into track_dir)
                resolved_audio_filepath = _resolve_path_for_cwd(audio_filepath, track_dir)

                reviewed_result, padded_audio_path, padding_added, padding_seconds = countdown_processor.process(
                    correction_result=reviewed_result,
                    audio_filepath=resolved_audio_filepath,
                )

                # Update track with countdown info
                if padding_added:
                    track["countdown_padding_added"] = True
                    track["countdown_padding_seconds"] = padding_seconds
                    track["padded_vocals_audio"] = padded_audio_path
                    logger.info(
                        f"Added {padding_seconds}s countdown padding to audio and shifted timestamps."
                    )
                else:
                    logger.info("No countdown needed - song starts after 3 seconds")

                # Save the updated corrections with countdown timestamps
                with open(corrections_json_path, 'w', encoding='utf-8') as f:
                    json.dump(reviewed_result.to_dict(), f, indent=2)
                logger.info(f"Saved countdown-adjusted corrections to: {corrections_json_path}")

                # Render video with the reviewed lyrics
                logger.info("Rendering karaoke video with synchronized lyrics...")

                output_config = OutputConfig(
                    output_dir=lyrics_dir,
                    cache_dir=cache_dir,
                    output_styles_json=args.style_params_json,
                    render_video=True,
                    generate_cdg=False,
                    generate_plain_text=False,
                    generate_lrc=False,
                    video_resolution="4k",
                )

                output_generator = OutputGenerator(output_config, logger)
                output_prefix = f"{sanitized_artist} - {sanitized_title}"

                outputs = output_generator.generate_outputs(
                    transcription_corrected=reviewed_result,
                    lyrics_results={},
                    audio_filepath=padded_audio_path,
                    output_prefix=output_prefix,
                )

                # Copy video to expected location in track directory (we're in track_dir)
                if outputs and outputs.video:
                    source_video = outputs.video
                    artist_title = f"{sanitized_artist} - {sanitized_title}"
                    dest_video = f"{artist_title} (With Vocals).mkv"  # Current dir is track_dir
                    shutil.copy2(source_video, dest_video)
                    logger.info(f"Video rendered successfully: {dest_video}")
                    track["with_vocals_video"] = dest_video

                    if outputs.ass:
                        track["ass_filepath"] = outputs.ass
                else:
                    logger.warning("Video rendering did not produce expected output")

            # Map instrumental selection to file path
            if instrumental_selection:
                if instrumental_selection == "clean":
                    clean_result = separated_audio.get("clean_instrumental", {})
                    if clean_result.get("instrumental"):
                        selected_instrumental_file = _resolve_path_for_cwd(
                            clean_result["instrumental"], track_dir
                        )
                elif instrumental_selection == "with_backing":
                    combined_result = separated_audio.get("combined_instrumentals", {})
                    for model, path in combined_result.items():
                        if path:
                            resolved = _resolve_path_for_cwd(path, track_dir)
                            if os.path.exists(resolved):
                                selected_instrumental_file = resolved
                                break
                else:
                    logger.warning(f"Unknown instrumental selection: {instrumental_selection}")

            # If no selection made in review, fall back to auto-select
            if not selected_instrumental_file:
                logger.info("No instrumental selected in review, auto-selecting...")
                try:
                    selected_instrumental_file = auto_select_instrumental(
                        track=track,
                        track_dir=track_dir,
                        logger=logger,
                    )
                except FileNotFoundError as e:
                    logger.error(f"Failed to auto-select instrumental: {e}")
                    sys.exit(1)
                    return  # Explicit return for testing
        else:
            # No review, no skip flag - shouldn't happen, but auto-select as fallback
            logger.info("Auto-selecting instrumental file...")
            try:
                selected_instrumental_file = auto_select_instrumental(
                    track=track,
                    track_dir=track_dir,
                    logger=logger,
                )
            except FileNotFoundError as e:
                logger.error(f"Failed to auto-select instrumental: {e}")
                sys.exit(1)
                return  # Explicit return for testing

        logger.info(f"Selected instrumental file: {selected_instrumental_file}")
        
        # Get countdown padding info from track (if vocals were padded, instrumental must match)
        countdown_padding_seconds = None
        if track.get("countdown_padding_added", False):
            countdown_padding_seconds = track.get("countdown_padding_seconds", 3.0)
            logger.info(f"Countdown padding detected: {countdown_padding_seconds}s (will be applied to instrumental if needed)")
        
        # Load CDG styles if CDG generation is enabled
        cdg_styles = None
        if args.enable_cdg:
            if not args.style_params_json:
                logger.error("CDG styles JSON file path (--style_params_json) is required when --enable_cdg is used")
                sys.exit(1)
                return  # Explicit return for testing
            try:
                with open(args.style_params_json, "r") as f:
                    style_params = json.loads(f.read())
                    cdg_styles = style_params["cdg"]
            except FileNotFoundError:
                logger.error(f"CDG styles configuration file not found: {args.style_params_json}")
                sys.exit(1)
                return  # Explicit return for testing
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in CDG styles configuration file: {e}")
                sys.exit(1)
                return  # Explicit return for testing
            except KeyError:
                logger.error(f"'cdg' key not found in style parameters file: {args.style_params_json}")
                sys.exit(1)
                return # Explicit return for testing

        kfinalise = KaraokeFinalise(
            log_formatter=log_formatter,
            log_level=log_level,
            dry_run=args.dry_run,
            instrumental_format=args.instrumental_format,
            enable_cdg=args.enable_cdg,
            enable_txt=args.enable_txt,
            brand_prefix=args.brand_prefix,
            organised_dir=args.organised_dir,
            organised_dir_rclone_root=args.organised_dir_rclone_root,
            public_share_dir=args.public_share_dir,
            youtube_client_secrets_file=args.youtube_client_secrets_file,
            youtube_description_file=args.youtube_description_file,
            rclone_destination=args.rclone_destination,
            discord_webhook_url=args.discord_webhook_url,
            email_template_file=args.email_template_file,
            cdg_styles=cdg_styles,
            keep_brand_code=getattr(args, 'keep_brand_code', False),
            non_interactive=args.yes,
            selected_instrumental_file=selected_instrumental_file,
            countdown_padding_seconds=countdown_padding_seconds,
            no_video=args.no_video,
        )

        try:
            final_track = kfinalise.process()
            logger.info(f"Successfully completed processing: {final_track['artist']} - {final_track['title']}")
            
            # Display summary of outputs
            logger.info(f"Karaoke generation complete! Output files:")
            logger.info(f"")
            logger.info(f"Track: {final_track['artist']} - {final_track['title']}")
            logger.info(f"")

            if not args.no_video:
                logger.info(f"Working Files:")
                logger.info(f" Video With Vocals: {final_track['video_with_vocals']}")
                logger.info(f" Video With Instrumental: {final_track['video_with_instrumental']}")
                logger.info(f"")
                logger.info(f"Final Videos:")
                logger.info(f" Lossless 4K MP4 (PCM): {final_track['final_video']}")
                logger.info(f" Lossless 4K MKV (FLAC): {final_track['final_video_mkv']}")
                logger.info(f" Lossy 4K MP4 (AAC): {final_track['final_video_lossy']}")
                logger.info(f" Lossy 720p MP4 (AAC): {final_track['final_video_720p']}")

            if "final_karaoke_cdg_zip" in final_track or "final_karaoke_txt_zip" in final_track:
                logger.info(f"")
                logger.info(f"Karaoke Files:")

            if "final_karaoke_cdg_zip" in final_track:
                logger.info(f" CDG+MP3 ZIP: {final_track['final_karaoke_cdg_zip']}")

            if "final_karaoke_txt_zip" in final_track:
                logger.info(f" TXT+MP3 ZIP: {final_track['final_karaoke_txt_zip']}")

            if final_track["brand_code"]:
                logger.info(f"")
                logger.info(f"Organization:")
                logger.info(f" Brand Code: {final_track['brand_code']}")
                logger.info(f" Directory: {final_track['new_brand_code_dir_path']}")

            if final_track["youtube_url"] or final_track["brand_code_dir_sharing_link"]:
                logger.info(f"")
                logger.info(f"Sharing:")

            if final_track["brand_code_dir_sharing_link"]:
                logger.info(f" Folder Link: {final_track['brand_code_dir_sharing_link']}")
                try:
                    time.sleep(1)  # Brief pause between clipboard operations
                    pyperclip.copy(final_track["brand_code_dir_sharing_link"])
                    logger.info(f" (Folder link copied to clipboard)")
                except Exception as e:
                    logger.warning(f" Failed to copy folder link to clipboard: {str(e)}")

            if final_track["youtube_url"]:
                logger.info(f" YouTube URL: {final_track['youtube_url']}")
                try:
                    pyperclip.copy(final_track["youtube_url"])
                    logger.info(f" (YouTube URL copied to clipboard)")
                except Exception as e:
                    logger.warning(f" Failed to copy YouTube URL to clipboard: {str(e)}")
        except Exception as e:
            logger.error(f"An error occurred during finalisation, see stack trace below: {str(e)}")
            raise e
        
        return


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
