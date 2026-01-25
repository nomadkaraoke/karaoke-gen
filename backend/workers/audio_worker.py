"""
Audio separation worker.

Handles the audio processing track of parallel processing:
1. Stage 1: Clean instrumental separation (Modal API, 3-5 min)
2. Stage 2: Backing vocals separation (Modal API, 2-3 min)
3. Post-processing: Combine instrumentals, normalize

Re-uses karaoke_gen.audio_processor.AudioProcessor for remote GPU separation.

Observability:
- All operations wrapped in tracing spans for Cloud Trace visibility
- Logs include [job:ID] prefix for easy filtering in Cloud Logging
- Worker start/end timing logged with WORKER_START/WORKER_END markers
"""
import logging
import os
import shutil
import tempfile
import time
from typing import Optional, Dict, Any
from pathlib import Path

from backend.models.job import JobStatus
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.config import get_settings
from backend.workers.worker_logging import create_job_logger, setup_job_logging, job_logging_context
from backend.workers.registry import worker_registry
from backend.services.tracing import job_span, add_span_event, add_span_attribute
from backend.services.metrics import metrics

# Import from karaoke_gen package
from karaoke_gen.audio_processor import AudioProcessor


logger = logging.getLogger(__name__)


async def _trigger_lyrics_worker_after_url_download(job_id: str) -> None:
    """
    Trigger lyrics worker after URL audio download completes.

    For URL jobs, we use sequential triggering:
    1. Audio worker downloads and uploads audio to GCS
    2. Audio worker triggers lyrics worker (this function)
    3. Both workers then proceed in parallel (audio separation + lyrics transcription)

    This prevents the race condition where lyrics worker times out waiting for audio.
    """
    from backend.services.worker_service import get_worker_service

    try:
        worker_service = get_worker_service()
        await worker_service.trigger_lyrics_worker(job_id)
        logger.info(f"Job {job_id}: Triggered lyrics worker after URL download")
    except Exception as e:
        # Log but don't fail - audio processing can still continue
        # The job will eventually timeout if lyrics worker doesn't run
        logger.error(f"Job {job_id}: Failed to trigger lyrics worker: {e}")


# Default model names - used by create_audio_processor and stored in state_data
DEFAULT_CLEAN_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
DEFAULT_BACKING_MODELS = ["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"]
DEFAULT_OTHER_MODELS = ["htdemucs_6s.yaml"]


# Loggers to capture for audio worker
AUDIO_WORKER_LOGGERS = [
    "karaoke_gen.audio_processor",
]


