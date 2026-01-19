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
import pyperclip
from karaoke_gen import KaraokePrep
from karaoke_gen.karaoke_finalise import KaraokeFinalise
from karaoke_gen.audio_fetcher import UserCancelledError
from karaoke_gen.instrumental_review import (
    AudioAnalyzer,
    WaveformGenerator,
    InstrumentalReviewServer,
)
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


def run_instrumental_review(track: dict, logger: logging.Logger) -> str | None:
    """
    Run the instrumental review UI to let user select the best instrumental track.
    
    This analyzes the backing vocals, generates a waveform, and opens a browser
    with an interactive UI for reviewing and selecting the instrumental.
    
    Args:
        track: The track dictionary from KaraokePrep containing separated audio info
        logger: Logger instance
        
    Returns:
        Path to the selected instrumental file, or None to use the old numeric selection
    """
    track_dir = track.get("track_output_dir", ".")
    artist = track.get("artist", "")
    title = track.get("title", "")
    base_name = f"{artist} - {title}"
    
    # Get separation results
    separated = track.get("separated_audio", {})
    if not separated:
        logger.info("No separated audio found, skipping instrumental review UI")
        return None
    
    # Find the backing vocals file
    # Note: Paths in separated_audio may be relative to the original working directory,
    # but we've already chdir'd into track_dir. Use _resolve_path_for_cwd to fix paths.
    backing_vocals_path = None
    backing_vocals_result = separated.get("backing_vocals", {})
    for model, paths in backing_vocals_result.items():
        if paths.get("backing_vocals"):
            backing_vocals_path = _resolve_path_for_cwd(paths["backing_vocals"], track_dir)
            break
    
    if not backing_vocals_path or not os.path.exists(backing_vocals_path):
        logger.info("No backing vocals file found, skipping instrumental review UI")
        return None
    
    # Find the clean instrumental file
    clean_result = separated.get("clean_instrumental", {})
    raw_clean_path = clean_result.get("instrumental")
    clean_instrumental_path = _resolve_path_for_cwd(raw_clean_path, track_dir) if raw_clean_path else None
    
    if not clean_instrumental_path or not os.path.exists(clean_instrumental_path):
        logger.info("No clean instrumental file found, skipping instrumental review UI")
        return None
    
    # Find the combined instrumental (with backing vocals) file - these have "(Padded)" suffix if padded
    combined_result = separated.get("combined_instrumentals", {})
    with_backing_path = None
    for model, path in combined_result.items():
        resolved_path = _resolve_path_for_cwd(path, track_dir) if path else None
        if resolved_path and os.path.exists(resolved_path):
            with_backing_path = resolved_path
            break
    
    # Find the original audio file (with vocals)
    original_audio_path = None
    raw_original_path = track.get("input_audio_wav")
    if raw_original_path:
        original_audio_path = _resolve_path_for_cwd(raw_original_path, track_dir)
        if not os.path.exists(original_audio_path):
            logger.warning(f"Original audio file not found: {original_audio_path}")
            original_audio_path = None
    
    try:
        logger.info("=== Starting Instrumental Review ===")
        logger.info(f"Analyzing backing vocals: {backing_vocals_path}")
        
        # Analyze backing vocals
        analyzer = AudioAnalyzer()
        analysis = analyzer.analyze(backing_vocals_path)
        
        logger.info(f"Analysis complete:")
        logger.info(f"  Has audible content: {analysis.has_audible_content}")
        logger.info(f"  Total duration: {analysis.total_duration_seconds:.1f}s")
        logger.info(f"  Audible segments: {len(analysis.audible_segments)}")
        logger.info(f"  Recommendation: {analysis.recommended_selection.value}")
        
        # Generate waveform
        # Note: We're already in track_dir after chdir, so use current directory
        logger.info("Generating waveform visualization...")
        waveform_generator = WaveformGenerator()
        waveform_path = f"{base_name} (Backing Vocals Waveform).png"
        waveform_generator.generate(
            audio_path=backing_vocals_path,
            output_path=waveform_path,
            segments=analysis.audible_segments,
        )
        
        # Start the review server
        # Note: We're already in track_dir after chdir, so output_dir is "."
        logger.info("Starting instrumental review UI...")
        server = InstrumentalReviewServer(
            output_dir=".",
            base_name=base_name,
            analysis=analysis,
            waveform_path=waveform_path,
            backing_vocals_path=backing_vocals_path,
            clean_instrumental_path=clean_instrumental_path,
            with_backing_path=with_backing_path,
            original_audio_path=original_audio_path,
        )
        
        # Start server and open browser, wait for selection
        server.start_and_open_browser()
        
        logger.info("Waiting for instrumental selection in browser...")
        logger.info("(Close the browser tab or press Ctrl+C to cancel)")
        
        try:
            # Wait for user selection (blocking)
            server._selection_event.wait()
            selection = server.get_selection()
            
            logger.info(f"User selected: {selection}")
            
            # Stop the server
            server.stop()
            
            # Return the selected instrumental path
            if selection == "clean":
                return clean_instrumental_path
            elif selection == "with_backing":
                return with_backing_path
            elif selection == "custom":
                custom_path = server.get_custom_instrumental_path()
                if custom_path and os.path.exists(custom_path):
                    return custom_path
                else:
                    logger.warning("Custom instrumental not found, falling back to clean")
                    return clean_instrumental_path
            elif selection == "uploaded":
                uploaded_path = server.get_uploaded_instrumental_path()
                if uploaded_path and os.path.exists(uploaded_path):
                    return uploaded_path
                else:
                    logger.warning("Uploaded instrumental not found, falling back to clean")
                    return clean_instrumental_path
            else:
                logger.warning(f"Unknown selection: {selection}, falling back to numeric selection")
                return None
                
        except KeyboardInterrupt:
            logger.info("Instrumental review cancelled by user")
            server.stop()
            return None
            
    except Exception as e:
        logger.error(f"Error during instrumental review: {e}")
        logger.info("Falling back to numeric selection")
        return None


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
        skip_transcription_review=args.skip_transcription_review,
        subtitle_offset_ms=args.subtitle_offset_ms,
        style_params_json=args.style_params_json,
        style_overrides=style_overrides,
        background_video=args.background_video,
        background_video_darkness=args.background_video_darkness,
        auto_download=getattr(args, 'auto_download', False),
        render_video=not args.no_video,
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

        # Select instrumental file - either via web UI, auto-selection, or custom instrumental
        # This ALWAYS produces a selected file - no silent fallback to legacy code
        selected_instrumental_file = None
        skip_review = getattr(args, 'skip_instrumental_review', False)
        
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
        elif skip_review:
            # Auto-select instrumental when review is skipped (non-interactive mode)
            logger.info("Instrumental review skipped (--skip_instrumental_review), auto-selecting instrumental file...")
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
        else:
            # Run instrumental review web UI
            selected_instrumental_file = run_instrumental_review(
                track=track,
                logger=logger,
            )
            
            # If instrumental review failed/returned None, show error and exit
            # NO SILENT FALLBACK - we want to know if the new flow has issues
            if selected_instrumental_file is None:
                logger.error("")
                logger.error("=" * 70)
                logger.error("INSTRUMENTAL SELECTION FAILED")
                logger.error("=" * 70)
                logger.error("")
                logger.error("The instrumental review UI could not find the required files.")
                logger.error("")
                logger.error("Common causes:")
                logger.error("  - No backing vocals file was found (check stems/ directory)")
                logger.error("  - No clean instrumental was found (audio separation may have failed)")
                logger.error("  - Path resolution failed after directory change")
                logger.error("")
                logger.error("To investigate:")
                logger.error("  - Check the stems/ directory for: *Backing Vocals*.flac and *Instrumental*.flac")
                logger.error("  - Look for separation errors earlier in the log")
                logger.error("  - Verify audio separation completed without errors")
                logger.error("")
                logger.error("Workarounds:")
                logger.error("  - Re-run with --skip_instrumental_review to auto-select an instrumental")
                logger.error("  - Re-run the full pipeline to regenerate stems")
                logger.error("")
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
