"""Local Whisper transcription service using whisper-timestamped for word-level timestamps."""

from dataclasses import dataclass
import os
import logging
from typing import Optional, Dict, Any, Union
from pathlib import Path

from karaoke_gen.lyrics_transcriber.types import TranscriptionData, LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.transcribers.base_transcriber import BaseTranscriber, TranscriptionError
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils


@dataclass
class LocalWhisperConfig:
    """Configuration for local Whisper transcription service."""

    model_size: str = "medium"  # tiny, base, small, medium, large, large-v2, large-v3
    device: Optional[str] = None  # None for auto-detect, or "cpu", "cuda", "mps"
    cache_dir: Optional[str] = None  # Directory for model downloads (~/.cache/whisper by default)
    language: Optional[str] = None  # Language code for transcription, None for auto-detect
    compute_type: str = "auto"  # float16, float32, int8, auto


class LocalWhisperTranscriber(BaseTranscriber):
    """
    Transcription service using local Whisper inference via whisper-timestamped.

    This transcriber runs Whisper models locally on your machine, supporting
    CPU, CUDA GPU, and Apple Silicon MPS acceleration. It uses the
    whisper-timestamped library to get accurate word-level timestamps.

    Requirements:
        pip install karaoke-gen[local-whisper]

    Configuration:
        Set environment variables to customize behavior:
        - WHISPER_MODEL_SIZE: Model size (tiny, base, small, medium, large)
        - WHISPER_DEVICE: Device to use (cpu, cuda, mps, or auto)
        - WHISPER_CACHE_DIR: Directory for model downloads
        - WHISPER_LANGUAGE: Language code (en, es, fr, etc.) or auto-detect
    """

    def __init__(
        self,
        cache_dir: Union[str, Path],
        config: Optional[LocalWhisperConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize local Whisper transcriber.

        Args:
            cache_dir: Directory for caching transcription results
            config: Configuration options for the transcriber
            logger: Logger instance to use
        """
        super().__init__(cache_dir=cache_dir, logger=logger)

        # Initialize configuration from env vars or defaults
        self.config = config or LocalWhisperConfig(
            model_size=os.getenv("WHISPER_MODEL_SIZE", "medium"),
            device=os.getenv("WHISPER_DEVICE"),  # None for auto-detect
            cache_dir=os.getenv("WHISPER_CACHE_DIR"),
            language=os.getenv("WHISPER_LANGUAGE"),  # None for auto-detect
        )

        # Lazy-loaded model instance (loaded on first use)
        self._model = None
        self._whisper_module = None

        self.logger.debug(
            f"LocalWhisperTranscriber initialized with model_size={self.config.model_size}, "
            f"device={self.config.device or 'auto'}, language={self.config.language or 'auto-detect'}"
        )

    def get_name(self) -> str:
        """Return the name of this transcription service."""
        return "LocalWhisper"

    def _check_dependencies(self) -> None:
        """Check that required dependencies are installed."""
        try:
            import whisper_timestamped  # noqa: F401
        except ImportError:
            raise TranscriptionError(
                "whisper-timestamped is not installed. "
                "Install it with: pip install karaoke-gen[local-whisper] "
                "or: pip install whisper-timestamped"
            )

    def _get_device(self) -> str:
        """Determine the best device to use for inference."""
        if self.config.device:
            return self.config.device

        # Auto-detect best available device
        try:
            import torch

            if torch.cuda.is_available():
                self.logger.info("Using CUDA GPU for Whisper inference")
                return "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self.logger.info("Using Apple Silicon MPS for Whisper inference")
                return "cpu"  # whisper-timestamped works better with CPU on MPS
            else:
                self.logger.info("Using CPU for Whisper inference (no GPU detected)")
                return "cpu"
        except ImportError:
            self.logger.warning("PyTorch not available, defaulting to CPU")
            return "cpu"

    def _load_model(self):
        """Load the Whisper model (lazy loading on first use)."""
        if self._model is not None:
            return self._model

        self._check_dependencies()
        import whisper_timestamped as whisper

        self._whisper_module = whisper

        device = self._get_device()
        self.logger.info(f"Loading Whisper model '{self.config.model_size}' on device '{device}'...")

        try:
            # Load model with optional custom cache directory
            download_root = self.config.cache_dir
            self._model = whisper.load_model(
                self.config.model_size,
                device=device,
                download_root=download_root,
            )
            self.logger.info(f"Whisper model '{self.config.model_size}' loaded successfully")
            return self._model
        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "CUDA" in str(e):
                raise TranscriptionError(
                    f"GPU out of memory loading model '{self.config.model_size}'. "
                    "Try using a smaller model (set WHISPER_MODEL_SIZE=small or tiny) "
                    "or force CPU mode (set WHISPER_DEVICE=cpu)"
                ) from e
            raise TranscriptionError(f"Failed to load Whisper model: {e}") from e
        except Exception as e:
            raise TranscriptionError(f"Failed to load Whisper model: {e}") from e

    def _perform_transcription(self, audio_filepath: str) -> Dict[str, Any]:
        """
        Perform local Whisper transcription with word-level timestamps.

        Args:
            audio_filepath: Path to the audio file to transcribe

        Returns:
            Raw transcription result dictionary
        """
        self.logger.info(f"Starting local Whisper transcription for {audio_filepath}")

        # Load model if not already loaded
        model = self._load_model()

        try:
            # Perform transcription with word-level timestamps
            transcribe_kwargs = {
                "verbose": False,
            }

            # Add language if specified
            if self.config.language:
                transcribe_kwargs["language"] = self.config.language

            self.logger.debug(f"Transcribing with options: {transcribe_kwargs}")
            result = self._whisper_module.transcribe_timestamped(
                model,
                audio_filepath,
                **transcribe_kwargs,
            )

            self.logger.info("Local Whisper transcription completed successfully")
            return result

        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                raise TranscriptionError(
                    f"GPU out of memory during transcription. "
                    "Try using a smaller model (WHISPER_MODEL_SIZE=small) "
                    "or force CPU mode (WHISPER_DEVICE=cpu)"
                ) from e
            raise TranscriptionError(f"Transcription failed: {e}") from e
        except Exception as e:
            raise TranscriptionError(f"Transcription failed: {e}") from e

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> TranscriptionData:
        """
        Convert whisper-timestamped output to standard TranscriptionData format.

        The whisper-timestamped library returns results in this format:
        {
            "text": "Full transcription text",
            "segments": [
                {
                    "id": 0,
                    "text": "Segment text",
                    "start": 0.0,
                    "end": 2.5,
                    "words": [
                        {"text": "word", "start": 0.0, "end": 0.5, "confidence": 0.95},
                        ...
                    ]
                },
                ...
            ],
            "language": "en"
        }

        Args:
            raw_data: Raw output from whisper_timestamped.transcribe_timestamped()

        Returns:
            TranscriptionData with segments, words, and metadata
        """
        segments = []
        all_words = []

        for seg in raw_data.get("segments", []):
            segment_words = []

            for word_data in seg.get("words", []):
                word = Word(
                    id=WordUtils.generate_id(),
                    text=word_data.get("text", "").strip(),
                    start_time=word_data.get("start", 0.0),
                    end_time=word_data.get("end", 0.0),
                    confidence=word_data.get("confidence"),
                )
                segment_words.append(word)
                all_words.append(word)

            # Create segment with its words
            segment = LyricsSegment(
                id=WordUtils.generate_id(),
                text=seg.get("text", "").strip(),
                words=segment_words,
                start_time=seg.get("start", 0.0),
                end_time=seg.get("end", 0.0),
            )
            segments.append(segment)

        return TranscriptionData(
            segments=segments,
            words=all_words,
            text=raw_data.get("text", "").strip(),
            source=self.get_name(),
            metadata={
                "model_size": self.config.model_size,
                "detected_language": raw_data.get("language", "unknown"),
                "device": self._get_device(),
            },
        )