async def download_from_url(url: str, temp_dir: str, artist: str, title: str, job_manager: JobManager = None, job_id: str = None) -> Optional[str]:
    """
    Download audio from a URL using local yt_dlp.

    IMPORTANT: This is a LEGACY FALLBACK for non-YouTube URLs only!

    For YouTube URLs, use YouTubeDownloadService instead, which:
    - Uses remote flacfetch when configured (avoids Cloud Run bot detection)
    - Has proper error handling and GCS upload

    This function uses local yt_dlp and will likely FAIL for YouTube URLs
    on Cloud Run due to bot detection (YouTube blocks Cloud Run IP ranges).

    Uses the FileHandler from karaoke_gen which includes:
    - Anti-detection options (user agent, headers, delays)
    - Cookie support for authenticated downloads
    - Retry logic

    If artist and/or title are not provided, attempts to extract them from
    the URL metadata.

    Args:
        url: URL to download from (NOT recommended for YouTube - use YouTubeDownloadService)
        temp_dir: Temporary directory to save to
        artist: Artist name for filename (can be None for auto-detection)
        title: Song title for filename (can be None for auto-detection)
        job_manager: Optional JobManager to update job with detected metadata
        job_id: Optional job ID to update

    Returns:
        Path to downloaded audio file, or None if failed
    """
    try:
        from karaoke_gen.file_handler import FileHandler
        from karaoke_gen.utils import sanitize_filename
        
        # Create FileHandler instance
        file_handler = FileHandler(
            logger=logger,
            ffmpeg_base_command="ffmpeg -hide_banner -loglevel error -nostats -y",
            create_track_subfolders=False,
            dry_run=False
        )
        
        # Try to extract metadata if artist or title not provided
        if not artist or not title:
            logger.info(f"Extracting metadata from URL: {url}")
            metadata = file_handler.extract_metadata_from_url(url)
            
            if metadata:
                if not artist:
                    artist = metadata.get('artist', 'Unknown')
                    logger.info(f"Auto-detected artist: {artist}")
                if not title:
                    title = metadata.get('title', 'Unknown')
                    logger.info(f"Auto-detected title: {title}")
                
                # Update job with detected metadata if job_manager provided
                if job_manager and job_id:
                    update_data = {}
                    if artist:
                        update_data['artist'] = artist
                    if title:
                        update_data['title'] = title
                    if update_data:
                        job_manager.update_job(job_id, update_data)
                        logger.info(f"Updated job {job_id} with detected metadata")
            else:
                logger.warning("Could not extract metadata from URL, using defaults")
                artist = artist or "Unknown"
                title = title or "Unknown"
        
        # Create output filename (without extension)
        safe_artist = sanitize_filename(artist) if artist else "Unknown"
        safe_title = sanitize_filename(title) if title else "Unknown"
        output_filename_no_extension = os.path.join(temp_dir, f"{safe_artist} - {safe_title}")
        
        # Get YouTube cookies from environment variable if available
        # This helps bypass "Sign in to confirm you're not a bot" errors
        cookies_str = os.environ.get("YOUTUBE_COOKIES")
        if cookies_str:
            logger.info("Using YouTube cookies for download authentication")
        else:
            logger.info("No YOUTUBE_COOKIES env var set - attempting download without cookies")
        
        # Download using FileHandler (includes anti-detection features)
        logger.info(f"Downloading from URL: {url}")
        downloaded_file = file_handler.download_video(
            url=url,
            output_filename_no_extension=output_filename_no_extension,
            cookies_str=cookies_str
        )
        
        if downloaded_file and os.path.exists(downloaded_file):
            logger.info(f"Downloaded video: {downloaded_file}")
            
            # Convert to WAV for processing
            wav_file = file_handler.convert_to_wav(
                input_filename=downloaded_file,
                output_filename_no_extension=output_filename_no_extension
            )
            
            if wav_file and os.path.exists(wav_file):
                logger.info(f"Converted to WAV: {wav_file}")
                return wav_file
            else:
                logger.error("WAV conversion failed")
                return None
        else:
            logger.error("Download failed - no file returned")
            return None
            
    except ImportError as e:
        logger.error(f"Import error: {e}. Check karaoke_gen installation.")
        return None
    except Exception as e:
        logger.error(f"Failed to download from URL {url}: {e}", exc_info=True)
        return None


def create_audio_processor(
    temp_dir: str,
    clean_instrumental_model: Optional[str] = None,
    backing_vocals_models: Optional[list] = None,
    other_stems_models: Optional[list] = None
) -> AudioProcessor:
    """
    Create an AudioProcessor instance configured for remote API processing.
    
    This reuses the karaoke_gen AudioProcessor with settings optimized for Cloud Run:
    - Uses remote Modal API (via AUDIO_SEPARATOR_API_URL env var)
    - No local models needed (model_file_dir=None)
    - FLAC output format for quality
    - Model configurations from job or CLI defaults
    
    Args:
        temp_dir: Temporary directory for processing
        clean_instrumental_model: Model for clean instrumental separation (optional, uses default if not provided)
        backing_vocals_models: List of models for backing vocals separation (optional, uses default if not provided)
        other_stems_models: List of models for other stems separation (optional, uses default if not provided)
        
    Returns:
        Configured AudioProcessor instance
    """
    # Configure logger for AudioProcessor
    audio_logger = logging.getLogger("karaoke_gen.audio_processor")
    audio_logger.setLevel(logging.INFO)
    
    # Model configurations - use provided values or defaults from module constants
    effective_clean_model = clean_instrumental_model or DEFAULT_CLEAN_MODEL
    effective_backing_models = backing_vocals_models or DEFAULT_BACKING_MODELS
    effective_other_models = other_stems_models or DEFAULT_OTHER_MODELS  # For 6-stem separation (bass, drums, etc.)
    
    # FFmpeg command for combining audio files (must be a string, not a list)
    ffmpeg_base_command = "ffmpeg -hide_banner -loglevel error -nostats -y"
    
    return AudioProcessor(
        logger=audio_logger,
        log_level=logging.INFO,
        log_formatter=None,  # Not needed for our use case
        model_file_dir=None,  # No local models, using remote API
        lossless_output_format="FLAC",
        clean_instrumental_model=effective_clean_model,
        backing_vocals_models=effective_backing_models,
        other_stems_models=effective_other_models,
        ffmpeg_base_command=ffmpeg_base_command
    )


