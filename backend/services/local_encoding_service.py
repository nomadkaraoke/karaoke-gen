"""
Local Encoding Service.

Provides local FFmpeg-based video encoding functionality, extracted from KaraokeFinalise
for use by both the cloud backend (video_worker as a fallback) and local CLI.

This service handles:
- Concatenating title, karaoke, and end videos
- Encoding to multiple output formats (4K lossless, 4K lossy, 720p, MKV)
- Hardware acceleration detection and fallback to software encoding
- Remuxing video with instrumental audio
"""

import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)


@dataclass
class EncodingConfig:
    """Configuration for video encoding."""
    title_video: str  # Path to title video
    karaoke_video: str  # Path to karaoke video (with vocals)
    instrumental_audio: str  # Path to instrumental audio
    end_video: Optional[str] = None  # Optional path to end credits video

    # Output paths
    output_karaoke_mp4: Optional[str] = None
    output_with_vocals_mp4: Optional[str] = None
    output_lossless_4k_mp4: Optional[str] = None
    output_lossy_4k_mp4: Optional[str] = None
    output_lossless_mkv: Optional[str] = None
    output_720p_mp4: Optional[str] = None

    # Audio synchronization
    countdown_padding_seconds: Optional[float] = None  # Pad instrumental to match countdown-padded vocals


@dataclass
class EncodingResult:
    """Result of video encoding operation."""
    success: bool
    output_files: Dict[str, str]
    error: Optional[str] = None


