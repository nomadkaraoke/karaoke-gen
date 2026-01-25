import os
import logging
import json
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from karaoke_gen.lyrics_transcriber.types import LyricsData, TranscriptionResult, CorrectionResult
from karaoke_gen.lyrics_transcriber.transcribers.base_transcriber import BaseTranscriber
from karaoke_gen.lyrics_transcriber.transcribers.audioshake import AudioShakeTranscriber, AudioShakeConfig
from karaoke_gen.lyrics_transcriber.transcribers.whisper import WhisperTranscriber, WhisperConfig
from karaoke_gen.lyrics_transcriber.transcribers.local_whisper import LocalWhisperTranscriber, LocalWhisperConfig
from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.lyrics.genius import GeniusProvider
from karaoke_gen.lyrics_transcriber.lyrics.spotify import SpotifyProvider
from karaoke_gen.lyrics_transcriber.lyrics.musixmatch import MusixmatchProvider
from karaoke_gen.lyrics_transcriber.lyrics.lrclib import LRCLIBProvider
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector
from karaoke_gen.lyrics_transcriber.core.config import TranscriberConfig, LyricsConfig, OutputConfig
from karaoke_gen.lyrics_transcriber.lyrics.file_provider import FileProvider


@dataclass
class LyricsControllerResult:
    """Holds the results of the transcription and correction process."""

    # Results from different sources
    lyrics_results: dict[str, LyricsData] = field(default_factory=dict)
    transcription_results: List[TranscriptionResult] = field(default_factory=list)

    # Corrected results
    transcription_corrected: Optional[CorrectionResult] = None

    # Output files
    lrc_filepath: Optional[str] = None
    ass_filepath: Optional[str] = None
    video_filepath: Optional[str] = None
    mp3_filepath: Optional[str] = None
    cdg_filepath: Optional[str] = None
    cdg_zip_filepath: Optional[str] = None
    original_txt: Optional[str] = None
    corrected_txt: Optional[str] = None
    corrections_json: Optional[str] = None

    # Countdown padding info (for applying same padding to other audio files)
    countdown_padding_added: bool = False
    countdown_padding_seconds: float = 0.0
    padded_audio_filepath: Optional[str] = None

    # Instrumental selection from combined review (for gen_cli.py to use)
    instrumental_selection: Optional[str] = None