async def process_audio_separation(job_id: str) -> bool:
    """
    Process audio separation for a job using karaoke_gen.AudioProcessor.
    
    This is the main entry point for the audio worker.
    Called asynchronously from the job submission endpoint.
    
    Workflow:
    1. Download audio from GCS
    2. Stage 1: Separate with clean instrumental + other stems models (Modal API)
    3. Stage 2: Separate vocals for backing vocals (Modal API)
    4. Post-process: Combine instrumentals, normalize audio
    5. Upload all stems to GCS
    6. Mark job as AUDIO_COMPLETE
    
    Args:
        job_id: Job ID to process
        
    Returns:
        True if successful, False otherwise
    """
    start_time = time.time()
    job_manager = JobManager()
    storage = StorageService()
    settings = get_settings()
    
    # Create job logger for remote debugging FIRST
    job_log = create_job_logger(job_id, "audio")
    
    # Log with structured markers for easy Cloud Logging queries
    logger.info(f"[job:{job_id}] WORKER_START worker=audio")
    job_log.info("=== AUDIO WORKER STARTED ===")
    job_log.info(f"Job ID: {job_id}")

    # Register with worker registry to prevent premature container shutdown
    # This ensures Cloud Run waits for this worker to complete before terminating
    await worker_registry.register(job_id, "audio")

    # Set up log capture for AudioProcessor
    log_handler = setup_job_logging(job_id, "audio", *AUDIO_WORKER_LOGGERS)
    job_log.info(f"Log handler attached for {len(AUDIO_WORKER_LOGGERS)} loggers")
    
    job = job_manager.get_job(job_id)
    if not job:
        logger.error(f"[job:{job_id}] Job not found in Firestore")
        job_log.error(f"Job {job_id} not found in Firestore!")
        return False
    
    # Create temporary working directory
    temp_dir = tempfile.mkdtemp(prefix=f"karaoke_{job_id}_")
    job_log.info(f"Created temp directory: {temp_dir}")
    
    try:
        # Wrap entire worker in a tracing span
        with job_span("audio-worker", job_id, {"artist": job.artist, "title": job.title}) as root_span:
            # Use job_logging_context for proper log isolation when multiple jobs run concurrently
            # This ensures logs from third-party libraries (karaoke_gen.audio_processor) are
            # only captured by this job's handler, not handlers from other concurrent jobs
            with job_logging_context(job_id):
                job_log.info(f"Starting audio separation for {job.artist} - {job.title}")
                logger.info(f"[job:{job_id}] Starting audio separation for {job.artist} - {job.title}")
                
                # Ensure AUDIO_SEPARATOR_API_URL is set
                api_url = os.environ.get("AUDIO_SEPARATOR_API_URL")
                if not api_url:
                    raise Exception("AUDIO_SEPARATOR_API_URL environment variable not set. "
                                  "Cannot perform audio separation without remote API access.")
                job_log.info(f"Audio separator API: {api_url}")
                add_span_attribute("audio_separator_api", api_url)
                
                # Download audio file from GCS or URL
                with job_span("download-audio", job_id) as download_span:
                    job_log.info("Downloading audio file...")
                    audio_path = await download_audio(job_id, temp_dir, storage, job, job_manager_instance=job_manager)
                    if not audio_path:
                        raise Exception("Failed to download audio file")
                    job_log.info(f"Audio downloaded: {os.path.basename(audio_path)}")
                    download_span.set_attribute("audio_file", os.path.basename(audio_path))
                    download_span.set_attribute("source", "url" if job.url else "gcs")
                
                # Update progress using state_data (don't change status during parallel processing)
                # The status is managed at a higher level - workers just track their progress
                job_manager.update_state_data(job_id, 'audio_progress', {
                    'stage': 'separating_stage1',
                    'progress': 10,
                    'message': 'Starting audio separation (Stage 1: Clean instrumental)'
                })
                
                # Create AudioProcessor instance (reuses karaoke_gen code)
                # Use model configuration from job if provided, otherwise use defaults
                job_log.info("Creating AudioProcessor instance...")
                if job.clean_instrumental_model:
                    job_log.info(f"  Using clean instrumental model: {job.clean_instrumental_model}")
                    add_span_attribute("clean_model", job.clean_instrumental_model)
                if job.backing_vocals_models:
                    job_log.info(f"  Using backing vocals models: {job.backing_vocals_models}")
                if job.other_stems_models:
                    job_log.info(f"  Using other stems models: {job.other_stems_models}")
                
                audio_processor = create_audio_processor(
                    temp_dir,
                    clean_instrumental_model=job.clean_instrumental_model,
                    backing_vocals_models=job.backing_vocals_models,
                    other_stems_models=job.other_stems_models
                )

                # Store effective model names in state_data for video_worker to use in file naming
                # This ensures output filenames match local CLI behavior (e.g., "Instrumental model_bs_roformer_ep_317_sdr_12.9755.ckpt")
                effective_model_names = {
                    'clean_instrumental_model': job.clean_instrumental_model or DEFAULT_CLEAN_MODEL,
                    'backing_vocals_models': job.backing_vocals_models or DEFAULT_BACKING_MODELS,
                    'other_stems_models': job.other_stems_models or DEFAULT_OTHER_MODELS,
                }
                job_manager.update_state_data(job_id, 'model_names', effective_model_names)
                job_log.info(f"Stored effective model names: clean={effective_model_names['clean_instrumental_model']}")

                # Format artist-title for file naming (matches CLI behavior)
                # Sanitize to handle Unicode characters (curly quotes, em dashes, etc.)
                # that cause HTTP header encoding issues with the remote API
                from karaoke_gen.utils import sanitize_filename
                safe_artist = sanitize_filename(job.artist) if job.artist else "Unknown"
                safe_title = sanitize_filename(job.title) if job.title else "Unknown"
                artist_title = f"{safe_artist} - {safe_title}"
                
                # Run audio separation (calls Modal API internally)
                # This returns a dict with paths to all separated stems
                with job_span("modal-separation", job_id) as sep_span:
                    sep_start = time.time()
                    job_log.info("Starting audio separation (this may take 5-10 minutes)...")
                    job_log.info("  Stage 1: Clean instrumental separation (MDX models)")
                    job_log.info("  Stage 2: Backing vocals separation (Demucs model)")
                    add_span_event("separation_started")
                    logger.info(f"[job:{job_id}] Calling Modal API for audio separation")
                    
                    with metrics.time_external_api("modal", job_id):
                        separation_result = audio_processor.process_audio_separation(
                            audio_file=audio_path,
                            artist_title=artist_title,
                            track_output_dir=temp_dir
                        )
                    
                    sep_duration = time.time() - sep_start
                    sep_span.set_attribute("duration_seconds", sep_duration)
                    sep_span.set_attribute("stem_count", len(separation_result))
                    add_span_event("separation_completed", {"duration_seconds": sep_duration})
                
                job_log.info("Audio separation complete!")
                job_log.info(f"  Generated {len(separation_result)} stem files")
                logger.info(f"[job:{job_id}] Audio separation complete, organizing results")
                
                # Update progress using state_data (don't change status during parallel processing)
                job_manager.update_state_data(job_id, 'audio_progress', {
                    'stage': 'audio_complete',
                    'progress': 45,
                    'message': 'Audio separation complete, uploading stems'
                })
                
                # Upload all stems to GCS
                with job_span("upload-stems", job_id) as upload_span:
                    await upload_separation_results(job_id, separation_result, storage, job_manager)
                    upload_span.set_attribute("stem_count", len(separation_result))
                
                logger.info(f"[job:{job_id}] All stems uploaded successfully")
                
                # Mark audio processing complete
                # This will check if lyrics are also complete and transition to next stage if so
                job_manager.mark_audio_complete(job_id)
                
                duration = time.time() - start_time
                root_span.set_attribute("duration_seconds", duration)
                logger.info(f"[job:{job_id}] WORKER_END worker=audio status=success duration={duration:.1f}s")
                return True
        
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"[job:{job_id}] WORKER_END worker=audio status=error duration={duration:.1f}s error={e}")
        job_manager.mark_job_failed(
            job_id=job_id,
            error_message=f"Audio separation failed: {str(e)}",
            error_details={"stage": "audio_separation", "error": str(e)}
        )
        return False
        
    finally:
        # Unregister from worker registry to signal completion
        await worker_registry.unregister(job_id, "audio")

        # Remove log handler to avoid duplicate logging on future runs
        for logger_name in AUDIO_WORKER_LOGGERS:
            try:
                logging.getLogger(logger_name).removeHandler(log_handler)
            except Exception:
                pass

        # Cleanup temporary directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.debug(f"[job:{job_id}] Cleaned up temp directory: {temp_dir}")


