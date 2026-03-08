"""
Audio editing service — server-side FFmpeg operations for trim, cut, mute, join.

All operations are lossless (FLAC in, FLAC out). Playback URLs use OGG Opus
via the existing AudioTranscodingService.
"""

import logging
import os
import json
import subprocess
import tempfile
from dataclasses import dataclass, asdict

from backend.services.storage_service import StorageService

logger = logging.getLogger(__name__)


@dataclass
class AudioMetadata:
    duration_seconds: float
    sample_rate: int
    channels: int
    format: str
    file_size_bytes: int


class AudioEditService:
    """Server-side audio editing via FFmpeg."""

    def __init__(self, storage_service: StorageService | None = None):
        self.storage = storage_service or StorageService()

    def get_metadata(self, audio_path: str) -> AudioMetadata:
        """Get audio metadata from a local file using ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr}")

        info = json.loads(result.stdout)
        fmt = info.get("format", {})
        streams = info.get("streams", [])
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

        return AudioMetadata(
            duration_seconds=float(fmt.get("duration", 0)),
            sample_rate=int(audio_stream.get("sample_rate", 44100)),
            channels=int(audio_stream.get("channels", 2)),
            format=fmt.get("format_name", "unknown"),
            file_size_bytes=int(fmt.get("size", 0)),
        )

    def get_metadata_from_gcs(self, gcs_path: str) -> AudioMetadata:
        """Download from GCS and get metadata."""
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = os.path.join(temp_dir, "audio")
            self.storage.download_file(gcs_path, local_path)
            return self.get_metadata(local_path)

    def trim_start(self, input_path: str, end_seconds: float, output_path: str) -> AudioMetadata:
        """Remove audio from 0 to end_seconds (skip the first N seconds)."""
        self._run_ffmpeg([
            "-ss", str(end_seconds),
            "-i", input_path,
            "-c:a", "flac",
            output_path,
        ])
        return self.get_metadata(output_path)

    def trim_end(self, input_path: str, start_seconds: float, output_path: str) -> AudioMetadata:
        """Keep audio from 0 to start_seconds (remove from start_seconds to end)."""
        self._run_ffmpeg([
            "-i", input_path,
            "-t", str(start_seconds),
            "-c:a", "flac",
            output_path,
        ])
        return self.get_metadata(output_path)

    def cut_region(self, input_path: str, start: float, end: float, output_path: str) -> AudioMetadata:
        """Remove a region from start to end, joining the remaining parts."""
        self._run_ffmpeg([
            "-i", input_path,
            "-filter_complex",
            f"[0]atrim=0:{start},asetpts=PTS-STARTPTS[a];"
            f"[0]atrim={end},asetpts=PTS-STARTPTS[b];"
            f"[a][b]concat=n=2:v=0:a=1[out]",
            "-map", "[out]",
            "-c:a", "flac",
            output_path,
        ])
        return self.get_metadata(output_path)

    def mute_region(self, input_path: str, start: float, end: float, output_path: str) -> AudioMetadata:
        """Silence a region (preserve duration)."""
        self._run_ffmpeg([
            "-i", input_path,
            "-af", f"volume=enable='between(t,{start},{end})':volume=0",
            "-c:a", "flac",
            output_path,
        ])
        return self.get_metadata(output_path)

    def join_audio(self, input_path: str, other_path: str, position: str, output_path: str) -> AudioMetadata:
        """Join two audio files. position: 'start' (prepend other) or 'end' (append other)."""
        if position == "start":
            first, second = other_path, input_path
        else:
            first, second = input_path, other_path

        self._run_ffmpeg([
            "-i", first,
            "-i", second,
            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
            "-map", "[out]",
            "-c:a", "flac",
            output_path,
        ])
        return self.get_metadata(output_path)

    def apply_edit(
        self,
        input_gcs_path: str,
        operation: str,
        params: dict,
        output_gcs_path: str,
        job_id: str,
    ) -> tuple[AudioMetadata, str]:
        """
        Apply an edit operation to an audio file in GCS.

        Returns (metadata, output_gcs_path).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            local_input = os.path.join(temp_dir, "input.flac")
            local_output = os.path.join(temp_dir, "output.flac")

            # Download current audio
            self.storage.download_file(input_gcs_path, local_input)

            # Apply the operation
            if operation == "trim_start":
                metadata = self.trim_start(local_input, params["end_seconds"], local_output)
            elif operation == "trim_end":
                metadata = self.trim_end(local_input, params["start_seconds"], local_output)
            elif operation == "cut":
                metadata = self.cut_region(
                    local_input, params["start_seconds"], params["end_seconds"], local_output
                )
            elif operation == "mute":
                metadata = self.mute_region(
                    local_input, params["start_seconds"], params["end_seconds"], local_output
                )
            elif operation in ("join_start", "join_end"):
                # Download the upload file
                upload_gcs_path = params["upload_gcs_path"]
                local_other = os.path.join(temp_dir, "other.flac")
                self.storage.download_file(upload_gcs_path, local_other)
                position = "start" if operation == "join_start" else "end"
                metadata = self.join_audio(local_input, local_other, position, local_output)
            else:
                raise ValueError(f"Unknown operation: {operation}")

            # Upload edited audio
            self.storage.upload_file(local_output, output_gcs_path)
            logger.info(f"[{job_id}] Applied {operation}, uploaded to {output_gcs_path}")

            return metadata, output_gcs_path

    def _run_ffmpeg(self, args: list[str]) -> None:
        """Run an ffmpeg command with standard options."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr}")
