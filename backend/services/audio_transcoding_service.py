"""
Audio transcoding service for review UI optimization.

Transcodes FLAC audio files to OGG Opus for efficient browser playback.
Original FLAC files are never modified — transcoded versions are cached
in GCS at jobs/{job_id}/review-audio/{filename}.ogg.

Eager transcoding: called from screens_worker before AWAITING_REVIEW
so all files are cached before the user opens the review UI.
Falls back to original FLAC signed URLs if transcoded version is missing.
"""

import asyncio
import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from backend.services.storage_service import StorageService


logger = logging.getLogger(__name__)


class AudioTranscodingService:
    """
    Transcodes audio files to OGG Opus and caches them in GCS.

    Used by the review UI to serve ~3 MB OGG files instead of 35+ MB FLACs.
    """

    def __init__(self, storage_service: Optional[StorageService] = None):
        self.storage = storage_service or StorageService()

    def _get_cache_path(self, source_gcs_path: str) -> str:
        """
        Derive the cache path for a transcoded file.

        Examples:
            jobs/abc123/input/song.flac -> jobs/abc123/review-audio/song.ogg
            jobs/abc123/stems/instrumental_clean.flac -> jobs/abc123/review-audio/instrumental_clean.ogg
        """
        parts = Path(source_gcs_path).parts
        # Find "jobs/{job_id}" prefix
        try:
            jobs_idx = list(parts).index("jobs")
            job_id = parts[jobs_idx + 1]
        except (ValueError, IndexError):
            # Fallback: use a hash of the full path to avoid collisions
            job_id = "_" + hashlib.sha256(source_gcs_path.encode()).hexdigest()[:12]

        filename = Path(source_gcs_path).stem + ".ogg"
        return f"jobs/{job_id}/review-audio/{filename}"

    def _transcode_and_upload(self, source_gcs_path: str, cache_path: str) -> str:
        """
        Download FLAC from GCS, transcode to OGG Opus, upload to cache path.

        Returns the cache GCS path on success.
        Raises on failure.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            local_input = os.path.join(temp_dir, "input.flac")
            local_output = os.path.join(temp_dir, "output.ogg")

            # Download source
            self.storage.download_file(source_gcs_path, local_input)

            # Transcode: FLAC -> OGG Opus 128kbps
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-y",
                "-i", local_input,
                "-c:a", "libopus",
                "-b:a", "128k",
                "-vn",
                local_output,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg transcoding failed (exit {result.returncode}): {result.stderr}"
                )

            # Upload transcoded file
            self.storage.upload_file(local_output, cache_path)

        return cache_path

    def transcode_if_needed(self, source_gcs_path: str) -> str:
        """
        Return the GCS path of the transcoded OGG file, transcoding if not cached.

        Returns the cache path (always an OGG file in review-audio/).
        Raises if transcoding fails.
        """
        cache_path = self._get_cache_path(source_gcs_path)

        if self.storage.file_exists(cache_path):
            logger.debug(f"Cache hit: {cache_path}")
            return cache_path

        logger.info(f"Cache miss, transcoding: {source_gcs_path} -> {cache_path}")
        return self._transcode_and_upload(source_gcs_path, cache_path)

    def get_review_audio_url(
        self, source_gcs_path: str, expiration_minutes: int = 120
    ) -> str:
        """
        Get a signed URL for the transcoded audio, falling back to original FLAC.
        """
        try:
            cache_path = self.transcode_if_needed(source_gcs_path)
            return self.storage.generate_signed_url(cache_path, expiration_minutes)
        except Exception as e:
            # Source may be deleted but cache from earlier transcode persists
            try:
                cache_path = self._get_cache_path(source_gcs_path)
                if self.storage.file_exists(cache_path):
                    logger.info(f"Source gone but cache exists, serving: {cache_path}")
                    return self.storage.generate_signed_url(cache_path, expiration_minutes)
            except Exception:
                pass  # Fall through to FLAC fallback below
            logger.warning(
                f"Transcoding failed for {source_gcs_path}, falling back to FLAC: {e}"
            )
            return self.storage.generate_signed_url(source_gcs_path, expiration_minutes)

    async def get_review_audio_url_async(
        self, source_gcs_path: str, expiration_minutes: int = 120
    ) -> str:
        """Async wrapper around get_review_audio_url via asyncio.to_thread."""
        return await asyncio.to_thread(
            self.get_review_audio_url, source_gcs_path, expiration_minutes
        )

    def prepare_review_audio_for_job(self, job) -> list[str]:
        """
        Transcode all review audio files for a job (eager transcoding).

        Called from screens_worker before AWAITING_REVIEW transition
        and from the admin backfill endpoint.

        Returns list of cache paths that were transcoded.
        """
        job_id = job.job_id
        transcoded = []

        # Collect all audio paths to transcode
        paths_to_transcode = []

        # Main input audio
        if job.input_media_gcs_path:
            paths_to_transcode.append(("input", job.input_media_gcs_path))

        # Stems
        stems = job.file_urls.get("stems", {})
        for stem_key in ["instrumental_clean", "instrumental_with_backing", "backing_vocals"]:
            path = stems.get(stem_key)
            if path:
                paths_to_transcode.append((stem_key, path))

        for label, source_path in paths_to_transcode:
            try:
                cache_path = self.transcode_if_needed(source_path)
                transcoded.append(cache_path)
                logger.info(f"[{job_id}] Transcoded {label}: {cache_path}")
            except Exception as e:
                # Non-fatal: review UI will fall back to FLAC
                logger.warning(
                    f"[{job_id}] Failed to transcode {label} ({source_path}): {e}"
                )

        logger.info(
            f"[{job_id}] Review audio preparation complete: "
            f"{len(transcoded)}/{len(paths_to_transcode)} files transcoded"
        )
        return transcoded