async def download_audio(
    job_id: str,
    temp_dir: str,
    storage: StorageService,
    job,
    job_manager_instance: JobManager = None
) -> Optional[str]:
    """
    Download or fetch audio file to local temp directory.
    
    Handles two cases:
    1. Uploaded file: Download from GCS using input_media_gcs_path
    2. URL (YouTube, etc.): Download using yt-dlp or other tools
    
    Args:
        job_id: Job ID
        temp_dir: Temporary directory to save to
        storage: StorageService instance
        job: Job object with URL or GCS path
        job_manager_instance: Optional JobManager instance for updating job metadata
    
    Returns:
        Path to downloaded audio file, or None if failed
    """
    try:
        from karaoke_gen.utils import sanitize_filename

        # Case 1: File was uploaded to GCS
        if job.input_media_gcs_path:
            logger.info(f"Job {job_id}: Downloading uploaded file from GCS: {job.input_media_gcs_path}")
            # Sanitize filename to handle Unicode chars that cause HTTP header encoding issues
            safe_filename = sanitize_filename(job.filename) if job.filename else "input.flac"
            local_path = os.path.join(temp_dir, safe_filename)
            storage.download_file(job.input_media_gcs_path, local_path)
            logger.info(f"Job {job_id}: Downloaded uploaded file to {local_path}")
            return local_path
        
        # Case 2: URL download (from file_urls if already downloaded, or from job.url)
        if job.file_urls and job.file_urls.get('input'):
            # Already downloaded and stored in GCS
            input_url = job.file_urls.get('input')
            local_path = os.path.join(temp_dir, "input.flac")
            storage.download_file(input_url, local_path)
            logger.info(f"Job {job_id}: Downloaded audio from GCS: {input_url}")
            return local_path
        
        # Case 3: Fresh URL that needs downloading (legacy fallback)
        # NOTE: YouTube URLs should be downloaded by YouTubeDownloadService in file_upload.py
        # BEFORE triggering this worker. If we reach here with a YouTube URL, it means
        # the download was not done upfront, which will likely fail due to bot detection.
        if job.url:
            # Check if this is a YouTube URL that should have been handled earlier
            is_youtube = any(domain in job.url.lower() for domain in ['youtube.com', 'youtu.be'])
            if is_youtube:
                logger.warning(
                    f"Job {job_id}: YouTube URL reached download_audio() fallback. "
                    "This may fail due to bot detection. YouTube URLs should be "
                    "downloaded via YouTubeDownloadService before triggering workers."
                )

            logger.info(f"Job {job_id}: Downloading from URL: {job.url}")

            # Use provided job_manager or create new one
            jm = job_manager_instance or JobManager()

            local_path = await download_from_url(
                job.url,
                temp_dir,
                job.artist,
                job.title,
                job_manager=jm,
                job_id=job_id
            )

            if local_path and os.path.exists(local_path):
                # Upload to GCS and update job
                gcs_path = f"jobs/{job_id}/input/{os.path.basename(local_path)}"
                url = storage.upload_file(local_path, gcs_path)

                # Update job with GCS path for lyrics worker
                jm.update_job(job_id, {'input_media_gcs_path': gcs_path})
                jm.update_file_url(job_id, 'input', 'audio', url)

                logger.info(f"Job {job_id}: Downloaded and uploaded audio to GCS: {gcs_path}")

                # For URL jobs, trigger lyrics worker now that audio is available
                # This is the sequential trigger pattern - audio first, then lyrics
                await _trigger_lyrics_worker_after_url_download(job_id)

                return local_path
            else:
                logger.error(f"Job {job_id}: Failed to download from URL: {job.url}")
                return None
        
        logger.error(f"Job {job_id}: No input source found (no GCS path, file_urls, or URL)")
        return None
        
    except Exception as e:
        logger.error(f"Job {job_id}: Failed to download audio: {e}", exc_info=True)
        return None


