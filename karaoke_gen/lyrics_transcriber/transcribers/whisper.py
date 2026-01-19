#! /usr/bin/env python3
from dataclasses import dataclass
import os
import json
import requests
import hashlib
import tempfile
import time
from typing import Optional, Dict, Any, Protocol, Union
from pathlib import Path
from pydub import AudioSegment
from karaoke_gen.lyrics_transcriber.types import TranscriptionData, LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.transcribers.base_transcriber import BaseTranscriber, TranscriptionError
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils


@dataclass
class WhisperConfig:
    """Configuration for Whisper transcription service."""

    runpod_api_key: Optional[str] = None
    endpoint_id: Optional[str] = None
    dropbox_app_key: Optional[str] = None
    dropbox_app_secret: Optional[str] = None
    dropbox_refresh_token: Optional[str] = None
    timeout_minutes: int = 10


class FileStorageProtocol(Protocol):
    """Protocol for file storage operations."""

    def file_exists(self, path: str) -> bool: ...  # pragma: no cover
    def upload_with_retry(self, file: Any, path: str) -> None: ...  # pragma: no cover
    def create_or_get_shared_link(self, path: str) -> str: ...  # pragma: no cover


class RunPodWhisperAPI:
    """Handles interactions with RunPod API."""

    def __init__(self, config: WhisperConfig, logger):
        self.config = config
        self.logger = logger
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate API configuration."""
        if not self.config.runpod_api_key or not self.config.endpoint_id:
            raise ValueError("RunPod API key and endpoint ID must be provided")

    def submit_job(self, audio_url: str) -> str:
        """Submit transcription job and return job ID."""
        run_url = f"https://api.runpod.ai/v2/{self.config.endpoint_id}/run"
        headers = {"Authorization": f"Bearer {self.config.runpod_api_key}"}

        payload = {
            "input": {
                "audio": audio_url,
                "word_timestamps": True,
                "model": "medium",
                "temperature": 0.2,
                "best_of": 5,
                "compression_ratio_threshold": 2.8,
                "no_speech_threshold": 1,
                "condition_on_previous_text": True,
                "enable_vad": True,
            }
        }

        self.logger.info("Submitting transcription job...")
        response = requests.post(run_url, json=payload, headers=headers)

        self.logger.debug(f"Response status code: {response.status_code}")

        # Try to parse and log the JSON response
        try:
            response_json = response.json()
            self.logger.debug(f"Response content: {json.dumps(response_json, indent=2)}")
        except ValueError:
            self.logger.debug(f"Raw response content: {response.text}")
            # Re-raise if we can't parse the response at all
            raise TranscriptionError(f"Invalid JSON response: {response.text}")

        response.raise_for_status()
        return response_json["id"]

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get job status and results."""
        status_url = f"https://api.runpod.ai/v2/{self.config.endpoint_id}/status/{job_id}"
        headers = {"Authorization": f"Bearer {self.config.runpod_api_key}"}

        response = requests.get(status_url, headers=headers)
        response.raise_for_status()
        return response.json()

    def cancel_job(self, job_id: str) -> None:
        """Cancel a running job."""
        cancel_url = f"https://api.runpod.ai/v2/{self.config.endpoint_id}/cancel/{job_id}"
        headers = {"Authorization": f"Bearer {self.config.runpod_api_key}"}

        try:
            response = requests.post(cancel_url, headers=headers)
            response.raise_for_status()
        except Exception as e:
            self.logger.warning(f"Failed to cancel job {job_id}: {e}")

    def wait_for_job_result(self, job_id: str) -> Dict[str, Any]:
        """Poll for job completion and return results."""
        self.logger.info(f"Getting job result for job {job_id}")

        start_time = time.time()
        last_status_log = start_time
        timeout_seconds = self.config.timeout_minutes * 60

        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time

            if elapsed_time > timeout_seconds:
                self.cancel_job(job_id)
                raise TranscriptionError(f"Transcription timed out after {self.config.timeout_minutes} minutes")

            # Log status periodically
            if current_time - last_status_log >= 60:
                self.logger.info(f"Still waiting for transcription... Elapsed time: {int(elapsed_time/60)} minutes")
                last_status_log = current_time

            status_data = self.get_job_status(job_id)

            if status_data["status"] == "COMPLETED":
                return status_data["output"]
            elif status_data["status"] == "FAILED":
                error_msg = status_data.get("error", "Unknown error")
                self.logger.error(f"Job failed with error: {error_msg}")
                raise TranscriptionError(f"Transcription failed: {error_msg}")

            time.sleep(5)


class AudioProcessor:
    """Handles audio file processing."""

    def __init__(self, logger):
        self.logger = logger

    def get_file_md5(self, filepath: str) -> str:
        """Calculate MD5 hash of a file."""
        md5_hash = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()

    def convert_to_flac(self, filepath: str) -> str:
        """Convert WAV to FLAC if needed for faster upload."""
        if not filepath.lower().endswith(".wav"):
            return filepath

        self.logger.info("Converting WAV to FLAC for faster upload...")
        audio = AudioSegment.from_wav(filepath)

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as temp_flac:
            flac_path = temp_flac.name
            audio.export(flac_path, format="flac")

        return flac_path


