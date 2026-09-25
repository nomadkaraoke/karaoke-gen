"""
Waveform visualization generator for audio files.

This module provides the WaveformGenerator class which creates waveform
images suitable for display in the instrumental review UI.
"""

import logging
import math
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import numpy as np
from pydub import AudioSegment

from .models import AudibleSegment, MuteRegion


logger = logging.getLogger(__name__)


# Sample rate used to decode audio for the RMS envelope in generate_data_only.
_ENVELOPE_SAMPLE_RATE = 16000
_PCM16_MAX_AMPLITUDE = 32768.0
# Decoding a full track at 16 kHz mono takes seconds; bound pathological inputs.
_DECODE_TIMEOUT_SECONDS = 300


class WaveformGenerator:
    """
    Generates waveform visualization images from audio files.
    
    This class creates PNG images showing the amplitude envelope of an
    audio file over time. It can highlight detected audible segments
    and mute regions with different colors.
    
    The generated images are suitable for display in web UIs and can
    be used for interactive seeking (click-to-seek) functionality.
    
    Attributes:
        width: Width of the output image in pixels (default: 1200)
        height: Height of the output image in pixels (default: 200)
        background_color: Background color (default: "#1a1a2e")
        waveform_color: Main waveform color (default: "#4a90d9")
        segment_color: Color for audible segments (default: "#e94560")
        mute_color: Color for mute regions (default: "#ff6b6b")
        time_axis_color: Color for time axis (default: "#ffffff")
    
    Example:
        >>> generator = WaveformGenerator(width=1200, height=200)
        >>> generator.generate(
        ...     audio_path="/path/to/backing_vocals.flac",
        ...     output_path="/path/to/waveform.png",
        ...     segments=analysis_result.audible_segments
        ... )
    """
    
    def __init__(
        self,
        width: int = 1200,
        height: int = 200,
        background_color: str = "#1a1a2e",
        waveform_color: str = "#4a90d9",
        segment_color: str = "#e94560",
        mute_color: str = "#ff6b6b",
        time_axis_color: str = "#ffffff",
        dpi: int = 100,
    ):
        """
        Initialize the waveform generator.
        
        Args:
            width: Width of the output image in pixels
            height: Height of the output image in pixels
            background_color: Background color (hex or named color)
            waveform_color: Main waveform color
            segment_color: Color for highlighting audible segments
            mute_color: Color for highlighting mute regions
            time_axis_color: Color for time axis labels
            dpi: DPI for the output image
        """
        self.width = width
        self.height = height
        self.background_color = background_color
        self.waveform_color = waveform_color
        self.segment_color = segment_color
        self.mute_color = mute_color
        self.time_axis_color = time_axis_color
        self.dpi = dpi
    
    def generate(
        self,
        audio_path: str,
        output_path: str,
        segments: Optional[List[AudibleSegment]] = None,
        mute_regions: Optional[List[MuteRegion]] = None,
        show_time_axis: bool = True,
        silence_threshold_db: float = -40.0,
    ) -> str:
        """
        Generate a waveform image from an audio file.
        
        Args:
            audio_path: Path to the audio file
            output_path: Path where the PNG image will be saved
            segments: Optional list of audible segments to highlight
            mute_regions: Optional list of mute regions to highlight
            show_time_axis: Whether to show time axis labels
            silence_threshold_db: Threshold for visual reference line
        
        Returns:
            Path to the generated image file
        
        Raises:
            FileNotFoundError: If the audio file doesn't exist
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        logger.info(f"Generating waveform for: {audio_path}")
        
        # Load audio
        audio = AudioSegment.from_file(audio_path)
        duration_seconds = len(audio) / 1000.0
        
        # Convert to mono if needed
        if audio.channels > 1:
            audio = audio.set_channels(1)
        
        # Get amplitude envelope
        envelope = self._get_envelope(audio)
        
        # Create the figure
        fig, ax = self._create_figure(duration_seconds, show_time_axis)
        
        # Draw waveform
        self._draw_waveform(ax, envelope, duration_seconds)
        
        # Highlight mute regions (if any) - draw first so waveform is on top
        if mute_regions:
            self._draw_mute_regions(ax, mute_regions, duration_seconds)
        
        # Highlight audible segments (if any)
        if segments:
            self._draw_segments(ax, segments, envelope, duration_seconds)
        
        # Draw silence threshold reference line
        self._draw_threshold_line(ax, silence_threshold_db, duration_seconds)
        
        # Save the figure
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        
        fig.savefig(
            output_path,
            facecolor=self.background_color,
            edgecolor='none',
            bbox_inches='tight',
            pad_inches=0.1,
        )
        plt.close(fig)
        
        logger.info(f"Waveform saved to: {output_path}")
        return output_path
    
    def generate_data_only(
        self,
        audio_path: str,
        num_points: int = 500,
    ) -> Tuple[List[float], float]:
        """
        Generate waveform data without creating an image.
        
        This is useful for sending data to a frontend that will
        render the waveform itself (e.g., using Canvas or SVG).
        
        Args:
            audio_path: Path to the audio file
            num_points: Number of data points to return
        
        Returns:
            Tuple of (amplitude_values, duration_seconds)
            Amplitude values are normalized to 0.0-1.0 range.
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # Decode straight to low-rate mono 16-bit PCM rather than loading the
        # source at native resolution: a 6-minute 24-bit/96kHz stereo FLAC
        # peaked at ~1 GB RSS through pydub and OOM-killed the 1 GiB
        # audio-download Cloud Run Job (job 2c1b922e). A 1000-point RMS
        # envelope doesn't need more than a few kHz of bandwidth.
        samples = self._decode_mono_pcm16(audio_path, _ENVELOPE_SAMPLE_RATE)
        samples_per_ms = _ENVELOPE_SAMPLE_RATE // 1000
        duration_ms = int(round(len(samples) * 1000 / _ENVELOPE_SAMPLE_RATE))
        duration_seconds = duration_ms / 1000.0

        # Calculate window size to get desired number of points
        window_ms = max(1, duration_ms // num_points)

        starts = np.arange(0, duration_ms, window_ms, dtype=np.int64) * samples_per_ms
        starts = starts[starts < len(samples)]
        if not len(starts):
            return [], duration_seconds

        squared = samples.astype(np.float64) ** 2
        sums = np.add.reduceat(squared, starts)
        widths = np.diff(np.append(starts, len(samples)))
        rms = np.sqrt(sums / widths)

        with np.errstate(divide="ignore"):
            db = np.where(rms > 0, 20 * np.log10(rms / _PCM16_MAX_AMPLITUDE), -100.0)

        # Normalize to 0-1 range (mapping -60dB to 0dB -> 0 to 1)
        amplitudes = np.clip((db + 60) / 60, 0.0, 1.0)
        return [float(a) for a in amplitudes], duration_seconds

    @staticmethod
    def _decode_mono_pcm16(audio_path: str, sample_rate: int) -> np.ndarray:
        """Decode any ffmpeg-readable file to mono int16 samples at ``sample_rate``."""
        cmd = [
            AudioSegment.converter, "-nostdin", "-v", "error",
            "-i", audio_path, "-vn",
            "-ac", "1", "-ar", str(sample_rate),
            "-f", "s16le", "-acodec", "pcm_s16le", "-",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, check=False, timeout=_DECODE_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"ffmpeg failed to decode {audio_path}: timed out after {e.timeout}s"
            ) from e
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed to decode {audio_path}: "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        return np.frombuffer(result.stdout, dtype=np.int16)

    def generate_peaks(
        self,
        audio_path: str,
        peaks_per_second: int = 400,
    ) -> Tuple[np.ndarray, float]:
        """
        Generate a max-abs peak envelope at a fixed time resolution.

        Mirrors the review frontend's ``computePeaks`` (lib/audio-data.ts):
        one peak per 1/peaks_per_second bucket, each the maximum absolute
        sample value across all channels, normalized to 0.0–1.0. Serving this
        pre-computed lets the review page draw per-segment waveform strips
        without downloading + decoding the whole vocal stem client-side.

        Vectorized (numpy) — a 4-minute stereo track computes in well under a
        second, vs the pure-Python RMS loop in generate_data_only.

        Returns:
            Tuple of (peaks float32 ndarray in 0..1, duration_seconds)
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        audio = AudioSegment.from_file(audio_path)
        duration_seconds = len(audio) / 1000.0

        samples = np.abs(
            np.asarray(audio.get_array_of_samples(), dtype=np.float32)
        ) / float(audio.max_possible_amplitude)
        if audio.channels > 1:
            # Interleaved -> (frames, channels) -> per-frame max across channels
            frames = len(samples) // audio.channels
            samples = samples[: frames * audio.channels]
            samples = samples.reshape(frames, audio.channels).max(axis=1)

        bucket_count = max(1, math.ceil(duration_seconds * peaks_per_second))
        # Fractional stride with floored boundaries, matching the frontend's
        # drift-free bucketing (see the 44.1kHz drift fix notes there).
        samples_per_bucket = len(samples) / bucket_count
        boundaries = np.floor(np.arange(bucket_count + 1) * samples_per_bucket).astype(np.int64)
        boundaries[-1] = len(samples)

        peaks = np.zeros(bucket_count, dtype=np.float32)
        # maximum.reduceat needs strictly valid starts; guard empty buckets.
        starts = boundaries[:-1]
        if len(samples):
            reduced = np.maximum.reduceat(samples, np.minimum(starts, len(samples) - 1))
            widths = boundaries[1:] - starts
            peaks = np.where(widths > 0, reduced, 0.0).astype(np.float32)

        return peaks, duration_seconds

    def _get_envelope(
        self,
        audio: AudioSegment,
        window_ms: int = 50,
    ) -> np.ndarray:
        """
        Extract amplitude envelope from audio.
        
        Returns array of amplitude values in dB.
        """
        duration_ms = len(audio)
        amplitudes = []
        
        for start_ms in range(0, duration_ms, window_ms):
            end_ms = min(start_ms + window_ms, duration_ms)
            window = audio[start_ms:end_ms]
            
            if window.rms > 0:
                db = 20 * math.log10(window.rms / window.max_possible_amplitude)
            else:
                db = -100.0
            
            amplitudes.append(db)
        
        return np.array(amplitudes)
    
    def _create_figure(
        self,
        duration_seconds: float,
        show_time_axis: bool,
    ) -> Tuple[plt.Figure, plt.Axes]:
        """
        Create matplotlib figure and axes.
        """
        fig_width = self.width / self.dpi
        fig_height = self.height / self.dpi
        
        fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=self.dpi)
        
        # Set background
        fig.patch.set_facecolor(self.background_color)
        ax.set_facecolor(self.background_color)
        
        # Configure axes
        ax.set_xlim(0, duration_seconds)
        ax.set_ylim(-60, 0)  # dB range
        
        # Remove spines
        for spine in ax.spines.values():
            spine.set_visible(False)
        
        # Configure ticks
        if show_time_axis:
            ax.tick_params(
                axis='x',
                colors=self.time_axis_color,
                labelsize=8,
            )
            ax.tick_params(axis='y', left=False, labelleft=False)
            
            # Set time axis ticks
            self._set_time_ticks(ax, duration_seconds)
        else:
            ax.tick_params(
                axis='both',
                left=False,
                bottom=False,
                labelleft=False,
                labelbottom=False,
            )
        
        return fig, ax
    
    def _set_time_ticks(self, ax: plt.Axes, duration_seconds: float):
        """
        Set appropriate time axis tick marks.
        """
        if duration_seconds <= 60:
            # Under 1 minute: tick every 10 seconds
            tick_interval = 10
        elif duration_seconds <= 300:
            # Under 5 minutes: tick every 30 seconds
            tick_interval = 30
        else:
            # Over 5 minutes: tick every minute
            tick_interval = 60
        
        ticks = np.arange(0, duration_seconds + 1, tick_interval)
        ax.set_xticks(ticks)
        
        # Format tick labels as MM:SS
        labels = []
        for t in ticks:
            minutes = int(t // 60)
            seconds = int(t % 60)
            labels.append(f"{minutes}:{seconds:02d}")
        ax.set_xticklabels(labels)
    
    def _draw_waveform(
        self,
        ax: plt.Axes,
        envelope: np.ndarray,
        duration_seconds: float,
    ):
        """
        Draw the main waveform.
        """
        num_points = len(envelope)
        time_points = np.linspace(0, duration_seconds, num_points)
        
        # Draw as filled area
        ax.fill_between(
            time_points,
            envelope,
            -60,  # Bottom of range
            color=self.waveform_color,
            alpha=0.7,
        )
        
        # Draw outline
        ax.plot(
            time_points,
            envelope,
            color=self.waveform_color,
            linewidth=0.5,
            alpha=0.9,
        )
    
    def _draw_segments(
        self,
        ax: plt.Axes,
        segments: List[AudibleSegment],
        envelope: np.ndarray,
        duration_seconds: float,
    ):
        """
        Highlight audible segments on the waveform.
        """
        num_points = len(envelope)
        time_points = np.linspace(0, duration_seconds, num_points)
        
        for segment in segments:
            # Find indices corresponding to this segment
            start_idx = int(segment.start_seconds / duration_seconds * num_points)
            end_idx = int(segment.end_seconds / duration_seconds * num_points)
            
            start_idx = max(0, min(start_idx, num_points - 1))
            end_idx = max(0, min(end_idx, num_points))
            
            if start_idx >= end_idx:
                continue
            
            segment_time = time_points[start_idx:end_idx]
            segment_envelope = envelope[start_idx:end_idx]
            
            # Highlight this segment with a different color
            ax.fill_between(
                segment_time,
                segment_envelope,
                -60,
                color=self.segment_color,
                alpha=0.6,
            )
    
    def _draw_mute_regions(
        self,
        ax: plt.Axes,
        mute_regions: List[MuteRegion],
        duration_seconds: float,
    ):
        """
        Draw mute region overlays.
        """
        for region in mute_regions:
            ax.axvspan(
                region.start_seconds,
                region.end_seconds,
                color=self.mute_color,
                alpha=0.3,
                zorder=0,
            )
    
    def _draw_threshold_line(
        self,
        ax: plt.Axes,
        threshold_db: float,
        duration_seconds: float,
    ):
        """
        Draw a reference line at the silence threshold.
        """
        ax.axhline(
            y=threshold_db,
            color=self.time_axis_color,
            linestyle='--',
            linewidth=0.5,
            alpha=0.3,
        )
