import os
import sys
import re
import glob
import logging
import tempfile
import shutil
import asyncio
import signal
import time
import fcntl
import errno
import psutil
from datetime import datetime
import importlib.resources as pkg_resources
import json
from dotenv import load_dotenv
from .config import (
    load_style_params,
    setup_title_format,
    setup_end_format,
    get_video_durations,
    get_existing_images,
    setup_ffmpeg_command,
)
from .metadata import extract_info_for_online_media, parse_track_metadata
from .file_handler import FileHandler
from .audio_processor import AudioProcessor
from .lyrics_processor import LyricsProcessor
from .video_generator import VideoGenerator
from .video_background_processor import VideoBackgroundProcessor
from .audio_fetcher import create_audio_fetcher, AudioFetcherError, NoResultsError, UserCancelledError

# Import lyrics_transcriber components for post-review countdown and video rendering
from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.lyrics_transcriber.types import CorrectionResult
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig as LyricsOutputConfig


class KaraokePrep:
    def __init__(
        self,
        # Basic inputs
        input_media=None,
        artist=None,
        title=None,
        filename_pattern=None,
        # Logging & Debugging
        dry_run=False,
        logger=None,
        log_level=logging.DEBUG,
        log_formatter=None,
        render_bounding_boxes=False,
        # Input/Output Configuration
        output_dir=".",
        create_track_subfolders=False,
        lossless_output_format="FLAC",
        output_png=True,
        output_jpg=True,
        # Audio Processing Configuration
        clean_instrumental_model="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        backing_vocals_models=["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"],
        other_stems_models=["htdemucs_6s.yaml"],
        model_file_dir=os.path.join(tempfile.gettempdir(), "audio-separator-models"),
        existing_instrumental=None,
        # Lyrics Configuration
        lyrics_artist=None,
        lyrics_title=None,
        lyrics_file=None,
        skip_lyrics=False,
        skip_transcription=False,
        skip_transcription_review=False,
        render_video=True,
        subtitle_offset_ms=0,
        # Style Configuration
        style_params_json=None,
        style_overrides=None,
        # Add the new parameter
        skip_separation=False,
        # Video Background Configuration
        background_video=None,
        background_video_darkness=50,
        # Audio Fetcher Configuration
        auto_download=False,
    ):
        self.log_level = log_level
        self.log_formatter = log_formatter

        if logger is None:
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(log_level)
            # Prevent log propagation to root logger to avoid duplicate logs
            # when external packages (like lyrics_converter) configure root logger handlers
            self.logger.propagate = False

            self.log_handler = logging.StreamHandler()

            if self.log_formatter is None:
                self.log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(module)s - %(message)s")

            self.log_handler.setFormatter(self.log_formatter)
            self.logger.addHandler(self.log_handler)
        else:
            self.logger = logger

        self.logger.debug(f"KaraokePrep instantiating with input_media: {input_media} artist: {artist} title: {title}")

        self.dry_run = dry_run
        self.extractor = None  # Will be set later based on source (Original or yt-dlp extractor)
        self.media_id = None  # Will be set by parse_track_metadata if applicable
        self.url = None  # Will be set by parse_track_metadata if applicable
        self.input_media = input_media
        self.artist = artist
        self.title = title
        self.filename_pattern = filename_pattern

        # Input/Output - Keep these as they might be needed for logic outside handlers or passed to multiple handlers
        self.output_dir = output_dir
        self.lossless_output_format = lossless_output_format.lower()
        self.create_track_subfolders = create_track_subfolders
        self.output_png = output_png
        self.output_jpg = output_jpg

        # Lyrics Config - Keep needed ones
        self.lyrics_artist = lyrics_artist
        self.lyrics_title = lyrics_title
        self.lyrics_file = lyrics_file # Passed to LyricsProcessor
        self.skip_lyrics = skip_lyrics # Used in prep_single_track logic
        self.skip_transcription = skip_transcription # Passed to LyricsProcessor
        self.skip_transcription_review = skip_transcription_review # Passed to LyricsProcessor
        self.render_video = render_video # Passed to LyricsProcessor
        self.subtitle_offset_ms = subtitle_offset_ms # Passed to LyricsProcessor

        # Audio Config - Keep needed ones
        self.existing_instrumental = existing_instrumental # Used in prep_single_track logic
        self.skip_separation = skip_separation # Used in prep_single_track logic
        self.model_file_dir = model_file_dir # Passed to AudioProcessor

        # Style Config - Keep needed ones
        self.render_bounding_boxes = render_bounding_boxes # Passed to VideoGenerator
        self.style_params_json = style_params_json
        self.style_overrides = style_overrides
        self.temp_style_file = None

        # Video Background Config
        self.background_video = background_video
        self.background_video_darkness = background_video_darkness

        # Audio Fetcher Config (replaces yt-dlp)
        self.auto_download = auto_download  # If True, automatically select best audio source
        
        # Initialize audio fetcher for searching and downloading audio when no input file is provided
        self.audio_fetcher = create_audio_fetcher(logger=self.logger)

        # Load style parameters using the config module
        self.style_params = load_style_params(self.style_params_json, self.style_overrides, self.logger)

        # If overrides were applied, write to a temp file and update the path
        if self.style_overrides:
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".json") as temp_file:
                json.dump(self.style_params, temp_file, indent=2)
                self.temp_style_file = temp_file.name
                self.style_params_json = self.temp_style_file
                self.logger.info(f"Style overrides applied. Using temporary style file: {self.temp_style_file}")

        # Set up title and end formats using the config module
        self.title_format = setup_title_format(self.style_params)
        self.end_format = setup_end_format(self.style_params)

        # Get video durations and existing images using the config module
        self.intro_video_duration, self.end_video_duration = get_video_durations(self.style_params)
        self.existing_title_image, self.existing_end_image = get_existing_images(self.style_params)

        # Set up ffmpeg command using the config module
        self.ffmpeg_base_command = setup_ffmpeg_command(self.log_level)

        # Instantiate Handlers
        self.file_handler = FileHandler(
            logger=self.logger,
            ffmpeg_base_command=self.ffmpeg_base_command,
            create_track_subfolders=self.create_track_subfolders,
            dry_run=self.dry_run,
        )

        self.audio_processor = AudioProcessor(
             logger=self.logger,
             log_level=self.log_level,
             log_formatter=self.log_formatter,
             model_file_dir=self.model_file_dir,
             lossless_output_format=self.lossless_output_format,
             clean_instrumental_model=clean_instrumental_model, # Passed directly from args
             backing_vocals_models=backing_vocals_models, # Passed directly from args
             other_stems_models=other_stems_models, # Passed directly from args
             ffmpeg_base_command=self.ffmpeg_base_command,
        )

        self.lyrics_processor = LyricsProcessor(
             logger=self.logger,
             style_params_json=self.style_params_json,
             lyrics_file=self.lyrics_file,
             skip_transcription=self.skip_transcription,
             skip_transcription_review=self.skip_transcription_review,
             render_video=self.render_video,
             subtitle_offset_ms=self.subtitle_offset_ms,
        )

        self.video_generator = VideoGenerator(
             logger=self.logger,
             ffmpeg_base_command=self.ffmpeg_base_command,
             render_bounding_boxes=self.render_bounding_boxes,
             output_png=self.output_png,
             output_jpg=self.output_jpg,
        )

        # Instantiate VideoBackgroundProcessor if background_video is provided
        if self.background_video:
            self.logger.info(f"Video background enabled: {self.background_video}")
            self.video_background_processor = VideoBackgroundProcessor(
                logger=self.logger,
                ffmpeg_base_command=self.ffmpeg_base_command,
            )
        else:
            self.video_background_processor = None

        self.logger.debug(f"Initialized title_format with extra_text: {self.title_format['extra_text']}")
        self.logger.debug(f"Initialized title_format with extra_text_region: {self.title_format['extra_text_region']}")

        self.logger.debug(f"Initialized end_format with extra_text: {self.end_format['extra_text']}")
        self.logger.debug(f"Initialized end_format with extra_text_region: {self.end_format['extra_text_region']}")

        self.extracted_info = None  # Will be populated by extract_info_for_online_media if needed
        self.persistent_artist = None  # Used for playlists

        self.logger.debug(f"KaraokePrep lossless_output_format: {self.lossless_output_format}")

        # Use FileHandler method to check/create output dir
        if not os.path.exists(self.output_dir):
            self.logger.debug(f"Overall output dir {self.output_dir} did not exist, creating")
            os.makedirs(self.output_dir)
        else:
            self.logger.debug(f"Overall output dir {self.output_dir} already exists")

    def __del__(self):
        # Cleanup the temporary style file if it was created
        if self.temp_style_file and os.path.exists(self.temp_style_file):
            try:
                os.remove(self.temp_style_file)
                self.logger.debug(f"Removed temporary style file: {self.temp_style_file}")
            except OSError as e:
                self.logger.warning(f"Error removing temporary style file {self.temp_style_file}: {e}")

    # Compatibility methods for tests - these call the new functions in metadata.py
    def extract_info_for_online_media(self, input_url=None, input_artist=None, input_title=None):
        """Compatibility method that calls the function in metadata.py"""
        self.extracted_info = extract_info_for_online_media(input_url, input_artist, input_title, self.logger)
        return self.extracted_info

    def parse_single_track_metadata(self, input_artist, input_title):
        """Compatibility method that calls the function in metadata.py"""
        metadata_result = parse_track_metadata(self.extracted_info, input_artist, input_title, self.persistent_artist, self.logger)
        self.url = metadata_result["url"]
        self.extractor = metadata_result["extractor"]
        self.media_id = metadata_result["media_id"]
        self.artist = metadata_result["artist"]
        self.title = metadata_result["title"]

    def _scan_directory_for_instrumentals(self, track_output_dir, artist_title):
        """
        Scan the directory for existing instrumental files and build a separated_audio structure.
        
        This is used when transcription was skipped (existing files found) but we need to 
        pad instrumentals due to countdown padding.
        
        Args:
            track_output_dir: The track output directory to scan
            artist_title: The "{artist} - {title}" string for matching files
            
        Returns:
            Dictionary with separated_audio structure containing found instrumental paths
        """
        self.logger.info(f"Scanning directory for existing instrumentals: {track_output_dir}")
        
        separated_audio = {
            "clean_instrumental": {},
            "backing_vocals": {},
            "other_stems": {},
            "combined_instrumentals": {},
        }
        
        # Search patterns for instrumental files
        # Files are named like: "{artist} - {title} (Instrumental {model}).flac"
        # Or with backing vocals: "{artist} - {title} (Instrumental +BV {model}).flac"
        
        # Look for files in the track output directory
        search_dir = track_output_dir
        
        # Find all instrumental files (not padded ones - we want the originals)
        instrumental_pattern = os.path.join(search_dir, f"{artist_title} (Instrumental*.flac")
        instrumental_files = glob.glob(instrumental_pattern)
        
        # Also check for wav files
        instrumental_pattern_wav = os.path.join(search_dir, f"{artist_title} (Instrumental*.wav")
        instrumental_files.extend(glob.glob(instrumental_pattern_wav))
        
        self.logger.debug(f"Found {len(instrumental_files)} instrumental files")
        
        for filepath in instrumental_files:
            filename = os.path.basename(filepath)
            
            # Skip already padded files
            if "(Padded)" in filename:
                self.logger.debug(f"Skipping already padded file: {filename}")
                continue
            
            # Determine if it's a combined instrumental (+BV) or clean instrumental
            if "+BV" in filename or "+bv" in filename.lower():
                # Combined instrumental with backing vocals
                # Extract model name from filename
                # Pattern: "(Instrumental +BV {model}).flac"
                model_match = re.search(r'\(Instrumental \+BV ([^)]+)\)', filename)
                if model_match:
                    model_name = model_match.group(1).strip()
                    separated_audio["combined_instrumentals"][model_name] = filepath
                    self.logger.info(f"Found combined instrumental: {filename}")
            else:
                # Clean instrumental (no backing vocals)
                # Pattern: "(Instrumental {model}).flac"
                model_match = re.search(r'\(Instrumental ([^)]+)\)', filename)
                if model_match:
                    # Use as clean instrumental if we don't have one yet
                    if not separated_audio["clean_instrumental"].get("instrumental"):
                        separated_audio["clean_instrumental"]["instrumental"] = filepath
                        self.logger.info(f"Found clean instrumental: {filename}")
                    else:
                        # Additional clean instrumentals go to combined_instrumentals for padding
                        model_name = model_match.group(1).strip()
                        separated_audio["combined_instrumentals"][model_name] = filepath
                        self.logger.info(f"Found additional instrumental: {filename}")
        
        # Also look for backing vocals files
        backing_vocals_pattern = os.path.join(search_dir, f"{artist_title} (Backing Vocals*.flac")
        backing_vocals_files = glob.glob(backing_vocals_pattern)
        backing_vocals_pattern_wav = os.path.join(search_dir, f"{artist_title} (Backing Vocals*.wav")
        backing_vocals_files.extend(glob.glob(backing_vocals_pattern_wav))
        
        for filepath in backing_vocals_files:
            filename = os.path.basename(filepath)
            model_match = re.search(r'\(Backing Vocals ([^)]+)\)', filename)
            if model_match:
                model_name = model_match.group(1).strip()
                if model_name not in separated_audio["backing_vocals"]:
                    separated_audio["backing_vocals"][model_name] = {"backing_vocals": filepath}
                    self.logger.info(f"Found backing vocals: {filename}")
        
        # Log summary
        clean_count = 1 if separated_audio["clean_instrumental"].get("instrumental") else 0
        combined_count = len(separated_audio["combined_instrumentals"])
        self.logger.info(f"Directory scan complete: {clean_count} clean instrumental, {combined_count} combined instrumentals")
        
        return separated_audio

    async def prep_single_track(self):
        # Add signal handler at the start
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.shutdown(s)))

        try:
            self.logger.info(f"Preparing single track: {self.artist} - {self.title}")

            # Determine extractor early based on input type
            # Assume self.extractor, self.url, self.media_id etc. are set by process() before calling this
            if self.input_media and os.path.isfile(self.input_media):
                if not self.extractor: # If extractor wasn't somehow set before (e.g., direct call)
                    self.extractor = "Original"
            elif self.url: # If it's a URL (set by process)
                 if not self.extractor: # Should have been set by parse_track_metadata in process()
                      self.logger.warning("Extractor not set before prep_single_track for URL, attempting fallback logic.")
                      # Fallback logic (less ideal, relies on potentially missing info)
                      if self.extracted_info and self.extracted_info.get('extractor'):
                          self.extractor = self.extracted_info['extractor']
                      elif self.media_id: # Try to guess based on ID format
                          # Basic youtube id check
                          if re.match(r'^[a-zA-Z0-9_-]{11}$', self.media_id):
                              self.extractor = "youtube"
                          else:
                              self.extractor = "UnknownSource" # Fallback if ID doesn't look like youtube
                      else:
                          self.extractor = "UnknownSource" # Final fallback
                      self.logger.info(f"Fallback extractor set to: {self.extractor}")
            elif self.input_media: # Not a file, not a URL -> maybe a direct URL string?
                self.logger.warning(f"Input media '{self.input_media}' is not a file and self.url was not set. Attempting to treat as URL.")
                # This path requires calling extract/parse again, less efficient
                try:
                    extracted = extract_info_for_online_media(self.input_media, self.artist, self.title, self.logger, self.cookies_str)
                    if extracted:
                         metadata_result = parse_track_metadata(
                             extracted, self.artist, self.title, self.persistent_artist, self.logger
                         )
                         self.url = metadata_result["url"]
                         self.extractor = metadata_result["extractor"]
                         self.media_id = metadata_result["media_id"]
                         self.artist = metadata_result["artist"]
                         self.title = metadata_result["title"]
                         self.logger.info(f"Successfully extracted metadata within prep_single_track for {self.input_media}")
                    else:
                         self.logger.error(f"Could not extract info for {self.input_media} within prep_single_track.")
                         self.extractor = "ErrorExtracting"
                         return None # Cannot proceed without metadata
                except Exception as meta_exc:
                     self.logger.error(f"Error during metadata extraction/parsing within prep_single_track: {meta_exc}")
                     self.extractor = "ErrorParsing"
                     return None # Cannot proceed
            else:
                 # If it's neither file nor URL, and input_media is None, check for existing files
                 # This path is mainly for the case where files exist from previous run
                 # We still need artist/title for filename generation
                 if not self.artist or not self.title:
                      self.logger.error("Cannot determine output path without artist/title when input_media is None and not a URL.")
                      return None
                 self.logger.info("Input media is None, assuming check for existing files based on artist/title.")
                 # We need a nominal extractor for filename matching if files exist
                 # Let's default to 'UnknownExisting' or try to infer if possible later
                 if not self.extractor:
                    self.extractor = "UnknownExisting"

            if not self.extractor:
                 self.logger.error("Could not determine extractor for the track.")
                 return None

            # Now self.extractor should be set correctly for path generation etc.

            self.logger.info(f"Preparing output path for track: {self.title} by {self.artist} (Extractor: {self.extractor})")
            if self.dry_run:
                return None

            # Delegate to FileHandler
            track_output_dir, artist_title = self.file_handler.setup_output_paths(self.output_dir, self.artist, self.title)

            processed_track = {
                "track_output_dir": track_output_dir,
                "artist": self.artist,
                "title": self.title,
                "extractor": self.extractor,
                "extracted_info": self.extracted_info,
                "lyrics": None,
                "processed_lyrics": None,
                "separated_audio": {},
            }

            processed_track["input_media"] = None
            processed_track["input_still_image"] = None
            processed_track["input_audio_wav"] = None

            if self.input_media and os.path.isfile(self.input_media):
                # --- Local File Input Handling ---
                input_wav_filename_pattern = os.path.join(track_output_dir, f"{artist_title} ({self.extractor}*).wav")
                input_wav_glob = glob.glob(input_wav_filename_pattern)

                if input_wav_glob:
                    processed_track["input_audio_wav"] = input_wav_glob[0]
                    self.logger.info(f"Input media WAV file already exists, skipping conversion: {processed_track['input_audio_wav']}")
                else:
                    output_filename_no_extension = os.path.join(track_output_dir, f"{artist_title} ({self.extractor})")

                    self.logger.info(f"Copying input media from {self.input_media} to new directory...")
                    # Delegate to FileHandler
                    processed_track["input_media"] = self.file_handler.copy_input_media(self.input_media, output_filename_no_extension)

                    self.logger.info("Converting input media to WAV for audio processing...")
                    # Delegate to FileHandler
                    processed_track["input_audio_wav"] = self.file_handler.convert_to_wav(processed_track["input_media"], output_filename_no_extension)

            else:
                # --- AudioFetcher or Existing Files Handling ---
                # Construct patterns using the determined extractor
                base_pattern = os.path.join(track_output_dir, f"{artist_title} ({self.extractor}*)")
                input_media_glob = glob.glob(f"{base_pattern}.*flac") + glob.glob(f"{base_pattern}.*mp3") + glob.glob(f"{base_pattern}.*wav") + glob.glob(f"{base_pattern}.*webm") + glob.glob(f"{base_pattern}.*mp4")
                input_png_glob = glob.glob(f"{base_pattern}.png")
                input_wav_glob = glob.glob(f"{base_pattern}.wav")

                if input_media_glob and input_wav_glob:
                    # Existing files found
                    processed_track["input_media"] = input_media_glob[0]
                    processed_track["input_still_image"] = input_png_glob[0] if input_png_glob else None
                    processed_track["input_audio_wav"] = input_wav_glob[0]
                    self.logger.info(f"Found existing media files matching extractor '{self.extractor}', skipping download/conversion.")

                elif getattr(self, '_use_audio_fetcher', False):
                    try:
                        # Check if this is a URL download or search+download
                        if getattr(self, '_use_url_download', False):
                            # Direct URL download (e.g., YouTube URL)
                            self.logger.info(f"Using flacfetch to download from URL: {self.url}")

                            fetch_result = self.audio_fetcher.download_from_url(
                                url=self.url,
                                output_dir=track_output_dir,
                                output_filename=f"{artist_title} (youtube)" if artist_title != "Unknown - Unknown" else None,
                                artist=self.artist,
                                title=self.title,
                            )

                            # Update extractor to reflect the source
                            self.extractor = "youtube"
                        else:
                            # Use flacfetch to search and download audio
                            self.logger.info(f"Using flacfetch to search and download: {self.artist} - {self.title}")

                            fetch_result = self.audio_fetcher.search_and_download(
                                artist=self.artist,
                                title=self.title,
                                output_dir=track_output_dir,
                                output_filename=f"{artist_title} (flacfetch)",
                                auto_select=self.auto_download,
                            )

                            # Update extractor to reflect the actual provider used
                            self.extractor = f"flacfetch-{fetch_result.provider}"

                        # Set up the output paths
                        output_filename_no_extension = os.path.join(track_output_dir, f"{artist_title} ({self.extractor})")

                        # Copy/move the downloaded file to the expected location
                        processed_track["input_media"] = self.file_handler.download_audio_from_fetcher_result(
                            fetch_result.filepath, output_filename_no_extension
                        )

                        self.logger.info(f"Audio downloaded from {fetch_result.provider}: {processed_track['input_media']}")

                        # Convert to WAV for audio processing
                        self.logger.info("Converting downloaded audio to WAV for processing...")
                        processed_track["input_audio_wav"] = self.file_handler.convert_to_wav(
                            processed_track["input_media"], output_filename_no_extension
                        )

                        # No still image for audio-only downloads
                        processed_track["input_still_image"] = None

                    except UserCancelledError:
                        # User cancelled - propagate up to CLI for graceful exit
                        raise
                    except NoResultsError as e:
                        self.logger.error(f"No audio found: {e}")
                        return None
                    except AudioFetcherError as e:
                        self.logger.error(f"Failed to fetch audio: {e}")
                        return None
                        
                else:
                    # This case means input_media was None, no audio fetcher flag, and no existing files found
                    self.logger.error(f"Cannot proceed: No input file and no existing files found for {artist_title}.")
                    self.logger.error("Please provide a local audio file or use artist+title to search for audio.")
                    return None

            if self.skip_lyrics:
                self.logger.info("Skipping lyrics fetch as requested.")
                processed_track["lyrics"] = None
                processed_track["processed_lyrics"] = None
                # No countdown padding when lyrics are skipped
                processed_track["countdown_padding_added"] = False
                processed_track["countdown_padding_seconds"] = 0.0
                processed_track["padded_vocals_audio"] = None
            else:
                lyrics_artist = self.lyrics_artist or self.artist
                lyrics_title = self.lyrics_title or self.title

                # Create futures for both operations
                transcription_future = None
                separation_future = None

                self.logger.info("=== Starting Parallel Processing ===")

                if not self.skip_lyrics:
                    self.logger.info("Creating transcription future...")
                    # Run transcription in a separate thread
                    transcription_future = asyncio.create_task(
                        asyncio.to_thread(
                            # Delegate to LyricsProcessor - pass original artist/title for filenames, lyrics_artist/lyrics_title for processing
                            self.lyrics_processor.transcribe_lyrics, 
                            processed_track["input_audio_wav"], 
                            self.artist,  # Original artist for filename generation
                            self.title,   # Original title for filename generation  
                            track_output_dir,
                            lyrics_artist,  # Lyrics artist for processing
                            lyrics_title    # Lyrics title for processing
                        )
                    )
                    self.logger.info(f"Transcription future created, type: {type(transcription_future)}")

                # Default to a placeholder task if separation won't run
                separation_future = asyncio.create_task(asyncio.sleep(0))

                # Only create real separation future if not skipping AND no existing instrumental provided
                if not self.skip_separation and not self.existing_instrumental:
                    self.logger.info("Creating separation future (not skipping and no existing instrumental)...")
                    # Run separation in a separate thread
                    separation_future = asyncio.create_task(
                        asyncio.to_thread(
                            # Delegate to AudioProcessor
                            self.audio_processor.process_audio_separation,
                            audio_file=processed_track["input_audio_wav"],
                            artist_title=artist_title,
                            track_output_dir=track_output_dir,
                        )
                    )
                    self.logger.info(f"Separation future created, type: {type(separation_future)}")
                elif self.existing_instrumental:
                     self.logger.info(f"Skipping separation future creation because existing instrumental was provided: {self.existing_instrumental}")
                elif self.skip_separation: # Check this condition explicitly for clarity
                     self.logger.info("Skipping separation future creation because skip_separation is True.")

                self.logger.info("About to await both operations with asyncio.gather...")
                # Wait for both operations to complete
                try:
                    results = await asyncio.gather(
                        transcription_future if transcription_future else asyncio.sleep(0), # Use placeholder if None
                        separation_future, # Already defaults to placeholder if not created
                        return_exceptions=True,
                    )
                except asyncio.CancelledError:
                    self.logger.info("Received cancellation request, cleaning up...")
                    # Cancel any running futures
                    if transcription_future and not transcription_future.done():
                        transcription_future.cancel()
                    if separation_future and not separation_future.done() and not isinstance(separation_future, asyncio.Task): # Check if it's a real task
                         # Don't try to cancel the asyncio.sleep(0) placeholder
                         separation_future.cancel()

                    # Wait for futures to complete cancellation
                    await asyncio.gather(
                        transcription_future if transcription_future else asyncio.sleep(0),
                        separation_future if separation_future else asyncio.sleep(0), # Use placeholder if None/Placeholder
                        return_exceptions=True,
                    )
                    raise

                # Handle transcription results
                if transcription_future:
                    self.logger.info("Processing transcription results...")
                    try:
                        # Index 0 corresponds to transcription_future in gather
                        transcriber_outputs = results[0]
                        # Check if the result is an exception or the actual output
                        if isinstance(transcriber_outputs, Exception):
                            self.logger.error(f"Error during lyrics transcription: {transcriber_outputs}")
                            # Optionally log traceback: self.logger.exception("Transcription error:")
                            raise transcriber_outputs  # Re-raise the exception
                        elif transcriber_outputs is not None and not isinstance(transcriber_outputs, asyncio.futures.Future): # Ensure it's not the placeholder future
                            self.logger.info(f"Successfully received transcription outputs: {type(transcriber_outputs)}")
                            # Ensure transcriber_outputs is a dictionary before calling .get()
                            if isinstance(transcriber_outputs, dict):
                                self.lyrics = transcriber_outputs.get("corrected_lyrics_text")
                                processed_track["lyrics"] = transcriber_outputs.get("corrected_lyrics_text_filepath")
                                
                                # Capture countdown padding information
                                processed_track["countdown_padding_added"] = transcriber_outputs.get("countdown_padding_added", False)
                                processed_track["countdown_padding_seconds"] = transcriber_outputs.get("countdown_padding_seconds", 0.0)
                                processed_track["padded_vocals_audio"] = transcriber_outputs.get("padded_audio_filepath")
                                
                                # Store ASS filepath for video background processing
                                processed_track["ass_filepath"] = transcriber_outputs.get("ass_filepath")
                                
                                if processed_track["countdown_padding_added"]:
                                    self.logger.info(
                                        f"=== COUNTDOWN PADDING DETECTED ==="
                                    )
                                    self.logger.info(
                                        f"Vocals have been padded with {processed_track['countdown_padding_seconds']}s of silence. "
                                        f"Instrumental tracks will be padded after separation to maintain synchronization."
                                    )
                            else:
                                self.logger.warning(f"Unexpected type for transcriber_outputs: {type(transcriber_outputs)}, value: {transcriber_outputs}")
                        else:
                             self.logger.info("Transcription task did not return results (possibly skipped or placeholder).")
                    except Exception as e:
                        self.logger.error(f"Error processing transcription results: {e}")
                        self.logger.exception("Full traceback:")
                        raise # Re-raise the exception

                # Handle separation results only if a real future was created and ran
                # Check if separation_future was the placeholder or a real task
                # The result index in `results` depends on whether transcription_future existed
                separation_result_index = 1 if transcription_future else 0
                if separation_future is not None and isinstance(separation_future, asyncio.Task) and len(results) > separation_result_index:
                    self.logger.info("Processing separation results...")
                    try:
                        separation_results = results[separation_result_index]
                         # Check if the result is an exception or the actual output
                        if isinstance(separation_results, Exception):
                            self.logger.error(f"Error during audio separation: {separation_results}")
                             # Optionally log traceback: self.logger.exception("Separation error:")
                            # Decide if you want to raise here or just log
                        elif separation_results is not None and not isinstance(separation_results, asyncio.futures.Future): # Ensure it's not the placeholder future
                            self.logger.info(f"Successfully received separation results: {type(separation_results)}")
                            if isinstance(separation_results, dict):
                                processed_track["separated_audio"] = separation_results
                            else:
                                 self.logger.warning(f"Unexpected type for separation_results: {type(separation_results)}, value: {separation_results}")
                        else:
                            self.logger.info("Separation task did not return results (possibly skipped or placeholder).")
                    except Exception as e:
                        self.logger.error(f"Error processing separation results: {e}")
                        self.logger.exception("Full traceback:")
                        # Decide if you want to raise here or just log
                elif not self.skip_separation and not self.existing_instrumental:
                    # This case means separation was supposed to run but didn't return results properly
                    self.logger.warning("Separation task was expected but did not yield results or resulted in an error captured earlier.")
                else:
                    # This case means separation was intentionally skipped
                    self.logger.info("Skipping processing of separation results as separation was not run.")

                self.logger.info("=== Parallel Processing Complete ===")

            # === POST-TRANSCRIPTION: Add countdown and render video ===
            # Since lyrics_processor.py now always defers countdown and video rendering,
            # we handle it here after human review is complete. This ensures the review UI
            # shows accurate, unshifted timestamps (same behavior as cloud backend).
            if processed_track.get("lyrics") and self.render_video:
                self.logger.info("=== Processing Countdown and Video Rendering ===")

                from .utils import sanitize_filename
                sanitized_artist = sanitize_filename(self.artist)
                sanitized_title = sanitize_filename(self.title)
                lyrics_dir = os.path.join(track_output_dir, "lyrics")

                # Find the corrections JSON file
                corrections_filename = f"{sanitized_artist} - {sanitized_title} (Lyrics Corrections).json"
                corrections_filepath = os.path.join(lyrics_dir, corrections_filename)

                if os.path.exists(corrections_filepath):
                    self.logger.info(f"Loading corrections from: {corrections_filepath}")

                    with open(corrections_filepath, 'r', encoding='utf-8') as f:
                        corrections_data = json.load(f)

                    # Convert to CorrectionResult
                    correction_result = CorrectionResult.from_dict(corrections_data)
                    self.logger.info(f"Loaded CorrectionResult with {len(correction_result.corrected_segments)} segments")

                    # Get the audio file path
                    audio_path = processed_track["input_audio_wav"]

                    # Add countdown intro if needed (songs that start within 3 seconds)
                    self.logger.info("Processing countdown intro (if needed)...")
                    cache_dir = os.path.join(track_output_dir, "cache")
                    os.makedirs(cache_dir, exist_ok=True)

                    countdown_processor = CountdownProcessor(
                        cache_dir=cache_dir,
                        logger=self.logger,
                    )

                    correction_result, audio_path, padding_added, padding_seconds = countdown_processor.process(
                        correction_result=correction_result,
                        audio_filepath=audio_path,
                    )

                    # Update processed_track with countdown info
                    processed_track["countdown_padding_added"] = padding_added
                    processed_track["countdown_padding_seconds"] = padding_seconds
                    if padding_added:
                        processed_track["padded_vocals_audio"] = audio_path
                        self.logger.info(
                            f"=== COUNTDOWN PADDING ADDED ===\n"
                            f"Added {padding_seconds}s padding to audio and shifted timestamps.\n"
                            f"Instrumental tracks will be padded after separation to maintain sync."
                        )
                    else:
                        self.logger.info("No countdown needed - song starts after 3 seconds")

                    # Save the updated corrections with countdown timestamps
                    updated_corrections_data = correction_result.to_dict()
                    with open(corrections_filepath, 'w', encoding='utf-8') as f:
                        json.dump(updated_corrections_data, f, indent=2)
                    self.logger.info(f"Saved countdown-adjusted corrections to: {corrections_filepath}")

                    # Render video with lyrics
                    self.logger.info("Rendering karaoke video with synchronized lyrics...")

                    output_config = LyricsOutputConfig(
                        output_dir=lyrics_dir,
                        cache_dir=cache_dir,
                        output_styles_json=self.style_params_json,
                        render_video=True,
                        generate_cdg=False,
                        generate_plain_text=True,
                        generate_lrc=True,
                        video_resolution="4k",
                        subtitle_offset_ms=self.subtitle_offset_ms,
                    )

                    output_generator = OutputGenerator(output_config, self.logger)
                    output_prefix = f"{sanitized_artist} - {sanitized_title}"

                    outputs = output_generator.generate_outputs(
                        transcription_corrected=correction_result,
                        lyrics_results={},  # Lyrics already written during transcription phase
                        audio_filepath=audio_path,
                        output_prefix=output_prefix,
                    )

                    # Copy video to expected location in parent directory
                    if outputs and outputs.video:
                        source_video = outputs.video
                        dest_video = os.path.join(track_output_dir, f"{artist_title} (With Vocals).mkv")
                        shutil.copy2(source_video, dest_video)
                        self.logger.info(f"Video rendered successfully: {dest_video}")
                        processed_track["with_vocals_video"] = dest_video

                        # Update ASS filepath for video background processing
                        if outputs.ass:
                            processed_track["ass_filepath"] = outputs.ass
                    else:
                        self.logger.warning("Video rendering did not produce expected output")
                else:
                    self.logger.warning(f"Corrections file not found: {corrections_filepath}")
                    self.logger.warning("Skipping countdown processing and video rendering")
            elif not self.render_video:
                self.logger.info("Video rendering disabled - skipping countdown and video generation")

            # Apply video background if requested and lyrics were processed
            if self.video_background_processor and processed_track.get("lyrics"):
                self.logger.info("=== Processing Video Background ===")
                
                # Find the With Vocals video file
                with_vocals_video = os.path.join(track_output_dir, f"{artist_title} (With Vocals).mkv")
                
                # Get ASS file from transcriber outputs if available
                ass_file = processed_track.get("ass_filepath")
                
                # If not in processed_track, try to find it in common locations
                if not ass_file or not os.path.exists(ass_file):
                    self.logger.info("ASS filepath not found in transcriber outputs, searching for it...")
                    from .utils import sanitize_filename
                    sanitized_artist = sanitize_filename(self.artist)
                    sanitized_title = sanitize_filename(self.title)
                    lyrics_dir = os.path.join(track_output_dir, "lyrics")
                    
                    possible_ass_files = [
                        os.path.join(lyrics_dir, f"{sanitized_artist} - {sanitized_title}.ass"),
                        os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title}.ass"),
                        os.path.join(lyrics_dir, f"{artist_title}.ass"),
                        os.path.join(track_output_dir, f"{artist_title}.ass"),
                        os.path.join(track_output_dir, f"{artist_title} (Karaoke).ass"),
                        os.path.join(lyrics_dir, f"{artist_title} (Karaoke).ass"),
                    ]
                    
                    for possible_file in possible_ass_files:
                        if os.path.exists(possible_file):
                            ass_file = possible_file
                            self.logger.info(f"Found ASS subtitle file: {ass_file}")
                            break
                
                if os.path.exists(with_vocals_video) and ass_file and os.path.exists(ass_file):
                    self.logger.info(f"Found With Vocals video, will replace with video background: {with_vocals_video}")
                    self.logger.info(f"Using ASS subtitle file: {ass_file}")
                    
                    # Get audio duration
                    audio_duration = self.video_background_processor.get_audio_duration(processed_track["input_audio_wav"])
                    
                    # Check if we need to use the padded audio instead
                    if processed_track.get("countdown_padding_added") and processed_track.get("padded_vocals_audio"):
                        self.logger.info(f"Using padded vocals audio for video background processing")
                        audio_for_video = processed_track["padded_vocals_audio"]
                    else:
                        audio_for_video = processed_track["input_audio_wav"]
                    
                    # Process video background
                    try:
                        self.video_background_processor.process_video_background(
                            video_path=self.background_video,
                            audio_path=audio_for_video,
                            ass_subtitles_path=ass_file,
                            output_path=with_vocals_video,
                            darkness_percent=self.background_video_darkness,
                            audio_duration=audio_duration,
                        )
                        self.logger.info(f"✓ Video background applied, With Vocals video updated: {with_vocals_video}")
                    except Exception as e:
                        self.logger.error(f"Failed to apply video background: {e}")
                        self.logger.exception("Full traceback:")
                        # Continue with original video if background processing fails
                else:
                    if not os.path.exists(with_vocals_video):
                        self.logger.warning(f"With Vocals video not found at {with_vocals_video}, skipping video background processing")
                    elif not ass_file or not os.path.exists(ass_file):
                        self.logger.warning("Could not find ASS subtitle file, skipping video background processing")
                        if 'possible_ass_files' in locals():
                            self.logger.warning("Searched locations:")
                            for possible_file in possible_ass_files:
                                self.logger.warning(f"  - {possible_file}")

            output_image_filepath_noext = os.path.join(track_output_dir, f"{artist_title} (Title)")
            processed_track["title_image_png"] = f"{output_image_filepath_noext}.png"
            processed_track["title_image_jpg"] = f"{output_image_filepath_noext}.jpg"
            processed_track["title_video"] = os.path.join(track_output_dir, f"{artist_title} (Title).mov")

            # Use FileHandler._file_exists
            if not self.file_handler._file_exists(processed_track["title_video"]) and not os.environ.get("KARAOKE_GEN_SKIP_TITLE_END_SCREENS"):
                self.logger.info(f"Creating title video...")
                # Delegate to VideoGenerator
                self.video_generator.create_title_video(
                    artist=self.artist,
                    title=self.title,
                    format=self.title_format,
                    output_image_filepath_noext=output_image_filepath_noext,
                    output_video_filepath=processed_track["title_video"],
                    existing_title_image=self.existing_title_image,
                    intro_video_duration=self.intro_video_duration,
                )

            output_image_filepath_noext = os.path.join(track_output_dir, f"{artist_title} (End)")
            processed_track["end_image_png"] = f"{output_image_filepath_noext}.png"
            processed_track["end_image_jpg"] = f"{output_image_filepath_noext}.jpg"
            processed_track["end_video"] = os.path.join(track_output_dir, f"{artist_title} (End).mov")

            # Use FileHandler._file_exists
            if not self.file_handler._file_exists(processed_track["end_video"]) and not os.environ.get("KARAOKE_GEN_SKIP_TITLE_END_SCREENS"):
                self.logger.info(f"Creating end screen video...")
                 # Delegate to VideoGenerator
                self.video_generator.create_end_video(
                    artist=self.artist,
                    title=self.title,
                    format=self.end_format,
                    output_image_filepath_noext=output_image_filepath_noext,
                    output_video_filepath=processed_track["end_video"],
                    existing_end_image=self.existing_end_image,
                    end_video_duration=self.end_video_duration,
                )

            if self.skip_separation:
                self.logger.info("Skipping audio separation as requested.")
                processed_track["separated_audio"] = {
                    "clean_instrumental": {},
                    "backing_vocals": {},
                    "other_stems": {},
                    "combined_instrumentals": {},
                }
            elif self.existing_instrumental:
                self.logger.info(f"Using existing instrumental file: {self.existing_instrumental}")
                existing_instrumental_extension = os.path.splitext(self.existing_instrumental)[1]

                instrumental_path = os.path.join(track_output_dir, f"{artist_title} (Instrumental Custom){existing_instrumental_extension}")

                # Use FileHandler._file_exists
                if not self.file_handler._file_exists(instrumental_path):
                    shutil.copy2(self.existing_instrumental, instrumental_path)

                processed_track["separated_audio"]["Custom"] = {
                    "instrumental": instrumental_path,
                    "vocals": None,
                }
                
                # If countdown padding was added to vocals, pad the custom instrumental too
                if processed_track.get("countdown_padding_added", False):
                    padding_seconds = processed_track["countdown_padding_seconds"]
                    self.logger.info(
                        f"Countdown padding detected - applying {padding_seconds}s padding to custom instrumental"
                    )
                    
                    base, ext = os.path.splitext(instrumental_path)
                    padded_instrumental_path = f"{base} (Padded){ext}"
                    
                    if not self.file_handler._file_exists(padded_instrumental_path):
                        self.audio_processor.pad_audio_file(instrumental_path, padded_instrumental_path, padding_seconds)
                    
                    # Update the path to use the padded version
                    processed_track["separated_audio"]["Custom"]["instrumental"] = padded_instrumental_path
                    self.logger.info(f"✓ Custom instrumental has been padded and synchronized with vocals")
            elif "separated_audio" not in processed_track or not processed_track["separated_audio"]:
                # Only run separation if it wasn't already done in parallel processing
                self.logger.info(f"Separation was not completed in parallel processing, running separation for track: {self.title} by {self.artist}")
                # Delegate to AudioProcessor (called directly, not in thread here)
                separation_results = self.audio_processor.process_audio_separation(
                    audio_file=processed_track["input_audio_wav"], artist_title=artist_title, track_output_dir=track_output_dir
                )
                processed_track["separated_audio"] = separation_results
            else:
                self.logger.info("Audio separation was already completed in parallel processing, skipping duplicate separation.")

            # Apply countdown padding to instrumental files if needed
            if processed_track.get("countdown_padding_added", False):
                padding_seconds = processed_track["countdown_padding_seconds"]
                self.logger.info(
                    f"=== APPLYING COUNTDOWN PADDING TO INSTRUMENTALS ==="
                )
                self.logger.info(
                    f"Applying {padding_seconds}s padding to all instrumental files to sync with vocal countdown"
                )
                
                # If separated_audio is empty (e.g., transcription was skipped but existing files have countdown),
                # scan the directory for existing instrumental files
                # Note: also check for Custom instrumental (provided via --existing_instrumental)
                has_instrumentals = (
                    processed_track["separated_audio"].get("clean_instrumental", {}).get("instrumental") or
                    processed_track["separated_audio"].get("combined_instrumentals") or
                    processed_track["separated_audio"].get("Custom", {}).get("instrumental")
                )
                if not has_instrumentals:
                    self.logger.info("No instrumentals in separated_audio, scanning directory for existing files...")
                    # Preserve existing Custom key if present before overwriting
                    custom_backup = processed_track["separated_audio"].get("Custom")
                    processed_track["separated_audio"] = self._scan_directory_for_instrumentals(
                        track_output_dir, artist_title
                    )
                    if custom_backup:
                        processed_track["separated_audio"]["Custom"] = custom_backup
                
                # Apply padding using AudioProcessor
                padded_separation_result = self.audio_processor.apply_countdown_padding_to_instrumentals(
                    separation_result=processed_track["separated_audio"],
                    padding_seconds=padding_seconds,
                    artist_title=artist_title,
                    track_output_dir=track_output_dir,
                )
                
                # Update processed_track with padded file paths
                processed_track["separated_audio"] = padded_separation_result
                
                self.logger.info(
                    f"✓ All instrumental files have been padded and are now synchronized with vocals"
                )

            self.logger.info("Script finished, audio downloaded, lyrics fetched and audio separated!")

            return processed_track

        except Exception as e:
            self.logger.error(f"Error in prep_single_track: {e}")
            raise
        finally:
            # Remove signal handlers
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

    async def shutdown(self, signal_received):
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received exit signal {signal_received.name}...")

        # Get all running tasks except the current shutdown task
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

        if tasks:
            self.logger.info(f"Cancelling {len(tasks)} outstanding tasks")
            # Cancel all running tasks
            for task in tasks:
                task.cancel()

            # Wait for all tasks to complete with cancellation
            # Use return_exceptions=True to gather all results without raising
            await asyncio.gather(*tasks, return_exceptions=True)

        self.logger.info("Cleanup complete")
        
        # Raise KeyboardInterrupt to propagate the cancellation up the call stack
        # This allows the main event loop to exit cleanly
        raise KeyboardInterrupt()

    async def process_playlist(self):
        if self.artist is None or self.title is None:
            raise Exception("Error: Artist and Title are required for processing a local file.")

        if "entries" in self.extracted_info:
            track_results = []
            self.logger.info(f"Found {len(self.extracted_info['entries'])} entries in playlist, processing each invididually...")
            for entry in self.extracted_info["entries"]:
                self.extracted_info = entry
                self.logger.info(f"Processing playlist entry with title: {self.extracted_info['title']}")
                if not self.dry_run:
                    track_results.append(await self.prep_single_track())
                self.artist = self.persistent_artist
                self.title = None
            return track_results
        else:
            raise Exception(f"Failed to find 'entries' in playlist, cannot process")

    async def process_folder(self):
        if self.filename_pattern is None or self.artist is None:
            raise Exception("Error: Filename pattern and artist are required for processing a folder.")

        folder_path = self.input_media
        output_folder_path = os.path.join(os.getcwd(), os.path.basename(folder_path))

        if not os.path.exists(output_folder_path):
            if not self.dry_run:
                self.logger.info(f"DRY RUN: Would create output folder: {output_folder_path}")
                os.makedirs(output_folder_path)
        else:
            self.logger.info(f"Output folder already exists: {output_folder_path}")

        pattern = re.compile(self.filename_pattern)
        tracks = []

        for filename in sorted(os.listdir(folder_path)):
            match = pattern.match(filename)
            if match:
                title = match.group("title")
                file_path = os.path.join(folder_path, filename)
                self.input_media = file_path
                self.title = title

                track_index = match.group("index") if "index" in match.groupdict() else None

                self.logger.info(f"Processing track: {track_index} with title: {title} from file: {filename}")

                track_output_dir = os.path.join(output_folder_path, f"{track_index} - {self.artist} - {title}")

                if not self.dry_run:
                    track = await self.prep_single_track()
                    tracks.append(track)

                    # Move the track folder to the output folder
                    track_folder = track["track_output_dir"]
                    shutil.move(track_folder, track_output_dir)
                else:
                    self.logger.info(f"DRY RUN: Would move track folder to: {os.path.basename(track_output_dir)}")

        return tracks

    def _is_url(self, string: str) -> bool:
        """Check if a string is a URL."""
        return string is not None and (string.startswith("http://") or string.startswith("https://"))

    async def process(self):
        if self.input_media is not None and os.path.isdir(self.input_media):
            self.logger.info(f"Input media {self.input_media} is a local folder, processing each file individually...")
            return await self.process_folder()
        elif self.input_media is not None and os.path.isfile(self.input_media):
            self.logger.info(f"Input media {self.input_media} is a local file, audio download will be skipped")
            return [await self.prep_single_track()]
        elif self.input_media is not None and self._is_url(self.input_media):
            # URL provided - download directly via flacfetch
            self.logger.info(f"Input media {self.input_media} is a URL, downloading via flacfetch...")

            # Extract video ID for metadata if it's a YouTube URL
            video_id = None
            youtube_patterns = [
                r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
                r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
                r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
            ]
            for pattern in youtube_patterns:
                match = re.search(pattern, self.input_media)
                if match:
                    video_id = match.group(1)
                    break

            # Set up the extracted_info for metadata consistency
            self.extracted_info = {
                "title": f"{self.artist} - {self.title}" if self.artist and self.title else video_id or "Unknown",
                "artist": self.artist or "",
                "track_title": self.title or "",
                "extractor_key": "youtube",
                "id": video_id or self.input_media,
                "url": self.input_media,
                "source": "youtube",
            }
            self.extractor = "youtube"
            self.url = self.input_media

            # Mark that we need to use audio fetcher for URL download
            self._use_audio_fetcher = True
            self._use_url_download = True  # New flag for URL-based download

            return [await self.prep_single_track()]
        elif self.artist and self.title:
            # No input file provided - use flacfetch to search and download audio
            self.logger.info(f"No input file provided, using flacfetch to search for: {self.artist} - {self.title}")

            # Set up the extracted_info for metadata consistency
            self.extracted_info = {
                "title": f"{self.artist} - {self.title}",
                "artist": self.artist,
                "track_title": self.title,
                "extractor_key": "flacfetch",
                "id": f"flacfetch_{self.artist}_{self.title}".replace(" ", "_"),
                "url": None,
                "source": "flacfetch",
            }
            self.extractor = "flacfetch"
            self.url = None  # URL will be determined by flacfetch

            # Mark that we need to use audio fetcher for download
            self._use_audio_fetcher = True

            return [await self.prep_single_track()]
        else:
            raise ValueError(
                "Either a local file path, a URL, or both artist and title must be provided."
            )
