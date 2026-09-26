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

    # Fade selections within this distance of a clip edge snap to it, matching the
    # editor UI's edge tolerance ("fade in from ~the start" means from 0).
    FADE_EDGE_SNAP_SECONDS = 1.0

    def fade_region(
        self, input_path: str, start: float, end: float, direction: str, output_path: str
    ) -> AudioMetadata:
        """Fade in or out across [start, end] only (preserves duration).

        Like Audacity's selection fades: direction='in' ramps silence -> full across
        the selection, direction='out' ramps full -> silence. Audio outside the
        selection is untouched, so fades work mid-track (e.g. fade out + mute +
        fade in to drop out a section). A selection starting within 1s of the clip
        start snaps to 0, and one ending within 1s of the end snaps to the end.

        Implemented as trim -> afade -> concat, because afade applied to the whole
        stream silences everything before a fade-in / after a fade-out.
        """
        if direction not in ("in", "out"):
            raise ValueError(f"Invalid fade direction: {direction}")
        if start < 0 or end <= start:
            raise ValueError(f"Invalid fade region: start={start}, end={end}")

        total = self.get_metadata(input_path).duration_seconds
        snap = self.FADE_EDGE_SNAP_SECONDS
        if end > total + snap:
            raise ValueError(f"Fade region exceeds clip duration ({end} > {total})")
        if start <= snap:
            start = 0.0
        reaches_end = end >= total - snap
        if reaches_end:
            end = total
        if end - start <= 0:
            raise ValueError(f"Fade region must have positive duration (got {end - start})")

        fade = f"afade=t={direction}:st=0:d={end - start}"
        chains = []
        if start > 0:
            chains.append(f"[0]atrim=start=0:end={start},asetpts=PTS-STARTPTS[pre]")
        fade_trim = f"atrim=start={start}" if reaches_end else f"atrim=start={start}:end={end}"
        chains.append(f"[0]{fade_trim},asetpts=PTS-STARTPTS,{fade}[fade]")
        if not reaches_end:
            chains.append(f"[0]atrim=start={end},asetpts=PTS-STARTPTS[post]")
        labels = "".join(c[c.rindex("["):] for c in chains)

        self._run_ffmpeg([
            "-i", input_path,
            "-filter_complex",
            ";".join(chains) + f";{labels}concat=n={len(chains)}:v=0:a=1[out]",
            "-map", "[out]",
            "-c:a", "flac",
            output_path,
        ])
        return self.get_metadata(output_path)

    # Tempo factors outside this range sound badly artefacted and are almost
    # certainly a mistake for a sing-along track. The editor UI uses the same bounds.
    MIN_TEMPO_FACTOR = 0.5
    MAX_TEMPO_FACTOR = 1.5

    _rubberband_available: bool | None = None

    @classmethod
    def _has_rubberband(cls) -> bool:
        """Whether this ffmpeg build includes the librubberband filter (cached)."""
        if cls._rubberband_available is None:
            try:
                result = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-filters"],
                    capture_output=True, text=True, timeout=30,
                )
                cls._rubberband_available = result.returncode == 0 and " rubberband " in result.stdout
            except (OSError, subprocess.TimeoutExpired):
                cls._rubberband_available = False
            if not cls._rubberband_available:
                logger.warning(
                    "ffmpeg has no rubberband filter; tempo changes will use the "
                    "lower-quality atempo filter"
                )
        return cls._rubberband_available

    def change_tempo(self, input_path: str, factor: float, output_path: str) -> AudioMetadata:
        """Speed up (factor > 1) or slow down (factor < 1) the whole track, preserving pitch.

        Uses Rubber Band (high-quality, pitch-preserving time-stretch) when the
        ffmpeg build has it — the production static build does — else atempo.
        """
        if not (self.MIN_TEMPO_FACTOR <= factor <= self.MAX_TEMPO_FACTOR):
            raise ValueError(
                f"Tempo factor must be between {self.MIN_TEMPO_FACTOR} and "
                f"{self.MAX_TEMPO_FACTOR} (got {factor})"
            )
        if abs(factor - 1.0) < 1e-6:
            raise ValueError("Tempo factor of 1.0 would not change the audio")

        if self._has_rubberband():
            audio_filter = f"rubberband=tempo={factor}:pitchq=quality:channels=together"
        else:
            audio_filter = f"atempo={factor}"

        self._run_ffmpeg([
            "-i", input_path,
            "-af", audio_filter,
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
            elif operation in ("fade_in", "fade_out"):
                direction = "in" if operation == "fade_in" else "out"
                metadata = self.fade_region(
                    local_input, params["start_seconds"], params["end_seconds"], direction, local_output
                )
            elif operation == "tempo":
                metadata = self.change_tempo(local_input, float(params["factor"]), local_output)
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