class WhisperTranscriber(BaseTranscriber):
    """Transcription service using Whisper API via RunPod."""

    def __init__(
        self,
        cache_dir: Union[str, Path],
        config: Optional[WhisperConfig] = None,
        logger: Optional[Any] = None,
        runpod_client: Optional[RunPodWhisperAPI] = None,
        storage_client: Optional[FileStorageProtocol] = None,
        audio_processor: Optional[AudioProcessor] = None,
    ):
        """Initialize Whisper transcriber."""
        super().__init__(cache_dir=cache_dir, logger=logger)

        # Initialize configuration
        self.config = config or WhisperConfig(
            runpod_api_key=os.getenv("RUNPOD_API_KEY"),
            endpoint_id=os.getenv("WHISPER_RUNPOD_ID"),
            dropbox_app_key=os.getenv("WHISPER_DROPBOX_APP_KEY"),
            dropbox_app_secret=os.getenv("WHISPER_DROPBOX_APP_SECRET"),
            dropbox_refresh_token=os.getenv("WHISPER_DROPBOX_REFRESH_TOKEN"),
        )

        # Initialize components (with dependency injection)
        self.runpod = runpod_client or RunPodWhisperAPI(self.config, self.logger)
        self.storage = storage_client or self._initialize_storage()
        self.audio_processor = audio_processor or AudioProcessor(self.logger)

    def _initialize_storage(self) -> FileStorageProtocol:
        """Initialize storage client."""
        from karaoke_gen.lyrics_transcriber.storage.dropbox import DropboxHandler, DropboxConfig

        # Create config using os.getenv directly
        config = DropboxConfig(
            app_key=os.getenv("WHISPER_DROPBOX_APP_KEY"),
            app_secret=os.getenv("WHISPER_DROPBOX_APP_SECRET"),
            refresh_token=os.getenv("WHISPER_DROPBOX_REFRESH_TOKEN"),
        )

        # Log the actual config values being used
        self.logger.debug("Initializing DropboxHandler with config")
        return DropboxHandler(config=config)

    def get_name(self) -> str:
        return "Whisper"

    def _perform_transcription(self, audio_filepath: str) -> TranscriptionData:
        """Actually perform the whisper transcription using Whisper API."""
        self.logger.info(f"Starting transcription for {audio_filepath}")

        # Start transcription and get results
        job_id = self.start_transcription(audio_filepath)
        result = self.get_transcription_result(job_id)
        return result

    def start_transcription(self, audio_filepath: str) -> str:
        """Prepare audio and start whisper transcription job."""
        audio_url, temp_filepath = self._prepare_audio_url(audio_filepath)
        try:
            return self.runpod.submit_job(audio_url)
        except Exception as e:
            if temp_filepath:
                self._cleanup_temporary_files(temp_filepath)
            raise TranscriptionError(f"Failed to submit job: {str(e)}") from e

    def _prepare_audio_url(self, audio_filepath: str) -> tuple[str, Optional[str]]:
        """Process audio file and return URL for API and path to any temporary files."""
        if audio_filepath.startswith(("http://", "https://")):
            return audio_filepath, None

        file_hash = self.audio_processor.get_file_md5(audio_filepath)
        temp_flac_filepath = self.audio_processor.convert_to_flac(audio_filepath)

        # Upload and get URL
        dropbox_path = f"/transcription_temp/{file_hash}{os.path.splitext(temp_flac_filepath)[1]}"
        url = self._upload_and_get_link(temp_flac_filepath, dropbox_path)
        return url, temp_flac_filepath

    def get_transcription_result(self, job_id: str) -> Dict[str, Any]:
        """Poll for whisper job completion and return raw results."""
        raw_data = self.runpod.wait_for_job_result(job_id)

        # Add job_id to raw data for later use
        raw_data["job_id"] = job_id

        return raw_data

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> TranscriptionData:
        """Convert Whisper API response to standard format."""
        self._validate_response(raw_data)

        job_id = raw_data.get("job_id")
        all_words = []

        # First collect all words from word_timestamps
        word_list = [
            Word(
                id=WordUtils.generate_id(),  # Generate unique ID for each word
                text=word["word"].strip(),
                start_time=word["start"],
                end_time=word["end"],
                confidence=word.get("probability"),  # Only set if provided
            )
            for word in raw_data.get("word_timestamps", [])
        ]
        all_words.extend(word_list)

        # Then create segments, using the words that fall within each segment's time range
        segments = []
        for seg in raw_data["segments"]:
            segment_words = [word for word in word_list if seg["start"] <= word.start_time < seg["end"]]
            segments.append(
                LyricsSegment(
                    id=WordUtils.generate_id(),  # Generate unique ID for each segment
                    text=seg["text"].strip(),
                    words=segment_words,
                    start_time=seg["start"],
                    end_time=seg["end"],
                )
            )

        return TranscriptionData(
            segments=segments,
            words=all_words,
            text=raw_data["transcription"],
            source=self.get_name(),
            metadata={
                "language": raw_data.get("detected_language", "en"),
                "model": raw_data.get("model"),
                "job_id": job_id,
            },
        )

    def _upload_and_get_link(self, filepath: str, dropbox_path: str) -> str:
        """Upload file to storage and return shared link."""
        if not self.storage.file_exists(dropbox_path):
            self.logger.info("Uploading file to storage...")
            with open(filepath, "rb") as f:
                self.storage.upload_with_retry(f, dropbox_path)
        else:
            self.logger.info("File already exists in storage, skipping upload...")

        audio_url = self.storage.create_or_get_shared_link(dropbox_path)
        self.logger.debug(f"Using shared link: {audio_url}")
        return audio_url

    def _cleanup_temporary_files(self, *filepaths: Optional[str]) -> None:
        """Clean up any temporary files that were created during transcription."""
        for filepath in filepaths:
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    self.logger.debug(f"Cleaned up temporary file: {filepath}")
                except Exception as e:
                    self.logger.warning(f"Failed to clean up temporary file {filepath}: {e}")

    def _validate_response(self, raw_data: Dict[str, Any]) -> None:
        """Validate the response contains required fields."""
        if "segments" not in raw_data:
            raise TranscriptionError("Response missing required 'segments' field")
        if "transcription" not in raw_data:
            raise TranscriptionError("Response missing required 'transcription' field")
