from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union
from pathlib import Path
import logging
import os
import json
import hashlib
from karaoke_gen.lyrics_transcriber.types import TranscriptionData


class TranscriptionError(Exception):
    """Base exception for transcription errors."""

    def __init__(self, message: str):
        super().__init__(message)


class BaseTranscriber(ABC):
    """Base class for all transcription services."""

    def __init__(self, cache_dir: Union[str, Path], logger: Optional[logging.Logger] = None):
        """
        Initialize transcriber with cache directory and logger.

        Args:
            cache_dir: Directory to store cache files. Must be provided.
            logger: Logger instance to use. If None, creates a new logger.
        """
        self.cache_dir = Path(cache_dir)
        self.logger = logger or logging.getLogger(__name__)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger.debug(f"Initialized {self.__class__.__name__} with cache dir: {self.cache_dir}")

    def _get_file_hash(self, filepath: str) -> str:
        """Calculate MD5 hash of a file."""
        self.logger.debug(f"Calculating hash for file: {filepath}")
        md5_hash = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5_hash.update(chunk)
        hash_result = md5_hash.hexdigest()
        self.logger.debug(f"File hash: {hash_result}")
        return hash_result

    def _get_cache_path(self, file_hash: str, suffix: str) -> str:
        """Get the cache file path for a given file hash."""
        cache_path = os.path.join(self.cache_dir, f"{self.get_name().lower()}_{file_hash}_{suffix}.json")
        self.logger.debug(f"Cache path: {cache_path}")
        return cache_path

    def _save_to_cache(self, cache_path: str, raw_data: Dict[str, Any]) -> None:
        """Save raw API response data to cache."""
        self.logger.debug(f"Saving JSON to cache: {cache_path}")
        with open(cache_path, "w") as f:
            json.dump(raw_data, f, indent=2)
        self.logger.debug("Cache save completed")

    def _load_from_cache(self, cache_path: str) -> Optional[Dict[str, Any]]:
        """Load raw API response data from cache if it exists."""
        self.logger.debug(f"Attempting to load from cache: {cache_path}")
        try:
            with open(cache_path, "r") as f:
                data = json.load(f)
                self.logger.debug("Raw API response loaded from cache")
                return data
        except FileNotFoundError:
            self.logger.debug("Cache file not found")
            return None
        except json.JSONDecodeError:
            self.logger.warning(f"Cache file {cache_path} is corrupted")
            return None

    def _save_and_convert_result(self, file_hash: str, raw_result: Dict[str, Any]) -> TranscriptionData:
        """Convert raw result to TranscriptionData, save to cache, and return."""
        converted_cache_path = self._get_cache_path(file_hash, "converted")
        converted_result = self._convert_result_format(raw_result)
        self._save_to_cache(converted_cache_path, converted_result.to_dict())
        return converted_result

    def transcribe(self, audio_filepath: str) -> TranscriptionData:
        """
        Transcribe an audio file, using cache if available.

        Args:
            audio_filepath: Path to the audio file to transcribe

        Returns:
            TranscriptionData containing segments, text, and metadata
        """
        self.logger.debug(f"Starting transcription for {audio_filepath}")

        try:
            self._validate_audio_file(audio_filepath)
            self.logger.debug("Audio file validation passed")

            # Check converted cache first
            file_hash = self._get_file_hash(audio_filepath)
            converted_cache_path = self._get_cache_path(file_hash, "converted")
            converted_data = self._load_from_cache(converted_cache_path)
            if converted_data:
                self.logger.info(f"Using cached converted data for {audio_filepath}")
                return TranscriptionData.from_dict(converted_data)

            # Check raw cache next
            raw_cache_path = self._get_cache_path(file_hash, "raw")
            raw_data = self._load_from_cache(raw_cache_path)
            if raw_data:
                self.logger.info(f"Using cached raw data for {audio_filepath}")
                converted_result = self._convert_result_format(raw_data)
                self._save_to_cache(converted_cache_path, converted_result.to_dict())
                return converted_result

            # If not in cache, perform transcription
            self.logger.info(f"No cache found, transcribing {audio_filepath}")
            raw_result = self._perform_transcription(audio_filepath)
            self.logger.debug("Transcription completed")

            # Save raw result to cache
            self._save_to_cache(raw_cache_path, raw_result)

            return self._save_and_convert_result(file_hash, raw_result)

        except Exception as e:
            self.logger.error(f"Error during transcription: {str(e)}")
            raise

    @abstractmethod
    def _perform_transcription(self, audio_filepath: str) -> TranscriptionData:
        """
        Actually perform the transcription (implemented by subclasses).

        Args:
            audio_filepath: Path to the audio file to transcribe

        Returns:
            TranscriptionData containing segments, text, and metadata
        """
        pass  # pragma: no cover

    @abstractmethod
    def get_name(self) -> str:
        """Return the name of this transcription service."""
        pass  # pragma: no cover

    def _validate_audio_file(self, audio_filepath: str) -> None:
        """Validate that the audio file exists and is accessible."""
        self.logger.debug(f"Validating audio file: {audio_filepath}")
        if not os.path.exists(audio_filepath):
            self.logger.error(f"Audio file not found: {audio_filepath}")
            raise FileNotFoundError(f"Audio file not found: {audio_filepath}")
        self.logger.debug("Audio file validation successful")

    @abstractmethod
    def _convert_result_format(self, raw_data: Dict[str, Any]) -> TranscriptionData:
        """Convert raw API response to TranscriptionData format."""
        pass  # pragma: no cover
