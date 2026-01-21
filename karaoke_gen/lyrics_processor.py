import os
import re
import logging
import shutil
import json
from typing import Optional
from karaoke_gen.lyrics_transcriber import LyricsTranscriber, OutputConfig, TranscriberConfig, LyricsConfig
from karaoke_gen.lyrics_transcriber.core.controller import LyricsControllerResult
from dotenv import load_dotenv
from .utils import sanitize_filename


# Placeholder class or functions for lyrics processing
class LyricsProcessor:
    # Standard countdown padding duration used by LyricsTranscriber
    COUNTDOWN_PADDING_SECONDS = 3.0
    
    def __init__(
        self, logger, style_params_json, lyrics_file, skip_transcription, skip_transcription_review, render_video, subtitle_offset_ms
    ):
        self.logger = logger
        self.style_params_json = style_params_json
        self.lyrics_file = lyrics_file
        self.skip_transcription = skip_transcription
        self.skip_transcription_review = skip_transcription_review
        self.render_video = render_video
        self.subtitle_offset_ms = subtitle_offset_ms

    def _detect_countdown_padding_from_lrc(self, lrc_filepath):
        """
        Detect if countdown padding was applied by checking for countdown text in the LRC file.
        
        The countdown segment has the text "3... 2... 1..." at timestamp 0.1-2.9s.
        We detect this by looking for the countdown text pattern.
        
        Args:
            lrc_filepath: Path to the LRC file
            
        Returns:
            Tuple of (countdown_padding_added: bool, countdown_padding_seconds: float)
        """
        try:
            with open(lrc_filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Method 1: Check for countdown text pattern "3... 2... 1..."
            # This is the most reliable detection method since the countdown text is unique
            countdown_text = "3... 2... 1..."
            if countdown_text in content:
                self.logger.info(f"Detected countdown padding from LRC: found countdown text '{countdown_text}'")
                return (True, self.COUNTDOWN_PADDING_SECONDS)
            
            # Method 2 (fallback): Check if first lyric timestamp is >= 3 seconds
            # This handles cases where countdown text format might differ
            # LRC timestamps: [mm:ss.xx] or [mm:ss.xxx]
            timestamp_pattern = r'\[(\d{1,2}):(\d{2})\.(\d{2,3})\]'
            matches = re.findall(timestamp_pattern, content)
            
            if not matches:
                self.logger.debug("No timestamps found in LRC file")
                return (False, 0.0)
            
            # Parse the first timestamp
            first_timestamp = matches[0]
            minutes = int(first_timestamp[0])
            seconds = int(first_timestamp[1])
            # Handle both .xx and .xxx formats
            centiseconds = first_timestamp[2]
            if len(centiseconds) == 2:
                milliseconds = int(centiseconds) * 10
            else:
                milliseconds = int(centiseconds)
            
            first_lyric_time = minutes * 60 + seconds + milliseconds / 1000.0
            
            self.logger.debug(f"First lyric timestamp in LRC: {first_lyric_time:.3f}s")
            
            # If first lyric is at or after 3 seconds, countdown padding was applied
            # Use a small buffer (2.5s) to account for songs that naturally start a bit late
            if first_lyric_time >= 2.5:
                self.logger.info(f"Detected countdown padding from LRC: first lyric at {first_lyric_time:.2f}s")
                return (True, self.COUNTDOWN_PADDING_SECONDS)
            
            return (False, 0.0)
            
        except Exception as e:
            self.logger.warning(f"Failed to detect countdown padding from LRC file: {e}")
            return (False, 0.0)

    def find_best_split_point(self, line):
        """
        Find the best split point in a line based on the specified criteria.
        """

        self.logger.debug(f"Finding best_split_point for line: {line}")
        words = line.split()
        mid_word_index = len(words) // 2
        self.logger.debug(f"words: {words} mid_word_index: {mid_word_index}")

        # Check for a comma within one or two words of the middle word
        if "," in line:
            mid_point = len(" ".join(words[:mid_word_index]))
            comma_indices = [i for i, char in enumerate(line) if char == ","]

            for index in comma_indices:
                if abs(mid_point - index) < 20 and len(line[: index + 1].strip()) <= 36:
                    self.logger.debug(
                        f"Found comma at index {index} which is within 20 characters of mid_point {mid_point} and results in a suitable line length, accepting as split point"
                    )
                    return index + 1  # Include the comma in the first line

        # Check for 'and'
        if " and " in line:
            mid_point = len(line) // 2
            and_indices = [m.start() for m in re.finditer(" and ", line)]
            for index in sorted(and_indices, key=lambda x: abs(x - mid_point)):
                if len(line[: index + len(" and ")].strip()) <= 36:
                    self.logger.debug(f"Found 'and' at index {index} which results in a suitable line length, accepting as split point")
                    return index + len(" and ")

        # If no better split point is found, try splitting at the middle word
        if len(words) > 2 and mid_word_index > 0:
            split_at_middle = len(" ".join(words[:mid_word_index]))
            if split_at_middle <= 36:
                self.logger.debug(f"Splitting at middle word index: {mid_word_index}")
                return split_at_middle

        # If the line is still too long, forcibly split at the maximum length
        forced_split_point = 36
        if len(line) > forced_split_point:
            self.logger.debug(f"Line is still too long, forcibly splitting at position {forced_split_point}")
            return forced_split_point

    def process_line(self, line):
        """
        Process a single line to ensure it's within the maximum length,
        and handle parentheses.
        """
        processed_lines = []
        iteration_count = 0
        max_iterations = 100  # Failsafe limit

        while len(line) > 36:
            if iteration_count > max_iterations:
                self.logger.error(f"Maximum iterations exceeded in process_line for line: {line}")
                break

            # Check if the line contains parentheses
            if "(" in line and ")" in line:
                start_paren = line.find("(")
                end_paren = line.find(")") + 1
                if end_paren < len(line) and line[end_paren] == ",":
                    end_paren += 1

                if start_paren > 0:
                    processed_lines.append(line[:start_paren].strip())
                processed_lines.append(line[start_paren:end_paren].strip())
                line = line[end_paren:].strip()
            else:
                split_point = self.find_best_split_point(line)
                processed_lines.append(line[:split_point].strip())
                line = line[split_point:].strip()

            iteration_count += 1

        if line:  # Add the remaining part if not empty
            processed_lines.append(line)

        return processed_lines

    def _check_transcription_providers(self) -> dict:
        """
        Check which transcription providers are configured and return their status.

        Returns:
            dict with 'configured' (list of provider names) and 'missing' (list of missing configs)
        """
        load_dotenv()

        configured = []
        missing = []

        # Check AudioShake
        audioshake_token = os.getenv("AUDIOSHAKE_API_TOKEN")
        if audioshake_token:
            configured.append("AudioShake")
            self.logger.debug("AudioShake transcription provider: configured")
        else:
            missing.append("AudioShake (AUDIOSHAKE_API_TOKEN)")
            self.logger.debug("AudioShake transcription provider: not configured (missing AUDIOSHAKE_API_TOKEN)")

        # Check Whisper via RunPod
        runpod_key = os.getenv("RUNPOD_API_KEY")
        whisper_id = os.getenv("WHISPER_RUNPOD_ID")
        if runpod_key and whisper_id:
            configured.append("Whisper (RunPod)")
            self.logger.debug("Whisper transcription provider: configured")
        elif runpod_key:
            missing.append("Whisper (missing WHISPER_RUNPOD_ID)")
            self.logger.debug("Whisper transcription provider: partially configured (missing WHISPER_RUNPOD_ID)")
        elif whisper_id:
            missing.append("Whisper (missing RUNPOD_API_KEY)")
            self.logger.debug("Whisper transcription provider: partially configured (missing RUNPOD_API_KEY)")
        else:
            missing.append("Whisper (RUNPOD_API_KEY + WHISPER_RUNPOD_ID)")
            self.logger.debug("Whisper transcription provider: not configured")

        # Check Local Whisper (whisper-timestamped)
        try:
            import whisper_timestamped
            configured.append("Local Whisper")
            self.logger.debug("Local Whisper transcription provider: configured (whisper-timestamped installed)")
        except ImportError:
            missing.append("Local Whisper (pip install karaoke-gen[local-whisper])")
            self.logger.debug("Local Whisper transcription provider: not configured (whisper-timestamped not installed)")

        return {"configured": configured, "missing": missing}

    def _build_transcription_provider_error_message(self, missing_providers: list) -> str:
        """Build a helpful error message when no transcription providers are configured."""
        return (
            "No transcription providers configured!\n"
            "\n"
            "Karaoke video generation requires at least one transcription provider to create "
            "synchronized lyrics. Without a transcription provider, the system cannot generate "
            "the word-level timing data needed for the karaoke video.\n"
            "\n"
            "AVAILABLE TRANSCRIPTION PROVIDERS:\n"
            "\n"
            "1. AudioShake (Recommended - Commercial, high-quality)\n"
            "   - Set environment variable: AUDIOSHAKE_API_TOKEN=your_token\n"
            "   - Get an API key at: https://www.audioshake.ai/\n"
            "\n"
            "2. Whisper via RunPod (Cloud-based open-source)\n"
            "   - Set environment variables:\n"
            "     RUNPOD_API_KEY=your_key\n"
            "     WHISPER_RUNPOD_ID=your_endpoint_id\n"
            "   - Set up a Whisper endpoint at: https://www.runpod.io/\n"
            "\n"
            "3. Local Whisper (No cloud required - runs on your machine)\n"
            "   - Install with: pip install karaoke-gen[local-whisper]\n"
            "   - For CPU-only: pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu\n"
            "                   pip install karaoke-gen[local-whisper]\n"
            "   - Requires 2-10GB RAM depending on model size\n"
            "\n"
            "ALTERNATIVES:\n"
            "\n"
            "- Use --skip-lyrics flag to generate instrumental-only karaoke (no synchronized lyrics)\n"
            "- Use --lyrics_file to provide pre-timed lyrics (still needs transcription for timing)\n"
            "\n"
            f"Missing provider configurations: {', '.join(missing_providers)}\n"
            "\n"
            "See README.md 'Transcription Providers' section for detailed setup instructions."
        )

    def transcribe_lyrics(self, input_audio_wav, artist, title, track_output_dir, lyrics_artist=None, lyrics_title=None,
                          agentic_deadline: Optional[float] = None):
        """
        Transcribe lyrics for a track.

        Args:
            input_audio_wav: Path to the audio file
            artist: Original artist name (used for filename generation)
            title: Original title (used for filename generation)
            track_output_dir: Output directory path
            lyrics_artist: Artist name for lyrics processing (defaults to artist if None)
            lyrics_title: Title for lyrics processing (defaults to title if None)
            agentic_deadline: Optional Unix timestamp. If agentic correction is still
                running after this time, it will abort and return uncorrected results.

        Raises:
            ValueError: If transcription is enabled but no providers are configured
        """
        # Use original artist/title for filename generation
        filename_artist = artist
        filename_title = title
        
        # Use lyrics_artist/lyrics_title for actual lyrics processing, fall back to originals if not provided
        processing_artist = lyrics_artist or artist
        processing_title = lyrics_title or title
        
        self.logger.info(
            f"Transcribing lyrics for track {processing_artist} - {processing_title} from audio file: {input_audio_wav} with output directory: {track_output_dir}"
        )

        # Check for existing files first using sanitized names from ORIGINAL artist/title for consistency
        sanitized_artist = sanitize_filename(filename_artist)
        sanitized_title = sanitize_filename(filename_title)
        parent_video_path = os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title} (With Vocals).mkv")
        parent_lrc_path = os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title} (Karaoke).lrc")

        # Check lyrics directory for existing files
        lyrics_dir = os.path.join(track_output_dir, "lyrics")
        lyrics_video_path = os.path.join(lyrics_dir, f"{sanitized_artist} - {sanitized_title} (With Vocals).mkv")
        lyrics_lrc_path = os.path.join(lyrics_dir, f"{sanitized_artist} - {sanitized_title} (Karaoke).lrc")

        # If files exist in parent directory, return early (but detect countdown padding first)
        if os.path.exists(parent_video_path) and os.path.exists(parent_lrc_path):
            self.logger.info("Found existing video and LRC files in parent directory, skipping transcription")
            
            # Detect countdown padding from existing LRC file
            countdown_padding_added, countdown_padding_seconds = self._detect_countdown_padding_from_lrc(parent_lrc_path)
            
            if countdown_padding_added:
                self.logger.info(f"Existing files have countdown padding: {countdown_padding_seconds}s")
            
            return {
                "lrc_filepath": parent_lrc_path,
                "ass_filepath": parent_video_path,
                "countdown_padding_added": countdown_padding_added,
                "countdown_padding_seconds": countdown_padding_seconds,
                "padded_audio_filepath": None,  # Original padded audio may not exist
            }

        # If files exist in lyrics directory, copy to parent and return (but detect countdown padding first)
        if os.path.exists(lyrics_video_path) and os.path.exists(lyrics_lrc_path):
            self.logger.info("Found existing video and LRC files in lyrics directory, copying to parent")
            os.makedirs(track_output_dir, exist_ok=True)
            shutil.copy2(lyrics_video_path, parent_video_path)
            shutil.copy2(lyrics_lrc_path, parent_lrc_path)
            
            # Detect countdown padding from existing LRC file
            countdown_padding_added, countdown_padding_seconds = self._detect_countdown_padding_from_lrc(parent_lrc_path)
            
            if countdown_padding_added:
                self.logger.info(f"Existing files have countdown padding: {countdown_padding_seconds}s")
            
            return {
                "lrc_filepath": parent_lrc_path,
                "ass_filepath": parent_video_path,
                "countdown_padding_added": countdown_padding_added,
                "countdown_padding_seconds": countdown_padding_seconds,
                "padded_audio_filepath": None,  # Original padded audio may not exist
            }

        # Check transcription provider configuration if transcription is not being skipped
        # Do this AFTER checking for existing files, since existing files don't need transcription
        if not self.skip_transcription:
            provider_status = self._check_transcription_providers()
            
            if provider_status["configured"]:
                self.logger.info(f"Transcription providers configured: {', '.join(provider_status['configured'])}")
            else:
                error_msg = self._build_transcription_provider_error_message(provider_status["missing"])
                raise ValueError(error_msg)

        # Create lyrics directory if it doesn't exist
        os.makedirs(lyrics_dir, exist_ok=True)
        self.logger.info(f"Created lyrics directory: {lyrics_dir}")

        # Set render_video to False if explicitly disabled
        render_video = self.render_video
        if not render_video:
            self.logger.info("Video rendering disabled, skipping video output")

        # Load environment variables
        load_dotenv()
        env_config = {
            "audioshake_api_token": os.getenv("AUDIOSHAKE_API_TOKEN"),
            "genius_api_token": os.getenv("GENIUS_API_TOKEN"),
            "spotify_cookie": os.getenv("SPOTIFY_COOKIE_SP_DC"),
            "runpod_api_key": os.getenv("RUNPOD_API_KEY"),
            "whisper_runpod_id": os.getenv("WHISPER_RUNPOD_ID"),
            "rapidapi_key": os.getenv("RAPIDAPI_KEY"),  # Add missing RAPIDAPI_KEY
        }

        # Create config objects for LyricsTranscriber
        transcriber_config = TranscriberConfig(
            audioshake_api_token=env_config.get("audioshake_api_token"),
            runpod_api_key=env_config.get("runpod_api_key"),
            whisper_runpod_id=env_config.get("whisper_runpod_id"),
            # Local Whisper is enabled by default as a fallback when no cloud providers are configured
            enable_local_whisper=True,
        )

        lyrics_config = LyricsConfig(
            genius_api_token=env_config.get("genius_api_token"),
            spotify_cookie=env_config.get("spotify_cookie"),
            rapidapi_key=env_config.get("rapidapi_key"),
            lyrics_file=self.lyrics_file,
        )
        
        # Debug logging for lyrics_config
        self.logger.info(f"LyricsConfig created with:")
        self.logger.info(f"  genius_api_token: {env_config.get('genius_api_token')[:3] + '...' if env_config.get('genius_api_token') else 'None'}")
        self.logger.info(f"  spotify_cookie: {env_config.get('spotify_cookie')[:3] + '...' if env_config.get('spotify_cookie') else 'None'}")
        self.logger.info(f"  rapidapi_key: {env_config.get('rapidapi_key')[:3] + '...' if env_config.get('rapidapi_key') else 'None'}")
        self.logger.info(f"  lyrics_file: {self.lyrics_file}")

        # Always defer countdown and video rendering to a later phase.
        # This ensures the review UI (both local and cloud) shows original timing
        # without the 3-second countdown shift. The caller is responsible for:
        # - Local CLI: karaoke_gen.py adds countdown and renders video after transcription
        # - Cloud backend: render_video_worker.py adds countdown and renders video
        #
        # This design ensures consistent behavior regardless of environment,
        # and the review UI always shows accurate, unshifted timestamps.
        self.logger.info("Deferring countdown and video rendering to post-review phase")

        # Check if auto-correction should be skipped
        # This is controlled by SKIP_CORRECTION env var, defaulting to True
        # When True, raw transcription goes directly to human review without automatic fixes
        skip_correction = os.getenv("SKIP_CORRECTION", "true").lower() in ("true", "1", "yes")
        if skip_correction:
            self.logger.info("Auto-correction disabled (SKIP_CORRECTION=true) - raw transcription will go to human review")

        output_config = OutputConfig(
            output_styles_json=self.style_params_json,
            output_dir=lyrics_dir,
            render_video=False,  # Always defer - caller handles video rendering after countdown
            allow_preview_video=self.render_video,  # Allow preview videos unless --no-video is set
            fetch_lyrics=True,
            run_transcription=not self.skip_transcription,
            run_correction=not skip_correction,  # Disabled when SKIP_CORRECTION=true
            generate_plain_text=True,
            generate_lrc=True,
            generate_cdg=False,  # CDG generation disabled (not currently supported)
            video_resolution="4k",
            enable_review=not self.skip_transcription_review,  # Honor the caller's setting
            subtitle_offset_ms=self.subtitle_offset_ms,
            add_countdown=False,  # Always defer - caller handles countdown after review
        )

        # Add this log entry to debug the OutputConfig
        self.logger.info(f"Instantiating LyricsTranscriber with OutputConfig: {output_config}")

        # Initialize transcriber with new config objects - use PROCESSING artist/title for lyrics work
        transcriber = LyricsTranscriber(
            audio_filepath=input_audio_wav,
            artist=processing_artist,  # Use lyrics_artist for processing
            title=processing_title,   # Use lyrics_title for processing
            transcriber_config=transcriber_config,
            lyrics_config=lyrics_config,
            output_config=output_config,
            logger=self.logger,
        )

        # Process and get results (pass deadline for agentic timeout)
        results: LyricsControllerResult = transcriber.process(agentic_deadline=agentic_deadline)
        self.logger.info(f"Transcriber Results Filepaths:")
        for key, value in results.__dict__.items():
            if key.endswith("_filepath"):
                self.logger.info(f"  {key}: {value}")

        # Build output dictionary
        transcriber_outputs = {}
        if results.lrc_filepath:
            transcriber_outputs["lrc_filepath"] = results.lrc_filepath
            self.logger.info(f"Moving LRC file from {results.lrc_filepath} to {parent_lrc_path}")
            shutil.copy2(results.lrc_filepath, parent_lrc_path)

        if results.ass_filepath:
            transcriber_outputs["ass_filepath"] = results.ass_filepath
            self.logger.info(f"Moving video file from {results.video_filepath} to {parent_video_path}")
            shutil.copy2(results.video_filepath, parent_video_path)

        if results.transcription_corrected:
            transcriber_outputs["corrected_lyrics_text"] = "\n".join(
                segment.text for segment in results.transcription_corrected.corrected_segments
            )
            transcriber_outputs["corrected_lyrics_text_filepath"] = results.corrected_txt

            # Save correction data to JSON file for review interface
            # Use the expected filename format: "{artist} - {title} (Lyrics Corrections).json"
            # Use sanitized names to be consistent with all other files created by lyrics_transcriber
            corrections_filename = f"{sanitized_artist} - {sanitized_title} (Lyrics Corrections).json"
            corrections_filepath = os.path.join(lyrics_dir, corrections_filename)
            
            # Use the CorrectionResult's to_dict() method to serialize
            correction_data = results.transcription_corrected.to_dict()
            
            with open(corrections_filepath, 'w') as f:
                json.dump(correction_data, f, indent=2)
            
            self.logger.info(f"Saved correction data to {corrections_filepath}")

        # Capture countdown padding information for syncing with instrumental audio
        transcriber_outputs["countdown_padding_added"] = getattr(results, "countdown_padding_added", False)
        transcriber_outputs["countdown_padding_seconds"] = getattr(results, "countdown_padding_seconds", 0.0)
        transcriber_outputs["padded_audio_filepath"] = getattr(results, "padded_audio_filepath", None)
        
        if transcriber_outputs["countdown_padding_added"]:
            self.logger.info(
                f"Countdown padding detected: {transcriber_outputs['countdown_padding_seconds']}s added to vocals. "
                f"Instrumental audio will need to be padded accordingly."
            )

        if transcriber_outputs:
            self.logger.info(f"*** Transcriber Filepath Outputs: ***")
            for key, value in transcriber_outputs.items():
                if key.endswith("_filepath"):
                    self.logger.info(f"  {key}: {value}")

        return transcriber_outputs
