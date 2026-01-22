from dataclasses import dataclass
import requests
import time
import os
import tempfile
from typing import Dict, Optional, Any, Union, Tuple
from pathlib import Path
from pydub import AudioSegment
from karaoke_gen.lyrics_transcriber.types import TranscriptionData, LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.transcribers.base_transcriber import BaseTranscriber, TranscriptionError
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils

# Formats natively supported by AudioShake (upload directly)
# Source: https://developer.audioshake.ai/supported-format
AUDIOSHAKE_SUPPORTED_AUDIO = {'.wav', '.mp3', '.aac', '.flac', '.aiff', '.aif', '.mp4a'}
AUDIOSHAKE_SUPPORTED_VIDEO = {'.mp4', '.mov'}  # AudioShake extracts audio from these
AUDIOSHAKE_SUPPORTED = AUDIOSHAKE_SUPPORTED_AUDIO | AUDIOSHAKE_SUPPORTED_VIDEO

# Uncompressed formats that benefit from FLAC conversion (smaller upload)
UNCOMPRESSED_FORMATS = {'.wav', '.aiff', '.aif', '.pcm'}


class AudioUploadOptimizer:
    """Optimizes audio files for upload by converting uncompressed formats to FLAC."""

    def __init__(self, logger):
        self.logger = logger

    def prepare_for_upload(self, filepath: str) -> Tuple[str, Optional[str]]:
        """
        Prepare audio file for optimal upload to AudioShake.

        Returns:
            Tuple of (filepath_to_upload, temp_file_to_cleanup)
            - If no conversion needed, returns (original_filepath, None)
            - If converted, returns (temp_flac_filepath, temp_flac_filepath)
        """
        ext = os.path.splitext(filepath)[1].lower()

        # Uncompressed formats: convert to FLAC for smaller upload
        if ext in UNCOMPRESSED_FORMATS:
            self.logger.info(f"Converting uncompressed format ({ext}) to FLAC for efficient upload")
            return self._convert_to_flac(filepath)

        # AudioShake-supported formats: upload directly
        if ext in AUDIOSHAKE_SUPPORTED:
            self.logger.info(f"Uploading AudioShake-supported format ({ext}) directly")
            return filepath, None

        # Unsupported formats: convert to FLAC for compatibility
        self.logger.info(f"Converting unsupported format ({ext}) to FLAC for AudioShake compatibility")
        return self._convert_to_flac(filepath)

    def _convert_to_flac(self, filepath: str) -> Tuple[str, str]:
        """Convert audio file to FLAC format."""
        ext = os.path.splitext(filepath)[1].lower()

        # Load audio based on format (pydub uses ffmpeg under the hood)
        if ext == '.wav':
            audio = AudioSegment.from_wav(filepath)
        elif ext in {'.aiff', '.aif'}:
            audio = AudioSegment.from_file(filepath, format='aiff')
        elif ext == '.mp3':
            audio = AudioSegment.from_mp3(filepath)
        elif ext in {'.ogg', '.oga'}:
            audio = AudioSegment.from_ogg(filepath)
        elif ext == '.flac':
            audio = AudioSegment.from_file(filepath, format='flac')
        else:
            # Generic loading for container formats (webm, mkv, m4a, etc.)
            # pydub/ffmpeg will extract audio automatically
            audio = AudioSegment.from_file(filepath)

        # Create temp file for FLAC output
        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as temp_flac:
            flac_path = temp_flac.name
            audio.export(flac_path, format="flac")

        # Log size info
        original_size = os.path.getsize(filepath)
        flac_size = os.path.getsize(flac_path)
        if flac_size < original_size:
            reduction_pct = (1 - flac_size / original_size) * 100
            self.logger.info(f"Converted to FLAC: {original_size / 1024 / 1024:.1f}MB → {flac_size / 1024 / 1024:.1f}MB ({reduction_pct:.0f}% smaller)")
        else:
            self.logger.info(f"Converted to FLAC: {original_size / 1024 / 1024:.1f}MB → {flac_size / 1024 / 1024:.1f}MB")

        return flac_path, flac_path

    def cleanup(self, temp_filepath: Optional[str]) -> None:
        """Clean up temporary file if it exists."""
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.unlink(temp_filepath)
                self.logger.debug(f"Cleaned up temporary file: {temp_filepath}")
            except OSError as e:
                self.logger.warning(f"Failed to clean up temporary file {temp_filepath}: {e}")


@dataclass
class AudioShakeConfig:
    """Configuration for AudioShake transcription service."""

    api_token: Optional[str] = None
    base_url: str = "https://api.audioshake.ai"
    output_prefix: Optional[str] = None
    timeout_minutes: int = 20  # Added timeout configuration