class LyricsTranscriber:
    """
    Controller class that orchestrates the lyrics transcription workflow:
    1. Fetch lyrics from internet sources
    2. Run multiple transcription methods
    3. Correct transcribed lyrics using fetched lyrics
    4. Generate output formats (LRC, ASS, video)
    """

    def __init__(
        self,
        audio_filepath: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        transcriber_config: Optional[TranscriberConfig] = None,
        lyrics_config: Optional[LyricsConfig] = None,
        output_config: Optional[OutputConfig] = None,
        transcribers: Optional[Dict[str, BaseTranscriber]] = None,
        lyrics_providers: Optional[Dict[str, BaseLyricsProvider]] = None,
        corrector: Optional[LyricsCorrector] = None,
        output_generator: Optional[OutputGenerator] = None,
        logger: Optional[logging.Logger] = None,
        log_level: int = logging.DEBUG,
        log_formatter: Optional[logging.Formatter] = None,
    ):
        # Set up logging
        self.logger = logger or logging.getLogger(__name__)
        if not logger:
            self.logger.setLevel(log_level)
            if not self.logger.handlers:
                handler = logging.StreamHandler()
                formatter = log_formatter or logging.Formatter("%(asctime)s - %(levelname)s - %(module)s - %(message)s")
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)

        self.logger.debug(f"LyricsTranscriber instantiating with input file: {audio_filepath}")

        # Store configs (with defaults if not provided)
        self.transcriber_config = transcriber_config or TranscriberConfig()
        self.lyrics_config = lyrics_config or LyricsConfig()
        self.output_config = output_config or OutputConfig()

        # Check if styles JSON is available for CDG and video features
        if not self.output_config.output_styles_json or not os.path.exists(self.output_config.output_styles_json):
            if self.output_config.generate_cdg or self.output_config.render_video:
                self.logger.warning(
                    f"Output styles JSON file not found: {self.output_config.output_styles_json}. "
                    "CDG and video generation will be disabled."
                )
                self.output_config.generate_cdg = False
                self.output_config.render_video = False

        # Basic settings with sanitized filenames
        self.audio_filepath = audio_filepath
        self.artist = artist
        self.title = title
        self.output_prefix = self._create_sanitized_output_prefix(artist, title)

        # Add after creating necessary folders
        self.logger.debug(f"Using cache directory: {self.output_config.cache_dir}")
        self.logger.debug(f"Using output directory: {self.output_config.output_dir}")

        # Create necessary folders
        os.makedirs(self.output_config.cache_dir, exist_ok=True)
        os.makedirs(self.output_config.output_dir, exist_ok=True)

        # Initialize results
        self.results = LyricsControllerResult()

        # Load styles early so lyrics providers can use them
        self._load_styles()

        # Initialize components (with dependency injection)
        self.transcribers = transcribers or self._initialize_transcribers()
        self.lyrics_providers = lyrics_providers or self._initialize_lyrics_providers()
        self.corrector = corrector or LyricsCorrector(cache_dir=self.output_config.cache_dir, logger=self.logger)
        self.output_generator = output_generator or self._initialize_output_generator()

        # Log enabled features
        self.logger.info("Enabled features:")
        self.logger.info(f"  Lyrics fetching: {'enabled' if self.output_config.fetch_lyrics else 'disabled'}")
        self.logger.info(f"  Transcription: {'enabled' if self.output_config.run_transcription else 'disabled'}")
        self.logger.info(f"  Lyrics correction: {'enabled' if self.output_config.run_correction else 'disabled'}")
        self.logger.info(f"  Plain text output: {'enabled' if self.output_config.generate_plain_text else 'disabled'}")
        self.logger.info(f"  LRC file generation: {'enabled' if self.output_config.generate_lrc else 'disabled'}")
        self.logger.info(f"  CDG file generation: {'enabled' if self.output_config.generate_cdg else 'disabled'}")
        self.logger.info(f"  Video rendering: {'enabled' if self.output_config.render_video else 'disabled'}")
        if self.output_config.render_video:
            self.logger.info(f"    Video resolution: {self.output_config.video_resolution}")

    def _load_styles(self) -> None:
        """Load styles from JSON file if available."""
        if self.output_config.output_styles_json and os.path.exists(self.output_config.output_styles_json):
            try:
                with open(self.output_config.output_styles_json, "r") as f:
                    self.output_config.styles = json.load(f)
                self.logger.debug(f"Loaded output styles from: {self.output_config.output_styles_json}")
            except Exception as e:
                self.logger.warning(f"Failed to load output styles file: {str(e)}")
                self.output_config.styles = {}
        else:
            self.logger.debug("No styles JSON file provided or file does not exist")
            self.output_config.styles = {}

    def _sanitize_filename(self, filename: str) -> str:
        """Replace or remove characters that are unsafe for filenames."""
        if not filename:
            return ""
        # Replace problematic characters with underscores
        for char in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
            filename = filename.replace(char, "_")
        # Remove any trailing spaces
        filename = filename.rstrip(" ")
        return filename

    def _create_sanitized_output_prefix(self, artist: Optional[str], title: Optional[str]) -> str:
        """Create a sanitized output prefix from artist and title."""
        if artist and title:
            sanitized_artist = self._sanitize_filename(artist)
            sanitized_title = self._sanitize_filename(title)
            return f"{sanitized_artist} - {sanitized_title}"
        else:
            return self._sanitize_filename(os.path.splitext(os.path.basename(self.audio_filepath))[0])

    def _initialize_transcribers(self) -> Dict[str, BaseTranscriber]:
        """Initialize available transcription services."""
        transcribers = {}

        # Add debug logging for config values
        self.logger.debug(f"Initializing transcribers with config: {self.transcriber_config}")
        self.logger.debug(f"Using cache directory for transcribers: {self.output_config.cache_dir}")

        if self.transcriber_config.audioshake_api_token:
            self.logger.debug("Initializing AudioShake transcriber")
            transcribers["audioshake"] = {
                "instance": AudioShakeTranscriber(
                    cache_dir=self.output_config.cache_dir,
                    config=AudioShakeConfig(api_token=self.transcriber_config.audioshake_api_token),
                    logger=self.logger,
                ),
                "priority": 1,  # AudioShake has highest priority
            }
        else:
            self.logger.debug("Skipping AudioShake transcriber - no API token provided")

        if self.transcriber_config.runpod_api_key and self.transcriber_config.whisper_runpod_id:
            self.logger.debug("Initializing Whisper transcriber")
            transcribers["whisper"] = {
                "instance": WhisperTranscriber(
                    cache_dir=self.output_config.cache_dir,
                    config=WhisperConfig(
                        runpod_api_key=self.transcriber_config.runpod_api_key, endpoint_id=self.transcriber_config.whisper_runpod_id
                    ),
                    logger=self.logger,
                ),
                "priority": 2,  # Whisper has lower priority
            }
        else:
            self.logger.debug("Skipping Whisper transcriber - missing runpod_api_key or whisper_runpod_id")

        # Local Whisper - lowest priority, fallback when cloud services unavailable
        if self.transcriber_config.enable_local_whisper:
            # Check if whisper-timestamped is available
            try:
                import whisper_timestamped  # noqa: F401

                self.logger.debug("Initializing LocalWhisper transcriber")
                transcribers["local_whisper"] = {
                    "instance": LocalWhisperTranscriber(
                        cache_dir=self.output_config.cache_dir,
                        config=LocalWhisperConfig(
                            model_size=self.transcriber_config.local_whisper_model_size,
                            device=self.transcriber_config.local_whisper_device,
                            cache_dir=self.transcriber_config.local_whisper_cache_dir,
                            language=self.transcriber_config.local_whisper_language,
                        ),
                        logger=self.logger,
                    ),
                    "priority": 3,  # Local Whisper has lowest priority (fallback)
                }
            except ImportError:
                self.logger.debug(
                    "Skipping LocalWhisper transcriber - whisper-timestamped not installed. "
                    "Install with: pip install karaoke-gen[local-whisper]"
                )
        else:
            self.logger.debug("Skipping LocalWhisper transcriber - disabled via enable_local_whisper=False")

        return transcribers

    def _initialize_lyrics_providers(self) -> Dict[str, BaseLyricsProvider]:
        """Initialize available lyrics providers."""
        providers = {}

        # Get max_line_length from styles if available, otherwise use config default
        max_line_length = self.output_config.styles.get("karaoke", {}).get("max_line_length", self.output_config.default_max_line_length)
        self.logger.info(f"Using max_line_length for lyrics providers: {max_line_length}")

        # Create provider config with all necessary parameters
        provider_config = LyricsProviderConfig(
            genius_api_token=self.lyrics_config.genius_api_token,
            rapidapi_key=self.lyrics_config.rapidapi_key,
            spotify_cookie=self.lyrics_config.spotify_cookie,
            lyrics_file=self.lyrics_config.lyrics_file,
            cache_dir=self.output_config.cache_dir,
            audio_filepath=self.audio_filepath,
            max_line_length=max_line_length,
        )

        if provider_config.lyrics_file and os.path.exists(provider_config.lyrics_file):
            self.logger.debug(f"Initializing File lyrics provider with file: {provider_config.lyrics_file}")
            providers["file"] = FileProvider(config=provider_config, logger=self.logger)
            return providers

        # LRCLIB - always enabled (no API key required)
        self.logger.debug("Initializing LRCLIB lyrics provider")
        providers["lrclib"] = LRCLIBProvider(config=provider_config, logger=self.logger)

        if provider_config.genius_api_token:
            self.logger.debug("Initializing Genius lyrics provider")
            providers["genius"] = GeniusProvider(config=provider_config, logger=self.logger)
        else:
            self.logger.debug("Skipping Genius provider - no API token provided")

        if provider_config.spotify_cookie:
            self.logger.debug("Initializing Spotify lyrics provider")
            providers["spotify"] = SpotifyProvider(config=provider_config, logger=self.logger)
        else:
            self.logger.debug("Skipping Spotify provider - no cookie provided")

        if provider_config.rapidapi_key:
            self.logger.debug("Initializing Musixmatch lyrics provider")
            providers["musixmatch"] = MusixmatchProvider(config=provider_config, logger=self.logger)
        else:
            self.logger.debug("Skipping Musixmatch provider - no RapidAPI key provided")

        return providers

    def _initialize_output_generator(self) -> OutputGenerator:
        """Initialize output generation service."""
        return OutputGenerator(config=self.output_config, logger=self.logger)

    def process(
        self,
        agentic_deadline: Optional[float] = None,
        separated_audio_paths: Optional[Dict[str, any]] = None,
    ) -> LyricsControllerResult:
        """Main processing method that orchestrates the entire workflow.

        Args:
            agentic_deadline: Optional Unix timestamp. If agentic correction is still
                running after this time, it will abort and return uncorrected results.
            separated_audio_paths: Optional dict containing paths to separated audio files.
                Used for combined review flow. Format matches KaraokePrep's separated_audio:
                {
                    "backing_vocals": {"model": {"backing_vocals": "path/to/backing.flac", ...}},
                    "clean_instrumental": {"instrumental": "path/to/clean.flac"},
                    "combined_instrumentals": {"model": "path/to/combined.flac"},
                }
        """
        # Store separated audio paths for use in review
        self.separated_audio_paths = separated_audio_paths

        self.logger.info(f"LyricsTranscriber controller beginning processing for {self.artist} - {self.title}")

        # Debug: Log package version and environment variables
        try:
            from karaoke_gen import lyrics_transcriber
            package_version = getattr(lyrics_transcriber, '__version__', 'unknown')
            self.logger.info(f"LyricsTranscriber package version: {package_version}")
        except Exception as e:
            self.logger.warning(f"Could not get package version: {e}")

        # Debug: Log environment variables (first 3 characters only for security)
        env_vars = {}
        for key, value in os.environ.items():
            if value:
                env_vars[key] = value[:3] + "..." if len(value) > 3 else value
            else:
                env_vars[key] = "(empty)"
        
        self.logger.info(f"Environment variables count: {len(env_vars)}")
        
        # Log specific API-related variables
        api_vars = {k: v for k, v in env_vars.items() if any(keyword in k.upper() for keyword in ['API', 'TOKEN', 'KEY', 'SECRET'])}
        if api_vars:
            self.logger.info(f"API-related environment variables: {api_vars}")
        else:
            self.logger.warning("No API-related environment variables found")
            
        # Log all env vars if in debug mode
        if self.logger.getEffectiveLevel() <= logging.DEBUG:
            self.logger.debug(f"All environment variables: {env_vars}")

        # Check for existing corrections JSON
        corrections_json_path = os.path.join(self.output_config.output_dir, f"{self.output_prefix} (Lyrics Corrections).json")

        if os.path.exists(corrections_json_path):
            self.logger.info(f"Found existing corrections JSON: {corrections_json_path}")
            try:
                with open(corrections_json_path, "r", encoding="utf-8") as f:
                    corrections_data = json.load(f)

                # Reconstruct CorrectionResult from JSON
                self.results.transcription_corrected = CorrectionResult.from_dict(corrections_data)
                self.logger.info("Successfully loaded existing corrections data")

                # Check if the loaded corrections have countdown padding applied
                # This is important because the video needs to use padded audio to sync
                # with the countdown-adjusted timestamps in the ASS subtitles
                if self.output_config.add_countdown:
                    from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
                    
                    countdown_processor = CountdownProcessor(
                        cache_dir=self.output_config.cache_dir,
                        logger=self.logger,
                    )
                    
                    if countdown_processor.has_countdown(self.results.transcription_corrected):
                        self.logger.info(
                            "Loaded corrections have countdown - creating padded audio for video sync"
                        )
                        # Create padded audio file to match the countdown-adjusted timestamps
                        padded_audio_path = countdown_processor.create_padded_audio_only(self.audio_filepath)
                        self.audio_filepath = padded_audio_path
                        
                        # Set countdown padding attributes on results
                        self.results.countdown_padding_added = True
                        self.results.countdown_padding_seconds = countdown_processor.COUNTDOWN_PADDING_SECONDS
                        self.results.padded_audio_filepath = padded_audio_path
                        
                        self.logger.info(
                            f"Countdown padding applied: {countdown_processor.COUNTDOWN_PADDING_SECONDS}s. "
                            f"Using padded audio: {padded_audio_path}"
                        )
                    else:
                        self.logger.info("Loaded corrections do not have countdown - no padding needed")

                # Skip to output generation
                self.generate_outputs()
                self.logger.info("Processing completed successfully using existing corrections")
                return self.results

            except Exception as e:
                self.logger.error(f"Failed to load existing corrections JSON: {str(e)}")
                # Continue with normal processing if loading fails

        # Normal processing flow continues...
        if self.output_config.fetch_lyrics and self.artist and self.title:
            self.fetch_lyrics()
        else:
            self.logger.info("Skipping lyrics fetching - no artist/title provided or fetching disabled")

        # Step 2: Run transcription if enabled
        if self.output_config.run_transcription:
            self.transcribe()
        else:
            self.logger.info("Skipping transcription - transcription disabled")

        # Step 3: Process and correct lyrics if enabled AND we have transcription results
        if self.output_config.run_correction and self.results.transcription_results:
            self.correct_lyrics(agentic_deadline=agentic_deadline)
        elif self.output_config.run_correction:
            self.logger.info("Skipping lyrics correction - no transcription results available")
        elif self.results.transcription_results:
            # Correction disabled but we have transcription - create uncorrected CorrectionResult
            # This allows the review UI to work with raw transcription
            self.logger.info("Auto-correction disabled - using raw transcription for review")
            self._create_uncorrected_result()

        # Step 4: Run review UI if we have corrected/uncorrected transcription data
        # This is called AFTER both correct_lyrics() and _create_uncorrected_result() paths
        # to ensure review always happens when enabled, regardless of SKIP_CORRECTION setting
        if self.results.transcription_corrected:
            self._run_review()

        # Step 5: Generate outputs based on what we have
        if self.results.transcription_corrected or self.results.lyrics_results:
            self.generate_outputs()
        else:
            self.logger.warning("No corrected transcription or lyrics available. Skipping output generation.")

        self.logger.info("Processing completed successfully")
        return self.results

    def fetch_lyrics(self) -> None:
        """Fetch lyrics from available providers."""
        self.logger.info(f"Fetching lyrics for {self.artist} - {self.title}")

        for name, provider in self.lyrics_providers.items():
            try:
                result = provider.fetch_lyrics(self.artist, self.title)
                if result:
                    self.results.lyrics_results[name] = result
                    self.logger.info(f"Successfully fetched lyrics from {name}")

            except Exception as e:
                self.logger.error(f"Failed to fetch lyrics from {name}: {str(e)}")
                continue

        if not self.results.lyrics_results:
            self.logger.warning("No lyrics found from any source")

    def transcribe(self) -> None:
        """Run transcription using all available transcribers."""
        provider_names = list(self.transcribers.keys())
        
        if not provider_names:
            self.logger.warning(
                "Starting transcription with providers: [] - NO TRANSCRIPTION PROVIDERS CONFIGURED!\n"
                "\n"
                "This means no word-level timing data will be generated, and synchronized karaoke "
                "lyrics cannot be created. The output will lack the '(With Vocals).mkv' video file.\n"
                "\n"
                "To enable transcription, configure at least one provider:\n"
                "  - AudioShake: Set AUDIOSHAKE_API_TOKEN environment variable\n"
                "  - Whisper/RunPod: Set RUNPOD_API_KEY and WHISPER_RUNPOD_ID environment variables\n"
                "\n"
                "See README.md 'Transcription Providers' section for detailed setup instructions."
            )
        else:
            self.logger.info(f"Starting transcription with providers: {provider_names}")
            self._log_provider_configuration_status()

        for name, transcriber_info in self.transcribers.items():
            self.logger.info(f"Running transcription with {name}")
            result = transcriber_info["instance"].transcribe(self.audio_filepath)
            if result:
                # Add the transcriber name and priority to the result
                self.results.transcription_results.append(
                    TranscriptionResult(name=name, priority=transcriber_info["priority"], result=result)
                )
                self.logger.debug(f"Transcription completed for {name}")

        if not self.results.transcription_results:
            self.logger.warning(
                "No successful transcriptions from any provider. "
                "Check that your API tokens are valid and the services are accessible."
            )

    def _log_provider_configuration_status(self) -> None:
        """Log detailed configuration status for each potential transcription provider."""
        self.logger.debug("Transcription provider configuration status:")
        
        # AudioShake status
        if self.transcriber_config.audioshake_api_token:
            self.logger.debug("  - AudioShake: CONFIGURED (API token provided)")
        else:
            self.logger.debug("  - AudioShake: NOT CONFIGURED (missing AUDIOSHAKE_API_TOKEN)")
        
        # Whisper/RunPod status
        has_runpod_key = bool(self.transcriber_config.runpod_api_key)
        has_whisper_id = bool(self.transcriber_config.whisper_runpod_id)

        if has_runpod_key and has_whisper_id:
            self.logger.debug("  - Whisper (RunPod): CONFIGURED (API key and endpoint ID provided)")
        elif has_runpod_key:
            self.logger.debug("  - Whisper (RunPod): PARTIALLY CONFIGURED (missing WHISPER_RUNPOD_ID)")
        elif has_whisper_id:
            self.logger.debug("  - Whisper (RunPod): PARTIALLY CONFIGURED (missing RUNPOD_API_KEY)")
        else:
            self.logger.debug("  - Whisper (RunPod): NOT CONFIGURED (missing RUNPOD_API_KEY and WHISPER_RUNPOD_ID)")

        # Local Whisper status
        if self.transcriber_config.enable_local_whisper:
            try:
                import whisper_timestamped  # noqa: F401
                self.logger.debug(
                    f"  - LocalWhisper: AVAILABLE (model={self.transcriber_config.local_whisper_model_size}, "
                    f"device={self.transcriber_config.local_whisper_device or 'auto'})"
                )
            except ImportError:
                self.logger.debug("  - LocalWhisper: ENABLED but whisper-timestamped not installed")
        else:
            self.logger.debug("  - LocalWhisper: DISABLED (enable_local_whisper=False)")

    def _create_uncorrected_result(self) -> None:
        """Create a CorrectionResult from raw transcription without any corrections.

        Used when auto-correction is disabled (SKIP_CORRECTION=true) to allow
        the review UI to work with raw transcription data. Still calculates
        anchor sequences and gaps to help reviewers identify uncertain areas.
        """
        if not self.results.transcription_results:
            self.logger.warning("No transcription results available for uncorrected result")
            return

        # Use the highest priority transcription result
        sorted_results = sorted(self.results.transcription_results, key=lambda x: x.priority)
        best_transcription = sorted_results[0]

        # Count total words in the transcription
        total_words = sum(len(segment.words) for segment in best_transcription.result.segments)

        # Calculate anchor sequences and gaps if we have reference lyrics
        anchor_sequences = []
        gap_sequences = []
        if self.results.lyrics_results:
            self.logger.info("Calculating anchor sequences for uncorrected result")
            transcribed_text = " ".join(
                " ".join(w.text for w in segment.words)
                for segment in best_transcription.result.segments
            )
            anchor_sequences = self.corrector.anchor_finder.find_anchors(
                transcribed_text, self.results.lyrics_results, best_transcription
            )
            gap_sequences = self.corrector.anchor_finder.find_gaps(
                transcribed_text, anchor_sequences, self.results.lyrics_results, best_transcription
            )
            self.logger.info(
                f"Found {len(anchor_sequences)} anchor sequences and {len(gap_sequences)} gaps"
            )

        # Create a CorrectionResult with no corrections applied
        self.results.transcription_corrected = CorrectionResult(
            original_segments=best_transcription.result.segments,
            corrected_segments=best_transcription.result.segments,
            corrections=[],
            corrections_made=0,
            confidence=1.0,  # Full confidence since we're using original
            reference_lyrics=self.results.lyrics_results,  # Include fetched lyrics for review UI
            anchor_sequences=anchor_sequences,
            gap_sequences=gap_sequences,
            resized_segments=[],
            correction_steps=[],
            word_id_map={},
            segment_id_map={},
            metadata={
                "correction_type": "none",
                "reason": "correction_disabled",
                "audio_filepath": self.audio_filepath,
                "anchor_sequences_count": len(anchor_sequences),
                "gap_sequences_count": len(gap_sequences),
                "total_words": total_words,
                "correction_ratio": 0.0,
                "available_handlers": [],
                "enabled_handlers": [],
                "agentic_routing": "disabled",
            },
        )
        self.logger.info(
            f"Created uncorrected result with {total_words} words, "
            f"{len(anchor_sequences)} anchors, {len(gap_sequences)} gaps from {best_transcription.name}"
        )

    def correct_lyrics(self, agentic_deadline: Optional[float] = None) -> None:
        """Run lyrics correction using transcription and internet lyrics.

        Args:
            agentic_deadline: Optional Unix timestamp. If agentic correction is still
                running after this time, it will abort and return uncorrected results.
        """
        self.logger.info("Starting lyrics correction process")

        # Check if we have reference lyrics to work with
        if not self.results.lyrics_results:
            self.logger.warning("No reference lyrics available for correction - using raw transcription")
            # Use the highest priority transcription result as the "corrected" version
            if self.results.transcription_results:
                sorted_results = sorted(self.results.transcription_results, key=lambda x: x.priority)
                best_transcription = sorted_results[0]

                # Count total words in the transcription
                total_words = sum(len(segment.words) for segment in best_transcription.result.segments)

                # Create a CorrectionResult with no corrections
                self.results.transcription_corrected = CorrectionResult(
                    original_segments=best_transcription.result.segments,
                    corrected_segments=best_transcription.result.segments,
                    corrections=[],  # No corrections made
                    corrections_made=0,  # No corrections made
                    confidence=1.0,  # Full confidence since we're using original
                    reference_lyrics={},
                    anchor_sequences=[],
                    gap_sequences=[],
                    resized_segments=[],
                    correction_steps=[],
                    word_id_map={},
                    segment_id_map={},
                    metadata={
                        "correction_type": "none",
                        "reason": "no_reference_lyrics",
                        "audio_filepath": self.audio_filepath,
                        "anchor_sequences_count": 0,
                        "gap_sequences_count": 0,
                        "total_words": total_words,
                        "correction_ratio": 0.0,
                        "available_handlers": [],
                        "enabled_handlers": [],
                    },
                )
        else:
            # Create metadata dict with song info
            metadata = {
                "artist": self.artist,
                "title": self.title,
                "full_reference_texts": {source: lyrics.get_full_text() for source, lyrics in self.results.lyrics_results.items()},
            }

            # Get enabled handlers from metadata if available
            enabled_handlers = metadata.get("enabled_handlers", None)

            # Create corrector with enabled handlers
            corrector = LyricsCorrector(cache_dir=self.output_config.cache_dir, enabled_handlers=enabled_handlers, logger=self.logger)

            corrected_data = corrector.run(
                transcription_results=self.results.transcription_results,
                lyrics_results=self.results.lyrics_results,
                metadata=metadata,
                agentic_deadline=agentic_deadline,
            )

            # Store corrected results
            self.results.transcription_corrected = corrected_data
            self.logger.info("Lyrics correction completed")

    def _prepare_instrumental_options(self) -> tuple:
        """Prepare instrumental options and analysis for the combined review UI.

        Returns:
            Tuple of (instrumental_options, backing_vocals_analysis, clean_path, with_backing_path, backing_vocals_path)
            Returns ([], None, None, None, None) if no separated audio is available.
        """
        if not getattr(self, 'separated_audio_paths', None):
            return [], None, None, None, None

        separated = self.separated_audio_paths
        instrumental_options = []
        backing_vocals_analysis = None

        # Find backing vocals path
        backing_vocals_path = None
        backing_vocals_result = separated.get("backing_vocals", {})
        for model, paths in backing_vocals_result.items():
            if isinstance(paths, dict) and paths.get("backing_vocals"):
                backing_vocals_path = paths["backing_vocals"]
                break

        # Find clean instrumental path
        clean_instrumental_path = None
        clean_result = separated.get("clean_instrumental", {})
        if isinstance(clean_result, dict):
            clean_instrumental_path = clean_result.get("instrumental")

        # Find combined instrumental (with backing vocals) path
        with_backing_path = None
        combined_result = separated.get("combined_instrumentals", {})
        for model, path in combined_result.items():
            if path and os.path.exists(path):
                with_backing_path = path
                break

        # Build instrumental options for UI
        if clean_instrumental_path and os.path.exists(clean_instrumental_path):
            instrumental_options.append({
                "id": "clean",
                "label": "Clean Instrumental",
                "audio_path": clean_instrumental_path,
            })

        if with_backing_path and os.path.exists(with_backing_path):
            instrumental_options.append({
                "id": "with_backing",
                "label": "With Backing Vocals",
                "audio_path": with_backing_path,
            })

        # Run backing vocals analysis if we have the file
        if backing_vocals_path and os.path.exists(backing_vocals_path):
            try:
                from karaoke_gen.instrumental_review import AudioAnalyzer

                self.logger.info(f"Analyzing backing vocals: {backing_vocals_path}")
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

                self.logger.info(
                    f"Backing vocals analysis: has_audible_content={analysis.has_audible_content}, "
                    f"recommendation={analysis.recommended_selection.value}"
                )
            except Exception as e:
                self.logger.warning(f"Failed to analyze backing vocals: {e}")

        return (
            instrumental_options,
            backing_vocals_analysis,
            clean_instrumental_path,
            with_backing_path,
            backing_vocals_path,
        )

    def _run_review(self) -> None:
        """Run the combined review UI for lyrics and instrumental selection.

        This method is called after either correct_lyrics() or _create_uncorrected_result()
        to ensure review always happens when enabled, regardless of the correction path taken.
        """
        if not self.results.transcription_corrected:
            self.logger.warning("No transcription data available for review")
            return

        # Add human review step
        if self.output_config.enable_review:
            from karaoke_gen.lyrics_transcriber.review.server import ReviewServer

            self.logger.info("Starting human review process")

            # Prepare instrumental options if separated audio is available
            (
                instrumental_options,
                backing_vocals_analysis,
                clean_instrumental_path,
                with_backing_path,
                backing_vocals_path,
            ) = self._prepare_instrumental_options()

            if instrumental_options:
                self.logger.info(f"Including {len(instrumental_options)} instrumental options in combined review")

            # Create and start review server
            review_server = ReviewServer(
                correction_result=self.results.transcription_corrected,
                output_config=self.output_config,
                audio_filepath=self.audio_filepath,
                logger=self.logger,
                # Instrumental review data
                instrumental_options=instrumental_options,
                backing_vocals_analysis=backing_vocals_analysis,
                clean_instrumental_path=clean_instrumental_path,
                with_backing_path=with_backing_path,
                backing_vocals_path=backing_vocals_path,
            )
            reviewed_data = review_server.start()

            self.logger.info("Human review completed, updated transcription_corrected with reviewed_data")
            self.results.transcription_corrected = reviewed_data

            # Store instrumental selection in results if it was made
            if review_server.instrumental_selection:
                self.results.instrumental_selection = review_server.instrumental_selection
                self.logger.info(f"Instrumental selection stored: {review_server.instrumental_selection}")

        # Add countdown intro if enabled and needed (after review, before output generation)
        if self.output_config.add_countdown and self.results.transcription_corrected:
            from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor

            self.logger.info("Processing countdown intro (if needed)")
            countdown_processor = CountdownProcessor(
                cache_dir=self.output_config.cache_dir,
                logger=self.logger,
            )

            # Process and potentially modify the correction result and audio filepath
            (
                self.results.transcription_corrected,
                self.audio_filepath,
                padding_added,
                padding_seconds,
            ) = countdown_processor.process(
                correction_result=self.results.transcription_corrected,
                audio_filepath=self.audio_filepath,
            )

            # Store padding information in results for parent code to use
            self.results.countdown_padding_added = padding_added
            self.results.countdown_padding_seconds = padding_seconds
            if padding_added:
                self.results.padded_audio_filepath = self.audio_filepath
                self.logger.info(
                    f"Countdown padding applied: {padding_seconds}s added to audio. "
                    f"Padded audio: {self.audio_filepath}"
                )

    def generate_outputs(self) -> None:
        """Generate output files based on enabled features and available data."""
        self.logger.info("Generating output files")

        # Only proceed with outputs that make sense based on what we have
        has_correction = bool(self.results.transcription_corrected)

        output_files = self.output_generator.generate_outputs(
            transcription_corrected=self.results.transcription_corrected if has_correction else None,
            lyrics_results=self.results.lyrics_results,
            output_prefix=self.output_prefix,
            audio_filepath=self.audio_filepath,
            artist=self.artist,
            title=self.title,
        )

        # Store results
        self.results.lrc_filepath = output_files.lrc
        self.results.ass_filepath = output_files.ass
        self.results.video_filepath = output_files.video
        self.results.original_txt = output_files.original_txt
        self.results.corrected_txt = output_files.corrected_txt
        self.results.corrections_json = output_files.corrections_json
        self.results.cdg_filepath = output_files.cdg
        self.results.mp3_filepath = output_files.mp3
        self.results.cdg_zip_filepath = output_files.cdg_zip
