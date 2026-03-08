"""
Audio download worker.

Downloads audio from the selected source (Spotify, YouTube, RED, OPS) and
triggers audio separation + lyrics transcription workers.

This runs as a Cloud Run Job to avoid the instance termination issue where
Cloud Run would shut down instances mid-download when using BackgroundTasks.
The download can take 30s-5min depending on the source.

Flow:
1. Read job from Firestore to get download params
2. Download audio via flacfetch (Spotify/RED/OPS) or YouTube service
3. Upload to GCS at uploads/{job_id}/audio/
4. Update Firestore with input_media_gcs_path
5. Transition job from DOWNLOADING_AUDIO to DOWNLOADING
6. Trigger audio separation + lyrics transcription workers
"""
import asyncio
import logging
import mimetypes
import os
import shutil
import tempfile

from backend.models.job import JobStatus
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.worker_service import get_worker_service
from backend.services.flacfetch_client import get_flacfetch_client, FlacfetchServiceError
from backend.services.audio_search_service import DownloadError


logger = logging.getLogger(__name__)


def _extract_gcs_path(filepath: str) -> str:
    """Extract relative GCS path from a full gs:// URL or path."""
    bucket_prefix = "gs://karaoke-gen-storage-nomadkaraoke/"
    if filepath.startswith(bucket_prefix):
        return filepath[len(bucket_prefix):]
    # Also handle the older bucket name
    bucket_prefix_alt = "gs://karaoke-gen-storage/"
    if filepath.startswith(bucket_prefix_alt):
        return filepath[len(bucket_prefix_alt):]
    # Check for any gs:// prefix
    if filepath.startswith("gs://"):
        parts = filepath.split("/", 3)
        if len(parts) == 2:
            return parts[1]
    return filepath


async def process_audio_download(job_id: str) -> bool:
    """
    Download audio for a job and trigger processing workers.

    This is the main entry point, called by the Cloud Run Job CLI.

    Args:
        job_id: Job ID to process

    Returns:
        True if download and worker triggers succeeded, False otherwise
    """
    job_manager = JobManager()
    storage_service = StorageService()

    try:
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"[job:{job_id}] Job not found")
            return False

        # Validate job is in correct state
        if job.status not in (JobStatus.DOWNLOADING_AUDIO, JobStatus.FAILED):
            logger.warning(
                f"[job:{job_id}] Unexpected status {job.status}, expected DOWNLOADING_AUDIO"
            )
            # Still proceed if there's download params - this handles retry cases
            if not (job.source_name and job.source_id):
                logger.error(f"[job:{job_id}] No download params available, aborting")
                return False

        # Get download params from job
        search_results = job.state_data.get('audio_search_results', [])
        selection_index = job.state_data.get('selected_audio_index')
        remote_search_id = job.state_data.get('remote_search_id')

        if selection_index is not None and search_results:
            selected = search_results[selection_index]
            source_name = selected.get('provider')
            source_id = selected.get('source_id')
            target_file = selected.get('target_file')
            download_url = selected.get('url')
        elif job.source_name and job.source_id:
            # Fallback to job-level params (set by _validate_and_prepare_selection)
            source_name = job.source_name
            source_id = job.source_id
            target_file = job.target_file
            download_url = job.download_url
            selected = {'artist': job.artist, 'title': job.title}
        else:
            raise DownloadError("No audio source selection found in job data")

        logger.info(
            f"[job:{job_id}] Starting audio download: "
            f"source={source_name}, id={source_id}"
        )

        # Route to appropriate download handler
        audio_gcs_path, filename = await _download_audio(
            job_id=job_id,
            source_name=source_name,
            source_id=source_id,
            target_file=target_file,
            download_url=download_url,
            remote_search_id=remote_search_id,
            selection_index=selection_index,
            selected=selected,
            storage_service=storage_service,
        )

        # Update job with GCS path
        job_manager.update_job(job_id, {
            'input_media_gcs_path': audio_gcs_path,
            'filename': filename,
        })

        # Transition from DOWNLOADING_AUDIO to DOWNLOADING
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.DOWNLOADING,
            progress=15,
            message="Audio downloaded, starting processing"
        )

        # Check if audio editing was requested — enter blocking state instead of processing
        job = job_manager.get_job(job_id)
        if job and job.state_data.get('requires_audio_edit'):
            # Preserve the original input path before any edits
            job_manager.update_state_data(job_id, 'original_input_media_gcs_path', audio_gcs_path)
            job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.AWAITING_AUDIO_EDIT,
                progress=15,
                message="Audio downloaded. Please review and edit the audio before processing."
            )
            logger.info(f"[job:{job_id}] Audio download complete, awaiting audio edit")
            return True

        # Trigger audio separation and lyrics workers
        worker_service = get_worker_service()
        await asyncio.gather(
            worker_service.trigger_audio_worker(job_id),
            worker_service.trigger_lyrics_worker(job_id)
        )

        logger.info(f"[job:{job_id}] Audio download complete, workers triggered")
        return True

    except (DownloadError, FlacfetchServiceError) as e:
        logger.error(f"[job:{job_id}] Download failed: {e}")
        job_manager.fail_job(job_id, f"Audio download failed: {e}")
        return False
    except Exception as e:
        logger.error(f"[job:{job_id}] Download failed: {e}", exc_info=True)
        job_manager.fail_job(job_id, f"Audio download failed: {e}")
        return False