class AudioShakeAPI:
    """Handles direct API interactions with AudioShake."""

    def __init__(self, config: AudioShakeConfig, logger):
        self.config = config
        self.logger = logger

    def _validate_config(self) -> None:
        """Validate API configuration."""
        if not self.config.api_token:
            raise ValueError("AudioShake API token must be provided")

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        self._validate_config()  # Validate before making any API calls
        return {"x-api-key": self.config.api_token, "Content-Type": "application/json"}

    def upload_file(self, filepath: str) -> str:
        """Upload audio file and return file URL."""
        self.logger.info(f"Uploading {filepath} to AudioShake")
        self._validate_config()  # Validate before making API call

        url = f"{self.config.base_url}/upload/"
        with open(filepath, "rb") as file:
            files = {"file": (os.path.basename(filepath), file)}
            response = requests.post(url, headers={"x-api-key": self.config.api_token}, files=files)

        self.logger.debug(f"Upload response: {response.status_code} - {response.text}")
        response.raise_for_status()
        return response.json()["link"]

    def create_task(self, file_url: str) -> str:
        """Create transcription task and return task ID."""
        self.logger.info(f"Creating task for file {file_url}")

        url = f"{self.config.base_url}/tasks"
        data = {
            "url": file_url,
            "targets": [
                {
                    "model": "alignment",
                    "formats": ["json"],
                    "language": "en"
                }
            ],
        }
        response = requests.post(url, headers=self._get_headers(), json=data)
        response.raise_for_status()
        return response.json()["id"]

    def wait_for_task_result(self, task_id: str) -> Dict[str, Any]:
        """Poll for task completion and return results."""
        self.logger.info(f"Getting task result for task {task_id}")

        # Use the list endpoint which has fresh data, not the individual task endpoint which caches
        url = f"{self.config.base_url}/tasks"
        start_time = time.time()
        last_status_log = start_time
        timeout_seconds = self.config.timeout_minutes * 60
        
        # Add initial retry logic for when task is not found yet
        initial_retry_count = 0
        max_initial_retries = 5
        initial_retry_delay = 2  # seconds

        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time

            # Check for timeout
            if elapsed_time > timeout_seconds:
                raise TranscriptionError(f"Transcription timed out after {self.config.timeout_minutes} minutes")

            # Log status every minute
            if current_time - last_status_log >= 60:
                self.logger.info(f"Still waiting for transcription... " f"Elapsed time: {int(elapsed_time/60)} minutes")
                last_status_log = current_time

            try:
                response = requests.get(url, headers=self._get_headers())
                response.raise_for_status()
                tasks_list = response.json()
                
                # Find our specific task in the list
                task_data = None
                for task in tasks_list:
                    if task.get("id") == task_id:
                        task_data = task
                        break
                
                if not task_data:
                    # Task not found in list yet
                    if initial_retry_count < max_initial_retries:
                        initial_retry_count += 1
                        self.logger.info(f"Task not found in list yet (attempt {initial_retry_count}/{max_initial_retries}), retrying in {initial_retry_delay} seconds...")
                        time.sleep(initial_retry_delay)
                        continue
                    else:
                        raise TranscriptionError(f"Task {task_id} not found in task list after {max_initial_retries} retries")
                
                # Log the full response for debugging
                self.logger.debug(f"Task status response: {task_data}")

                # Check status of targets (not the task itself)
                targets = task_data.get("targets", [])
                if not targets:
                    raise TranscriptionError("No targets found in task response")
                
                # Check if all targets are completed or if any failed
                all_completed = True
                for target in targets:
                    target_status = target.get("status")
                    target_model = target.get("model")
                    self.logger.debug(f"Target {target_model} status: {target_status}")
                    
                    if target_status == "failed":
                        error_msg = target.get("error", "Unknown error")
                        raise TranscriptionError(f"Target {target_model} failed: {error_msg}")
                    elif target_status != "completed":
                        all_completed = False
                
                if all_completed:
                    self.logger.info("All targets completed successfully")
                    return task_data

                # Reset retry count on successful response
                initial_retry_count = 0
                
            except requests.exceptions.HTTPError as e:
                raise

            time.sleep(30)  # Wait before next poll


