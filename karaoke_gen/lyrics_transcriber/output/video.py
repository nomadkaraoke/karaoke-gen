import logging
import os
import json
import subprocess
from typing import List, Optional, Tuple


class VideoGenerator:
    """Handles generation of video files with lyrics overlay."""

    def __init__(
        self,
        output_dir: str,
        cache_dir: str,
        video_resolution: Tuple[int, int],
        styles: dict,
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize VideoGenerator.

        Args:
            output_dir: Directory where output files will be written
            cache_dir: Directory for temporary files
            video_resolution: Tuple of (width, height) for video resolution
            styles: Dictionary of output video & CDG styling configuration
            logger: Optional logger instance
        """
        if not all(x > 0 for x in video_resolution):
            raise ValueError("Video resolution dimensions must be greater than 0")

        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.video_resolution = video_resolution
        self.styles = styles
        self.logger = logger or logging.getLogger(__name__)

        # Get background settings from styles, with defaults
        karaoke_styles = styles.get("karaoke", {})
        self.background_image = karaoke_styles.get("background_image")
        self.background_color = karaoke_styles.get("background_color", "black")

        # Validate background image if specified
        if self.background_image and not os.path.isfile(self.background_image):
            raise FileNotFoundError(f"Video background image not found: {self.background_image}")

        # Detect and configure hardware acceleration
        self.nvenc_available = self.detect_nvenc_support()
        self.configure_hardware_acceleration()

    def detect_nvenc_support(self):
        """Detect if NVENC hardware encoding is available."""
        try:
            self.logger.info("🔍 Detecting NVENC hardware acceleration...")
            
            # Check if NVIDIA GPU is available
            gpu_detected = False
            try:
                nvidia_smi_cmd = ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"]
                nvidia_result = subprocess.run(nvidia_smi_cmd, capture_output=True, text=True, timeout=10)
                if nvidia_result.returncode == 0:
                    gpu_info = nvidia_result.stdout.strip()
                    self.logger.info(f"  ✓ NVIDIA GPU detected: {gpu_info}")
                    gpu_detected = True
                else:
                    self.logger.debug(f"nvidia-smi failed: {nvidia_result.stderr}")
            except Exception as e:
                self.logger.debug(f"nvidia-smi not available: {e}")
            
            # Check for NVENC encoders in FFmpeg
            nvenc_in_ffmpeg = False
            try:
                encoders_cmd = ["ffmpeg", "-hide_banner", "-encoders"]
                encoders_result = subprocess.run(encoders_cmd, capture_output=True, text=True, timeout=10)
                if encoders_result.returncode == 0:
                    encoder_lines = encoders_result.stdout.split('\n')
                    nvenc_encoders = [line for line in encoder_lines if 'nvenc' in line.lower()]
                    if nvenc_encoders:
                        self.logger.debug(f"Found NVENC encoders: {[e.strip() for e in nvenc_encoders]}")
                        nvenc_in_ffmpeg = True
                    else:
                        self.logger.debug("No NVENC encoders found in FFmpeg encoder list")
            except Exception as e:
                self.logger.debug(f"Error listing FFmpeg encoders: {e}")
            
            # Test h264_nvenc encoder
            test_cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
                "-c:v", "h264_nvenc", "-f", "null", "-"
            ]
            
            self.logger.debug(f"Testing NVENC: {' '.join(test_cmd)}")
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
            nvenc_available = result.returncode == 0
            
            if nvenc_available:
                self.logger.info("  ✓ NVENC encoding available")
                return True
            
            # NVENC test failed - log details at debug level
            self.logger.debug(f"NVENC test failed (return code {result.returncode})")
            self.logger.debug(f"NVENC test stderr: {result.stderr}")
            
            # Try alternative test
            alt_test_cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=red:size=320x240:duration=0.1",
                "-c:v", "h264_nvenc", "-preset", "fast", "-f", "null", "-"
            ]
            
            alt_result = subprocess.run(alt_test_cmd, capture_output=True, text=True, timeout=30)
            if alt_result.returncode == 0:
                self.logger.info("  ✓ NVENC encoding available")
                return True
            
            self.logger.debug(f"Alternative NVENC test also failed: {alt_result.stderr}")
            
            # Check CUDA availability for debug info
            try:
                cuda_test_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-hwaccels"]
                cuda_result = subprocess.run(cuda_test_cmd, capture_output=True, text=True, timeout=10)
                if cuda_result.returncode == 0:
                    hwaccels = cuda_result.stdout.strip()
                    cuda_available = 'cuda' in hwaccels
                    self.logger.debug(f"FFmpeg hardware accelerators: {hwaccels}")
                    if cuda_available:
                        self.logger.debug("CUDA found in FFmpeg but NVENC still not working")
            except Exception as e:
                self.logger.debug(f"Error checking CUDA: {e}")
            
            # Check CUDA libraries for debug info
            try:
                ldconfig_cmd = ["ldconfig", "-p"]
                ldconfig_result = subprocess.run(ldconfig_cmd, capture_output=True, text=True, timeout=10)
                if ldconfig_result.returncode == 0:
                    has_libcuda = "libcuda.so.1" in ldconfig_result.stdout
                    has_nvenc_lib = "libnvidia-encode.so" in ldconfig_result.stdout
                    self.logger.debug(f"CUDA libraries: libcuda.so.1={has_libcuda}, libnvidia-encode.so={has_nvenc_lib}")
                    if not has_libcuda:
                        self.logger.debug("Missing libcuda.so.1 - may need nvidia/cuda:*-devel image")
            except Exception as e:
                self.logger.debug(f"Error checking CUDA libraries: {e}")
            
            self.logger.info("  ✗ NVENC not available")
            return False
            
        except subprocess.TimeoutExpired:
            self.logger.debug("NVENC detection timed out")
            self.logger.info("  ✗ NVENC not available (timeout)")
            return False
        except Exception as e:
            self.logger.debug(f"Failed to detect NVENC support: {e}")
            import traceback
            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            self.logger.info("  ✗ NVENC not available (error)")
            return False

    def configure_hardware_acceleration(self):
        """Configure hardware acceleration settings based on detected capabilities."""
        if self.nvenc_available:
            self.video_encoder = "h264_nvenc"
            self.hwaccel_flags = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
            self.logger.info("🚀 Using NVENC hardware acceleration for video encoding")
        else:
            self.video_encoder = "libx264"
            self.hwaccel_flags = []
            self.logger.info("🔧 Using software encoding (libx264) for video")

    def get_nvenc_settings(self, quality_mode="high", is_preview=False):
        """Get optimized NVENC settings for subtitle overlay content."""
        if not self.nvenc_available:
            return []
            
        if is_preview:
            # Fast encoding for preview
            return [
                "-preset", "p1",  # Fastest preset
                "-tune", "ll",    # Low latency
                "-rc", "vbr",     # Variable bitrate
            ]
        elif quality_mode == "high":
            # High quality for final output
            return [
                "-preset", "p4",     # Balanced preset
                "-tune", "hq",       # High quality
                "-rc", "vbr",        # Variable bitrate  
                "-cq", "18",         # Constant quality (higher quality)
                "-spatial-aq", "1",  # Spatial adaptive quantization
                "-temporal-aq", "1", # Temporal adaptive quantization
            ]
        else:
            # Balanced settings
            return [
                "-preset", "p4",
                "-rc", "vbr",
            ]

    def generate_video(self, ass_path: str, audio_path: str, output_prefix: str) -> str:
        """Generate MP4 video with lyrics overlay.

        Args:
            ass_path: Path to ASS subtitles file
            audio_path: Path to audio file
            output_prefix: Prefix for output filename

        Returns:
            Path to generated video file
        """
        self.logger.info("Generating video with lyrics overlay")
        output_path = self._get_output_path(f"{output_prefix} (With Vocals)", "mkv")

        # Check input files exist before running FFmpeg
        if not os.path.isfile(ass_path):
            raise FileNotFoundError(f"Subtitles file not found: {ass_path}")
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            # Create a temporary copy of the ASS file with a unique filename
            import time

            safe_prefix = "".join(c if c.isalnum() else "_" for c in output_prefix)
            timestamp = int(time.time() * 1000)
            temp_ass_path = os.path.join(self.cache_dir, f"temp_subtitles_{safe_prefix}_{timestamp}.ass")
            import shutil

            shutil.copy2(ass_path, temp_ass_path)
            self.logger.debug(f"Created temporary ASS file: {temp_ass_path}")

            cmd = self._build_ffmpeg_command(temp_ass_path, audio_path, output_path)
            self._run_ffmpeg_command(cmd)
            self.logger.info(f"Video generated: {output_path}")

            # Clean up temporary file
            if os.path.exists(temp_ass_path):
                os.remove(temp_ass_path)
            return output_path

        except Exception as e:
            self.logger.error(f"Failed to generate video: {str(e)}")
            # Clean up temporary file in case of error
            if "temp_ass_path" in locals() and os.path.exists(temp_ass_path):
                try:
                    os.remove(temp_ass_path)
                except:
                    pass
            raise

    def generate_preview_video(self, ass_path: str, audio_path: str, output_prefix: str) -> str:
        """Generate lower resolution MP4 preview video with lyrics overlay.

        Delegates to LocalPreviewEncodingService for the actual encoding to ensure
        consistent preview generation across local CLI, Cloud Run, and GCE worker.

        Args:
            ass_path: Path to ASS subtitles file
            audio_path: Path to audio file
            output_prefix: Prefix for output filename

        Returns:
            Path to generated preview video file
        """
        from backend.services.local_preview_encoding_service import (
            LocalPreviewEncodingService,
            PreviewEncodingConfig,
        )

        self.logger.info("Generating preview video with lyrics overlay")
        output_path = os.path.join(self.cache_dir, f"{output_prefix}_preview.mp4")

        # Check input files exist before running FFmpeg
        if not os.path.isfile(ass_path):
            raise FileNotFoundError(f"Subtitles file not found: {ass_path}")
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            # Create a temporary copy of the ASS file with a unique filename
            import time
            import shutil

            safe_prefix = "".join(c if c.isalnum() else "_" for c in output_prefix)
            timestamp = int(time.time() * 1000)
            temp_ass_path = os.path.join(self.cache_dir, f"temp_preview_subtitles_{safe_prefix}_{timestamp}.ass")

            shutil.copy2(ass_path, temp_ass_path)
            self.logger.debug(f"Created temporary ASS file: {temp_ass_path}")

            # Get font path from styles configuration
            karaoke_styles = self.styles.get("karaoke", {})
            font_path = karaoke_styles.get("font_path")

            # Build config for LocalPreviewEncodingService
            # Force black background for preview videos (ignore background_image from styles)
            config = PreviewEncodingConfig(
                ass_path=temp_ass_path,
                audio_path=audio_path,
                output_path=output_path,
                background_image_path=None,  # Always use solid color for previews
                background_color="black",  # Always use black background for previews
                font_path=font_path if font_path and os.path.isfile(font_path) else None,
            )

            # Use LocalPreviewEncodingService for consistent encoding
            service = LocalPreviewEncodingService(logger=self.logger)
            result = service.encode_preview(config)

            if not result.success:
                raise RuntimeError(f"Preview encoding failed: {result.error}")

            self.logger.info(f"Preview video generated: {output_path}")

            # Clean up temporary file
            if os.path.exists(temp_ass_path):
                os.remove(temp_ass_path)
            return output_path

        except Exception as e:
            self.logger.error(f"Failed to generate preview video: {str(e)}")
            # Clean up temporary file in case of error
            if "temp_ass_path" in locals() and os.path.exists(temp_ass_path):
                try:
                    os.remove(temp_ass_path)
                except:
                    pass
            raise

    def _get_output_path(self, output_prefix: str, extension: str) -> str:
        """Generate full output path for a file."""
        return os.path.join(self.output_dir, f"{output_prefix}.{extension}")

    def _resize_background_image(self, input_path: str) -> str:
        """Resize background image to match target resolution and save to temp file."""
        target_width, target_height = self.video_resolution

        # Get current image dimensions using ffprobe
        try:
            probe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                input_path,
            ]
            probe_output = subprocess.check_output(probe_cmd, universal_newlines=True)
            probe_data = json.loads(probe_output)
            current_width = probe_data["streams"][0]["width"]
            current_height = probe_data["streams"][0]["height"]

            # If dimensions already match, return original path
            if current_width == target_width and current_height == target_height:
                self.logger.debug("Background image already at target resolution")
                return input_path

        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            self.logger.warning(f"Failed to get image dimensions: {e}")
            # Continue with resize attempt if probe fails

        temp_path = os.path.join(self.cache_dir, "resized_background.png")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-vf",
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2",
            temp_path,
        ]

        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True)
            return temp_path
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to resize background image: {e.output}")
            raise

    def _escape_ffmpeg_filter_path(self, path: str) -> str:
        """Escape a path for FFmpeg filter expressions (for subprocess without shell).

        When using subprocess with a command list (no shell), FFmpeg receives the
        filter string directly. FFmpeg's filter parser requires escaping:
        - Backslashes: double them (\ -> \\)
        - Single quotes/apostrophes: escape with three backslashes (' -> \\')
        - Spaces: escape with backslash ( -> \ )

        Note: This is different from shell escaping. The '\\'\\''' pattern used for
        shell escaping does NOT work when subprocess passes args directly to FFmpeg.

        Example: "I'm With You" becomes "I\\\\'m\\ With\\ You"
        """
        # First escape existing backslashes (\ -> \\)
        escaped = path.replace("\\", "\\\\")
        # Escape single quotes (' -> \\')
        # In the actual string we need 3 backslashes before the quote
        escaped = escaped.replace("'", "\\\\\\'")
        # Escape spaces
        escaped = escaped.replace(" ", "\\ ")
        return escaped

    def _build_ass_filter(self, ass_path: str) -> str:
        """Build ASS filter with font directory support."""
        escaped_ass_path = self._escape_ffmpeg_filter_path(ass_path)
        ass_filter = f"ass={escaped_ass_path}"

        # Get font path from styles configuration
        karaoke_styles = self.styles.get("karaoke", {})
        font_path = karaoke_styles.get("font_path")

        if font_path and os.path.isfile(font_path):
            font_dir = os.path.dirname(font_path)
            escaped_font_dir = self._escape_ffmpeg_filter_path(font_dir)
            ass_filter += f":fontsdir={escaped_font_dir}"
            self.logger.info(f"Returning ASS filter with fonts dir: {ass_filter}")

        return ass_filter

    def _build_ffmpeg_command(self, ass_path: str, audio_path: str, output_path: str) -> List[str]:
        """Build FFmpeg command for video generation with hardware acceleration when available."""
        width, height = self.video_resolution

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-r", "30",  # Set frame rate to 30 fps
        ]

        # Add hardware acceleration flags if available
        cmd.extend(self.hwaccel_flags)

        # Input source (background)
        if self.background_image:
            # Resize background image first
            resized_bg = self._resize_background_image(self.background_image)
            self.logger.debug(f"Using resized background image: {resized_bg}")
            cmd.extend([
                "-loop", "1",  # Loop the image
                "-i", resized_bg,
            ])
        else:
            self.logger.debug(
                f"Using solid {self.background_color} background "
                f"with resolution: {width}x{height}"
            )
            cmd.extend([
                "-f", "lavfi",
                "-i", f"color=c={self.background_color}:s={width}x{height}:r=30"
            ])

        cmd.extend([
            "-i", audio_path,
            "-c:a", "flac",  # Re-encode audio as FLAC
            "-vf", self._build_ass_filter(ass_path),  # Add subtitles with font directories
            "-c:v", self.video_encoder,
        ])

        # Add encoder-specific settings
        if self.nvenc_available:
            # NVENC settings optimized for subtitle content
            cmd.extend(self.get_nvenc_settings("high", is_preview=False))
            # Use higher bitrate for NVENC as it's more efficient
            cmd.extend([
                "-b:v", "8000k",      # Higher base bitrate for NVENC
                "-maxrate", "15000k", # Reasonable max for 4K
                "-bufsize", "16000k", # Buffer size
            ])
            self.logger.debug("Using NVENC encoding for high-quality video generation")
        else:
            # Software encoding fallback settings
            cmd.extend([
                "-preset", "fast",     # Better compression efficiency
                "-b:v", "5000k",       # Base video bitrate
                "-minrate", "5000k",   # Minimum bitrate
                "-maxrate", "20000k",  # Maximum bitrate
                "-bufsize", "10000k",  # Buffer size (2x base rate)
            ])
            self.logger.debug("Using software encoding for video generation")

        cmd.extend([
            "-shortest",  # End encoding after shortest stream
            "-y",         # Overwrite output without asking
        ])

        # Add output path
        cmd.append(output_path)

        return cmd

    def _get_video_codec(self) -> str:
        """Determine the best available video codec (legacy method - use video_encoder instead)."""
        # This method is kept for backwards compatibility but is deprecated
        # The new hardware acceleration system uses self.video_encoder instead
        self.logger.warning("_get_video_codec is deprecated, use self.video_encoder instead")
        return self.video_encoder

    def _run_ffmpeg_command(self, cmd: List[str]) -> None:
        """Execute FFmpeg command with output handling."""
        self.logger.debug(f"Running FFmpeg command: {' '.join(cmd)}")
        try:
            output = subprocess.check_output(cmd, universal_newlines=True, stderr=subprocess.STDOUT)
            self.logger.debug(f"FFmpeg output: {output}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"FFmpeg error: {e.output}")
            raise