class LocalEncodingService:
    """
    Service for local FFmpeg-based video encoding.

    Supports hardware acceleration (NVENC) with automatic fallback
    to software encoding (libx264) when unavailable.
    """

    # MP4 flags for better compatibility
    MP4_FLAGS = "-movflags +faststart"

    def __init__(
        self,
        dry_run: bool = False,
        log_level: int = logging.INFO,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the local encoding service.

        Args:
            dry_run: If True, log commands without executing them
            log_level: Logging level (affects FFmpeg verbosity)
            logger: Optional logger instance
        """
        self.dry_run = dry_run
        self.log_level = log_level
        self.logger = logger or logging.getLogger(__name__)

        # Hardware acceleration settings (detected on first use)
        self._hwaccel_available: Optional[bool] = None
        self._video_encoder: Optional[str] = None
        self._scale_filter: Optional[str] = None
        self._hwaccel_decode_flags: Optional[str] = None
        self._aac_codec: Optional[str] = None

        # Build FFmpeg base command
        self._ffmpeg_base_command = self._build_ffmpeg_base_command()

    def _build_ffmpeg_base_command(self) -> str:
        """Build the FFmpeg base command with appropriate flags."""
        # Use bundled FFmpeg for frozen builds
        ffmpeg_path = os.path.join(sys._MEIPASS, "ffmpeg.exe") if getattr(sys, "frozen", False) else "ffmpeg"

        base_cmd = f"{ffmpeg_path} -hide_banner -nostats -y"

        if self.log_level == logging.DEBUG:
            base_cmd += " -loglevel verbose"
        else:
            base_cmd += " -loglevel fatal"

        return base_cmd

    def _detect_hardware_acceleration(self) -> Tuple[bool, str, str, str, str]:
        """
        Detect available hardware acceleration.

        Returns:
            Tuple of (hwaccel_available, video_encoder, scale_filter,
                      hwaccel_decode_flags, aac_codec)
        """
        self.logger.info("Detecting hardware acceleration capabilities...")

        # Try NVENC (NVIDIA)
        try:
            test_cmd = f"{self._ffmpeg_base_command} -hide_banner -loglevel error " \
                       f"-f lavfi -i testsrc=duration=1:size=320x240:rate=1 " \
                       f"-c:v h264_nvenc -f null -"
            subprocess.run(
                test_cmd, shell=True, check=True,
                capture_output=True, timeout=30
            )
            self.logger.info("NVIDIA NVENC hardware acceleration available")
            return (
                True,
                "h264_nvenc",
                "scale_cuda",
                "-hwaccel cuda -hwaccel_output_format cuda",
                "aac"
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # No hardware acceleration available
        self.logger.info("No hardware acceleration available, using software encoding")
        return (False, "libx264", "scale", "", "aac")

    @property
    def hwaccel_available(self) -> bool:
        """Check if hardware acceleration is available."""
        if self._hwaccel_available is None:
            self._detect_and_set_hwaccel()
        return self._hwaccel_available

    @property
    def video_encoder(self) -> str:
        """Get the video encoder to use."""
        if self._video_encoder is None:
            self._detect_and_set_hwaccel()
        return self._video_encoder

    @property
    def scale_filter(self) -> str:
        """Get the scale filter to use."""
        if self._scale_filter is None:
            self._detect_and_set_hwaccel()
        return self._scale_filter

    @property
    def hwaccel_decode_flags(self) -> str:
        """Get hardware acceleration decode flags."""
        if self._hwaccel_decode_flags is None:
            self._detect_and_set_hwaccel()
        return self._hwaccel_decode_flags

    @property
    def aac_codec(self) -> str:
        """Get the AAC codec to use."""
        if self._aac_codec is None:
            self._detect_and_set_hwaccel()
        return self._aac_codec

    def _detect_and_set_hwaccel(self) -> None:
        """Detect and set hardware acceleration settings."""
        (
            self._hwaccel_available,
            self._video_encoder,
            self._scale_filter,
            self._hwaccel_decode_flags,
            self._aac_codec
        ) = self._detect_hardware_acceleration()

    def _get_nvenc_quality_settings(self, preset: str = "medium") -> str:
        """Get NVENC quality settings for different presets."""
        if not self.hwaccel_available:
            return ""

        settings = {
            "lossless": "-preset p7 -tune hq -rc vbr -cq 0 -qmin 0 -qmax 0",
            "high": "-preset p7 -tune hq -rc vbr -cq 19 -b:v 0",
            "medium": "-preset p4 -tune hq -rc vbr -cq 23 -b:v 0",
            "fast": "-preset p2 -tune ll -rc vbr -cq 28 -b:v 0",
        }
        return settings.get(preset, settings["medium"])

    def _execute_command(
        self,
        command: str,
        description: str,
        timeout: int = 3600,  # 1 hour default
    ) -> bool:
        """
        Execute an FFmpeg command.

        Args:
            command: The FFmpeg command to execute
            description: Human-readable description of the operation
            timeout: Command timeout in seconds

        Returns:
            True if successful, False otherwise
        """
        self.logger.info(f"Executing: {description}")
        self.logger.debug(f"Command: {command}")

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would execute: {command}")
            return True

        try:
            result = subprocess.run(
                command,
                shell=True,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            self.logger.info(f"Completed: {description}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed: {description}")
            self.logger.error(f"Error: {e.stderr}")
            return False
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout: {description}")
            return False

    def _execute_command_with_fallback(
        self,
        gpu_command: str,
        cpu_command: str,
        description: str,
    ) -> bool:
        """
        Execute a command with GPU, falling back to CPU if it fails.

        Args:
            gpu_command: Hardware-accelerated command
            cpu_command: Software fallback command
            description: Human-readable description

        Returns:
            True if successful, False otherwise
        """
        if self.hwaccel_available:
            self.logger.info(f"Trying hardware-accelerated encoding for: {description}")
            if self._execute_command(gpu_command, description):
                return True
            self.logger.warning(f"Hardware encoding failed, falling back to software")

        return self._execute_command(cpu_command, description)

    def _pad_audio_file(self, input_audio: str, output_audio: str, padding_seconds: float) -> str:
        """Pad an audio file by prepending silence at the beginning.

        Uses FFmpeg adelay filter - same approach as KaraokeFinalise._pad_audio_file().
        This ensures the instrumental audio is synchronized with countdown-padded vocals.

        Args:
            input_audio: Path to input audio file
            output_audio: Path for the padded output file
            padding_seconds: Amount of silence to prepend (in seconds)

        Returns:
            Path to the output audio file
        """
        delay_ms = int(padding_seconds * 1000)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", input_audio,
            "-af", f"adelay={delay_ms}|{delay_ms}",
            output_audio
        ]
        self.logger.info(f"Padding audio with {padding_seconds}s silence: {os.path.basename(input_audio)}")

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would pad audio file")
            return output_audio

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg padding failed: {result.stderr}")
        return output_audio

    def remux_with_instrumental(
        self,
        input_video: str,
        instrumental_audio: str,
        output_file: str,
    ) -> bool:
        """
        Remux video with instrumental audio track.

        Args:
            input_video: Path to input video file
            instrumental_audio: Path to instrumental audio file
            output_file: Path for output file

        Returns:
            True if successful, False otherwise
        """
        command = (
            f'{self._ffmpeg_base_command} -i "{input_video}" '
            f'-i "{instrumental_audio}" -map 0:v -map 1:a -c copy '
            f'{self.MP4_FLAGS} "{output_file}"'
        )
        return self._execute_command(command, "Remuxing with instrumental audio")

    def convert_mov_to_mp4(
        self,
        input_file: str,
        output_file: str,
    ) -> bool:
        """
        Convert MOV to MP4 format.

        Args:
            input_file: Path to input MOV file
            output_file: Path for output MP4 file

        Returns:
            True if successful, False otherwise
        """
        gpu_command = (
            f'{self._ffmpeg_base_command} {self.hwaccel_decode_flags} -i "{input_file}" '
            f'-c:v {self.video_encoder} -c:a copy {self.MP4_FLAGS} "{output_file}"'
        )
        cpu_command = (
            f'{self._ffmpeg_base_command} -i "{input_file}" '
            f'-c:v libx264 -c:a copy {self.MP4_FLAGS} "{output_file}"'
        )
        return self._execute_command_with_fallback(
            gpu_command, cpu_command, "Converting MOV to MP4"
        )

    def encode_lossless_mp4(
        self,
        title_video: str,
        karaoke_video: str,
        output_file: str,
        end_video: Optional[str] = None,
    ) -> bool:
        """
        Create lossless 4K MP4 by concatenating title, karaoke, and optionally end videos.

        Args:
            title_video: Path to title video
            karaoke_video: Path to karaoke video
            output_file: Path for output file
            end_video: Optional path to end credits video

        Returns:
            True if successful, False otherwise
        """
        # Quote file paths
        title_quoted = shlex.quote(os.path.abspath(title_video))
        karaoke_quoted = shlex.quote(os.path.abspath(karaoke_video))

        # Build filter and inputs for concatenation
        if end_video and os.path.isfile(end_video):
            end_quoted = shlex.quote(os.path.abspath(end_video))
            extra_input = f"-i {end_quoted}"
            concat_filter = (
                '-filter_complex "[0:v:0][0:a:0][1:v:0][1:a:0][2:v:0][2:a:0]'
                'concat=n=3:v=1:a=1[outv][outa]"'
            )
        else:
            extra_input = ""
            concat_filter = (
                '-filter_complex "[0:v:0][0:a:0][1:v:0][1:a:0]'
                'concat=n=2:v=1:a=1[outv][outa]"'
            )

        gpu_command = (
            f"{self._ffmpeg_base_command} {self.hwaccel_decode_flags} -i {title_quoted} "
            f"{self.hwaccel_decode_flags} -i {karaoke_quoted} {extra_input} "
            f'{concat_filter} -map "[outv]" -map "[outa]" -c:v {self.video_encoder} '
            f'{self._get_nvenc_quality_settings("lossless")} -c:a pcm_s16le '
            f'{self.MP4_FLAGS} "{output_file}"'
        )
        cpu_command = (
            f"{self._ffmpeg_base_command} -i {title_quoted} -i {karaoke_quoted} {extra_input} "
            f'{concat_filter} -map "[outv]" -map "[outa]" -c:v libx264 -c:a pcm_s16le '
            f'{self.MP4_FLAGS} "{output_file}"'
        )

        return self._execute_command_with_fallback(
            gpu_command, cpu_command, "Encoding lossless 4K MP4"
        )

    def encode_lossy_mp4(
        self,
        input_file: str,
        output_file: str,
    ) -> bool:
        """
        Create lossy 4K MP4 with AAC audio.

        Args:
            input_file: Path to input file (typically lossless MP4)
            output_file: Path for output file

        Returns:
            True if successful, False otherwise
        """
        command = (
            f'{self._ffmpeg_base_command} -i "{input_file}" '
            f'-c:v copy -c:a {self.aac_codec} -ar 48000 -b:a 320k '
            f'{self.MP4_FLAGS} "{output_file}"'
        )
        return self._execute_command(command, "Encoding lossy 4K MP4 with AAC")

    def encode_lossless_mkv(
        self,
        input_file: str,
        output_file: str,
    ) -> bool:
        """
        Create MKV with FLAC audio (for YouTube upload).

        Args:
            input_file: Path to input file
            output_file: Path for output file

        Returns:
            True if successful, False otherwise
        """
        command = (
            f'{self._ffmpeg_base_command} -i "{input_file}" '
            f'-c:v copy -c:a flac "{output_file}"'
        )
        return self._execute_command(command, "Creating MKV with FLAC for YouTube")

    def encode_720p(
        self,
        input_file: str,
        output_file: str,
    ) -> bool:
        """
        Create 720p MP4 with AAC audio.

        Args:
            input_file: Path to input file
            output_file: Path for output file

        Returns:
            True if successful, False otherwise
        """
        gpu_command = (
            f'{self._ffmpeg_base_command} {self.hwaccel_decode_flags} -i "{input_file}" '
            f'-c:v {self.video_encoder} -vf "{self.scale_filter}=1280:720" '
            f'{self._get_nvenc_quality_settings("medium")} -b:v 2000k '
            f'-c:a {self.aac_codec} -ar 48000 -b:a 128k '
            f'{self.MP4_FLAGS} "{output_file}"'
        )
        cpu_command = (
            f'{self._ffmpeg_base_command} -i "{input_file}" '
            f'-c:v libx264 -vf "scale=1280:720" -b:v 2000k -preset medium -tune animation '
            f'-c:a {self.aac_codec} -ar 48000 -b:a 128k '
            f'{self.MP4_FLAGS} "{output_file}"'
        )
        return self._execute_command_with_fallback(
            gpu_command, cpu_command, "Encoding 720p MP4"
        )

    def encode_all_formats(
        self,
        config: EncodingConfig,
    ) -> EncodingResult:
        """
        Encode video to all output formats.

        This performs the full encoding pipeline:
        1. Remux with instrumental audio
        2. Convert to MP4 if needed
        3. Encode lossless 4K MP4 (concatenated)
        4. Encode lossy 4K MP4
        5. Encode lossless MKV (for YouTube)
        6. Encode 720p MP4

        Args:
            config: Encoding configuration with input/output paths

        Returns:
            EncodingResult with success status and output file paths
        """
        output_files = {}

        try:
            # Pad instrumental if countdown padding was applied to vocals
            actual_instrumental = config.instrumental_audio
            if config.countdown_padding_seconds and config.countdown_padding_seconds > 0:
                if "(Padded)" not in config.instrumental_audio:
                    base, ext = os.path.splitext(config.instrumental_audio)
                    padded_path = f"{base} (Padded){ext}"
                    if not os.path.exists(padded_path) or self.dry_run:
                        self._pad_audio_file(config.instrumental_audio, padded_path, config.countdown_padding_seconds)
                        self.logger.info(f"Created padded instrumental: {padded_path}")
                    else:
                        self.logger.info(f"Using existing padded instrumental: {padded_path}")
                    actual_instrumental = padded_path
                else:
                    self.logger.info(f"Instrumental already padded: {config.instrumental_audio}")

            # Step 1: Remux with instrumental audio
            if config.output_karaoke_mp4:
                self.logger.info("[Step 1/6] Remuxing video with instrumental audio...")
                if not self.remux_with_instrumental(
                    config.karaoke_video,
                    actual_instrumental,
                    config.output_karaoke_mp4
                ):
                    return EncodingResult(
                        success=False,
                        output_files=output_files,
                        error="Failed to remux with instrumental audio"
                    )
                output_files["karaoke_mp4"] = config.output_karaoke_mp4

            # Step 2: Convert to MP4 if needed
            if config.output_with_vocals_mp4:
                if not config.karaoke_video.endswith(".mp4"):
                    self.logger.info("[Step 2/6] Converting karaoke video to MP4...")
                    if not self.convert_mov_to_mp4(
                        config.karaoke_video,
                        config.output_with_vocals_mp4
                    ):
                        return EncodingResult(
                            success=False,
                            output_files=output_files,
                            error="Failed to convert to MP4"
                        )
                    output_files["with_vocals_mp4"] = config.output_with_vocals_mp4
                else:
                    self.logger.info("[Step 2/6] Skipped - video already MP4")

            # Step 3: Encode lossless 4K MP4
            if config.output_lossless_4k_mp4:
                self.logger.info("[Step 3/6] Encoding lossless 4K MP4...")
                karaoke_for_concat = config.output_karaoke_mp4 or config.karaoke_video
                if not self.encode_lossless_mp4(
                    config.title_video,
                    karaoke_for_concat,
                    config.output_lossless_4k_mp4,
                    config.end_video
                ):
                    return EncodingResult(
                        success=False,
                        output_files=output_files,
                        error="Failed to encode lossless 4K MP4"
                    )
                output_files["lossless_4k_mp4"] = config.output_lossless_4k_mp4

            # Step 4: Encode lossy 4K MP4
            if config.output_lossy_4k_mp4 and config.output_lossless_4k_mp4:
                self.logger.info("[Step 4/6] Encoding lossy 4K MP4...")
                if not self.encode_lossy_mp4(
                    config.output_lossless_4k_mp4,
                    config.output_lossy_4k_mp4
                ):
                    return EncodingResult(
                        success=False,
                        output_files=output_files,
                        error="Failed to encode lossy 4K MP4"
                    )
                output_files["lossy_4k_mp4"] = config.output_lossy_4k_mp4

            # Step 5: Create MKV with FLAC audio
            if config.output_lossless_mkv and config.output_lossless_4k_mp4:
                self.logger.info("[Step 5/6] Creating MKV with FLAC audio...")
                if not self.encode_lossless_mkv(
                    config.output_lossless_4k_mp4,
                    config.output_lossless_mkv
                ):
                    return EncodingResult(
                        success=False,
                        output_files=output_files,
                        error="Failed to create MKV"
                    )
                output_files["lossless_mkv"] = config.output_lossless_mkv

            # Step 6: Encode 720p version
            if config.output_720p_mp4 and config.output_lossless_4k_mp4:
                self.logger.info("[Step 6/6] Encoding 720p MP4...")
                if not self.encode_720p(
                    config.output_lossless_4k_mp4,
                    config.output_720p_mp4
                ):
                    return EncodingResult(
                        success=False,
                        output_files=output_files,
                        error="Failed to encode 720p"
                    )
                output_files["720p_mp4"] = config.output_720p_mp4

            self.logger.info("All encoding steps completed successfully")
            return EncodingResult(success=True, output_files=output_files)

        except Exception as e:
            self.logger.error(f"Encoding failed with exception: {e}")
            return EncodingResult(
                success=False,
                output_files=output_files,
                error=str(e)
            )


# Singleton instance and factory function (following existing service pattern)
_local_encoding_service: Optional[LocalEncodingService] = None


def get_local_encoding_service(**kwargs) -> LocalEncodingService:
    """
    Get a local encoding service instance.

    Args:
        **kwargs: Arguments passed to LocalEncodingService

    Returns:
        LocalEncodingService instance
    """
    global _local_encoding_service

    if _local_encoding_service is None:
        _local_encoding_service = LocalEncodingService(**kwargs)

    return _local_encoding_service
