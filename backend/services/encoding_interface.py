"""
Encoding Interface.

Defines the abstract interface for video encoding backends, allowing
the video worker orchestrator to use either GCE or local encoding
interchangeably.

This follows the Strategy pattern - different encoding implementations
can be swapped without changing the orchestration logic.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class EncodingInput:
    """
    Input configuration for video encoding.

    Contains all the paths and metadata needed to encode a karaoke video.
    """
    # Required input files
    title_video_path: str  # Title card video (MOV)
    karaoke_video_path: str  # Main karaoke video with vocals (MOV/MKV)
    instrumental_audio_path: str  # Instrumental audio track (FLAC)

    # Optional input files
    end_video_path: Optional[str] = None  # End credits video (MOV)

    # Metadata for output naming
    artist: str = ""
    title: str = ""
    brand_code: Optional[str] = None

    # Output directory
    output_dir: str = ""

    # Instrumental selection (clean, with_backing, or custom)
    instrumental_selection: str = "clean"

    # Additional options
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EncodingOutput:
    """
    Output from video encoding.

    Contains paths to all generated video files and status information.
    """
    success: bool
    error_message: Optional[str] = None

    # Output file paths (relative to output_dir or absolute)
    karaoke_mp4_path: Optional[str] = None  # Karaoke video with instrumental audio
    with_vocals_mp4_path: Optional[str] = None  # With vocals MP4 version
    lossless_4k_mp4_path: Optional[str] = None  # Final lossless 4K MP4
    lossy_4k_mp4_path: Optional[str] = None  # Final lossy 4K MP4
    lossless_mkv_path: Optional[str] = None  # MKV with FLAC (for YouTube)
    lossy_720p_mp4_path: Optional[str] = None  # 720p web version

    # All output files as a dict for convenience
    output_files: Dict[str, str] = field(default_factory=dict)

    # Encoding metadata
    encoding_time_seconds: Optional[float] = None
    encoding_backend: Optional[str] = None  # "gce" or "local"


class EncodingBackend(ABC):
    """
    Abstract base class for video encoding backends.

    Implementations:
    - GCEEncodingBackend: Cloud-based encoding using Google Compute Engine
    - LocalEncodingBackend: Local FFmpeg-based encoding
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of this encoding backend."""
        pass

    @abstractmethod
    async def encode(self, input_config: EncodingInput) -> EncodingOutput:
        """
        Encode video files according to the input configuration.

        This method should:
        1. Create karaoke video with instrumental audio
        2. Convert with-vocals video to MP4 if needed
        3. Encode lossless 4K MP4 (concatenating title + karaoke + end)
        4. Encode lossy 4K MP4 with AAC audio
        5. Create MKV with FLAC audio for YouTube
        6. Encode 720p version for web

        Args:
            input_config: EncodingInput with all required paths and options

        Returns:
            EncodingOutput with paths to generated files and status
        """
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """
        Check if this encoding backend is available and configured.

        Returns:
            True if the backend can be used, False otherwise
        """
        pass

    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """
        Get the current status of the encoding backend.

        Returns:
            Dict with status information (availability, queue length, etc.)
        """
        pass


class LocalEncodingBackend(EncodingBackend):
    """
    Local FFmpeg-based encoding backend.

    Wraps the LocalEncodingService to implement the EncodingBackend interface.
    Uses asyncio.to_thread() to run synchronous FFmpeg operations asynchronously.
    """

    def __init__(
        self,
        dry_run: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the local encoding backend.

        Args:
            dry_run: If True, log operations without executing
            logger: Optional logger instance
        """
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger(__name__)
        self._service = None

    @property
    def name(self) -> str:
        return "local"

    def _get_service(self):
        """Lazy-load the local encoding service."""
        if self._service is None:
            from backend.services.local_encoding_service import LocalEncodingService
            self._service = LocalEncodingService(
                dry_run=self.dry_run,
                logger=self.logger
            )
        return self._service

    async def encode(self, input_config: EncodingInput) -> EncodingOutput:
        """
        Encode video using local FFmpeg.

        Args:
            input_config: Encoding input configuration

        Returns:
            EncodingOutput with results
        """
        import asyncio
        import time
        from backend.services.local_encoding_service import EncodingConfig

        start_time = time.time()

        # Build output file paths
        base_name = f"{input_config.artist} - {input_config.title}"
        output_dir = input_config.output_dir or "."

        import os
        config = EncodingConfig(
            title_video=input_config.title_video_path,
            karaoke_video=input_config.karaoke_video_path,
            instrumental_audio=input_config.instrumental_audio_path,
            end_video=input_config.end_video_path,
            output_karaoke_mp4=os.path.join(output_dir, f"{base_name} (Karaoke).mp4"),
            output_with_vocals_mp4=os.path.join(output_dir, f"{base_name} (With Vocals).mp4"),
            output_lossless_4k_mp4=os.path.join(output_dir, f"{base_name} (Final Karaoke Lossless 4k).mp4"),
            output_lossy_4k_mp4=os.path.join(output_dir, f"{base_name} (Final Karaoke Lossy 4k).mp4"),
            output_lossless_mkv=os.path.join(output_dir, f"{base_name} (Final Karaoke Lossless 4k).mkv"),
            output_720p_mp4=os.path.join(output_dir, f"{base_name} (Final Karaoke Lossy 720p).mp4"),
            countdown_padding_seconds=input_config.options.get("countdown_padding_seconds"),
        )

        # Run encoding in thread pool to avoid blocking
        service = self._get_service()
        result = await asyncio.to_thread(service.encode_all_formats, config)

        encoding_time = time.time() - start_time

        if result.success:
            return EncodingOutput(
                success=True,
                karaoke_mp4_path=config.output_karaoke_mp4,
                with_vocals_mp4_path=config.output_with_vocals_mp4,
                lossless_4k_mp4_path=config.output_lossless_4k_mp4,
                lossy_4k_mp4_path=config.output_lossy_4k_mp4,
                lossless_mkv_path=config.output_lossless_mkv,
                lossy_720p_mp4_path=config.output_720p_mp4,
                output_files=result.output_files,
                encoding_time_seconds=encoding_time,
                encoding_backend=self.name
            )
        else:
            return EncodingOutput(
                success=False,
                error_message=result.error,
                output_files=result.output_files,
                encoding_time_seconds=encoding_time,
                encoding_backend=self.name
            )

    async def is_available(self) -> bool:
        """Check if local encoding is available (FFmpeg installed)."""
        import subprocess
        import asyncio

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-version"],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get local encoding status."""
        available = await self.is_available()
        service = self._get_service()

        return {
            "backend": self.name,
            "available": available,
            "hwaccel_available": service.hwaccel_available if available else False,
            "video_encoder": service.video_encoder if available else None,
        }