class AudioShakeTranscriber(BaseTranscriber):
    """Transcription service using AudioShake's API."""

    def __init__(
        self,
        cache_dir: Union[str, Path],
        config: Optional[AudioShakeConfig] = None,
        logger: Optional[Any] = None,
        api_client: Optional[AudioShakeAPI] = None,
        upload_optimizer: Optional[AudioUploadOptimizer] = None,
    ):
        """Initialize AudioShake transcriber."""
        super().__init__(cache_dir=cache_dir, logger=logger)
        self.config = config or AudioShakeConfig(api_token=os.getenv("AUDIOSHAKE_API_TOKEN"))
        self.api = api_client or AudioShakeAPI(self.config, self.logger)
        self.upload_optimizer = upload_optimizer or AudioUploadOptimizer(self.logger)

    def get_name(self) -> str:
        return "AudioShake"

    def _perform_transcription(self, audio_filepath: str) -> TranscriptionData:
        """Actually perform the transcription using AudioShake API."""
        self.logger.debug(f"Entering _perform_transcription() for {audio_filepath}")
        self.logger.info(f"Starting transcription for {audio_filepath}")

        try:
            # Start task and get results
            self.logger.debug("Calling start_transcription()")
            task_id = self.start_transcription(audio_filepath)
            self.logger.debug(f"Got task_id: {task_id}")

            self.logger.debug("Calling get_transcription_result()")
            result = self.get_transcription_result(task_id)
            self.logger.debug("Got transcription result")

            return result
        except Exception as e:
            self.logger.error(f"Error in _perform_transcription: {str(e)}")
            raise

    def start_transcription(self, audio_filepath: str) -> str:
        """Starts the transcription task and returns the task ID."""
        self.logger.debug(f"Entering start_transcription() for {audio_filepath}")

        # Optimize file format for upload (convert WAV to FLAC, etc.)
        upload_filepath, temp_filepath = self.upload_optimizer.prepare_for_upload(audio_filepath)
        
        try:
            # Upload file and create task
            file_url = self.api.upload_file(upload_filepath)
            self.logger.debug(f"File uploaded successfully. File URL: {file_url}")

            task_id = self.api.create_task(file_url)
            self.logger.debug(f"Task created successfully. Task ID: {task_id}")

            return task_id
        finally:
            # Clean up any temporary file created during optimization
            self.upload_optimizer.cleanup(temp_filepath)

    def get_transcription_result(self, task_id: str) -> Dict[str, Any]:
        """Gets the raw results for a previously started task."""
        self.logger.debug(f"Entering get_transcription_result() for task ID: {task_id}")

        # Wait for task completion
        task_data = self.api.wait_for_task_result(task_id)
        self.logger.debug("Task completed. Getting results...")

        # Find the alignment target output
        alignment_target = None
        for target in task_data.get("targets", []):
            if target.get("model") == "alignment":
                alignment_target = target
                break
        
        if not alignment_target:
            raise TranscriptionError("Required output not found in task results")
        
        # Get the output file URL
        output = alignment_target.get("output", [])
        if not output:
            raise TranscriptionError("No output found in alignment target")
        
        output_url = output[0].get("link")
        if not output_url:
            raise TranscriptionError("Output link not found in alignment target")

        # Fetch transcription data
        response = requests.get(output_url)
        response.raise_for_status()

        # Return combined raw data
        raw_data = {"task_data": task_data, "transcription": response.json()}

        self.logger.debug("Raw results retrieved successfully")
        return raw_data

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> TranscriptionData:
        """Process raw Audioshake API response into standard format."""
        self.logger.debug(f"Processing result for task {raw_data['task_data']['id']}")

        transcription_data = raw_data["transcription"]
        task_data = raw_data["task_data"]

        segments = []
        all_words = []  # Collect all words across segments

        for line in transcription_data.get("lines", []):
            words = [
                Word(
                    id=WordUtils.generate_id(),  # Generate unique ID for each word
                    text=word["text"].strip(" "),
                    start_time=word.get("start", 0.0),
                    end_time=word.get("end", 0.0),
                )
                for word in line.get("words", [])
            ]
            all_words.extend(words)  # Add words to flat list

            segments.append(
                LyricsSegment(
                    id=WordUtils.generate_id(),  # Generate unique ID for each segment
                    text=line.get("text", " ".join(w.text for w in words)),
                    words=words,
                    start_time=min((w.start_time for w in words), default=0.0),
                    end_time=max((w.end_time for w in words), default=0.0),
                )
            )

        return TranscriptionData(
            text=transcription_data.get("text", ""),
            words=all_words,
            segments=segments,
            source=self.get_name(),
            metadata={
                "language": transcription_data.get("metadata", {}).get("language"),
                "task_id": task_data["id"],
                "duration": task_data.get("duration"),
            },
        )

    def get_output_filename(self, suffix: str) -> str:
        """Generate consistent filename with (Purpose) suffix pattern."""
        return f"{self.config.output_prefix}{suffix}"