async def upload_separation_results(
    job_id: str,
    separation_result: Dict[str, Any],
    storage: StorageService,
    job_manager: JobManager
) -> None:
    """
    Upload all audio separation results to GCS.
    
    The separation_result dict from AudioProcessor.process_audio_separation() contains:
    - clean_instrumental: Dict with 'vocals' and 'instrumental' paths
    - other_stems: Dict with stem paths (bass, drums, guitar, piano, other)
    - backing_vocals: Dict with model-keyed paths to lead_vocals and backing_vocals
    - combined_instrumentals: Dict with model-keyed paths to instrumental+BV files
    
    Args:
        job_id: Job ID
        separation_result: Result dict from AudioProcessor
        storage: StorageService instance
        job_manager: JobManager instance
    """
    logger.info(f"Job {job_id}: Uploading separation results to GCS")
    
    # Upload clean instrumental + vocals (Stage 1, clean model)
    if separation_result.get("clean_instrumental"):
        clean = separation_result["clean_instrumental"]
        
        if clean.get("instrumental") and os.path.exists(clean["instrumental"]):
            gcs_path = f"jobs/{job_id}/stems/instrumental_clean.flac"
            url = storage.upload_file(clean["instrumental"], gcs_path)
            job_manager.update_file_url(job_id, 'stems', 'instrumental_clean', url)
            logger.info(f"Job {job_id}: Uploaded clean instrumental")
        
        if clean.get("vocals") and os.path.exists(clean["vocals"]):
            gcs_path = f"jobs/{job_id}/stems/vocals_clean.flac"
            url = storage.upload_file(clean["vocals"], gcs_path)
            job_manager.update_file_url(job_id, 'stems', 'vocals_clean', url)
            logger.info(f"Job {job_id}: Uploaded clean vocals")
    
    # Upload other stems (Stage 1, htdemucs 6-stem)
    if separation_result.get("other_stems"):
        for stem_name, stem_value in separation_result["other_stems"].items():
            # Handle both string paths and nested dicts
            if isinstance(stem_value, str):
                stem_path = stem_value
            elif isinstance(stem_value, dict):
                # Some models return nested dicts like {"path": "/path/to/file"}
                stem_path = stem_value.get("path") or stem_value.get("file")
                logger.debug(f"Job {job_id}: other_stems[{stem_name}] is dict: {stem_value}")
            else:
                logger.warning(f"Job {job_id}: Unexpected type for other_stems[{stem_name}]: {type(stem_value)}")
                continue
            
            if stem_path and isinstance(stem_path, str) and os.path.exists(stem_path):
                gcs_path = f"jobs/{job_id}/stems/{stem_name.lower()}.flac"
                url = storage.upload_file(stem_path, gcs_path)
                job_manager.update_file_url(job_id, 'stems', stem_name.lower(), url)
                logger.info(f"Job {job_id}: Uploaded {stem_name} stem")
    
    # Upload backing vocals separation (Stage 2)
    if separation_result.get("backing_vocals"):
        # backing_vocals is a dict keyed by model name
        for model_name, bv_stems in separation_result["backing_vocals"].items():
            if bv_stems.get("lead_vocals") and os.path.exists(bv_stems["lead_vocals"]):
                gcs_path = f"jobs/{job_id}/stems/lead_vocals.flac"
                url = storage.upload_file(bv_stems["lead_vocals"], gcs_path)
                job_manager.update_file_url(job_id, 'stems', 'lead_vocals', url)
                logger.info(f"Job {job_id}: Uploaded lead vocals")
            
            if bv_stems.get("backing_vocals") and os.path.exists(bv_stems["backing_vocals"]):
                gcs_path = f"jobs/{job_id}/stems/backing_vocals.flac"
                url = storage.upload_file(bv_stems["backing_vocals"], gcs_path)
                job_manager.update_file_url(job_id, 'stems', 'backing_vocals', url)
                logger.info(f"Job {job_id}: Uploaded backing vocals")
            
            # Only process first model (we typically only use one backing vocals model)
            break
    
    # Upload combined instrumentals (instrumental + backing vocals)
    if separation_result.get("combined_instrumentals"):
        # combined_instrumentals is a dict keyed by model name
        for model_name, combined_path in separation_result["combined_instrumentals"].items():
            if combined_path and os.path.exists(combined_path):
                gcs_path = f"jobs/{job_id}/stems/instrumental_with_backing.flac"
                url = storage.upload_file(combined_path, gcs_path)
                job_manager.update_file_url(job_id, 'stems', 'instrumental_with_backing', url)
                logger.info(f"Job {job_id}: Uploaded instrumental with backing vocals")
            
            # Only process first model
            break
    
    # Store instrumental options in state_data for later selection
    instrumental_options = {}
    if separation_result.get("clean_instrumental", {}).get("instrumental"):
        instrumental_options["clean"] = f"jobs/{job_id}/stems/instrumental_clean.flac"
    if separation_result.get("combined_instrumentals"):
        instrumental_options["with_backing"] = f"jobs/{job_id}/stems/instrumental_with_backing.flac"
    
    if instrumental_options:
        job_manager.update_state_data(job_id, 'instrumental_options', instrumental_options)
        logger.info(f"Job {job_id}: Stored instrumental options: {list(instrumental_options.keys())}")