class GCEEncodingBackend(EncodingBackend):
    """
    GCE-based encoding backend.

    Wraps the existing EncodingService to implement the EncodingBackend interface.
    Submits jobs to a remote GCE worker for high-performance encoding.
    """

    def __init__(
        self,
        dry_run: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the GCE encoding backend.

        Args:
            dry_run: Ignored for GCE backend (remote service doesn't support dry run)
            logger: Optional logger instance
        """
        self.dry_run = dry_run  # Stored but not used (GCE is remote)
        self.logger = logger or logging.getLogger(__name__)
        self._service = None

    @property
    def name(self) -> str:
        return "gce"

    def _get_service(self):
        """Lazy-load the GCE encoding service."""
        if self._service is None:
            from backend.services.encoding_service import get_encoding_service
            self._service = get_encoding_service()
        return self._service

    async def encode(self, input_config: EncodingInput) -> EncodingOutput:
        """
        Encode video using GCE worker.

        Note: GCE encoding requires files to be in GCS, so this method
        expects input_config.options to contain 'input_gcs_path' and
        'output_gcs_path'.

        Args:
            input_config: Encoding input configuration

        Returns:
            EncodingOutput with results
        """
        import time

        start_time = time.time()
        service = self._get_service()

        # GCE requires GCS paths
        input_gcs_path = input_config.options.get("input_gcs_path")
        output_gcs_path = input_config.options.get("output_gcs_path")
        job_id = input_config.options.get("job_id", input_config.brand_code or "unknown")

        if not input_gcs_path or not output_gcs_path:
            return EncodingOutput(
                success=False,
                error_message="GCE encoding requires input_gcs_path and output_gcs_path in options",
                encoding_backend=self.name
            )

        try:
            # Build encoding config
            encoding_config = {
                "formats": ["mp4_4k_lossless", "mp4_4k_lossy", "mkv_4k", "mp4_720p"],
                "artist": input_config.artist,
                "title": input_config.title,
                "instrumental_selection": input_config.instrumental_selection,
            }

            # Add countdown padding if present (for audio sync with countdown-padded vocals)
            countdown_padding = input_config.options.get("countdown_padding_seconds")
            if countdown_padding:
                encoding_config["countdown_padding_seconds"] = countdown_padding
                self.logger.info(f"GCE encoding with countdown_padding_seconds={countdown_padding}")

            # Submit and wait for completion
            result = await service.encode_videos(
                job_id=job_id,
                input_gcs_path=input_gcs_path,
                output_gcs_path=output_gcs_path,
                encoding_config=encoding_config,
            )

            encoding_time = time.time() - start_time

            # Extract output file paths from result
            # Handle case where GCE worker returns a list or unexpected format
            if isinstance(result, list):
                # If result is a list, try to find the output_files in the first dict
                self.logger.warning(f"GCE returned list instead of dict: {result}")
                result = result[0] if result and isinstance(result[0], dict) else {}
            if not isinstance(result, dict):
                self.logger.error(f"Unexpected GCE result type: {type(result)}")
                result = {}
            raw_output_files = result.get("output_files", {})

            # Convert output_files from list of paths to dict
            # GCE worker returns list like: ["path/Artist - Title (Final Karaoke Lossless 4k).mp4", ...]
            # We need dict like: {"mp4_4k_lossless": "path/...", "mp4_720p": "path/..."}
            if isinstance(raw_output_files, list):
                output_files = {}
                for path in raw_output_files:
                    if not isinstance(path, str):
                        continue
                    filename = path.split("/")[-1] if "/" in path else path
                    filename_lower = filename.lower()
                    # Map filename patterns to output format keys
                    # Files are named like "Artist - Title (Final Karaoke Lossless 4k).mp4"
                    if "lossless 4k" in filename_lower:
                        if filename.endswith(".mkv"):
                            output_files["mkv_4k"] = path
                        else:
                            output_files["mp4_4k_lossless"] = path
                    elif "lossy 4k" in filename_lower:
                        output_files["mp4_4k_lossy"] = path
                    elif "720p" in filename_lower:
                        output_files["mp4_720p"] = path
                self.logger.info(f"Converted output_files list to dict: {output_files}")
            else:
                output_files = raw_output_files if isinstance(raw_output_files, dict) else {}

            return EncodingOutput(
                success=True,
                lossless_4k_mp4_path=output_files.get("mp4_4k_lossless"),
                lossy_4k_mp4_path=output_files.get("mp4_4k_lossy"),
                lossless_mkv_path=output_files.get("mkv_4k"),
                lossy_720p_mp4_path=output_files.get("mp4_720p"),
                output_files=output_files,
                encoding_time_seconds=encoding_time,
                encoding_backend=self.name
            )

        except Exception as e:
            encoding_time = time.time() - start_time
            self.logger.error(f"GCE encoding failed: {e}")
            return EncodingOutput(
                success=False,
                error_message=str(e),
                encoding_time_seconds=encoding_time,
                encoding_backend=self.name
            )

    async def is_available(self) -> bool:
        """Check if GCE encoding is available and configured."""
        service = self._get_service()
        return service.is_enabled

    async def get_status(self) -> Dict[str, Any]:
        """Get GCE encoding status."""
        service = self._get_service()

        status = {
            "backend": self.name,
            "available": service.is_enabled,
            "configured": service.is_configured,
        }

        if service.is_configured:
            try:
                health = await service.health_check()
                status["health"] = health
            except Exception as e:
                status["health_error"] = str(e)

        return status


# Factory function to get an encoding backend
def get_encoding_backend(
    backend_type: str = "auto",
    **kwargs
) -> EncodingBackend:
    """
    Get an encoding backend instance.

    Args:
        backend_type: Type of backend - "local", "gce", or "auto"
        **kwargs: Additional arguments for the backend

    Returns:
        EncodingBackend instance

    Raises:
        ValueError: If backend_type is unknown
    """
    if backend_type == "local":
        return LocalEncodingBackend(**kwargs)
    elif backend_type == "gce":
        return GCEEncodingBackend(**kwargs)
    elif backend_type == "auto":
        # Check if GCE is available, otherwise use local
        gce_backend = GCEEncodingBackend(**kwargs)
        if gce_backend._get_service().is_enabled:
            return gce_backend
        return LocalEncodingBackend(**kwargs)
    else:
        raise ValueError(f"Unknown encoding backend type: {backend_type}")