async def _download_audio(
    job_id: str,
    source_name: str,
    source_id: str,
    target_file: str | None,
    download_url: str | None,
    remote_search_id: str | None,
    selection_index: int | None,
    selected: dict,
    storage_service: StorageService,
) -> tuple[str, str]:
    """
    Download audio from the selected source and return (gcs_path, filename).

    Routes to the appropriate download handler based on source type.
    """
    if source_name == 'YouTube':
        return await _download_youtube(job_id, source_id, download_url, selected)

    elif source_name in ('RED', 'OPS'):
        return await _download_torrent(
            job_id, source_name, source_id, target_file,
            download_url, remote_search_id, selection_index
        )

    elif source_name == 'Spotify':
        return await _download_spotify(
            job_id, source_id, download_url
        )

    else:
        raise DownloadError(f"Unsupported audio source: {source_name}")


async def _download_youtube(
    job_id: str,
    source_id: str,
    download_url: str | None,
    selected: dict,
) -> tuple[str, str]:
    """Download from YouTube via YouTubeDownloadService."""
    from backend.services.youtube_download_service import (
        get_youtube_download_service,
        YouTubeDownloadError,
    )

    youtube_service = get_youtube_download_service()

    if not source_id and download_url:
        source_id = youtube_service._extract_video_id(download_url)

    if not source_id:
        raise DownloadError("No video ID available for YouTube download")

    logger.info(f"[job:{job_id}] YouTube download: video_id={source_id}")

    try:
        audio_gcs_path = await youtube_service.download_by_id(
            video_id=source_id,
            job_id=job_id,
            artist=selected.get('artist'),
            title=selected.get('title'),
        )
        filename = os.path.basename(audio_gcs_path)
        return audio_gcs_path, filename
    except YouTubeDownloadError as e:
        raise DownloadError(f"YouTube download failed: {e}")


async def _download_torrent(
    job_id: str,
    source_name: str,
    source_id: str | None,
    target_file: str | None,
    download_url: str | None,
    remote_search_id: str | None,
    selection_index: int | None,
) -> tuple[str, str]:
    """Download from torrent source (RED/OPS) via flacfetch."""
    flacfetch_client = get_flacfetch_client()
    if not flacfetch_client:
        raise DownloadError(
            f"Cannot download from {source_name} without remote flacfetch service. "
            "Configure FLACFETCH_API_URL."
        )

    gcs_destination = f"uploads/{job_id}/audio/"

    if source_id:
        logger.info(f"[job:{job_id}] Torrent download: {source_name} ID={source_id}")
        download_id = await flacfetch_client.download_by_id(
            source_name=source_name,
            source_id=source_id,
            target_file=target_file,
            download_url=download_url,
            gcs_path=gcs_destination,
        )
    else:
        if not remote_search_id:
            raise DownloadError(
                f"No source_id or remote_search_id available for {source_name} download"
            )
        logger.info(f"[job:{job_id}] Torrent download via search-based download")
        download_id = await flacfetch_client.download(
            search_id=remote_search_id,
            result_index=selection_index,
            gcs_path=gcs_destination,
        )

    final_status = await flacfetch_client.wait_for_download(
        download_id,
        timeout=600,
    )

    filepath = final_status.get("gcs_path") or final_status.get("output_path")
    if not filepath:
        raise DownloadError("Remote download completed but no file path returned")

    audio_gcs_path = _extract_gcs_path(filepath)
    filename = os.path.basename(filepath)
    logger.info(f"[job:{job_id}] Torrent download complete: {audio_gcs_path}")
    return audio_gcs_path, filename


async def _download_spotify(
    job_id: str,
    source_id: str,
    download_url: str | None,
) -> tuple[str, str]:
    """Download from Spotify via flacfetch."""
    flacfetch_client = get_flacfetch_client()
    if not flacfetch_client:
        raise DownloadError(
            "Cannot download from Spotify without remote flacfetch service. "
            "Configure FLACFETCH_API_URL."
        )

    gcs_destination = f"uploads/{job_id}/audio/"
    logger.info(f"[job:{job_id}] Spotify download: source_id={source_id}")

    download_id = await flacfetch_client.download_by_id(
        source_name="Spotify",
        source_id=source_id,
        download_url=download_url,
        gcs_path=gcs_destination,
    )

    final_status = await flacfetch_client.wait_for_download(
        download_id,
        timeout=600,
    )

    filepath = final_status.get("gcs_path") or final_status.get("output_path")
    if not filepath:
        raise DownloadError("Remote download completed but no file path returned")

    audio_gcs_path = _extract_gcs_path(filepath)
    filename = os.path.basename(filepath)
    logger.info(f"[job:{job_id}] Spotify download complete: {audio_gcs_path}")
    return audio_gcs_path, filename


def main():
    """
    CLI entry point for running audio download worker as a Cloud Run Job.

    Usage:
        python -m backend.workers.audio_download_worker --job-id abc123
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Audio download worker for karaoke generation"
    )
    parser.add_argument(
        "--job-id",
        required=True,
        help="Job ID to process"
    )

    args = parser.parse_args()
    job_id = args.job_id

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    logger.info(f"Starting audio download worker for job {job_id}")

    try:
        success = asyncio.run(process_audio_download(job_id))
        if success:
            logger.info(f"Audio download completed successfully for job {job_id}")
            sys.exit(0)
        else:
            logger.error(f"Audio download failed for job {job_id}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Audio download worker crashed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