# ==================== CLI Entry Point for Cloud Run Jobs ====================
# This allows the audio worker to be run as a standalone Cloud Run Job.
# Usage: python -m backend.workers.audio_worker --job-id <job_id>

def main():
    """
    CLI entry point for running audio worker as a Cloud Run Job.

    Cloud Run Jobs run to completion without HTTP request lifecycle concerns,
    avoiding the instance termination issue where Cloud Run would shut down
    instances mid-processing when using BackgroundTasks.

    Usage:
        python -m backend.workers.audio_worker --job-id abc123

    Environment Variables:
        GOOGLE_CLOUD_PROJECT: GCP project ID (required)
        GCS_BUCKET_NAME: Storage bucket name (required)
        AUDIO_SEPARATOR_API_URL: Modal API URL (required)
    """
    import argparse
    import asyncio
    import sys

    # Set up argument parser
    parser = argparse.ArgumentParser(
        description="Audio separation worker for karaoke generation"
    )
    parser.add_argument(
        "--job-id",
        required=True,
        help="Job ID to process"
    )

    args = parser.parse_args()
    job_id = args.job_id

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    logger.info(f"Starting audio worker CLI for job {job_id}")

    # Run the async worker function
    try:
        success = asyncio.run(process_audio_separation(job_id))
        if success:
            logger.info(f"Audio separation completed successfully for job {job_id}")
            sys.exit(0)
        else:
            logger.error(f"Audio separation failed for job {job_id}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Audio worker crashed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

