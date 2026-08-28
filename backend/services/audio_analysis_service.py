"""
Audio analysis service for analyzing backing vocals.

This service wraps the shared karaoke_gen.instrumental_review module
to provide GCS-integrated audio analysis capabilities.
"""

import json
import logging
import os
import tempfile
from typing import Optional

from backend.services.storage_service import StorageService
from karaoke_gen.instrumental_review import (
    AudioAnalyzer,
    AnalysisResult,
    WaveformGenerator,
)


logger = logging.getLogger(__name__)


class AudioAnalysisService:
    """
    Service for analyzing backing vocals audio files stored in GCS.
    
    This service acts as a thin wrapper around the shared AudioAnalyzer
    and WaveformGenerator classes, handling GCS download/upload operations.
    
    The actual analysis logic is in the shared karaoke_gen.instrumental_review
    module, ensuring feature parity between local and remote workflows.
    """
    
    def __init__(
        self,
        storage_service: Optional[StorageService] = None,
        silence_threshold_db: float = -40.0,
        min_segment_duration_ms: int = 100,
    ):
        """
        Initialize the audio analysis service.
        
        Args:
            storage_service: GCS storage service. If not provided, a new
                instance will be created.
            silence_threshold_db: Threshold for considering audio as silent.
                Default is -40.0 dB.
            min_segment_duration_ms: Minimum duration for audible segments.
                Default is 100ms.
        """
        self.storage_service = storage_service or StorageService()
        self.analyzer = AudioAnalyzer(
            silence_threshold_db=silence_threshold_db,
            min_segment_duration_ms=min_segment_duration_ms,
        )
        self.waveform_generator = WaveformGenerator()
    
    def analyze_backing_vocals(
        self,
        gcs_audio_path: str,
        job_id: str,
    ) -> AnalysisResult:
        """
        Analyze a backing vocals audio file from GCS.
        
        This method:
        1. Downloads the audio file from GCS to a temp file
        2. Runs the analysis using the shared AudioAnalyzer
        3. Returns the analysis result
        
        Args:
            gcs_audio_path: Path to the audio file in GCS
            job_id: Job ID for logging
        
        Returns:
            AnalysisResult containing analysis data
        """
        logger.info(f"[{job_id}] Analyzing backing vocals: {gcs_audio_path}")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download audio file
            local_audio_path = os.path.join(temp_dir, "backing_vocals.flac")
            self.storage_service.download_file(gcs_audio_path, local_audio_path)
            
            # Run analysis
            result = self.analyzer.analyze(local_audio_path)
            
            logger.info(
                f"[{job_id}] Analysis complete: "
                f"has_audible={result.has_audible_content}, "
                f"segments={result.segment_count}, "
                f"recommendation={result.recommended_selection.value}"
            )
            
            return result
    
    def analyze_and_generate_waveform(
        self,
        gcs_audio_path: str,
        job_id: str,
        gcs_waveform_destination: str,
        gcs_lead_vocals_path: Optional[str] = None,
        gcs_vocals_path: Optional[str] = None,
    ) -> tuple[AnalysisResult, str, Optional[dict]]:
        """
        Analyze backing vocals, generate a waveform image, and (when the lead
        and full-vocals stems are provided) compute the 3-stem comparison
        signals for the backing-vocals decider.

        This method:
        1. Downloads the audio file from GCS
        2. Runs analysis using AudioAnalyzer
        3. Generates waveform image using WaveformGenerator
        4. Uploads the waveform image to GCS
        5. Optionally downloads lead_vocals + vocals stems and runs
           compare_stems (best-effort — a failure yields a comparison dict
           with ``error`` set, never an exception)

        Args:
            gcs_audio_path: Path to the backing-vocals audio file in GCS
            job_id: Job ID for logging
            gcs_waveform_destination: Where to upload the waveform image in GCS
            gcs_lead_vocals_path: Optional GCS path of the lead_vocals stem
            gcs_vocals_path: Optional GCS path of the full vocals stem

        Returns:
            Tuple of (AnalysisResult, waveform_gcs_path, stem_comparison dict or None)
        """
        logger.info(f"[{job_id}] Analyzing and generating waveform: {gcs_audio_path}")

        with tempfile.TemporaryDirectory() as temp_dir:
            # Download audio file
            local_audio_path = os.path.join(temp_dir, "backing_vocals.flac")
            self.storage_service.download_file(gcs_audio_path, local_audio_path)

            # Run analysis
            result = self.analyzer.analyze(local_audio_path)

            # Generate waveform
            local_waveform_path = os.path.join(temp_dir, "waveform.png")
            self.waveform_generator.generate(
                audio_path=local_audio_path,
                output_path=local_waveform_path,
                segments=result.audible_segments,
                show_time_axis=True,
                silence_threshold_db=self.analyzer.silence_threshold_db,
            )

            # Upload waveform to GCS
            self.storage_service.upload_file(
                local_waveform_path,
                gcs_waveform_destination
            )

            comparison: Optional[dict] = None
            if gcs_lead_vocals_path and gcs_vocals_path:
                comparison = self._compare_stems(
                    job_id, temp_dir, local_audio_path,
                    gcs_lead_vocals_path, gcs_vocals_path,
                )

            logger.info(
                f"[{job_id}] Analysis and waveform generation complete. "
                f"Waveform uploaded to: {gcs_waveform_destination}"
            )

            return result, gcs_waveform_destination, comparison

    def _compare_stems(
        self,
        job_id: str,
        temp_dir: str,
        local_backing_path: str,
        gcs_lead_vocals_path: str,
        gcs_vocals_path: str,
    ) -> Optional[dict]:
        """Download lead + vocals stems and run the 3-stem comparison.

        Best-effort: returns None when the extra stems can't even be
        downloaded; compare_stems itself reports internal failures via the
        ``error`` field rather than raising.
        """
        from karaoke_gen.instrumental_review.stem_comparison import compare_stems

        try:
            local_lead = os.path.join(temp_dir, "lead_vocals.flac")
            local_vocals = os.path.join(temp_dir, "vocals.flac")
            self.storage_service.download_file(gcs_lead_vocals_path, local_lead)
            self.storage_service.download_file(gcs_vocals_path, local_vocals)
        except Exception as e:
            logger.warning(f"[{job_id}] stem comparison skipped (download failed): {e}")
            return None
        comparison = compare_stems(
            local_backing_path,
            local_lead,
            local_vocals,
            silence_threshold_db=self.analyzer.silence_threshold_db,
        )
        logger.info(
            f"[{job_id}] Stem comparison: coverage_ratio={comparison.coverage_ratio}, "
            f"corr_backing_vocals={comparison.corr_backing_vocals}, "
            f"lead_overlap={comparison.lead_overlap_fraction}, "
            f"flat_fraction={comparison.flat_fraction}, error={comparison.error}"
        )
        return comparison.to_dict()
    
    def get_waveform_data(
        self,
        gcs_audio_path: str,
        job_id: str,
        num_points: int = 500,
    ) -> tuple[list[float], float]:
        """
        Get waveform data (amplitude envelope) for client-side rendering.
        
        This is useful when the frontend wants to render the waveform
        itself using Canvas or SVG, rather than displaying a pre-generated
        image.
        
        Args:
            gcs_audio_path: Path to the audio file in GCS
            job_id: Job ID for logging
            num_points: Number of data points to return
        
        Returns:
            Tuple of (amplitude_values, duration_seconds)
        """
        logger.info(f"[{job_id}] Getting waveform data: {gcs_audio_path}")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download audio file
            local_audio_path = os.path.join(temp_dir, "backing_vocals.flac")
            self.storage_service.download_file(gcs_audio_path, local_audio_path)
            
            # Generate waveform data
            amplitudes, duration = self.waveform_generator.generate_data_only(
                audio_path=local_audio_path,
                num_points=num_points,
            )
            
            return amplitudes, duration

    def cache_waveform_data(
        self,
        gcs_audio_path: str,
        job_id: str,
        cache_gcs_path: str,
        num_points: int = 1000,
    ) -> tuple[list[float], float]:
        """
        Generate waveform data and cache it as JSON in GCS.

        Args:
            gcs_audio_path: Path to the audio file in GCS
            job_id: Job ID for logging
            cache_gcs_path: GCS path to store the cached waveform JSON
            num_points: Number of data points to generate

        Returns:
            Tuple of (amplitude_values, duration_seconds)
        """
        logger.info(f"[{job_id}] Generating and caching waveform data: {gcs_audio_path}")

        amplitudes, duration = self.get_waveform_data(
            gcs_audio_path=gcs_audio_path,
            job_id=job_id,
            num_points=num_points,
        )

        cache_data = {
            "amplitudes": list(amplitudes),
            "duration_seconds": duration,
            "num_points": num_points,
            "source_gcs_path": gcs_audio_path,
        }
        self.storage_service.upload_json(cache_gcs_path, cache_data)
        logger.info(f"[{job_id}] Waveform data cached at {cache_gcs_path}")

        return amplitudes, duration

    def load_cached_waveform(
        self,
        cache_gcs_path: str,
    ) -> Optional[tuple[list[float], float]]:
        """
        Load cached waveform data from GCS.

        Args:
            cache_gcs_path: GCS path to the cached waveform JSON

        Returns:
            Tuple of (amplitude_values, duration_seconds) or None if not found
        """
        try:
            if not self.storage_service.file_exists(cache_gcs_path):
                return None

            with tempfile.TemporaryDirectory() as temp_dir:
                local_path = os.path.join(temp_dir, "waveform.json")
                self.storage_service.download_file(cache_gcs_path, local_path)
                with open(local_path) as f:
                    data = json.load(f)

            amplitudes = data.get("amplitudes", [])
            duration = data.get("duration_seconds", 0.0)
            if not amplitudes:
                return None

            return amplitudes, duration
        except Exception as e:
            logger.warning(f"Failed to load cached waveform from {cache_gcs_path}: {e}")
            return None

    def generate_waveform_with_mutes(
        self,
        gcs_audio_path: str,
        job_id: str,
        gcs_waveform_destination: str,
        mute_regions: list,
    ) -> str:
        """
        Generate a waveform image with mute regions highlighted.
        
        This is useful for showing the user which regions will be muted
        in the custom instrumental.
        
        Args:
            gcs_audio_path: Path to the audio file in GCS
            job_id: Job ID for logging
            gcs_waveform_destination: Where to upload the waveform image
            mute_regions: List of MuteRegion objects to highlight
        
        Returns:
            GCS path to the uploaded waveform image
        """
        from karaoke_gen.instrumental_review import MuteRegion
        
        logger.info(
            f"[{job_id}] Generating waveform with {len(mute_regions)} mute regions"
        )
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download audio file
            local_audio_path = os.path.join(temp_dir, "backing_vocals.flac")
            self.storage_service.download_file(gcs_audio_path, local_audio_path)
            
            # First run analysis to get segments
            result = self.analyzer.analyze(local_audio_path)
            
            # Generate waveform with mute regions
            local_waveform_path = os.path.join(temp_dir, "waveform_with_mutes.png")
            self.waveform_generator.generate(
                audio_path=local_audio_path,
                output_path=local_waveform_path,
                segments=result.audible_segments,
                mute_regions=mute_regions,
                show_time_axis=True,
            )
            
            # Upload to GCS
            self.storage_service.upload_file(
                local_waveform_path,
                gcs_waveform_destination
            )
            
            return gcs_waveform_destination
